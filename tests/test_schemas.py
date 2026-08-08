"""Schema tests: lock the guide's invariants (especially zero-hallucination)."""
import pytest
from pydantic import ValidationError

from resume_tailor.schemas import (
    Evidence,
    EvidenceChunk,
    EvidenceGraph,
    Resume,
    ResumeBullet,
    ResumeEntry,
    ResumeSection,
)
from resume_tailor.sample_data import sample_resume


def test_evidence_roundtrip_json():
    evidence = Evidence(
        source_id="repo:backend-api#file:queue.py#L45",
        source_type="code",
        skill_tags=["redis", "bullmq"],
        snippet="const queue = new Queue('jobs');",
        confidence=0.93,
        url="https://github.com/me/backend-api/blob/main/queue.py#L45",
    )
    restored = Evidence.model_validate_json(evidence.model_dump_json())
    assert restored == evidence


def test_verified_bullet_requires_evidence():
    with pytest.raises(ValidationError):
        ResumeBullet(text="Led a team of 10", evidence_ids=[], verified=True)


def test_unverified_bullet_may_be_empty():
    bullet = ResumeBullet(text="Led a team of 10", evidence_ids=[], verified=False)
    assert bullet.verified is False


def test_evidence_graph_provenance():
    graph = EvidenceGraph()
    graph.add_source(Evidence(source_id="s1", source_type="commit", snippet="shipped redis", skill_tags=["redis"]))
    graph.add_source(Evidence(source_id="s2", source_type="cert", snippet="AWS cert"))
    graph.add_chunk(EvidenceChunk(chunk_id="s1#c0", source_id="s1", text="shipped redis", skill_tags=["redis"]))
    graph.add_claim("claim:led-redis", ["s1#c0"])

    prov = graph.provenance_for("claim:led-redis")
    assert len(prov) == 1 and prov[0].source_id == "s1"
    assert graph.chunks_for_skill("redis")[0].chunk_id == "s1#c0"
    assert graph.provenance_for("claim:missing") == []


def test_resume_roundtrip_with_entries():
    resume = sample_resume()
    restored = Resume.model_validate_json(resume.model_dump_json())
    assert restored == resume
    assert restored.sections[1].entries[0].bullets[0].evidence_ids == [
        "repo:backend-api#file:queue.py#L45"
    ]


def test_section_cannot_mix_bullets_and_entries():
    from resume_tailor.schemas import ResumeEntry

    with pytest.raises(ValidationError):
        ResumeSection(
            title="Bad",
            bullets=[ResumeBullet(text="b", evidence_ids=["e"], verified=True)],
            entries=[ResumeEntry(title="e")],
        )


def test_resume_schema_invariants():
    # A generated bullet must always cite evidence — this is structural.
    for section in sample_resume().sections:
        for entry in section.entries:
            assert all(b.evidence_ids for b in entry.bullets)
            assert all(b.verified for b in entry.bullets)
        assert all(b.evidence_ids for b in section.bullets)
