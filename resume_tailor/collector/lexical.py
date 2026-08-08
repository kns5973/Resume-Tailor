"""Lexical (keyword) retrieval — the cheap half of hybrid search.

JD requirements are full of exact technology names (Redis, Docker, Node.js).
Short/noisy chunks — commit messages especially — can drift in embedding space,
so exact-term hits are pooled into the matcher's evidence set as high-
confidence lexical evidence alongside the semantic (Chroma) hits.

No index and no dependencies: a word-boundary scan over the corpus
(thousands of chunks) takes microseconds, which is why this is worth having
even though full BM25 is a possible later upgrade.
"""
from __future__ import annotations

import re

from resume_tailor.schemas import EvidenceHit


class LexicalIndex:
    """Scan a chunk corpus for exact-keyword hits (case-insensitive whole words)."""

    def __init__(self, chunks: list[dict]) -> None:
        # chunks: [{chunk_id, text, metadata}] — the shape VectorStore.get_all() returns
        self._entries: list[tuple[str, str, dict, str]] = []  # (id, lowered_text, meta, original_text)
        for c in chunks:
            text = c.get("text") or ""
            self._entries.append((c["chunk_id"], text.lower(), c.get("metadata") or {}, text))

    @staticmethod
    def _pattern(keyword: str) -> re.Pattern:
        # (?<!\w)...(?!\w) instead of \b so tech names like "C++" or "R" match
        # cleanly, while "redis" does not match "predis" or "rediscovery".
        return re.compile(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)")

    def hit_ids_for(self, keywords: list[str], limit: int | None = None) -> list[str]:
        """Chunk ids containing at least one keyword, best-first.

        Ranking: chunks matching MORE distinct keywords come first (a chunk
        about "redis" + "queue" is stronger evidence than one hitting only one).
        """
        keywords = [k for k in keywords if k and k.strip()]
        if not keywords:
            return []
        patterns = [self._pattern(k) for k in keywords]
        scored: list[tuple[int, str]] = []
        for cid, lowered, _meta, _orig in self._entries:
            count = sum(1 for p in patterns if p.search(lowered))
            if count:
                scored.append((count, cid))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        out = [cid for _count, cid in scored]
        return out if limit is None else out[:limit]

    def hits_for(
        self,
        keywords: list[str],
        *,
        n_results: int = 5,
        distance: float = 0.2,
    ) -> list[EvidenceHit]:
        """Lexical EvidenceHits for the keywords, ranked by coverage.

        `distance` is the synthetic cosine distance given to lexical hits — it
        must sit below the matcher's match_threshold for the hit to count as
        matched evidence (the matcher supplies config.lexical_distance).
        """
        ids = self.hit_ids_for(keywords, limit=n_results)
        if not ids:
            return []
        by_id = {cid: (lowered, meta, orig) for cid, lowered, meta, orig in self._entries}
        out: list[EvidenceHit] = []
        for cid in ids:
            _lowered, meta, orig = by_id[cid]
            out.append(
                EvidenceHit(
                    chunk_id=cid,
                    text=orig,
                    distance=distance,
                    source_id=meta.get("source_id", ""),
                    source_type=meta.get("source_type", "unknown"),
                    skill_tags=[t for t in meta.get("skill_tags", "").split(",") if t],
                    retrieval_source="lexical",
                )
            )
        return out
