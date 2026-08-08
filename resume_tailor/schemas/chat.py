"""Chat Refinement schemas (Phase 5: guide §3 step 7).

The chat agent returns typed edit OPERATIONS, not mutated resume JSON. The
model only ever produces a target + new content; Python applies the op
deterministically, so everything the model didn't touch stays byte-identical
and every applied edit is undoable via the patch log.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from resume_tailor.schemas.resume import Resume

ChatIntent = Literal[
    "rewrite_bullet",
    "add_claim",
    "reorder_section",
    "remove_bullet",
    "tone_change",
    "none",
]


class ChatEditOp(BaseModel):
    """A typed edit to apply to the resume. Indices refer to the document map
    the classifier is shown (see chat.document_map), so targeting is exact.
    -1 = "not applicable" (or "append", for add_claim's bullet)."""

    intent: ChatIntent = "none"
    section: int = Field(default=-1)
    entry: int = Field(default=-1)  # -1 = flat bullets / not applicable
    bullet: int = Field(default=-1)  # -1 = append (add_claim) / not applicable
    to: int = Field(default=-1)  # destination index (reorder_section)
    text: str = Field(default="", description="claim to add (add_claim); filled by rewrite stage otherwise")
    instruction: str = Field(default="", description="user's verbatim edit instruction (rewrite/tone_change)")
    reply: str = Field(default="", description="assistant's short chat reply")


class PatchEntry(BaseModel):
    """One applied edit plus the resume before it, enabling undo."""

    op: ChatEditOp
    before: Resume
    summary: str


class ChatResult(BaseModel):
    """Outcome of one chat message."""

    message: str  # what the chat shows the user
    resume: Resume  # resulting resume (unchanged when refused/flagged)
    applied: bool = False  # an edit was applied
    flagged: bool = False  # an unverifiable claim was refused/flagged (moment #2)
    op: ChatEditOp | None = None
