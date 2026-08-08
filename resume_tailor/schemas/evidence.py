"""Evidence provenance models — the citation layer of the whole pipeline.

From implementation guide §4, extended with the Evidence Graph (see project.md):
Chroma answers "what is similar?"; the graph answers "show me the provenance" —
which is exactly what the trace panel (demo moment #1) and the Verifier (demo
moment #2) consume.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["code", "commit", "cert", "readme", "resume", "brain_dump"]


class Evidence(BaseModel):
    """A single verifiable proof item extracted from an input source."""

    source_id: str  # e.g. "repo:backend-api#file:queue.py#L45"
    source_type: SourceType
    skill_tags: list[str] = Field(default_factory=list)  # e.g. ["redis", "bullmq", "async"]
    snippet: str  # the actual proof text/code
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    url: str = Field(default="", description="Deep link for the citation")


class EvidenceChunk(BaseModel):
    """A retrievable chunk of a source, embedded once and indexed in Chroma."""

    chunk_id: str  # e.g. "...#file:queue.py#L45#chunk0"
    source_id: str
    text: str
    skill_tags: list[str] = Field(default_factory=list)
    embedding_id: str | None = Field(default=None, description="Chroma vector id, if stored")


class EvidenceGraph(BaseModel):
    """Provenance graph: sources -> chunks -> skills / claims.

    Persisted as versioned JSON alongside Chroma. Edges are adjacency dicts so
    the trace panel gets O(1) lookups and the Verifier can enumerate a claim's
    backing evidence.
    """

    version: int = 1
    sources: dict[str, Evidence] = Field(default_factory=dict)  # source_id -> Evidence
    chunks: dict[str, EvidenceChunk] = Field(default_factory=dict)  # chunk_id -> chunk
    skill_to_chunks: dict[str, list[str]] = Field(default_factory=dict)
    claim_to_chunks: dict[str, list[str]] = Field(default_factory=dict)

    def add_source(self, evidence: Evidence) -> None:
        self.sources[evidence.source_id] = evidence

    def add_chunk(self, chunk: EvidenceChunk) -> None:
        self.chunks[chunk.chunk_id] = chunk
        for tag in chunk.skill_tags:
            self.skill_to_chunks.setdefault(tag, [])
            if chunk.chunk_id not in self.skill_to_chunks[tag]:
                self.skill_to_chunks[tag].append(chunk.chunk_id)

    def add_claim(self, claim_id: str, chunk_ids: list[str]) -> None:
        self.claim_to_chunks[claim_id] = chunk_ids

    def chunks_for_skill(self, skill: str) -> list[EvidenceChunk]:
        return [self.chunks[cid] for cid in self.skill_to_chunks.get(skill, []) if cid in self.chunks]

    def provenance_for(self, claim_id: str) -> list[Evidence]:
        """Evidence backing a claim, deduplicated by source (for citations)."""
        seen: dict[str, Evidence] = {}
        for cid in self.claim_to_chunks.get(claim_id, []):
            chunk = self.chunks.get(cid)
            if chunk is None:
                continue
            evidence = self.sources.get(chunk.source_id)
            if evidence is not None and evidence.source_id not in seen:
                seen[evidence.source_id] = evidence
        return list(seen.values())
