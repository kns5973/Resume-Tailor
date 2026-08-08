"""Resume JSON — the single source of truth. No agent ever edits .tex directly.

From implementation guide §4, extended with ResumeEntry so sections can carry
subheading-shaped content (experience jobs, education degrees, projects) that
Jake's template renders natively.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ResumeBullet(BaseModel):
    """A single bullet, always traceable to evidence once verified."""

    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    verified: bool = Field(default=False)

    @model_validator(mode="after")
    def verified_requires_evidence(self) -> "ResumeBullet":
        """Structural zero-hallucination rule (guide §4).

        A bullet may not be marked verified without at least one evidence_id.
        Unverified bullets (pending proof, e.g. flagged by the chat agent) may
        be empty but must never claim to be verified.
        """
        if self.verified and not self.evidence_ids:
            raise ValueError("a verified bullet must cite at least one evidence_id")
        return self


class ResumeEntry(BaseModel):
    """Subheading-shaped block: experience job, education degree, or project."""

    entry_type: Literal["job", "education", "project"] = "job"
    title: str  # bold main line (role / school / project name)
    subtitle: str = Field(default="", description="company / institution / tech stack")
    location: str = Field(default="")
    dates: str = Field(default="")
    bullets: list[ResumeBullet] = Field(default_factory=list)


class ResumeSection(BaseModel):
    """A titled section: either flat bullets (e.g. Technical Skills) or entries.

    bullets and entries are mutually exclusive: the template renders one shape,
    so allowing both would silently drop one of them.
    """

    title: str
    bullets: list[ResumeBullet] = Field(default_factory=list)
    entries: list[ResumeEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def not_both_bullets_and_entries(self) -> "ResumeSection":
        if self.bullets and self.entries:
            raise ValueError("a section must have either bullets or entries, not both")
        return self


class Resume(BaseModel):
    """Top-level resume document — the artifact agents build and mutate."""

    name: str
    contact: dict[str, str] = Field(default_factory=dict)
    sections: list[ResumeSection] = Field(default_factory=list)
