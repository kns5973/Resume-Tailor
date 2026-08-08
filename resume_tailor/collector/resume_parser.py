"""Old-resume / LinkedIn-export PDF parser (pdfplumber).

Each page becomes an Evidence source (type "resume") with per-page chunks.
"""
from __future__ import annotations

from pathlib import Path

from resume_tailor.collector.base import SourceArtifact
from resume_tailor.collector.chunking import chunk_text
from resume_tailor.schemas import Evidence, EvidenceChunk


def parse_resume_pdf(path: str | Path) -> list[SourceArtifact]:
    import pdfplumber

    path = Path(path)
    artifacts: list[SourceArtifact] = []
    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            source_id = f"resume:old#page{page_no}"
            chunks = [
                EvidenceChunk(chunk_id=f"{source_id}#c{i}", source_id=source_id, text=chunk)
                for i, chunk in enumerate(chunk_text(text))
            ]
            artifacts.append(
                SourceArtifact(
                    evidence=Evidence(
                        source_id=source_id,
                        source_type="resume",
                        snippet=text[:200],
                        confidence=1.0,
                        url=f"file://{path.resolve()}#page{page_no}",
                    ),
                    chunks=chunks,
                )
            )
    return artifacts


def parse_resumes(paths: list[str | Path]) -> list[SourceArtifact]:
    """Parse several PDFs; raises on the first unreadable file (caller warns)."""
    artifacts: list[SourceArtifact] = []
    for path in paths:
        artifacts.extend(parse_resume_pdf(path))
    return artifacts
