"""Embedding interface + implementations.

The Collector embeds every chunk exactly once (efficiency rule #2). The
interface lets tests and offline/CI runs use FakeEmbedder instead of loading
sentence-transformers + torch (~2 GB).
"""
from __future__ import annotations

import math
from typing import Protocol


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Deterministic, dependency-free embedder for tests and dry runs.

    Hash-based bag-of-characters vectors, L2-normalized so cosine distance in
    Chroma is meaningful. Not semantically useful — tests only.
    """

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for ch in text:
                vec[ord(ch) % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class SentenceTransformerEmbedder:
    """Real embedder (guide: all-MiniLM-L6-v2), imported lazily so importing
    the collector package never requires torch."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        # API renamed in newer sentence-transformers; keep the old name working
        dim_getter = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        self.dim = int(dim_getter())

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return vectors.tolist()
