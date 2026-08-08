"""Web app tests: FastAPI TestClient over create_app() with injected mock
factories + a tiny persisted corpus (no torch, no network)."""
import json

from fastapi.testclient import TestClient

from resume_tailor.collector.embed import FakeEmbedder
from resume_tailor.llm import MockLLMClient
from resume_tailor.schemas import Evidence, EvidenceChunk, EvidenceGraph
from resume_tailor.web import create_app

EID = "repo:acme#file:queue.py#L45"
SNIPPET = "Built a Redis-backed job queue with retries"

JD_TEXT = "Senior Backend Engineer @ Acme, Redis queues"

CHAT_INTENT_REWRITE = json.dumps(
    {"intent": "rewrite_bullet", "section": 0, "entry": 0, "bullet": 0, "instruction": "sound more confident", "reply": "Rewrote."}
)
CHAT_INTENT_ADD = json.dumps(
    {"intent": "add_claim", "section": 0, "entry": 0, "bullet": -1, "text": "mmm ggg vvv fff", "reply": ""}
)


def _client_factory() -> MockLLMClient:
    client = MockLLMClient()
    client.register(
        "jd_parser",
        [
            json.dumps(
                {
                    "title": "Senior Backend Engineer",
                    "company": "Acme",
                    "requirements": [
                        {"requirement": "Redis and message queues", "priority": "must_have", "keywords": ["redis"]}
                    ],
                }
            )
        ],
    )
    client.register("matcher_reformulation", [json.dumps({"queries": [SNIPPET]})])
    client.register(
        "bullet_generation",
        [
            json.dumps(
                {
                    "sections": [
                        {
                            "title": "Experience",
                            "entries": [
                                {
                                    "entry_type": "job",
                                    "title": "Backend Engineer",
                                    "subtitle": "Acme",
                                    "bullets": [{"text": SNIPPET, "evidence_ids": [EID]}],
                                }
                            ],
                        }
                    ]
                }
            )
        ],
    )
    client.register("verifier", [json.dumps({"verdicts": [{"bullet_id": 0, "verdict": "pass", "reason": "supported"}]})])
    client.register("chat_intent", [CHAT_INTENT_REWRITE])
    client.register("chat_rewrites", ['{"text": "Shipped a Redis-backed job queue to production"}'])
    return client


def _app(tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    emb = FakeEmbedder(dim=256)
    from resume_tailor.collector.vector_store import VectorStore

    store = VectorStore(path=corpus_dir / "chroma")
    store.add(
        ids=[f"{EID}#chunk0"],
        embeddings=emb.embed([SNIPPET]),
        documents=[SNIPPET],
        metadatas=[{"source_id": EID, "source_type": "code", "skill_tags": "redis"}],
    )
    graph = EvidenceGraph()
    graph.add_source(Evidence(source_id=EID, source_type="code", snippet=SNIPPET, skill_tags=["redis"]))
    graph.add_chunk(EvidenceChunk(chunk_id=f"{EID}#chunk0", source_id=EID, text=SNIPPET))
    (corpus_dir / "evidence_graph.json").write_text(graph.model_dump_json(), encoding="utf-8")

    app = create_app(
        client_factory=_client_factory,
        embedder_factory=lambda: FakeEmbedder(dim=256),
        corpus_dir=corpus_dir,
        out_dir=tmp_path / "out",
        jobname="web_resume",
    )
    return TestClient(app)


def test_index_served(tmp_path):
    res = _app(tmp_path).get("/")
    assert res.status_code == 200
    assert "Resume Tailor" in res.text
    assert "app.js" in res.text


def test_static_assets(tmp_path):
    client = _app(tmp_path)
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_run_end_to_end(tmp_path):
    client = _app(tmp_path)
    res = client.post("/api/run", json={"jd_text": JD_TEXT, "name": "Jake Ryan", "contact": {"email": "j@x.com"}})
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["stats"]["matched"] == 1
    assert payload["matches"][0]["status"] == "matched"
    assert payload["resume"]["name"] == "Jake Ryan"
    assert payload["session_id"]
    # pdf endpoint works after a run (session-scoped)
    pdf = client.get("/api/pdf", params={"session_id": payload["session_id"]})
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"


def test_run_requires_jd(tmp_path):
    res = _app(tmp_path).post("/api/run", json={"jd_text": "  "})
    assert res.status_code == 400


def test_chat_rewrite_applies(tmp_path):
    client = _app(tmp_path)
    sid = client.post("/api/run", json={"jd_text": JD_TEXT}).json()["session_id"]
    res = client.post("/api/chat", json={"session_id": sid, "message": "rewrite it"})
    assert res.status_code == 200
    data = res.json()
    assert data["result"]["applied"] is True
    bullet = data["resume"]["sections"][0]["entries"][0]["bullets"][0]
    assert bullet["text"] == "Shipped a Redis-backed job queue to production"
    assert data["can_undo"] is True
    assert data["transcript"][-1]["applied"] is True


def test_chat_add_claim_flagged_moment2(tmp_path):
    client = _app(tmp_path)
    # swap the intent handler to add_claim for this run
    app_under = create_app(
        client_factory=lambda: _add_factory(),
        embedder_factory=lambda: FakeEmbedder(dim=256),
        corpus_dir=tmp_path / "corpus",
        out_dir=tmp_path / "out",
        jobname="web_resume",
    )
    tc = TestClient(app_under)
    sid = tc.post("/api/run", json={"jd_text": JD_TEXT}).json()["session_id"]
    res = tc.post("/api/chat", json={"session_id": sid, "message": "add: I led a team of 10 engineers"})
    data = res.json()
    assert data["result"]["flagged"] is True
    assert data["result"]["applied"] is False
    assert "couldn't find evidence" in data["result"]["message"]


def test_undo_restores(tmp_path):
    client = _app(tmp_path)
    sid = client.post("/api/run", json={"jd_text": JD_TEXT}).json()["session_id"]
    client.post("/api/chat", json={"session_id": sid, "message": "rewrite it"})
    res = client.post("/api/chat/undo", json={"session_id": sid})
    assert res.status_code == 200
    data = res.json()
    assert data["result"]["applied"] is True
    bullet = data["resume"]["sections"][0]["entries"][0]["bullets"][0]
    assert bullet["text"] == SNIPPET  # back to the original
    assert data["can_undo"] is False


def test_artifacts_scoped_per_session(tmp_path):
    """A second run must not serve its PDF over the first session's."""
    client = _app(tmp_path)
    sid_a = client.post("/api/run", json={"jd_text": JD_TEXT}).json()["session_id"]
    sid_b = client.post("/api/run", json={"jd_text": JD_TEXT}).json()["session_id"]
    assert sid_a != sid_b
    # each session serves its own artifact, keyed by session_id
    pdf_a = client.get("/api/pdf", params={"session_id": sid_a})
    pdf_b = client.get("/api/pdf", params={"session_id": sid_b})
    assert pdf_a.status_code == 200 and pdf_b.status_code == 200
    assert pdf_a.headers["content-type"] == "application/pdf"
    assert client.get("/api/tex", params={"session_id": sid_a}).status_code == 200
    # unknown session falls back to the app-level jobname (no crash)
    assert client.get("/api/pdf", params={"session_id": "nope"}).status_code in (200, 404)


def test_run_draft_mode_skips_verification(tmp_path):
    """evidence_based=false through the API: no verifier pass, drafts survive."""
    client = _app(tmp_path)
    res = client.post("/api/run", json={"jd_text": JD_TEXT, "evidence_based": False})
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["stats"]["evidence_based"] is False
    assert payload["verification"]["verdicts"] == []
    assert payload["verification"]["dropped"] == []
    bullets = payload["resume"]["sections"][0]["entries"][0]["bullets"]
    assert bullets[0]["text"] == SNIPPET
    assert bullets[0]["verified"] is False
    # PDF still renders in draft mode
    pdf = client.get("/api/pdf", params={"session_id": payload["session_id"]})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_run_evidence_based_default_true(tmp_path):
    """Default is evidence-based (the product premise): verification runs."""
    client = _app(tmp_path)
    payload = client.post("/api/run", json={"jd_text": JD_TEXT}).json()
    assert payload["stats"]["evidence_based"] is True
    assert len(payload["verification"]["verdicts"]) >= 1


def test_state_returns_full_payload(tmp_path):
    client = _app(tmp_path)
    sid = client.post("/api/run", json={"jd_text": JD_TEXT}).json()["session_id"]
    res = client.get("/api/state", params={"session_id": sid})
    assert res.status_code == 200
    payload = res.json()
    assert payload["session_id"] == sid
    assert payload["jd"]["title"] == "Senior Backend Engineer"
    assert payload["matches"][0]["status"] == "matched"
    assert payload["verification"]["verdicts"]
    assert payload["resume"]["name"] == "Candidate Name"
    assert payload["pdf_url"] == f"/api/pdf?session_id={sid}"


def test_unknown_session_404(tmp_path):
    res = _app(tmp_path).post("/api/chat", json={"session_id": "nope", "message": "hi"})
    assert res.status_code == 404


def test_tex_export(tmp_path):
    client = _app(tmp_path)
    sid = client.post("/api/run", json={"jd_text": JD_TEXT}).json()["session_id"]
    res = client.get("/api/tex", params={"session_id": sid})
    assert res.status_code == 200
    assert "\\documentclass" in res.text


def _add_factory() -> MockLLMClient:
    client = _client_factory()
    client.register("chat_intent", [CHAT_INTENT_ADD])
    return client
