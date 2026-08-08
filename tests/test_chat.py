"""Chat Refinement tests: typed edit ops apply deterministically; text edits
are re-verified; add_claim attaches evidence or flags (demo moment #2); undo
restores via the patch log."""
import json
import uuid

from resume_tailor.chat import ChatSession, apply_chat, document_map
from resume_tailor.collector.embed import FakeEmbedder
from resume_tailor.collector.vector_store import VectorStore
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

EID = "repo:acme#file:queue.py#L45"
SNIPPET = "Built a Redis-backed job queue with retries"


def _resume() -> Resume:
    return Resume(
        name="Jake Ryan",
        contact={"email": "jake@x.com"},
        sections=[
            ResumeSection(
                title="Experience",
                entries=[
                    ResumeEntry(
                        entry_type="job",
                        title="Backend Engineer",
                        subtitle="Acme",
                        bullets=[
                            ResumeBullet(text="Built a Redis-backed job queue", evidence_ids=[EID], verified=True),
                            ResumeBullet(text="Wrote tests for the queue", evidence_ids=[EID], verified=True),
                        ],
                    )
                ],
            ),
            ResumeSection(title="Technical Skills", bullets=[ResumeBullet(text="Python, Redis, Docker", evidence_ids=[EID], verified=True)]),
        ],
    )


def _graph() -> EvidenceGraph:
    g = EvidenceGraph()
    g.add_source(Evidence(source_id=EID, source_type="code", snippet=SNIPPET, skill_tags=["redis"]))
    g.add_chunk(EvidenceChunk(chunk_id=f"{EID}#chunk0", source_id=EID, text=SNIPPET))
    return g


def _store() -> tuple[VectorStore, FakeEmbedder]:
    store = VectorStore(collection_name=f"chat_{uuid.uuid4().hex[:8]}")
    emb = FakeEmbedder(dim=256)
    store.add(
        ids=["c0"],
        embeddings=emb.embed([SNIPPET]),
        documents=[SNIPPET],
        metadatas=[{"source_id": EID, "source_type": "code", "skill_tags": "redis"}],
    )
    return store, emb


def _intent(op: dict) -> str:
    return json.dumps(op)


def _pass_verdict() -> str:
    return json.dumps({"verdicts": [{"bullet_id": 0, "verdict": "pass", "reason": "supported by cited evidence"}]})


def test_document_map_lists_indices():
    m = document_map(_resume())
    assert '[section 0] "Experience" (entries)' in m
    assert '[entry 0] "Backend Engineer" (Acme)' in m
    assert "[bullet 1] Wrote tests for the queue" in m
    assert '[section 1] "Technical Skills" (bullets)' in m


def test_rewrite_bullet_applied_keeps_evidence():
    client = MockLLMClient(
        {
            "chat_intent": [_intent({"intent": "rewrite_bullet", "section": 0, "entry": 0, "bullet": 0, "instruction": "sound more confident", "reply": "Rewrote it."})],
            "chat_rewrites": ['{"text": "Shipped a Redis-backed job queue to production"}'],
            "verifier": [_pass_verdict()],
        }
    )
    result = apply_chat(_resume(), "rewrite bullet", client=client, graph=_graph())
    assert result.applied
    bullet = result.resume.sections[0].entries[0].bullets[0]
    assert bullet.text == "Shipped a Redis-backed job queue to production"
    assert bullet.evidence_ids == [EID]
    assert bullet.verified is True
    assert result.resume.sections[0].entries[0].bullets[1].text == "Wrote tests for the queue"  # untouched


def test_rewrite_refused_when_verifier_fails():
    reason = "the rewrite claims production experience not in the evidence"
    client = MockLLMClient(
        {
            "chat_intent": [_intent({"intent": "rewrite_bullet", "section": 0, "entry": 0, "bullet": 0, "instruction": "claim we ran it in prod", "reply": ""})],
            "chat_rewrites": ['{"text": "Ran the queue in production for millions of users"}'],
            "verifier": [json.dumps({"verdicts": [{"bullet_id": 0, "verdict": "fail", "reason": reason}]})],
        }
    )
    original = _resume()
    result = apply_chat(original, "rewrite", client=client, graph=_graph())
    assert not result.applied
    assert result.flagged
    assert reason in result.message
    assert result.resume == original  # nothing changed


def test_tone_change_uses_rewrite_path():
    client = MockLLMClient(
        {
            "chat_intent": [_intent({"intent": "tone_change", "section": 1, "bullet": 0, "instruction": "make it formal", "reply": "Done."})],
            "chat_rewrites": ['{"text": "Proficient in Python, Redis, and Docker"}'],
            "verifier": [_pass_verdict()],
        }
    )
    result = apply_chat(_resume(), "make formal", client=client, graph=_graph())
    assert result.applied
    assert result.resume.sections[1].bullets[0].text == "Proficient in Python, Redis, and Docker"


def test_remove_bullet_prunes_empty_sections():
    client = MockLLMClient(
        {"chat_intent": [_intent({"intent": "remove_bullet", "section": 1, "bullet": 0, "reply": "Removed."})]}
    )
    result = apply_chat(_resume(), "remove it", client=client, graph=_graph())
    assert result.applied
    assert len(result.resume.sections) == 1  # Technical Skills became empty -> pruned
    assert result.resume.sections[0].title == "Experience"


def test_reorder_section():
    client = MockLLMClient(
        {"chat_intent": [_intent({"intent": "reorder_section", "section": 1, "to": 0, "reply": "Moved."})]}
    )
    result = apply_chat(_resume(), "move it up", client=client, graph=_graph())
    assert result.applied
    assert [s.title for s in result.resume.sections] == ["Technical Skills", "Experience"]


def test_add_claim_with_evidence():
    store, emb = _store()
    client = MockLLMClient(
        {
            "chat_intent": [_intent({"intent": "add_claim", "section": 0, "entry": 0, "bullet": -1, "text": "Built a Redis-backed job queue", "reply": "Added."})],
            "verifier": [_pass_verdict()],
        }
    )
    result = apply_chat(_resume(), "add: built a redis queue", client=client, graph=_graph(), store=store, embedder=emb)
    assert result.applied
    bullets = result.resume.sections[0].entries[0].bullets
    assert len(bullets) == 3
    added = bullets[-1]
    assert added.text == "Built a Redis-backed job queue"
    assert added.evidence_ids == [EID]
    assert added.verified is True


def test_add_claim_no_evidence_flagged_moment2():
    """A claim with no support in the corpus -> flagged, not inserted.

    The claim uses letters absent from the FakeEmbedder corpus (char-bag
    artifact: English sentences share too many characters to stay far apart),
    so retrieval genuinely finds nothing — the honest refusal path.
    """
    store, emb = _store()
    client = MockLLMClient(
        {
            "chat_intent": [_intent({"intent": "add_claim", "section": 0, "entry": 0, "bullet": -1, "text": "mmm ggg vvv fff", "reply": ""})],
            "verifier": [_pass_verdict()],  # should never be reached
        }
    )
    original = _resume()
    result = apply_chat(original, "add: I led a team of 10 engineers", client=client, graph=_graph(), store=store, embedder=emb)
    assert not result.applied
    assert result.flagged
    assert "couldn't find evidence" in result.message
    assert result.resume == original  # nothing inserted
    assert len(original.sections[0].entries[0].bullets) == 2


def test_add_claim_evidence_found_but_verifier_fails():
    store, emb = _store()
    reason = "the evidence shows a library list, not your own team leadership"
    client = MockLLMClient(
        {
            "chat_intent": [_intent({"intent": "add_claim", "section": 0, "entry": 0, "bullet": -1, "text": "I led the redis queue project", "reply": ""})],
            "verifier": [json.dumps({"verdicts": [{"bullet_id": 0, "verdict": "fail", "reason": reason}]})],
        }
    )
    original = _resume()
    result = apply_chat(original, "add: I led the redis queue project", client=client, graph=_graph(), store=store, embedder=emb)
    assert not result.applied
    assert result.flagged
    assert reason in result.message
    assert result.resume == original


def test_add_claim_without_store_flagged():
    client = MockLLMClient(
        {"chat_intent": [_intent({"intent": "add_claim", "section": 0, "entry": 0, "bullet": -1, "text": "anything", "reply": ""})]}
    )
    result = apply_chat(_resume(), "add something", client=client, graph=_graph())  # no store/embedder
    assert result.flagged and not result.applied
    assert "evidence store" in result.message


def test_none_intent_replies_only():
    client = MockLLMClient(
        {"chat_intent": [_intent({"intent": "none", "reply": "Sure — what would you like to change?"})]}
    )
    original = _resume()
    result = apply_chat(original, "hello", client=client)
    assert not result.applied
    assert result.message == "Sure — what would you like to change?"
    assert result.resume == original


def test_bad_target_rejected_without_crash():
    client = MockLLMClient(
        {"chat_intent": [_intent({"intent": "remove_bullet", "section": 99, "bullet": 0, "reply": ""})]}
    )
    result = apply_chat(_resume(), "remove", client=client)
    assert not result.applied
    assert "Unknown section" in result.message


def test_bullet_minus_one_rejected_for_rewrite_and_remove():
    """bullet=-1 means append ONLY for add_claim — for rewrite/remove it would
    silently hit the wrong bullet (or remove nothing) while claiming success."""
    for intent in ("rewrite_bullet", "remove_bullet"):
        client = MockLLMClient(
            {"chat_intent": [_intent({"intent": intent, "section": 0, "entry": 0, "bullet": -1, "reply": ""})]}
        )
        result = apply_chat(_resume(), "do it", client=client)
        assert not result.applied
        assert "requires a bullet index" in result.message


def test_add_claim_without_graph_flagged():
    store, emb = _store()
    client = MockLLMClient(
        {"chat_intent": [_intent({"intent": "add_claim", "section": 0, "entry": 0, "bullet": -1, "text": "Built a Redis-backed job queue", "reply": ""})]}
    )
    result = apply_chat(_resume(), "add a claim", client=client, store=store, embedder=emb)  # no graph
    assert result.flagged and not result.applied
    assert "evidence graph" in result.message


def test_classifier_retries_on_bad_json():
    client = MockLLMClient(
        {
            "chat_intent": [
                "not json",
                _intent({"intent": "remove_bullet", "section": 1, "bullet": 0, "reply": "Removed."}),
            ],
            "verifier": [_pass_verdict()],
        }
    )
    result = apply_chat(_resume(), "remove", client=client)
    assert result.applied
    assert len(result.resume.sections) == 1


def test_session_undo_restores():
    store, emb = _store()
    client = MockLLMClient(
        {
            "chat_intent": [_intent({"intent": "rewrite_bullet", "section": 0, "entry": 0, "bullet": 0, "instruction": "more confident", "reply": "Rewrote."})],
            "chat_rewrites": ['{"text": "Shipped a Redis-backed job queue to production"}'],
            "verifier": [_pass_verdict()],
        }
    )
    session = ChatSession(_resume(), client=client, graph=_graph(), store=store, embedder=emb)
    r1 = session.send("rewrite it")
    assert r1.applied
    assert session.resume.sections[0].entries[0].bullets[0].text == "Shipped a Redis-backed job queue to production"
    assert len(session.log) == 1

    undo = session.undo()
    assert undo is not None
    assert "Undid" in undo.message
    assert session.resume.sections[0].entries[0].bullets[0].text == "Built a Redis-backed job queue"
    assert session.log == []

    assert session.undo() is None  # nothing left to undo


def test_session_undo_after_multiple_ops():
    client = MockLLMClient(
        {
            "chat_intent": [
                _intent({"intent": "rewrite_bullet", "section": 0, "entry": 0, "bullet": 0, "instruction": "x", "reply": "1"}),
                _intent({"intent": "reorder_section", "section": 1, "to": 0, "reply": "2"}),
            ],
            "chat_rewrites": ['{"text": "v2 text"}'],
            "verifier": [_pass_verdict()],
        }
    )
    session = ChatSession(_resume(), client=client, graph=_graph())
    session.send("rewrite")
    session.send("reorder")
    assert [s.title for s in session.resume.sections] == ["Technical Skills", "Experience"]
    session.undo()  # undo reorder
    assert [s.title for s in session.resume.sections] == ["Experience", "Technical Skills"]
    assert session.resume.sections[0].entries[0].bullets[0].text == "v2 text"
    session.undo()  # undo rewrite
    assert session.resume.sections[0].entries[0].bullets[0].text == "Built a Redis-backed job queue"


def test_add_claim_embeds_only_claim():
    """Efficiency rule #2: chat's add_claim embeds only the new claim."""

    class CountingEmbedder(FakeEmbedder):
        def __init__(self):
            super().__init__(dim=256)
            self.embed_calls = 0

        def embed(self, texts):
            self.embed_calls += len(texts)
            return super().embed(texts)

    store = VectorStore(collection_name=f"chat_{uuid.uuid4().hex[:8]}")
    emb = CountingEmbedder()
    store.add(ids=["c0"], embeddings=emb.embed([SNIPPET]), documents=[SNIPPET], metadatas=[{"source_id": EID, "source_type": "code", "skill_tags": "redis"}])
    client = MockLLMClient(
        {
            "chat_intent": [_intent({"intent": "add_claim", "section": 0, "entry": 0, "bullet": -1, "text": "Built a Redis-backed job queue", "reply": "Added."})],
            "verifier": [_pass_verdict()],
        }
    )
    emb.embed_calls = 0  # ignore the corpus embed above
    apply_chat(_resume(), "add a claim", client=client, graph=_graph(), store=store, embedder=emb)
    assert emb.embed_calls == 1  # exactly one claim embedded
