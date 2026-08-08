from unittest import mock

import pytest

from resume_tailor.collector.resume_parser import parse_resume_pdf


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
