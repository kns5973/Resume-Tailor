import uuid

import pytest

from resume_tailor.collector.embed import FakeEmbedder
from resume_tailor.collector.vector_store import VectorStore
from resume_tailor.llm import MockLLMClient
from resume_tailor.matcher import MatchConfig, match_jd
from resume_tailor.schemas import JDRequirement, ParsedJD

# Short chunks + queries engineered for the character-based FakeEmbedder at
# dim=256 (exact character buckets): identical text -> distance 0 (matched);
# "queuing" vs "redis" -> ~0.66 (mid-range, reformulation territory);
# "zzz qqq www" (chars absent from the corpus) -> 1.0 (hopeless gap).
CORPUS = [("c1", "redis"), ("c2", "python fastapi"), ("c3", "typescript react")]

# Thresholds tuned to FakeEmbedder's compressed distances (vs all-MiniLM's scale)
FAKE_CONFIG = MatchConfig(match_threshold=0.25, low_conf_threshold=0.5)


def _emb() -> FakeEmbedder:
    return FakeEmbedder(dim=256)  # exact per-character buckets, no % 8 collisions


def _store() -> VectorStore:
    store = VectorStore(collection_name=f"matcher_{uuid.uuid4().hex[:8]}")
    emb = _emb()
    store.add(
        ids=[c[0] for c in CORPUS],
        embeddings=emb.embed([c[1] for c in CORPUS]),
        documents=[c[1] for c in CORPUS],
        metadatas=[{"source_id": f"repo:x#file:{c[0]}.py", "source_type": "code", "skill_tags": ""} for c in CORPUS],
    )
    return store


def _jd(*requirements: str) -> ParsedJD:
    return ParsedJD(requirements=[JDRequirement(requirement=r) for r in requirements])


def test_matched_and_gap():
    client = MockLLMClient({"matcher_reformulation": ['{"queries": ["kafka kafka"]}']})
    result = match_jd(_jd("redis", "kafka"), _store(), _emb(), client=client, config=FAKE_CONFIG)
    assert [m.requirement for m in result.matches] == ["redis"]
    assert [g.requirement for g in result.gaps] == ["kafka"]
    # matched carries real evidence
    assert result.matches[0].hits
    assert result.matches[0].hits[0].source_id == "repo:x#file:c1.py"
    # gap carries no fabricated hits
    assert result.gaps[0].hits == []
    assert result.gaps[0].best_distance is not None


def test_reformulation_saves_a_near_miss():
    """'queuing' lands between thresholds -> reformulate to 'redis' -> matched."""
    client = MockLLMClient({"matcher_reformulation": ['{"queries": ["redis"]}']})
    config = MatchConfig(match_threshold=0.35, low_conf_threshold=0.8)
    result = match_jd(_jd("queuing"), _store(), _emb(), client=client, config=config)
    assert result.matches and result.matches[0].status == "matched"
    assert result.matches[0].query_trace == ["queuing", "redis"]
    # the trace is what demo moment #1 displays
    assert len(client.calls) >= 1


def test_reformulation_multiquery_trace_and_pooling():
    """A reformulation may return 2 queries; both land in the trace and pool."""
    client = MockLLMClient({"matcher_reformulation": ['{"queries": ["zzz qqq www", "redis"]}']})
    config = MatchConfig(match_threshold=0.35, low_conf_threshold=0.8)
    result = match_jd(_jd("queuing"), _store(), _emb(), client=client, config=config)
    assert result.matches[0].query_trace == ["queuing", "zzz qqq www", "redis"]
    assert result.matches[0].status == "matched"
    assert result.matches[0].hits[0].chunk_id == "c1"


def test_degenerate_config_rejected():
    with pytest.raises(ValueError):
        MatchConfig(match_threshold=0.7, low_conf_threshold=0.5)


def test_reformulation_exhausted_becomes_gap():
    """'queuing' keeps landing mid-range even after ≤2 reformulations -> gap."""
    client = MockLLMClient({"matcher_reformulation": ['{"queries": ["queuing"]}']})
    config = MatchConfig(match_threshold=0.35, low_conf_threshold=0.8)
    result = match_jd(_jd("queuing"), _store(), _emb(), client=client, config=config)
    assert result.gaps and result.gaps[0].status == "gap"
    assert result.gaps[0].query_trace == ["queuing", "queuing", "queuing"]  # original + 2 retries
    assert result.matches == []


def test_hopeless_gap_skips_reformulation():
    client = MockLLMClient()  # would raise if called — proves we don't waste tokens
    result = match_jd(_jd("zzz qqq www"), _store(), _emb(), client=client, config=MatchConfig())
    assert result.gaps and len(result.gaps[0].query_trace) == 1
    assert client.calls == []


def test_batch_embed_once():
    class CountingEmbedder(FakeEmbedder):
        def __init__(self):
            super().__init__(dim=256)
            self.batch_sizes = []

        def embed(self, texts):
            self.batch_sizes.append(len(texts))
            return super().embed(texts)

    emb = CountingEmbedder()
    # "kafka" lands just above the default match threshold (0.627) -> it will
    # reformulate; register a mock so the reformulation path is exercised, not crashed.
    client = MockLLMClient({"matcher_reformulation": ['{"queries": ["kafka kafka"]}']})
    match_jd(_jd("redis", "python fastapi", "kafka"), _store(), emb, client=client)
    assert emb.batch_sizes[0] == 3  # efficiency rule #3: one batched call


def test_keyword_boost_rescues_exact_term_miss():
    """'docker' misses semantically (hopeless gap) but its exact keyword lands
    in the lexical pool -> matched, tagged retrieval_source='lexical'."""
    store = VectorStore(collection_name=f"matcher_{uuid.uuid4().hex[:8]}")
    emb = _emb()
    store.add(
        ids=["c4"],
        embeddings=emb.embed(["zzz qqq www yyy docker"]),
        documents=["zzz qqq www yyy docker"],
        metadatas=[{"source_id": "repo:x#file:docker.py", "source_type": "code", "skill_tags": "docker"}],
    )
    jd = _jd("docker")
    jd.requirements[0].keywords = ["docker"]

    # Semantic-only (default): distance ~0.62 >= low_conf 0.5 -> hopeless gap,
    # and no LLM call is made.
    client = MockLLMClient()
    without = match_jd(jd, store, emb, client=client, config=FAKE_CONFIG)
    assert without.gaps and without.gaps[0].status == "gap"
    assert client.calls == []

    # Hybrid: lexical hit at 0.2 < match_threshold 0.25 -> matched.
    with_kw = match_jd(
        jd,
        store,
        emb,
        client=MockLLMClient(),
        config=MatchConfig(match_threshold=0.25, low_conf_threshold=0.5, use_keywords=True),
    )
    assert with_kw.matches and with_kw.matches[0].status == "matched"
    best = with_kw.matches[0].hits[0]
    assert best.chunk_id == "c4"
    assert best.retrieval_source == "lexical"
    assert best.distance == 0.2


def test_keyword_boost_dedupes_with_semantic_hits():
    """A chunk hit both semantically AND lexically appears once — semantic wins."""
    store = _store()  # c1 = "redis"
    emb = _emb()
    jd = _jd("redis")
    jd.requirements[0].keywords = ["redis"]
    result = match_jd(
        jd,
        store,
        emb,
        client=MockLLMClient(),
        config=MatchConfig(match_threshold=0.25, low_conf_threshold=0.5, use_keywords=True),
    )
    c1_hits = [h for h in result.matches[0].hits if h.chunk_id == "c1"]
    assert len(c1_hits) == 1  # deduped
    assert c1_hits[0].retrieval_source == "semantic"  # distance 0.0 < lexical 0.2


def test_keyword_boost_off_by_default():
    """use_keywords defaults to False — existing behavior is untouched."""
    config = MatchConfig()
    assert config.use_keywords is False


def test_keyword_boost_requires_lexical_distance_below_match():
    with pytest.raises(ValueError):
        MatchConfig(match_threshold=0.3, low_conf_threshold=0.5, use_keywords=True, lexical_distance=0.4)


def test_empty_store_means_all_gaps():
    empty = VectorStore(collection_name=f"matcher_{uuid.uuid4().hex[:8]}")
    result = match_jd(_jd("redis", "kafka"), empty, _emb(), client=MockLLMClient())
    assert len(result.gaps) == 2
    assert all(g.best_distance is None for g in result.gaps)


def test_no_requirements_yields_empty_result():
    assert match_jd(_jd(), _store(), _emb()).all == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("python fastapi", "matched"),
        ("typescript react", "matched"),
        ("zzz qqq www", "gap"),
    ],
)
def test_known_corpus_behaviors(text, expected):
    result = match_jd(_jd(text), _store(), _emb(), client=MockLLMClient())
    statuses = {m.status for m in result.all}
    assert (expected == "matched") == ("matched" in statuses)
