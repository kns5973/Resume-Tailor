"""Resume Builder Agent (strong tier) — guide §3 step 4.

Turns matched requirements + evidence into an XYZ-format Resume draft whose
bullets cite evidence_ids from the matched evidence. The header (name/contact)
is enforced from the ResumeHeader input — the model never invents identity.
Verification is a separate agent (verifier.py); the builder only proposes.

XYZ bullets: "Accomplished X, as measured by Y, by doing Z" — the model is
prompted to write this shape while staying strictly inside the cited evidence
(no invented numbers/employers; if the evidence is thin, modest bullets).
"""
from __future__ import annotations

from pydantic import ValidationError

from resume_tailor.llm import LLMClient, LLMError, get_client
from resume_tailor.schemas import MatchResult, ParsedJD, Resume, ResumeHeader, ResumeSection

BUILDER_SYSTEM = (
    "You are a resume builder. You write resume bullet points in XYZ format: "
    "Accomplished [X], as measured by [Y], by doing [Z]. Every bullet must be "
    "strictly supported by the provided evidence snippets and must cite its "
    "evidence via the provided evidence_id values. Never invent achievements, "
    "numbers, employers, or skills that are not in the evidence; if evidence "
    "is thin, write honest, modest bullets. "
    "A PREVIOUS RESUME is provided as a format reference and fallback content: "
    "mirror its section structure and formatting (e.g. a Career Objective or "
    "Summary section). For a section that has no evidence-backed content from "
    "the matched requirements, you MAY write it from the previous resume's "
    "corresponding section and cite that section's evidence_id. Never invent "
    "content that appears in neither the matched evidence nor the previous "
    "resume. Respond with ONLY JSON matching: "
    '{"sections": [{"title": str, "entries": [{"entry_type": "job" | "education" '
    '| "project", "title": str, "subtitle": str, "location": str, "dates": str, '
    '"bullets": [{"text": str, "evidence_ids": [str]}]}]}]}. The candidate name '
    "and contact are set separately — do not include them. Empty sections or "
    "entries are allowed."
)

_MAX_EVIDENCE_PER_REQ = 3  # keep the prompt small: 3 best hits per requirement
_SNIPPET_LEN = 200
_PREV_SECTION_LEN = 600  # per-section cap for the previous-resume block


def _previous_resume_lines(previous_resume: list[dict] | None) -> str:
    """Format the previous resume as a format-reference + fallback block.

    Every section carries an evidence_id (= its source_id) so the model can
    cite ``resume:old#<section>`` and the Verifier can resolve it.
    """
    if not previous_resume:
        return ""
    lines = ["Previous resume (FORMAT REFERENCE + fallback content for sections with no matched evidence):"]
    for section in previous_resume:
        title = section.get("title", "Section")
        source_id = section.get("source_id", "resume:old")
        text = section.get("text", "").replace("\n", " ")[:_PREV_SECTION_LEN]
        lines.append(f"- ## {title}  [evidence_id={source_id}]  \"{text}\"")
    return "\n".join(lines)


def _evidence_lines(match_result: MatchResult) -> str:
    """Format matched requirements + evidence snippets for the prompt.

    evidence_id lines are regex-fetchable by the demo's offline mock
    (evidence_id=<value>), and duplicated hit ids are skipped.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for m in match_result.matches:
        lines.append(f"- {m.requirement} [priority: {m.priority}]")
        for h in m.hits[:_MAX_EVIDENCE_PER_REQ]:
            eid = h.source_id or h.chunk_id
            if not eid or eid in seen:
                continue
            seen.add(eid)
            snippet = h.text.replace("\n", " ")[:_SNIPPET_LEN]
            lines.append(f'    evidence_id={eid}  ({h.source_type})  "{snippet}"')
    return "\n".join(lines) or "(no matched evidence — write no claims beyond it)"


def generate_resume(
    jd: ParsedJD,
    match_result: MatchResult,
    header: ResumeHeader,
    *,
    client: LLMClient | None = None,
    max_retries: int = 1,
    feedback: str = "",
    previous_resume: list[dict] | None = None,
) -> Resume:
    """Generate a draft Resume from matched requirements and evidence.

    The header is taken verbatim from `header` — anything the model returns for
    name/contact is ignored. Bullets keep the evidence_ids the model cites;
    verifying (and possibly dropping) them is the Verifier's job, so the draft
    comes back with verified=False everywhere.
    """
    client = client or get_client()
    prompt = f"Job: {jd.title or '(unknown)'}"
    if jd.company:
        prompt += f" at {jd.company}"
    prompt += f"\n\nMatched requirements and evidence:\n{_evidence_lines(match_result)}"
    prev = _previous_resume_lines(previous_resume)
    if prev:
        prompt += f"\n\n{prev}"
    if feedback:
        prompt += f"\n\nREVISION FEEDBACK FROM THE VERIFIER (address every point):\n{feedback}"

    last_error: Exception | None = None
    for _attempt in range(max_retries + 1):
        try:
            data = client.complete_json(task="bullet_generation", system=BUILDER_SYSTEM, prompt=prompt)
            sections = [ResumeSection.model_validate(s) for s in data.get("sections", [])]
            return Resume(name=header.name, contact=header.contact, sections=sections)
        except (LLMError, ValidationError) as exc:
            last_error = exc
            prompt += f"\n\nYour previous response failed validation: {exc}. Respond with valid JSON only."
    raise LLMError(f"bullet_generation failed after {max_retries + 1} attempts: {last_error}")
