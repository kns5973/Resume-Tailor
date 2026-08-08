"""ChromaDB-backed vector store — thin wrapper, no business logic.

The Matcher (Phase 2) will issue one batched query over all JD requirements;
VectorStore exposes that shape directly.
"""
from __future__ import annotations

from pathlib import Path

import chromadb


class VectorStore:
    """Local ChromaDB store of evidence chunks.

    path=None -> ephemeral (in-memory, for tests). Otherwise a persistent
    local file store (guide: no external DB service).
    """

    def __init__(self, path: str | Path | None = None, collection_name: str = "evidence") -> None:
        if path is None:
            self._client = chromadb.EphemeralClient()
        else:
            Path(path).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(path))
        # NOTE: collection metadata only applies at creation; a pre-existing
        # collection keeps its own config (we always use cosine space).
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict]) -> None:
        """Insert or replace chunks (upsert) — repeated collects are idempotent.

        chroma's add() raises on duplicate ids; upsert() makes re-collecting
        against the same persistent store safe. Batched in case the corpus
        exceeds chromadb's per-call batch cap (5,461 in chromadb 1.5.x).
        """
        if not ids:
            return
        batch = 1000
        for i in range(0, len(ids), batch):
            self._collection.upsert(
                ids=ids[i : i + batch],
                embeddings=embeddings[i : i + batch],
                documents=documents[i : i + batch],
                metadatas=metadatas[i : i + batch],
            )

    def get_all(self) -> list[dict]:
        """All stored chunks as [{chunk_id, text, metadata}] — for lexical scanning.

        The corpus is small (thousands of chunks), so the matcher can load it
        once and scan exact keywords instead of embedding another query.
        """
        data = self._collection.get(include=["documents", "metadatas"])
        rows: list[dict] = []
        for i, cid in enumerate(data["ids"]):
            rows.append(
                {
                    "chunk_id": cid,
                    "text": data["documents"][i] or "",
                    "metadata": data["metadatas"][i] or {},
                }
            )
        return rows

    def query(
        self,
        query_embeddings: list[list[float]],
        n_results: int = 5,
        where: dict | None = None,
    ) -> list[list[dict]]:
        """Batch query; returns one list of hits per query embedding.

        Each hit: {chunk_id, text, distance, metadata}. Distance is cosine
        distance (lower = closer). Empty store -> one empty list per query.
        """
        count = self.count()
        if count == 0 or not query_embeddings:
            return [[] for _ in query_embeddings]
        n = min(n_results, count)
        result = self._collection.query(
            query_embeddings=query_embeddings,
            n_results=n,
            where=where,
        )
        rows: list[list[dict]] = []
        for i in range(len(query_embeddings)):
            rows.append(
                [
                    {
                        "chunk_id": result["ids"][i][j],
                        "text": result["documents"][i][j],
                        "distance": result["distances"][i][j],
                        "metadata": result["metadatas"][i][j],
                    }
                    for j in range(n)
                ]
            )
        return rows

    def count(self) -> int:
        return self._collection.count()
