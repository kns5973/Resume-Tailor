"""Web app tests: FastAPI TestClient over create_app() with injected mock
factories + a tiny persisted corpus (no torch, no network)."""
import json

from tests._pdf_helper import minimal_pdf

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


def test_run_with_previous_resume_multipart(tmp_path):
    """Attaching a previous resume via multipart upload: sections are ingested
    as citeable evidence and the builder is given the format reference."""
    client = _app(tmp_path)
    prev_pdf = minimal_pdf(["CAREER OBJECTIVE", "Seeking a backend role.", "TECHNICAL SKILLS", "Python, Redis, Docker"])
    res = client.post(
        "/api/run",
        data={
            "jd_text": JD_TEXT,
            "name": "Jake Ryan",
            "contact": json.dumps({"email": "j@x.com"}),
        },
        files={"previous_resume": ("old_resume.pdf", prev_pdf, "application/pdf")},
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["stats"]["matched"] == 1
    # the uploaded resume's sections became part of the persisted evidence graph
    graph = EvidenceGraph.model_validate_json(
        (tmp_path / "corpus" / "evidence_graph.json").read_text(encoding="utf-8")
    )
    assert "resume:old#career-objective" in graph.sources
    assert "resume:old#technical-skills" in graph.sources


def test_run_surfaces_llm_error_friendly(tmp_path):
    """A provider failure (e.g. Groq 429 rate limit) becomes a diagnosable 502
    with the real message — not a bare 'Internal Server Error'."""
    from resume_tailor.llm import LLMError

    def boom():
        raise LLMError("Groq API error (llama-3.3-70b-versatile): HTTP 429: rate limit reached")

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    from resume_tailor.collector.vector_store import VectorStore

    store = VectorStore(path=corpus_dir / "chroma")
    emb = FakeEmbedder(dim=256)
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
        client_factory=boom,
        embedder_factory=lambda: FakeEmbedder(dim=256),
        corpus_dir=corpus_dir,
        out_dir=tmp_path / "out",
        jobname="web_resume",
    )
    res = TestClient(app).post("/api/run", json={"jd_text": JD_TEXT, "evidence_based": False})
    assert res.status_code == 502
    assert "rate limit" in res.json()["detail"]


def test_run_rejects_non_pdf_previous_resume(tmp_path):
    """A non-PDF upload gets a friendly 400, not a raw 500."""
    res = _app(tmp_path).post(
        "/api/run",
        data={"jd_text": JD_TEXT, "name": "Jake Ryan"},
        files={"previous_resume": ("old.docx", b"% not a pdf at all", "application/pdf")},
    )
    assert res.status_code == 400
    assert "PDF" in res.json()["detail"]


def test_run_requires_corpus_or_previous_resume(tmp_path):
    """The empty-corpus guard is relaxed when a previous resume is uploaded:
    a resume alone can seed a run."""
    corpus_dir = tmp_path / "empty_corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(
        client_factory=_client_factory,
        embedder_factory=lambda: FakeEmbedder(dim=256),
        corpus_dir=corpus_dir,
        out_dir=tmp_path / "out",
        jobname="web_resume",
    )
    tc = TestClient(app)
    # no corpus, no github username, no previous resume -> 400
    assert tc.post("/api/run", json={"jd_text": JD_TEXT}).status_code == 400
    # a previous resume alone unlocks the run
    prev_pdf = minimal_pdf(["CAREER OBJECTIVE", "Seeking a backend role."])
    res = tc.post(
        "/api/run",
        data={"jd_text": JD_TEXT, "name": "Jake Ryan"},
        files={"previous_resume": ("old.pdf", prev_pdf, "application/pdf")},
    )
    assert res.status_code == 200, res.text
    # the uploaded resume alone seeded the corpus (previously it was empty)
    graph = EvidenceGraph.model_validate_json((corpus_dir / "evidence_graph.json").read_text(encoding="utf-8"))
    assert "resume:old#career-objective" in graph.sources


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


# --------------------------------------------------------------------------
# Session records: list/search/filter, tracking fields, review packet
# --------------------------------------------------------------------------


def test_sessions_listed_after_run_with_filters(tmp_path):
    client = _app(tmp_path)
    client.post("/api/run", json={"jd_text": JD_TEXT, "name": "Jake Ryan"})

    res = client.get("/api/sessions")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    rec = data["records"][0]
    assert rec["jd_title"] == "Senior Backend Engineer"
    assert rec["candidate_name"] == "Jake Ryan"
    assert rec["evidence_based"] is True
    assert rec["status"] == "verified"  # 1/1 bullets verified, evidence-based
    assert rec["difficulty"] == "easy"  # 1/1 requirements matched
    assert rec["progress"] == 90  # 20 + 40*1.0 + 30 + 0
    assert rec["topic"] == "Senior Backend Engineer"

    # keyword search (role, company, skills, bullets) — `total` stays the stored
    # count; `records` is the filtered result set
    assert len(client.get("/api/sessions", params={"q": "queue"}).json()["records"]) == 1
    assert len(client.get("/api/sessions", params={"q": "Acme"}).json()["records"]) == 1
    assert len(client.get("/api/sessions", params={"q": "zzz"}).json()["records"]) == 0
    # the raw JD text is stored and listed, so the UI can re-tailor this session
    assert rec["jd_text"] == JD_TEXT
    # facet filters
    assert len(client.get("/api/sessions", params={"status": "verified"}).json()["records"]) == 1
    assert len(client.get("/api/sessions", params={"status": "draft"}).json()["records"]) == 0
    assert len(client.get("/api/sessions", params={"topic": "Senior Backend Engineer"}).json()["records"]) == 1
    assert len(client.get("/api/sessions", params={"topic": "Data Scientist"}).json()["records"]) == 0
    assert len(client.get("/api/sessions", params={"source": "evidence"}).json()["records"]) == 1
    assert len(client.get("/api/sessions", params={"source": "draft"}).json()["records"]) == 0
    assert len(client.get("/api/sessions", params={"difficulty": "easy"}).json()["records"]) == 1
    assert len(client.get("/api/sessions", params={"difficulty": "hard"}).json()["records"]) == 0


def test_sessions_empty_before_any_run(tmp_path):
    res = _app(tmp_path).get("/api/sessions")
    assert res.status_code == 200
    assert res.json() == {"records": [], "topics": [], "total": 0}


def test_session_status_saved_and_persisted(tmp_path):
    from resume_tailor import sessions

    corpus_dir = tmp_path / "corpus"
    client = _app(tmp_path)
    sid = client.post("/api/run", json={"jd_text": JD_TEXT}).json()["session_id"]

    res = client.post(
        f"/api/sessions/{sid}/status",
        json={"status": "needs_work", "confidence": 65, "note": "needs distributed-systems evidence"},
    )
    assert res.status_code == 200
    rec = res.json()
    assert rec["status"] == "needs_work"
    assert rec["confidence"] == 65
    assert rec["note"] == "needs distributed-systems evidence"

    # persisted to disk, reloadable (fresh record from the same corpus dir)
    disk = sessions.load_record(sid, corpus_dir)
    assert disk is not None and disk.confidence == 65 and disk.status == "needs_work"

    # detail endpoint reflects the saved values
    assert client.get(f"/api/sessions/{sid}").json()["confidence"] == 65
    # validation
    assert client.post(f"/api/sessions/{sid}/status", json={"status": "bogus"}).status_code == 400
    assert client.post(f"/api/sessions/{sid}/status", json={"confidence": 101}).status_code == 400
    assert client.get("/api/sessions/nope").status_code == 404


def test_chat_edit_marks_record_refined(tmp_path):
    client = _app(tmp_path)
    sid = client.post("/api/run", json={"jd_text": JD_TEXT}).json()["session_id"]
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "verified"

    client.post("/api/chat", json={"session_id": sid, "message": "rewrite it"})
    rec = client.get(f"/api/sessions/{sid}").json()
    assert rec["status"] == "refined"
    assert rec["has_chat_edits"] is True
    # the user's confidence survives a chat update (merge, not rebuild)
    client.post(f"/api/sessions/{sid}/status", json={"confidence": 80})
    client.post("/api/chat", json={"session_id": sid, "message": "rewrite it again"})
    assert client.get(f"/api/sessions/{sid}").json()["confidence"] == 80


def test_review_packet_download(tmp_path):
    client = _app(tmp_path)
    sid = client.post("/api/run", json={"jd_text": JD_TEXT, "name": "Jake Ryan"}).json()["session_id"]
    res = client.get(f"/api/sessions/{sid}/packet")
    assert res.status_code == 200
    assert "text/markdown" in res.headers["content-type"]
    assert "attachment" in res.headers["content-disposition"]
    md = res.text
    assert md.startswith("# Resume Review Packet")
    assert "Jake Ryan" in md
    assert "Senior Backend Engineer" in md
    assert "Redis and message queues" in md
    assert "## 2. Tailored resume changes" in md
    assert "## 3. Supporting evidence" in md
    assert SNIPPET in md  # evidence snippet from the corpus graph
    assert "## 4. Weak areas" in md
    assert "## 6. Recommended next actions" in md


def test_packet_404_for_unknown_session(tmp_path):
    assert _app(tmp_path).get("/api/sessions/nope/packet").status_code == 404
