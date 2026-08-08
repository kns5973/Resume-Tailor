"""Collector Agent — ingest all inputs in parallel, index once.

Guide §3 step 1: the collectors (GitHub / resume / certs / JD-URL / brain dump)
are independent, so they run concurrently via asyncio.gather. Everything that
comes back is chunked, embedded exactly once, stored in Chroma, and merged into
the EvidenceGraph JSON.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, Field

from resume_tailor.collector import brain_dump, github_reader, jd_scraper, ocr_certs, resume_parser
from resume_tailor.collector.base import SourceArtifact
from resume_tailor.collector.embed import Embedder, FakeEmbedder, SentenceTransformerEmbedder
from resume_tailor.collector.github_reader import RepoFilters
from resume_tailor.collector.vector_store import VectorStore
from resume_tailor.schemas import EvidenceGraph

__all__ = [
    "CollectorInput",
    "CollectorResult",
    "collect",
    "collect_sync",
    "Embedder",
    "FakeEmbedder",
    "SentenceTransformerEmbedder",
    "RepoFilters",
    "VectorStore",
]


class CollectorInput(BaseModel):
    """Everything the user can hand the pipeline."""

    jd_text: str = Field(default="", description="Pasted job description (takes priority over jd_url)")
    jd_url: str = Field(default="", description="Job description URL, scraped via trafilatura")
    github_username: str = Field(default="")
    resume_pdf_paths: list[str] = Field(default_factory=list)
    cert_paths: list[str] = Field(default_factory=list)
    brain_dump: str = Field(default="")

    def is_empty(self) -> bool:
        return not (
            self.jd_text.strip()
            or self.jd_url.strip()
            or self.github_username.strip()
            or self.resume_pdf_paths
            or self.cert_paths
            or self.brain_dump.strip()
        )


class CollectorResult(BaseModel):
    """Output of the Collector: everything downstream (Matcher/Builder) needs."""

    graph: EvidenceGraph = Field(default_factory=EvidenceGraph)
    jd_text: str = Field(default="", description="Resolved JD text (pasted or scraped)")
    warnings: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)

    def persist(self, cache_dir: str | Path) -> Path:
        """Write the EvidenceGraph as versioned JSON (fixture/offline mode)."""
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / "evidence_graph.json"
        path.write_text(self.graph.model_dump_json(indent=2), encoding="utf-8")
        return path


async def _read(name: str, fn):
    """Run a sync reader in a thread; return (name, result-or-exception)."""
    try:
        return name, await asyncio.to_thread(fn)
    except Exception as exc:  # noqa: BLE001 — reader failure becomes a warning
        return name, exc


async def collect(
    input_: CollectorInput,
    *,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
    github_token: str | None = None,
    cache_dir: str | Path | None = None,
    repo_filters: RepoFilters | None = None,
    refresh: bool = False,
) -> CollectorResult:
    """Run every reader in parallel (incl. JD-URL scraping), then index once."""
    if input_.is_empty():
        raise ValueError("provide at least one input (JD text/URL, GitHub, resume PDF, certs, or brain dump)")

    warnings: list[str] = []
    jd_text = input_.jd_text.strip()  # pasted text wins over URL scraping

    # One gather task per reader — everything runs in parallel (guide rule #1).
    tasks = []
    if input_.jd_url and not jd_text:
        tasks.append(_read("jd", lambda: jd_scraper.scrape_jd_url(input_.jd_url)))
    if input_.github_username:
        tasks.append(
            _read(
                "github",
                lambda: github_reader.collect_github(
                    input_.github_username,
                    token=github_token,
                    cache_dir=cache_dir,
                    filters=repo_filters,
                    refresh=refresh,
                ),
            )
        )
    if input_.resume_pdf_paths:
        tasks.append(_read("resume", lambda: resume_parser.parse_resumes(input_.resume_pdf_paths)))
    if input_.brain_dump:
        tasks.append(_read("brain_dump", lambda: brain_dump.read_brain_dump(input_.brain_dump)))
    if input_.cert_paths:
        tasks.append(_read("certs", lambda: ocr_certs.ocr_certs(input_.cert_paths)))

    results = await asyncio.gather(*tasks)

    artifacts: list[SourceArtifact] = []
    for name, res in results:
        if name == "jd":
            if isinstance(res, Exception):
                warnings.append(f"jd_url scrape failed: {res}")
            elif not (res or "").strip():
                warnings.append(f"jd_url scrape returned no text: {input_.jd_url}")
            else:
                jd_text = (res or "").strip()
            continue
        if isinstance(res, Exception):
            warnings.append(f"{name}: {res}")
        else:
            artifacts.extend(res)

    # Merge into the EvidenceGraph.
    graph = EvidenceGraph()
    for artifact in artifacts:
        graph.add_source(artifact.evidence)
        for chunk in artifact.chunks:
            graph.add_chunk(chunk)

    # Embed once + store (efficiency rule #2).
    store = store or VectorStore()
    texts = [c.text for a in artifacts for c in a.chunks]
    ids = [c.chunk_id for a in artifacts for c in a.chunks]
    metadatas: list[dict] = []
    for artifact in artifacts:
        for chunk in artifact.chunks:
            src = graph.sources.get(chunk.source_id)  # never KeyError on a stray chunk
            metadatas.append(
                {
                    "source_id": chunk.source_id,
                    "source_type": src.source_type if src else "unknown",
                    "skill_tags": ",".join(src.skill_tags) if src else "",
                }
            )
    if texts:
        embedder = embedder or SentenceTransformerEmbedder()
        embeddings = embedder.embed(texts)
        store.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)

    result = CollectorResult(
        graph=graph,
        jd_text=jd_text,
        warnings=warnings,
        stats={
            "sources": len(graph.sources),
            "chunks": len(graph.chunks),
            "embedded_chunks": len(texts),
        },
    )
    if cache_dir is not None:
        result.persist(cache_dir)
    return result


def collect_sync(
    input_: CollectorInput,
    *,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
    github_token: str | None = None,
    cache_dir: str | Path | None = None,
    repo_filters: RepoFilters | None = None,
    refresh: bool = False,
) -> CollectorResult:
    """Sync wrapper around collect() for CLI/Streamlit use."""
    return asyncio.run(
        collect(
            input_,
            embedder=embedder,
            store=store,
            github_token=github_token,
            cache_dir=cache_dir,
            repo_filters=repo_filters,
            refresh=refresh,
        )
    )
