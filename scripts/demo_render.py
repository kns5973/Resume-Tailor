"""Phase 0 demo — render a sample Resume JSON to PDF via local LaTeX.

Run:  .venv/bin/python scripts/demo_render.py
Output: out/demo_resume.pdf (the Phase-0 "dummy pdflatex compile")
"""
from __future__ import annotations

from pathlib import Path

from resume_tailor.render.latex import render_resume
from resume_tailor.sample_data import sample_resume

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    out_dir = PROJECT_ROOT / "out"
    pdf = render_resume(sample_resume(), out_dir, jobname="demo_resume")
    print(f"Rendered {pdf} ({pdf.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
