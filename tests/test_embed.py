import math

from resume_tailor.collector.embed import FakeEmbedder


def test_fake_embedder_dim_and_normalized():
    emb = FakeEmbedder(dim=8)
    vec = emb.embed(["hello"])[0]
    assert len(vec) == 8
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0)


def test_fake_embedder_deterministic():
    emb = FakeEmbedder()
    assert emb.embed(["redis queue"]) == emb.embed(["redis queue"])


def test_fake_embedder_similar_texts_closer():
    emb = FakeEmbedder()
    base = emb.embed(["redis bullmq async"])[0]
    similar = emb.embed(["redis bullmq async jobs"])[0]
    different = emb.embed(["lawn care landscaping"])[0]

    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b))

    assert cosine(base, similar) > cosine(base, different)
