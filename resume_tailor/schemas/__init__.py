"""Locked schemas — the single source of truth for pipeline artifacts."""

from resume_tailor.schemas.build import BuildResult, BulletVerdict, DroppedBullet, ResumeHeader, Verification
from resume_tailor.schemas.chat import ChatEditOp, ChatIntent, ChatResult, PatchEntry
from resume_tailor.schemas.evidence import Evidence, EvidenceChunk, EvidenceGraph, SourceType
from resume_tailor.schemas.jd import JDRequirement, ParsedJD, Priority
from resume_tailor.schemas.match import EvidenceHit, MatchResult, RequirementMatch
from resume_tailor.schemas.resume import Resume, ResumeBullet, ResumeEntry, ResumeSection

__all__ = [
    "BuildResult",
    "BulletVerdict",
    "DroppedBullet",
    "ResumeHeader",
    "Verification",
    "ChatEditOp",
    "ChatIntent",
    "ChatResult",
    "PatchEntry",
    "Evidence",
    "EvidenceChunk",
    "EvidenceGraph",
    "SourceType",
    "JDRequirement",
    "ParsedJD",
    "Priority",
    "EvidenceHit",
    "MatchResult",
    "RequirementMatch",
    "Resume",
    "ResumeBullet",
    "ResumeEntry",
    "ResumeSection",
]
