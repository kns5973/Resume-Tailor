"""Render Agent: Resume JSON -> Jinja2 -> Jake's-Resume .tex -> pdflatex -> PDF.

The .tex is always regenerated from the Resume JSON — never edited by hand
(guide: Resume JSON is the single source of truth).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from resume_tailor.schemas.resume import Resume

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "jakes_resume.tex.j2"

_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "^": r"\textasciicircum{}",
    "_": r"\_",
    "%": r"\%",
    "~": r"\textasciitilde{}",
}


def latex_escape(text: str) -> str:
    """Escape LaTeX special characters for literal (non-verbatim) text.

    Char-by-char mapping avoids re-escaping artifacts of sequential replace.
    """
    if not text:
        return text
    return "".join(_LATEX_SPECIAL.get(ch, ch) for ch in str(text))


_CONTACT_ORDER = ("phone", "email", "linkedin", "github", "website")


def _href(url: str, display: str) -> str:
    return rf"\href{{{latex_escape(url)}}}{{\underline{{{latex_escape(display)}}}}}"


def _contact_items(resume: Resume) -> list[str]:
    """Header contact line, in a stable order, hyperlinked where appropriate."""
    items: list[str] = []
    contact = resume.contact or {}
    known = set(_CONTACT_ORDER)
    for key in _CONTACT_ORDER:
        value = str(contact.get(key, "")).strip()
        if not value:
            continue
        if key == "email":
            items.append(_href(f"mailto:{value}", value))
        elif key in ("linkedin", "github", "website"):
            url = value if value.startswith(("http://", "https://")) else f"https://{value}"
            items.append(_href(url, value))
        else:  # phone — plain text
            items.append(latex_escape(value))
    # Unknown contact keys: keep them rather than silently dropping them.
    for key, value in contact.items():
        if key not in known and str(value).strip():
            items.append(latex_escape(str(value)))
    return items


def render_tex(resume: Resume, template_name: str = TEMPLATE_NAME) -> str:
    """Render a Resume into LaTeX source."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["latex"] = latex_escape
    template = env.get_template(template_name)
    return template.render(resume=resume, contact_items=_contact_items(resume))


def compile_tex(tex: str, workdir: Path, jobname: str = "resume", passes: int = 2) -> Path:
    """Compile LaTeX source to PDF with local pdflatex. Returns the PDF path."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / f"{jobname}.tex").write_text(tex, encoding="utf-8")

    for _ in range(passes):  # second pass stabilizes cross-references/layout
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-jobname={jobname}",
                f"{jobname}.tex",
            ],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            tail = "\n".join(result.stdout.splitlines()[-30:])
            raise RuntimeError(f"pdflatex failed (exit {result.returncode}):\n{tail}")

    pdf = workdir / f"{jobname}.pdf"
    if not pdf.exists() or pdf.stat().st_size == 0:
        raise RuntimeError("pdflatex exited 0 but produced no PDF")
    return pdf


def render_resume(resume: Resume, out_dir: Path, jobname: str = "resume") -> Path:
    """Full render: Resume JSON -> LaTeX -> PDF. Returns the PDF path."""
    return compile_tex(render_tex(resume), out_dir, jobname=jobname)
