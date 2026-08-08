"""Old-resume / LinkedIn-export PDF parser (pdfplumber).

A previous resume serves two roles: (1) **evidence** — its own text is the
user's prior claims, so anything adapted from it cites ``resume:old#<section>``
and passes the verifier like any other source; (2) **format + fallback
content** — the Builder gets the parsed sections (Career Objective, Skills,
Education, ...) so it can mirror the structure and fill sections that have no
matched JD evidence (e.g. a career objective).

Parsing is section-aware: text is split on common resume headers
(OBJECTIVE / SUMMARY / SKILLS / EXPERIENCE / EDUCATION / ...). Each section
becomes its own Evidence source (``resume:old#career-objective``). When no
headers are found the previous per-page behavior is kept, so existing callers
and fixtures are unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

from resume_tailor.collector.base import SourceArtifact
from resume_tailor.collector.chunking import chunk_text
from resume_tailor.schemas import Evidence, EvidenceChunk

# Longest/most specific headers must come first (they match full lines, so the
# order only matters for cosmetic title precedence, not correctness).
_SECTION_HEADERS = [
    "career objective",
    "professional summary",
    "professional experience",
    "work experience",
    "employment history",
    "technical skills",
    "core competencies",
    "academic projects",
    "personal projects",
    "additional information",
    "objective",
    "summary",
    "profile",
    "skills",
    "technologies",
    "education",
    "experience",
    "projects",
    "certifications",
    "certificates",
    "licenses",
    "awards",
    "honors",
    "publications",
    "languages",
    "interests",
    "volunteer",
    "leadership",
    "activities",
    "references",
]
_HEADER_RE = re.compile(rf"^\s*(?:{'|'.join(_SECTION_HEADERS)})\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def parse_resume_sections(text: str) -> list[dict]:
    """Split resume text into named sections.

    Returns ``[{"title", "source_id", "text"}]``. Without recognizable headers
    a single unstructured section is returned (source_id ``resume:old``) so the
    content still works as fallback material.
    """
    matches = list(_HEADER_RE.finditer(text or ""))
    if not matches:
        return [{"title": "Previous Resume", "source_id": "resume:old", "text": (text or "").strip()}]
    sections: list[dict] = []
    for i, m in enumerate(matches):
        title = m.group(0).strip(" :").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        slug = _SLUG_RE.sub("-", title.lower()).strip("-")
        sections.append({"title": title, "source_id": f"resume:old#{slug}", "text": body})
    return sections


def _artifact_for_section(section: dict) -> SourceArtifact:
    source_id = section["source_id"]
    text = section["text"]
    chunks = [
        EvidenceChunk(chunk_id=f"{source_id}#c{i}", source_id=source_id, text=chunk)
        for i, chunk in enumerate(chunk_text(text))
    ]
    return SourceArtifact(
        evidence=Evidence(
            source_id=source_id,
            source_type="resume",
            snippet=text,
            confidence=1.0,
            url=f"resume://previous#{source_id.split('#', 1)[-1]}",
        ),
        chunks=chunks,
    )


def _extract_pages(path: str | Path) -> list[tuple[int, str]]:
    """[(page_no, stripped_text)] for non-empty pages, in order."""
    import pdfplumber

    pages: list[tuple[int, str]] = []
    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append((page_no, text))
    return pages


def parse_resume_pdf_sections(path: str | Path) -> tuple[list[dict], list[SourceArtifact]]:
    """Parse a PDF into (sections, artifacts) sharing the same boundaries.

    ``sections`` feeds the Builder (format reference + fallback content);
    ``artifacts`` are the per-section Evidence sources for the graph, so any
    source_id the Builder cites resolves for the Verifier.
    """
    pages = _extract_pages(path)
    full_text = "\n".join(text for _, text in pages)
    sections = parse_resume_sections(full_text)

    if len(sections) == 1 and sections[0]["source_id"] == "resume:old":
        # No section headers found — there is no structure to mirror, so no
        # format-reference block (sections=[]); the per-page artifacts still
        # land in the graph, so the content stays retrievable via matching.
        artifacts = [
            SourceArtifact(
                evidence=Evidence(
                    source_id=f"resume:old#page{page_no}",
                    source_type="resume",
                    snippet=text,
                    confidence=1.0,
                    url=f"file://{Path(path).resolve()}#page{page_no}",
                ),
                chunks=[
                    EvidenceChunk(chunk_id=f"resume:old#page{page_no}#c{i}", source_id=f"resume:old#page{page_no}", text=chunk)
                    for i, chunk in enumerate(chunk_text(text))
                ],
            )
            for page_no, text in pages
        ]
        return [], artifacts

    return sections, [_artifact_for_section(s) for s in sections]


def parse_resume_pdf(path: str | Path) -> list[SourceArtifact]:
    """Parse a PDF into per-section (or per-page) resume evidence artifacts."""
    _, artifacts = parse_resume_pdf_sections(path)
    return artifacts


def parse_resumes(paths: list[str | Path]) -> list[SourceArtifact]:
    """Parse several PDFs; raises on the first unreadable file (caller warns)."""
    artifacts: list[SourceArtifact] = []
    for path in paths:
        artifacts.extend(parse_resume_pdf(path))
    return artifacts
