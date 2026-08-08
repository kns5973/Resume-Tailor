"""Deterministic text chunking — no LLM involved.

Chunks are the unit of retrieval: embedded once at ingestion (efficiency rule #2:
embed once, cache always). Chunking is line-based with a soft size target and
overlap so retrieval never loses context across boundaries.
"""
from __future__ import annotations


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 60) -> list[str]:
    """Split text into overlapping chunks of ~chunk_size characters.

    Line-based (code and prose both chunk cleanly); a single line longer than
    chunk_size is kept whole rather than split mid-line (code lines lose meaning
    when cut). Returns [] for empty/whitespace-only input.
    """
    if not text or not text.strip():
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line)
        if current and current_len + line_len > chunk_size:
            chunks.append("\n".join(current))
            # carry the tail of the finished chunk into the next one (overlap)
            keep: list[str] = []
            keep_len = 0
            for prev in reversed(current):
                if keep_len + len(prev) > overlap:
                    break
                keep.insert(0, prev)
                keep_len += len(prev)
            current = keep
            current_len = keep_len
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks
