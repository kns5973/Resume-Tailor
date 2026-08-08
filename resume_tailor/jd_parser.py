"""JD Parser (fast tier) — guide §3 step 2.

Turns raw JD text into a structured requirement list that the Matcher consumes.
Never invents requirements: whatever the model returns is schema-validated and
retried at most once on malformed output.
"""
from __future__ import annotations

from pydantic import ValidationError

from resume_tailor.llm import LLMClient, LLMError, get_client
from resume_tailor.schemas import ParsedJD

JD_SYSTEM = (
    "You extract structured job requirements from a job description. "
    "Respond with ONLY a JSON object: "
    '{"title": str, "company": str, "requirements": ['
    '{"requirement": str, "priority": "must_have" | "nice_to_have", "keywords": [str]}]}. '
    "Extract 5-12 requirements covering the skills, technologies, and experience "
    "explicitly mentioned in the job description. Never invent requirements."
)


def parse_jd(jd_text: str, client: LLMClient | None = None, max_retries: int = 1) -> ParsedJD:
    """Parse a job description into a ParsedJD. Retries once on bad output."""
    client = client or get_client()
    prompt = f"Job description:\n\n{jd_text}"
    last_error: Exception | None = None
    for _attempt in range(max_retries + 1):
        try:
            data = client.complete_json(task="jd_parser", system=JD_SYSTEM, prompt=prompt)
            return ParsedJD.model_validate(data)
        except (LLMError, ValidationError) as exc:
            last_error = exc
            prompt += f"\n\nYour previous response failed validation: {exc}. Respond with valid JSON only."
    raise LLMError(f"jd_parser failed after {max_retries + 1} attempts: {last_error}")
