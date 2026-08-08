"""Builder tests: XYZ drafts cite evidence, the header is enforced from input,
and bad model output retries once before failing loudly."""
import json

import pytest

from resume_tailor.builder import generate_resume
from resume_tailor.llm import LLMError, MockLLMClient
from resume_tailor.schemas import (
    EvidenceHit,
    JDRequirement,
    MatchResult,
    ParsedJD,
    RequirementMatch,
    ResumeHeader,
)

EVIDENCE_ID = "repo:acme#file:queue.py#L45"


def _match_result() -> MatchResult:
    hit = EvidenceHit(
        chunk_id="c1",
        text="Built a Redis-backed job queue with retries and dead-letter handling",
        distance=0.3,
        source_id=EVIDENCE_ID,
        source_type="code",
        skill_tags=["redis"],
    )
    req = RequirementMatch(requirement="Redis and message queues", status="matched", hits=[hit], best_distance=0.3)
    return MatchResult(matches=[req])


def _jd() -> ParsedJD:
    return ParsedJD(
        title="Senior Backend Engineer",
        company="Acme",
        requirements=[JDRequirement(requirement="Redis and message queues", priority="must_have", keywords=["redis"])],
    )


GOOD_SECTIONS = {
    "sections": [
        {
            "title": "Experience",
            "entries": [
                {
                    "entry_type": "job",
                    "title": "Backend Engineer",
                    "subtitle": "Acme",
                    "location": "Remote",
                    "dates": "2020 - Present",
                    "bullets": [
                        {
                            "text": "Accomplished a production job queue handling millions of jobs, as measured by on-time delivery, by building it on Redis with retries",
                            "evidence_ids": [EVIDENCE_ID],
                        }
                    ],
                }
            ],
        }
    ]
}


def test_builder_produces_resume_from_matched_evidence():
    client = MockLLMClient({"bullet_generation": [json.dumps(GOOD_SECTIONS)]})
    resume = generate_resume(
        _jd(), _match_result(), ResumeHeader(name="Jake Ryan", contact={"email": "jake@x.com"}), client=client
    )
    assert resume.name == "Jake Ryan"
    assert resume.contact == {"email": "jake@x.com"}
    bullet = resume.sections[0].entries[0].bullets[0]
    assert bullet.evidence_ids == [EVIDENCE_ID]
    assert bullet.verified is False  # draft — the Verifier decides


def test_builder_header_enforced_from_input():
    """Whatever name/contact the model invents is ignored — only the header wins."""
    data = dict(GOOD_SECTIONS)
    data["name"] = "Impostor Candidate"
    data["contact"] = {"phone": "999-000-1111"}
    client = MockLLMClient({"bullet_generation": [json.dumps(data)]})
    resume = generate_resume(
        _jd(), _match_result(), ResumeHeader(name="Jake Ryan", contact={"email": "jake@x.com"}), client=client
    )
    assert resume.name == "Jake Ryan"
    assert resume.contact == {"email": "jake@x.com"}


def test_builder_prompt_includes_evidence_and_feedback():
    client = MockLLMClient({"bullet_generation": [json.dumps(GOOD_SECTIONS)]})
    generate_resume(
        _jd(), _match_result(), ResumeHeader(name="Jake"), client=client, feedback="bullet 0: unsupported claim"
    )
    prompt = client.calls[0][2]
    assert EVIDENCE_ID in prompt
    assert "unsupported claim" in prompt  # revision feedback reaches the builder


def test_builder_prompt_includes_previous_resume_sections():
    """The previous resume arrives as format reference + fallback content,
    each section carrying its evidence_id so citations resolve for the verifier."""
    client = MockLLMClient({"bullet_generation": [json.dumps(GOOD_SECTIONS)]})
    previous_resume = [
        {"title": "Career Objective", "source_id": "resume:old#career-objective", "text": "Seeking a backend role."},
        {"title": "Technical Skills", "source_id": "resume:old#technical-skills", "text": "Python, Redis, Docker"},
    ]
    generate_resume(_jd(), _match_result(), ResumeHeader(name="Jake"), client=client, previous_resume=previous_resume)
    prompt = client.calls[0][2]
    assert "FORMAT REFERENCE" in prompt
    assert "Career Objective" in prompt and "Seeking a backend role." in prompt
    assert "resume:old#career-objective" in prompt  # citeable evidence_id for the verifier
    assert "resume:old#technical-skills" in prompt


def test_builder_prompt_omits_previous_resume_when_none():
    client = MockLLMClient({"bullet_generation": [json.dumps(GOOD_SECTIONS)]})
    generate_resume(_jd(), _match_result(), ResumeHeader(name="Jake"), client=client)
    assert "FORMAT REFERENCE" not in client.calls[0][2]


def test_builder_retries_once_on_bad_output():
    client = MockLLMClient({"bullet_generation": ["not json", json.dumps(GOOD_SECTIONS)]})
    resume = generate_resume(_jd(), _match_result(), ResumeHeader(name="Jake"), client=client)
    assert resume.sections
    assert len(client.calls) == 2


def test_builder_raises_after_retries_exhausted():
    client = MockLLMClient({"bullet_generation": ["not json", '{"sections": "wrong shape"}']})
    with pytest.raises(LLMError):
        generate_resume(_jd(), _match_result(), ResumeHeader(name="Jake"), client=client)


def test_builder_empty_sections_allowed():
    client = MockLLMClient({"bullet_generation": ['{"sections": []}']})
    resume = generate_resume(_jd(), _match_result(), ResumeHeader(name="Jake"), client=client)
    assert resume.sections == []
    assert resume.name == "Jake"
