"""Job description models (Phase 2: JD Parser + Matcher).

Guide §3 step 2: JD Parser -> [{requirement, priority, keywords}].
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Priority = Literal["must_have", "nice_to_have"]


class JDRequirement(BaseModel):
    requirement: str
    priority: Priority = "nice_to_have"
    keywords: list[str] = Field(default_factory=list)


class ParsedJD(BaseModel):
    """Output of the JD Parser; input to the Matcher."""

    title: str = Field(default="")
    company: str = Field(default="")
    requirements: list[JDRequirement] = Field(default_factory=list)
