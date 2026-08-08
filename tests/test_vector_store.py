import uuid

from resume_tailor.collector.embed import FakeEmbedder
from resume_tailor.collector.vector_store import VectorStore


def _store():
    # chroma's EphemeralClient shares one in-process system, so collections must
    # be uniquely named per test to stay isolated.
    return VectorStore(collection_name=f"test_{uuid.uuid4().hex[:8]}")


def test_add_and_count():
    store = _store()
    emb = FakeEmbedder()
    store.add(ids=["a", "b"], embeddings=emb.embed(["x", "y"]), documents=["x", "y"], metadatas=[{"source_id": "s1"}, {"source_id": "s2"}])
    assert store.count() == 2


def test_get_all_returns_chunks_with_metadata():
    store = _store()
    emb = FakeEmbedder()
    store.add(
        ids=["a", "b"],
        embeddings=emb.embed(["redis queue", "docker"]),
        documents=["redis queue", "docker"],
        metadatas=[{"source_id": "s1", "source_type": "code"}, {"source_id": "s2", "source_type": "commit"}],
    )
    rows = {r["chunk_id"]: r for r in store.get_all()}
    assert set(rows) == {"a", "b"}
    assert rows["a"]["text"] == "redis queue"
    assert rows["a"]["metadata"]["source_id"] == "s1"
    assert rows["b"]["metadata"]["source_type"] == "commit"


def test_get_all_empty_store():
    assert _store().get_all() == []


def test_query_returns_self_as_top_hit():
    store = _store()
    emb = FakeEmbedder()
    store.add(ids=["a"], embeddings=emb.embed(["redis queue"]), documents=["redis queue"], metadatas=[{"source_id": "s1"}])
    hits = store.query(emb.embed(["redis queue"]), n_results=3)
    assert len(hits) == 1
    assert hits[0][0]["chunk_id"] == "a"
    assert hits[0][0]["distance"] < 0.01  # identical normalized vector -> ~0 cosine distance


def test_query_empty_store():
    store = _store()
    emb = FakeEmbedder()
    assert store.query(emb.embed(["anything"])) == [[]]
    assert store.query([], n_results=5) == []


def test_query_clamps_n_results_to_count():
    store = _store()
    emb = FakeEmbedder()
    store.add(ids=["a"], embeddings=emb.embed(["x"]), documents=["x"], metadatas=[{"k": "v"}])
    hits = store.query(emb.embed(["x"]), n_results=10)
    assert len(hits[0]) == 1


def test_add_batches_large_corpus_past_chromadb_batch_cap():
    # Regression: chromadb 1.5.x raises InternalError when a single upsert
    # exceeds its batch cap (5,461). add() must chunk the write so a corpus
    # larger than the cap can be seeded (the sindresorhus demo corpus is 5,614).
    store = _store()
    emb = FakeEmbedder()
    n = 6000
    store.add(
        ids=[f"c{i}" for i in range(n)],
        embeddings=emb.embed([f"chunk {i}" for i in range(n)]),
        documents=[f"chunk {i}" for i in range(n)],
        metadatas=[{"source_id": "s"} for _ in range(n)],
    )
    assert store.count() == n
    # re-seeding stays idempotent across batches (upsert semantics)
    store.add(
        ids=[f"c{i}" for i in range(n)],
        embeddings=emb.embed([f"chunk {i}" for i in range(n)]),
        documents=[f"chunk {i}" for i in range(n)],
        metadatas=[{"source_id": "s"} for _ in range(n)],
    )
    assert store.count() == n


def test_add_same_id_twice_is_idempotent():
    # upsert semantics: re-collecting against the same store must not crash or dup
    store = _store()
    emb = FakeEmbedder()
    store.add(ids=["a"], embeddings=emb.embed(["x"]), documents=["x"], metadatas=[{"k": "v"}])
    store.add(ids=["a"], embeddings=emb.embed(["x"]), documents=["x"], metadatas=[{"k": "v"}])
    assert store.count() == 1


def test_persistent_store_reloads_count(tmp_path):
    path = tmp_path / "chroma"
    store = VectorStore(path=path, collection_name="evidence")
    emb = FakeEmbedder()
    store.add(ids=["a"], embeddings=emb.embed(["x"]), documents=["x"], metadatas=[{"source_id": "s1"}])

    reopened = VectorStore(path=path, collection_name="evidence")
    assert reopened.count() == 1
    hits = reopened.query(emb.embed(["x"]))
    assert hits[0][0]["chunk_id"] == "a"
