"""Session records: derivation, persistence, filtering, review packet."""
import json

from resume_tailor.sessions import (
    SessionRecord,
    build_packet,
    derive_difficulty,
    derive_progress,
    derive_status,
    filter_records,
    load_record,
    load_records,
    recommended_actions,
    save_record,
)


def _record(**overrides) -> SessionRecord:
    base = dict(
        session_id="abc123",
        created_at="2026-08-08T10:00:00+00:00",
        candidate_name="Jake Ryan",
        jd_title="Senior Backend Engineer",
        company="Acme",
        topic="Senior Backend Engineer",
        evidence_based=True,
        status="verified",
        progress=90,
        difficulty="easy",
        stats={"requirements": 4, "matched": 3, "gaps": 1, "bullets_verified": 2, "dropped_bullets": 1},
        requirements=[
            {"requirement": "Redis and message queues", "priority": "must_have", "status": "matched"},
            {"requirement": "Knowledge of distributed systems", "priority": "nice_to_have", "status": "gap"},
        ],
        gaps=["Knowledge of distributed systems"],
        dropped=[{"claim": "Led a team of 25 engineers", "reason": "unknown evidence id(s): repo:fake#x"}],
        verdicts=[
            {"bullet_id": 0, "claim": "Built a Redis-backed job queue", "verdict": "pass", "reason": "supported", "source": "llm"}
        ],
        sections=[
            {
                "title": "Education",
                "entries": [
                    {
                        "entry_type": "education",
                        "title": "Southwestern University",
                        "subtitle": "BA Computer Science",
                        "bullets": [{"text": "Relevant coursework: Data Structures", "evidence_ids": ["edu:su#1"], "verified": True}],
                    }
                ],
                "bullets": [],
            },
            {
                "title": "Experience",
                "entries": [
                    {
                        "entry_type": "job",
                        "title": "Backend Engineer",
                        "subtitle": "Acme",
                        "bullets": [{"text": "Built a Redis-backed job queue", "evidence_ids": ["repo:acme#queue.py"], "verified": True}],
                    }
                ],
                "bullets": [],
            },
        ],
        transcript=[{"role": "assistant", "text": "Rewrote the first bullet.", "applied": True, "flagged": False}],
        has_chat_edits=True,
    )
    base.update(overrides)
    return SessionRecord(**base)


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------

def test_derive_status():
    assert derive_status(True, 3, False) == "verified"
    assert derive_status(True, 0, False) == "draft"  # honest empty result stays a draft
    assert derive_status(False, 3, False) == "draft"  # quick-draft mode
    assert derive_status(False, 0, True) == "refined"  # chat edits win


def test_derive_progress_formula_and_clamp():
    # 4 reqs, 3 matched (0.75), 2 verified, chat edits -> 20 + 30 + 30 + 10 = 90
    assert derive_progress({"requirements": 4, "matched": 3}, 2, True) == 90
    # 0 matched, nothing verified -> floor 20
    assert derive_progress({"requirements": 4, "matched": 0}, 0, False) == 20
    # never exceeds 100
    assert derive_progress({"requirements": 1, "matched": 1}, 5, True) == 100
    # division by zero guarded
    assert derive_progress({}, 0, False) == 20


def test_derive_difficulty():
    assert derive_difficulty({"requirements": 4, "matched": 3}) == "easy"
    assert derive_difficulty({"requirements": 4, "matched": 2}) == "medium"
    assert derive_difficulty({"requirements": 4, "matched": 1}) == "hard"
    assert derive_difficulty({}) == "hard"  # nothing matched


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def test_save_load_roundtrip_newest_first(tmp_path):
    older = _record(session_id="a", created_at="2026-08-08T09:00:00+00:00")
    newer = _record(session_id="b", created_at="2026-08-08T11:00:00+00:00")
    save_record(older, tmp_path)
    save_record(newer, tmp_path)

    assert load_record("a", tmp_path).candidate_name == "Jake Ryan"
    assert load_record("missing", tmp_path) is None
    ids = [r.session_id for r in load_records(tmp_path)]
    assert ids == ["b", "a"]  # newest first


def test_load_records_ignores_corrupt_files(tmp_path):
    (tmp_path / "sessions").mkdir(exist_ok=True)
    (tmp_path / "sessions" / "good.json").write_text(_record(session_id="g").model_dump_json(), encoding="utf-8")
    (tmp_path / "sessions" / "bad.json").write_text("{not json", encoding="utf-8")
    assert [r.session_id for r in load_records(tmp_path)] == ["g"]


# --------------------------------------------------------------------------
# Filtering (search input + facets)
# --------------------------------------------------------------------------

def test_filter_records_keyword_and_facets():
    recs = [
        _record(session_id="1", jd_title="Backend Engineer", company="Acme", topic="Backend Engineer", difficulty="easy"),
        _record(
            session_id="2",
            jd_title="Data Scientist",
            company="Nimbus",
            topic="Data Scientist",
            difficulty="hard",
            evidence_based=False,
            status="draft",
            sections=[],  # no redis bullet text here
            requirements=[],  # no "Redis and message queues" requirement here
        ),
    ]
    assert [r.session_id for r in filter_records(recs, q="redis")] == ["1"]  # bullet text of rec1
    assert [r.session_id for r in filter_records(recs, q="Nimbus")] == ["2"]
    assert [r.session_id for r in filter_records(recs, q="zzz")] == []
    assert [r.session_id for r in filter_records(recs, status="verified")] == ["1"]
    assert [r.session_id for r in filter_records(recs, status="draft")] == ["2"]
    assert [r.session_id for r in filter_records(recs, topic="data scientist")] == ["2"]  # case-insensitive
    assert [r.session_id for r in filter_records(recs, source="evidence")] == ["1"]
    assert [r.session_id for r in filter_records(recs, source="draft")] == ["2"]
    assert [r.session_id for r in filter_records(recs, difficulty="hard")] == ["2"]


# --------------------------------------------------------------------------
# Review packet
# --------------------------------------------------------------------------

def test_recommended_actions():
    acts = recommended_actions(_record())
    assert any("distributed systems" in a for a in acts)  # gap mentioned
    empty = _record(stats={**_record().stats, "bullets_verified": 0})
    assert any("own evidence" in a for a in recommended_actions(empty))
    draft = _record(evidence_based=False, stats={**_record().stats, "bullets_verified": 0})
    assert any("evidence-based drafting ON" in a for a in recommended_actions(draft))


def test_build_packet_structured_output():
    evidence = {
        "repo:acme#queue.py": {"snippet": "Built a Redis-backed job queue with retries", "source_type": "code", "url": "https://github.com/acme/x/blob/main/queue.py"},
        "edu:su#1": {"snippet": "Coursework list", "source_type": "resume", "url": ""},
    }
    md = build_packet(_record(), evidence)

    assert md.startswith("# Resume Review Packet")
    # candidate + education/career fields are included
    assert "Jake Ryan" in md
    assert "Education" in md
    assert "Southwestern University" in md
    assert "Experience" in md
    assert "Backend Engineer" in md
    # structured sections
    assert "## 1. Session overview" in md
    assert "Redis and message queues" in md  # requirement listed with match status
    assert "## 2. Tailored resume changes" in md
    assert "## 3. Supporting evidence" in md
    assert "Built a Redis-backed job queue with retries" in md  # snippet from graph
    assert "## 4. Weak areas" in md
    assert "distributed systems" in md
    assert "Led a team of 25 engineers" in md  # dropped bullet
    assert "## 5. Key questions raised by verification" in md
    assert "## 6. Recommended next actions" in md
    assert "## 7. Chat refinement log" in md
    assert "Rewrote the first bullet." in md


def test_build_packet_plain_export_roundtrip():
    """The packet is plain markdown (JSON-safe, no HTML) — demoable as a file."""
    md = build_packet(_record(), {})
    json.dumps(md)  # must not raise (serializable)
    assert "**Confidence:** 0/100" in md
