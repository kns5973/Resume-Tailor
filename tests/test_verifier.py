"""Verifier tests: deterministic auto-fails (no evidence / unknown ids) never
call the LLM; LLM verdicts check claim ⊆ evidence; failures are honest."""
import json

from resume_tailor.llm import MockLLMClient
from resume_tailor.schemas import (
    Evidence,
    EvidenceChunk,
    EvidenceGraph,
    Resume,
    ResumeBullet,
    ResumeEntry,
    ResumeSection,
)
from resume_tailor.verifier import iter_bullets, verify_resume

EID = "repo:acme#file:queue.py#L45"
SNIPPET = "Implemented a Redis-backed job queue with retries and dead-letter handling"


def _graph() -> EvidenceGraph:
    g = EvidenceGraph()
    g.add_source(Evidence(source_id=EID, source_type="code", snippet=SNIPPET, skill_tags=["redis"]))
    g.add_chunk(EvidenceChunk(chunk_id=f"{EID}#chunk0", source_id=EID, text=SNIPPET))
    return g


def _resume(*bullets: ResumeBullet) -> Resume:
    return Resume(
        name="Jake",
        contact={},
        sections=[ResumeSection(title="Experience", entries=[ResumeEntry(entry_type="job", title="Engineer", bullets=list(bullets))])],
    )


def _pass_verdicts(*ids: int) -> str:
    return json.dumps({"verdicts": [{"bullet_id": i, "verdict": "pass", "reason": "supported by cited evidence"} for i in ids]})


def test_deterministic_fail_empty_evidence():
    resume = _resume(ResumeBullet(text="Led a team of 10", evidence_ids=[]))
    v = verify_resume(resume, client=MockLLMClient(), graph=_graph())
    assert len(v.verdicts) == 1
    assert v.verdicts[0].verdict == "fail"
    assert v.verdicts[0].reason == "no evidence cited"
    assert v.verdicts[0].source == "deterministic"
    assert v.dropped[0].claim == "Led a team of 10"


def test_deterministic_fail_unknown_evidence_id_no_llm_call():
    resume = _resume(ResumeBullet(text="Built a thing", evidence_ids=["repo:unknown#file:x.py"]))
    client = MockLLMClient()  # would raise if called — proves no LLM for auto-fails
    v = verify_resume(resume, client=client, graph=_graph())
    assert "unknown evidence id" in v.verdicts[0].reason
    assert v.verdicts[0].source == "deterministic"
    assert client.calls == []


def test_llm_verdict_pass():
    resume = _resume(ResumeBullet(text="Built a Redis-backed job queue", evidence_ids=[EID]))
    client = MockLLMClient({"verifier": [_pass_verdicts(0)]})
    v = verify_resume(resume, client=client, graph=_graph())
    assert v.passed[0].verdict == "pass"
    assert v.passed[0].source == "llm"
    assert v.failed == []


def test_llm_verdict_fail_claim_not_in_evidence():
    reason = "claim of managing 10 engineers is not in the cited evidence"
    resume = _resume(ResumeBullet(text="Managed a team of 10 engineers", evidence_ids=[EID]))
    client = MockLLMClient({"verifier": [json.dumps({"verdicts": [{"bullet_id": 0, "verdict": "fail", "reason": reason}]})]})
    v = verify_resume(resume, client=client, graph=_graph())
    assert v.failed[0].reason == reason
    assert v.failed[0].source == "llm"


def test_bullet_citing_chunk_id_resolves_too():
    """Citing a chunk_id (not just a source_id) also resolves in the graph."""
    resume = _resume(ResumeBullet(text="Built a Redis-backed job queue", evidence_ids=[f"{EID}#chunk0"]))
    client = MockLLMClient({"verifier": [_pass_verdicts(0)]})
    v = verify_resume(resume, client=client, graph=_graph())
    assert v.passed[0].verdict == "pass"


def test_missing_verdict_defaults_to_fail():
    resume = _resume(ResumeBullet(text="Built a queue", evidence_ids=[EID]))
    client = MockLLMClient({"verifier": ['{"verdicts": []}']})
    v = verify_resume(resume, client=client, graph=_graph())
    assert v.failed and "no verdict returned" in v.failed[0].reason


def test_llm_outage_fails_closed_after_retry():
    """Two bad responses -> fail closed, never mark unverified as verified.
    The source is 'operational' (an outage), not a deterministic judgment."""
    resume = _resume(ResumeBullet(text="Built a queue", evidence_ids=[EID]))
    client = MockLLMClient({"verifier": ["not json", "still not json"]})
    v = verify_resume(resume, client=client, graph=_graph())
    assert len(v.verdicts) == 1
    assert v.verdicts[0].verdict == "fail"
    assert v.verdicts[0].source == "operational"
    assert "LLM error" in v.verdicts[0].reason
    assert len(client.calls) == 2  # exactly one retry


def test_one_batched_llm_call_for_many_bullets():
    resume = _resume(
        ResumeBullet(text="Built a queue", evidence_ids=[EID]),
        ResumeBullet(text="Built another queue", evidence_ids=[EID]),
        ResumeBullet(text="And a third", evidence_ids=[EID]),
    )
    client = MockLLMClient({"verifier": [_pass_verdicts(0, 1, 2)]})
    verify_resume(resume, client=client, graph=_graph())
    assert len(client.calls) == 1  # efficiency rule #3: one batched call


def test_no_bullets_skips_llm():
    resume = Resume(name="Jake", contact={}, sections=[ResumeSection(title="Skills", bullets=[])])
    client = MockLLMClient()
    v = verify_resume(resume, client=client)
    assert v.verdicts == []
    assert client.calls == []


def test_flat_bullet_ids_across_sections_and_entries():
    resume = Resume(
        name="J",
        contact={},
        sections=[
            ResumeSection(title="A", bullets=[ResumeBullet(text="x", evidence_ids=["e1"])]),
            ResumeSection(
                title="B",
                entries=[ResumeEntry(title="T", bullets=[ResumeBullet(text="y", evidence_ids=["e2"]), ResumeBullet(text="z", evidence_ids=["e3"])])],
            ),
        ],
    )
    assert [i for i, _ in iter_bullets(resume)] == [0, 1, 2]
