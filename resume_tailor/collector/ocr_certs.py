"""Certification OCR (Tesseract via pytesseract).

⚠ Requires the tesseract-ocr system binary (not installed on this machine yet).
The Collector catches the RuntimeError and degrades to a warning, so a missing
binary never breaks a run — certs are simply skipped.

Supports PNG/JPG. PDF certs need poppler (pdf2image) — deferred.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from resume_tailor.collector.base import SourceArtifact
from resume_tailor.collector.chunking import chunk_text
from resume_tailor.schemas import Evidence, EvidenceChunk

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def ocr_cert(path: str | Path) -> SourceArtifact:
    if not shutil.which("tesseract"):
        raise RuntimeError(
            "tesseract binary not found — install tesseract-ocr (e.g. 'sudo apt install tesseract-ocr') to OCR certificates"
        )
    from PIL import Image
    import pytesseract

    path = Path(path)
    if path.suffix.lower() not in IMAGE_EXTS:
        raise RuntimeError(f"cert OCR supports PNG/JPG for now, got: {path.suffix or path.name}")
    text = (pytesseract.image_to_string(Image.open(path)) or "").strip()
    if not text:
        raise RuntimeError(f"no text extracted from {path.name}")

    source_id = f"cert:{path.parent.name}:{path.stem}"  # parent disambiguates same-named files
    chunks = [
        EvidenceChunk(chunk_id=f"{source_id}#c{i}", source_id=source_id, text=chunk)
        for i, chunk in enumerate(chunk_text(text))
    ]
    return SourceArtifact(
        evidence=Evidence(
            source_id=source_id,
            source_type="cert",
            snippet=text[:200],
            confidence=1.0,
            url=f"file://{path.resolve()}",
        ),
        chunks=chunks,
    )


def ocr_certs(paths: list[str | Path]) -> list[SourceArtifact]:
    return [ocr_cert(p) for p in paths]
