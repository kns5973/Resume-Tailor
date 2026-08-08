import json
from pathlib import Path

import pytest

from resume_tailor.collector import CollectorInput, collect, collect_sync
from resume_tailor.collector.embed import FakeEmbedder
from resume_tailor.collector.github_reader import RepoFilters, save_snapshot
from resume_tailor.collector.vector_store import VectorStore

FIXTURE = Path(__file__).parent / "fixtures" / "github_snapshot.json"


def _seed_cache(tmp_path) -> Path:
    cache = tmp_path / "cache"
    save_snapshot(cache, "janedemo", json.loads(FIXTURE.read_text(encoding="utf-8")))
    return cache


def _full_input(tmp_path) -> CollectorInput:
    return CollectorInput(
        jd_text="Senior Backend Engineer. Must know Python, Redis, PostgreSQL, Docker.",
        github_username="janedemo",
        brain_dump="I built async queues with Redis at my last job. Also did frontend work with React.",
    )


def test_collect_parallel_populates_graph_and_store(tmp_path):
    cache = _seed_cache(tmp_path)
    store = VectorStore(path=tmp_path / "chroma")  # persistent -> isolated per test
    result = collect_sync(
        _full_input(tmp_path),
        embedder=FakeEmbedder(),
        store=store,
        cache_dir=cache,
        repo_filters=RepoFilters(min_stars=1),
    )

    assert result.jd_text.startswith("Senior Backend Engineer")
    assert result.warnings == []
    assert result.stats["sources"] >= 5  # 2 readmes + 2 files + 3 commits (backend-api, gitlytics)
    assert result.stats["chunks"] == result.stats["embedded_chunks"]
    assert store.count() == result.stats["chunks"]

    # Graph is persisted as JSON
    graph_path = cache / "evidence_graph.json"
    assert graph_path.exists()
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert graph["version"] == 1
    assert "repo:janedemo/backend-api#readme" in graph["sources"]


def test_collect_retrieval_returns_evidence(tmp_path):
    cache = _seed_cache(tmp_path)
    store = VectorStore(path=tmp_path / "chroma")
    result = collect_sync(_full_input(tmp_path), embedder=FakeEmbedder(), store=store, cache_dir=cache)

    hits = store.query(FakeEmbedder().embed(["Redis async task queue"]), n_results=3)
    assert hits and hits[0]
    top = hits[0][0]
    assert top["chunk_id"].startswith("repo:janedemo/")
    assert "source_id" in top["metadata"]
    assert "source_type" in top["metadata"]


def test_collect_reader_failure_becomes_warning(tmp_path):
    cache = _seed_cache(tmp_path)
    store = VectorStore(path=tmp_path / "chroma")
    input_ = CollectorInput(
        jd_text="JD text",
        github_username="janedemo",
        resume_pdf_paths=["/nonexistent/resume.pdf"],  # pdfplumber raises
    )
    result = collect_sync(input_, embedder=FakeEmbedder(), store=store, cache_dir=cache)
    assert any("resume:" in w for w in result.warnings)
    # GitHub evidence still collected despite the resume failure
    assert result.stats["sources"] > 0
    assert store.count() > 0


def test_collect_requires_an_input():
    with pytest.raises(ValueError):
        collect_sync(CollectorInput(), embedder=FakeEmbedder())


def test_collect_jd_scrape_failure_is_warning(tmp_path):
    cache = _seed_cache(tmp_path)
    store = VectorStore(path=tmp_path / "chroma")
    result = collect_sync(
        CollectorInput(jd_url="https://127.0.0.1:1/nonexistent", github_username="janedemo"),
        embedder=FakeEmbedder(),
        store=store,
        cache_dir=cache,
    )
    assert any("jd_url" in w for w in result.warnings)
    assert result.jd_text == ""
