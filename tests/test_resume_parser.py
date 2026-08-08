from unittest import mock

import pytest

from resume_tailor.collector.resume_parser import parse_resume_pdf, parse_resume_pdf_sections, parse_resume_sections
from tests._pdf_helper import minimal_pdf


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_parse_pdf_pages_become_evidence(monkeypatch):
    fake = _FakePDF([_FakePage("page one content\nsecond line"), _FakePage(""), _FakePage("page three")])
    monkeypatch.setattr("pdfplumber.open", lambda path: fake)

    artifacts = parse_resume_pdf("old_resume.pdf")
    assert len(artifacts) == 2  # empty page skipped
    ids = [a.evidence.source_id for a in artifacts]
    assert ids == ["resume:old#page1", "resume:old#page3"]
    assert all(a.evidence.source_type == "resume" for a in artifacts)
    assert artifacts[0].chunks[0].source_id == "resume:old#page1"


def test_parse_real_rendered_pdf(tmp_path):
    """Integration: render our own sample resume to PDF, then parse it back."""
    from resume_tailor.render.latex import render_resume
    from resume_tailor.sample_data import sample_resume

    pdf = render_resume(sample_resume(), tmp_path, jobname="roundtrip")
    artifacts = parse_resume_pdf(pdf)
    assert artifacts, "pdfplumber should extract text from the pdflatex output"
    all_text = " ".join(a.evidence.snippet for a in artifacts)
    assert "Jake Ryan" in all_text or "Southwestern" in all_text or "Python" in all_text


def test_missing_file_raises():
    with pytest.raises(Exception):
        parse_resume_pdf("does_not_exist.pdf")


# --------------------------------------------------------------------------
# Section-aware parsing (previous resume as format/content reference)
# --------------------------------------------------------------------------


def test_section_headers_split_into_named_sources(monkeypatch):
    fake = _FakePDF(
        [
            _FakePage("CAREER OBJECTIVE\nTo build reliable software for learners."),
            _FakePage("TECHNICAL SKILLS\nPython, Redis, Docker"),
        ]
    )
    monkeypatch.setattr("pdfplumber.open", lambda path: fake)

    sections, artifacts = parse_resume_pdf_sections("old_resume.pdf")
    assert [s["title"] for s in sections] == ["CAREER OBJECTIVE", "TECHNICAL SKILLS"]
    assert [s["source_id"] for s in sections] == ["resume:old#career-objective", "resume:old#technical-skills"]
    assert sections[0]["text"] == "To build reliable software for learners."
    assert sections[1]["text"] == "Python, Redis, Docker"

    # artifacts mirror the sections so builder citations resolve in the graph
    assert [a.evidence.source_id for a in artifacts] == ["resume:old#career-objective", "resume:old#technical-skills"]
    assert all(a.evidence.source_type == "resume" for a in artifacts)
    assert artifacts[0].chunks[0].source_id == "resume:old#career-objective"


def test_parse_resume_pdf_uses_sections_when_headers_present(monkeypatch):
    fake = _FakePDF([_FakePage("SUMMARY\nSeasoned engineer.\nEDUCATION\nBSc Computer Science")])
    monkeypatch.setattr("pdfplumber.open", lambda path: fake)
    artifacts = parse_resume_pdf("old.pdf")
    assert [a.evidence.source_id for a in artifacts] == ["resume:old#summary", "resume:old#education"]


def test_parse_real_previous_resume_pdf(tmp_path):
    """Integration: a real (minimal) PDF is sectioned the same way as text."""
    pdf = minimal_pdf(["CAREER OBJECTIVE", "To build reliable software.", "TECHNICAL SKILLS", "Python, Redis, Docker"])
    path = tmp_path / "old.pdf"
    path.write_bytes(pdf)
    sections, artifacts = parse_resume_pdf_sections(path)
    assert [s["source_id"] for s in sections] == ["resume:old#career-objective", "resume:old#technical-skills"]
    assert len(artifacts) == 2
    assert "To build reliable software." in artifacts[0].evidence.snippet


def test_parse_resume_sections_without_headers_returns_single_section():
    sections = parse_resume_sections("Just some unstructured resume text.")
    assert sections == [{"title": "Previous Resume", "source_id": "resume:old", "text": "Just some unstructured resume text."}]


def test_empty_text_returns_single_empty_section():
    assert parse_resume_sections("") == [{"title": "Previous Resume", "source_id": "resume:old", "text": ""}]
