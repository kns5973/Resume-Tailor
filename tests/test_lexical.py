"""LexicalIndex tests: whole-word matching, case-insensitivity, ranking by
keyword coverage, and EvidenceHit construction for the matcher's hybrid path."""
from resume_tailor.collector.lexical import LexicalIndex
from resume_tailor.schemas import EvidenceHit

CHUNKS = [
    {
        "chunk_id": "c1",
        "text": "Built a Redis-backed message queue with retries",
        "metadata": {"source_id": "repo:x#file:q.py", "source_type": "code", "skill_tags": "redis"},
    },
    {
        "chunk_id": "c2",
        "text": "Docker compose for local dev",
        "metadata": {"source_id": "repo:x#file:d.py", "source_type": "code", "skill_tags": "docker"},
    },
    {
        "chunk_id": "c3",
        "text": "node.js cli tools",
        "metadata": {"source_id": "repo:x#file:n.py", "source_type": "code", "skill_tags": ""},
    },
    {
        "chunk_id": "c4",
        "text": "unrelated lawn care",
        "metadata": {"source_id": "repo:x#file:l.py", "source_type": "code", "skill_tags": ""},
    },
    {
        "chunk_id": "c5",
        "text": "prediscovery and rediscovery are not redis",
        "metadata": {"source_id": "repo:x#file:r.py", "source_type": "code", "skill_tags": ""},
    },
]


def _index() -> LexicalIndex:
    return LexicalIndex(CHUNKS)


def test_single_keyword_hits_whole_words_only():
    # c5 matches because it contains the bare word "redis" — but NOT via
    # "rediscovery" (covered by test_word_boundaries below).
    assert _index().hit_ids_for(["redis"]) == ["c1", "c5"]


def test_word_boundaries():
    # "redis" must not match inside "rediscovery"/"prediscovery"
    idx = LexicalIndex(
        [
            {"chunk_id": "a", "text": "rediscovery", "metadata": {}},
            {"chunk_id": "b", "text": "redis here", "metadata": {}},
            {"chunk_id": "c", "text": "predis redis", "metadata": {}},
        ]
    )
    assert idx.hit_ids_for(["redis"]) == ["b", "c"]


def test_case_insensitive():
    assert _index().hit_ids_for(["REDIS"]) == ["c1", "c5"]


def test_multiple_keywords_ranked_by_coverage():
    hits = _index().hit_ids_for(["redis", "queue"])
    assert hits[0] == "c1"  # matches BOTH redis and queue -> ranked first
    assert set(hits[1:]) == {"c5"}


def test_multiword_keyword():
    assert _index().hit_ids_for(["message queue"]) == ["c1"]


def test_no_keywords_returns_empty():
    assert _index().hit_ids_for([]) == []
    assert _index().hits_for([]) == []


def test_no_match_returns_empty():
    assert _index().hit_ids_for(["kafka"]) == []


def test_limit():
    assert len(_index().hit_ids_for(["redis"], limit=1)) == 1


def test_hits_for_returns_lexical_evidence():
    hits = _index().hits_for(["redis"], n_results=2, distance=0.2)
    assert len(hits) == 2
    h = hits[0]
    assert isinstance(h, EvidenceHit)
    assert h.chunk_id == "c1"
    assert h.retrieval_source == "lexical"
    assert h.distance == 0.2
    assert h.source_id == "repo:x#file:q.py"
    assert h.skill_tags == ["redis"]
    assert h.text == "Built a Redis-backed message queue with retries"  # original case preserved


def test_hits_for_keeps_original_text_case():
    hits = _index().hits_for(["redis"])
    assert "Redis-backed" in hits[0].text
