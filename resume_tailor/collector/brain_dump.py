"""Freeform brain-dump reader — the user's own notes, treated as evidence.

The chat agent can later add claims here (add_claim intent); the collector
just indexes what the user wrote.
"""
from __future__ import annotations

from resume_tailor.collector.base import SourceArtifact
from resume_tailor.collector.chunking import chunk_text
from resume_tailor.schemas import Evidence, EvidenceChunk


def read_brain_dump(text: str) -> list[SourceArtifact]:
    text = text.strip()
    if not text:
        return []
    source_id = "brain_dump:notes"
    chunks = [
        EvidenceChunk(chunk_id=f"{source_id}#c{i}", source_id=source_id, text=chunk)
        for i, chunk in enumerate(chunk_text(text))
    ]
    return [
        SourceArtifact(
            evidence=Evidence(
                source_id=source_id,
                source_type="brain_dump",
                snippet=text[:200],
                confidence=1.0,
                url="",
            ),
            chunks=chunks,
        )
    ]
