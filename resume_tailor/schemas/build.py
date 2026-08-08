"""Builder + Verifier schemas (Phase 3: guide §3 steps 4-5).

The Builder (strong tier) proposes an XYZ-bullet Resume draft with evidence_ids;
the Verifier (fast tier) checks claim ⊆ cited evidence. These models carry the
verdicts, the dropped-bullet ledger, and the final verified Resume.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from resume_tailor.schemas.resume import Resume


class ResumeHeader(BaseModel):
    """The only header the pipeline trusts.

    name/contact come from the user — never from the model. A builder might
    embellish identity or leak the JD's company as the candidate's employer, so
    the header is enforced from this input in builder.py.
    """

    name: str
    contact: dict[str, str] = Field(default_factory=dict)


class BulletVerdict(BaseModel):
    """One bullet's verification outcome."""

    bullet_id: int  # flat index into the draft's bullet list (stable per draft)
    claim: str
    evidence_ids: list[str] = Field(default_factory=list)
    verdict: Literal["pass", "fail"]
    reason: str
    source: Literal["llm", "deterministic", "operational"]  # how the verdict was decided


class DroppedBullet(BaseModel):
    """A bullet that could not be verified and was dropped from the final resume."""

    claim: str
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str


class Verification(BaseModel):
    """Verifier output: a verdict for every bullet in the draft.

    Deterministic failures (no evidence, unknown evidence ids, LLM outage) and
    LLM judgments live in the same list; `dropped` is derived from failures so
    the trace panel can show exactly what was cut and why.
    """

    verdicts: list[BulletVerdict] = Field(default_factory=list)

    @property
    def failed(self) -> list[BulletVerdict]:
        return [v for v in self.verdicts if v.verdict == "fail"]

    @property
    def passed(self) -> list[BulletVerdict]:
        return [v for v in self.verdicts if v.verdict == "pass"]

    @property
    def dropped(self) -> list[DroppedBullet]:
        return [
            DroppedBullet(claim=v.claim, evidence_ids=v.evidence_ids, reason=v.reason)
            for v in self.failed
        ]


class BuildResult(BaseModel):
    """Output of build_verified_resume: the final verified Resume + the trace.

    `resume` contains only bullets that passed verification (verified=True,
    each citing resolvable evidence). `draft` is the last model draft before
    dropping, so the UI can diff proposed vs. verified content. `dropped` is
    cumulative across revision rounds — bullets cut in round 1 stay visible in
    the trace even when a later revision removed them from the draft.
    """

    resume: Resume
    draft: Resume | None = Field(default=None, description="Last draft before dropping unverifiable bullets")
    verification: Verification = Field(default_factory=Verification)
    revisions: int = 0  # builder bounce rounds actually executed (≤ max_revisions)
    dropped: list[DroppedBullet] = Field(default_factory=list)
