"""Matcher schemas — output of agentic RAG matching (guide §3 step 3)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from resume_tailor.schemas.jd import Priority


class EvidenceHit(BaseModel):
    """A retrieved evidence chunk backing a requirement."""

    chunk_id: str
    text: str
    distance: float  # cosine distance, lower = closer
    source_id: str
    source_type: str
    skill_tags: list[str] = Field(default_factory=list)
    retrieval_source: Literal["semantic", "lexical"] = Field(
        default="semantic", description="semantic = Chroma embedding search; lexical = exact-keyword hit"
    )


class RequirementMatch(BaseModel):
    """One JD requirement, resolved to evidence or an honest gap."""

    requirement: str
    priority: Priority = "nice_to_have"
    keywords: list[str] = Field(default_factory=list)
    status: Literal["matched", "gap"]
    hits: list[EvidenceHit] = Field(default_factory=list)
    best_distance: float | None = Field(default=None)
    query_trace: list[str] = Field(
        default_factory=list, description="[original, reformulation1, ...] — powers the trace panel"
    )


class MatchResult(BaseModel):
    """All JD requirements matched or gapped. Gaps are honest — never fabricated."""

    matches: list[RequirementMatch] = Field(default_factory=list)
    gaps: list[RequirementMatch] = Field(default_factory=list)

    @property
    def all(self) -> list[RequirementMatch]:
        return self.matches + self.gaps
