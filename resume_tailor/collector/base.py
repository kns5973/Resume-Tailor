"""Shared collector types."""
from __future__ import annotations

from dataclasses import dataclass, field

from resume_tailor.schemas import Evidence, EvidenceChunk


@dataclass
class SourceArtifact:
    """One Evidence source plus its pre-chunked chunks.

    Readers produce these; the Collector orchestrator merges them into the
    EvidenceGraph and the vector store.
    """

    evidence: Evidence
    chunks: list[EvidenceChunk] = field(default_factory=list)
