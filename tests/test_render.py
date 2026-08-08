"""Render tests: LaTeX escaping, template output, and the Phase-0 gate —
a real dummy pdflatex compile producing a valid PDF."""
from resume_tailor.render.latex import latex_escape, render_resume, render_tex
from resume_tailor.sample_data import sample_resume


def test_latex_escape():
    assert latex_escape("100% & #1 _score_") == r"100\% \& \#1 \_score\_"
    assert latex_escape("") == ""


def test_render_tex_contains_expected():
    tex = render_tex(sample_resume())
    assert "\\documentclass" in tex
    assert "Jake Ryan" in tex
    assert "\\section{ Education }" in tex
    assert "\\resumeSubheading" in tex
    assert "\\resumeProjectHeading" in tex
    # GitHub username underscores must be escaped or pdflatex breaks
    assert "jaker" in tex  # no underscore in this fixture's username


def test_render_tex_escapes_underscore_in_contact_url():
    """GitHub/linkedin usernames contain _ — must be escaped or pdflatex breaks."""
    from resume_tailor.schemas import Resume

    resume = Resume(name="Jane Doe", contact={"github": "github.com/jane_doe"})
    tex = render_tex(resume)
    assert r"https://github.com/jane\_doe" in tex
    assert r"github.com/jane\_doe" in tex


def test_unknown_contact_keys_are_kept():
    from resume_tailor.schemas import Resume

    resume = Resume(name="Jane Doe", contact={"phone": "123", "website": "example.com"})
    tex = render_tex(resume)
    assert "123" in tex
    assert "example.com" in tex


def test_render_tex_escapes_special_chars():
    from resume_tailor.schemas import Resume, ResumeSection, ResumeBullet

    resume = Resume(
        name="A&B C#1",
        contact={"phone": "100%"},
        sections=[ResumeSection(title="Skills", bullets=[ResumeBullet(text="100% & #1", evidence_ids=["e"], verified=True)])],
    )
    tex = render_tex(resume)
    assert r"A\&B C\#1" in tex
    assert r"100\% \& \#1" in tex


def test_render_resume_compiles_to_pdf(tmp_path):
    """Phase 0 gate: Resume JSON -> LaTeX -> pdflatex -> valid PDF."""
    pdf = render_resume(sample_resume(), tmp_path, jobname="sample")
    assert pdf.exists()
    assert pdf.stat().st_size > 0
    assert pdf.read_bytes()[:4] == b"%PDF"
