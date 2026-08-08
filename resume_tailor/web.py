"""FastAPI web app — the professional frontend's backend (guide §7 Phase 5-6).

Wraps `run_full` + `ChatSession` in a small REST API and serves the no-build
SPA (static/index.html) from the same app. One in-memory ChatSession per run,
keyed by session_id — enough for a demo-grade tool.

Run:  RESUME_TAILOR_PORT=8177 .venv/bin/python -m resume_tailor.web
      (uvicorn on 127.0.0.1, default port 8000 — override with RESUME_TAILOR_PORT)
Endpoints:
  POST /api/run        {jd_text, name, contact, github_username?} -> full session payload
  POST /api/chat       {session_id, message} -> result + updated state
  POST /api/chat/undo  {session_id} -> revert last applied edit
  GET  /api/state?session_id=... -> current session payload
  GET  /api/pdf?session_id=... | /api/tex?session_id=... -> per-session artifacts
The PDF is re-rendered on applied chat sends only (efficiency rule #5).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from resume_tailor import sessions
from resume_tailor.chat import ChatSession
from resume_tailor.collector import CollectorInput, SentenceTransformerEmbedder, VectorStore
from resume_tailor.collector.embed import Embedder
from resume_tailor.llm import LLMClient, get_client
from resume_tailor.matcher import MatchConfig
from resume_tailor.pipeline import PipelineResult, run_full
from resume_tailor.render.latex import render_resume
from resume_tailor.schemas import EvidenceGraph, Resume, ResumeHeader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------

class RunRequest(BaseModel):
    jd_text: str
    name: str = "Candidate Name"
    contact: dict[str, str] = Field(default_factory=dict)
    github_username: str = Field(default="", description="optional: collect a live profile corpus")
    evidence_based: bool = Field(default=True, description="verify claims against evidence and drop unverifiable bullets; False = quick draft")



class ChatRequest(BaseModel):
    session_id: str
    message: str


class UndoRequest(BaseModel):
    session_id: str


class StatusUpdate(BaseModel):
    status: str | None = None
    confidence: int | None = None
    note: str | None = None


# --------------------------------------------------------------------------
# Serialization helpers (payloads the SPA renders)
# --------------------------------------------------------------------------

def _matches_payload(match_result) -> list[dict]:
    out = []
    for m in match_result.all:
        out.append(
            {
                "requirement": m.requirement,
                "priority": m.priority,
                "status": m.status,
                "best_distance": m.best_distance,
                "retrieval_source": m.hits[0].retrieval_source if m.hits else None,
                "query_trace": m.query_trace,
                "hits": [
                    {
                        "source_id": h.source_id,
                        "source_type": h.source_type,
                        "distance": round(h.distance, 3),
                        "text": h.text[:300],
                        "retrieval_source": h.retrieval_source,
                    }
                    for h in m.hits[:5]
                ],
            }
        )
    return out


def _verification_payload(build_result) -> dict:
    return {
        "verdicts": [
            {"bullet_id": v.bullet_id, "claim": v.claim, "verdict": v.verdict, "reason": v.reason, "source": v.source}
            for v in build_result.verification.verdicts
        ],
        "dropped": [{"claim": d.claim, "reason": d.reason} for d in build_result.dropped],
    }


def _transcript(chat: ChatSession) -> list[dict]:
    out: list[dict] = []
    for message, result in chat.history:
        out.append({"role": "user", "text": message})
        out.append({"role": "assistant", "text": result.message, "applied": result.applied, "flagged": result.flagged})
    return out


def _session_payload(session_id: str, chat: ChatSession, result: PipelineResult) -> dict:
    return {
        "session_id": session_id,
        "stats": result.stats,
        "jd": {
            "title": result.jd.title,
            "company": result.jd.company,
            "requirements": [{"requirement": r.requirement, "priority": r.priority} for r in result.jd.requirements],
        },
        "matches": _matches_payload(result.match_result),
        "verification": _verification_payload(result.build_result),
        "resume": result.build_result.resume.model_dump(),
        "transcript": _transcript(chat),
        "can_undo": bool(chat.log),
        "pdf_url": f"/api/pdf?session_id={session_id}",
        "pdf": str(result.pdf),
    }


# --------------------------------------------------------------------------
# App factory
# --------------------------------------------------------------------------

class _WebSession:
    def __init__(self, session_id: str, chat: ChatSession, result: PipelineResult, jobname: str) -> None:
        self.session_id = session_id
        self.chat = chat
        self.result = result
        self.jobname = jobname


def create_app(
    *,
    client_factory: Callable[[], LLMClient] = get_client,
    embedder_factory: Callable[[], Embedder] = SentenceTransformerEmbedder,
    corpus_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    jobname: str = "web_resume",
) -> FastAPI:
    """Build the FastAPI app. Tests inject mock factories + tmp dirs."""
    corpus_dir = Path(corpus_dir) if corpus_dir else PROJECT_ROOT / "data"
    out_dir = Path(out_dir) if out_dir else PROJECT_ROOT / "out"
    web_sessions: dict[str, _WebSession] = {}

    def _get(session_id: str) -> _WebSession:
        ws = web_sessions.get(session_id)
        if ws is None:
            raise HTTPException(404, f"Unknown session {session_id!r} — run a new job first.")
        return ws

    def _rerender(ws: _WebSession) -> None:
        render_resume(ws.chat.resume, out_dir, jobname=ws.jobname)

    def _persist_session(ws: _WebSession) -> None:
        """Snapshot the current session to disk, preserving user-set tracking
        fields (confidence/note/status override) across updates."""
        fresh = sessions.record_from_result(
            ws.session_id, ws.result, ws.chat.resume, _transcript(ws.chat), has_chat_edits=bool(ws.chat.log)
        )
        existing = sessions.load_record(ws.session_id, corpus_dir)
        if existing is not None:
            fresh.confidence = existing.confidence
            fresh.note = existing.note
            fresh.status_override = existing.status_override
            if existing.status_override:
                fresh.status = existing.status
        sessions.save_record(fresh, corpus_dir)

    app = FastAPI(title="Resume Tailor", version="0.1.0")

    @app.post("/api/run")
    def run(req: RunRequest) -> dict:
        if not req.jd_text.strip():
            raise HTTPException(400, "Job description text is required.")
        store = VectorStore(path=corpus_dir / "chroma")
        if store.count() == 0 and not req.github_username.strip():
            raise HTTPException(
                400,
                "No evidence corpus found. Pass a github_username to collect one live, or seed data/ first.",
            )
        collector = (
            CollectorInput(jd_text=req.jd_text, github_username=req.github_username)
            if req.github_username.strip()
            else None
        )
        client = client_factory()
        embedder = embedder_factory()
        header = ResumeHeader(name=req.name, contact=req.contact)
        session_id = uuid.uuid4().hex[:12]
        session_jobname = f"web_{session_id}"
        result = run_full(
            req.jd_text,
            header,
            client=client,
            collector=collector,
            corpus_dir=corpus_dir,
            out_dir=out_dir,
            jobname=session_jobname,  # per-session artifacts: no cross-session PDF bleed
            embedder=embedder,
            evidence_based=req.evidence_based,
        )
        graph_path = corpus_dir / "evidence_graph.json"
        graph = EvidenceGraph.model_validate_json(graph_path.read_text(encoding="utf-8")) if graph_path.exists() else EvidenceGraph()
        chat = ChatSession(
            result.build_result.resume,
            client=client,
            graph=graph,
            store=VectorStore(path=corpus_dir / "chroma"),
            embedder=embedder,
            match_config=MatchConfig(use_keywords=True),
        )
        web_sessions[session_id] = _WebSession(session_id, chat, result, jobname=session_jobname)
        _persist_session(web_sessions[session_id])
        return _session_payload(session_id, chat, result)

    @app.post("/api/chat")
    def chat(req: ChatRequest) -> dict:
        ws = _get(req.session_id)
        result = ws.chat.send(req.message)
        if result.applied:
            _rerender(ws)  # efficiency rule #5: recompile only on applied sends
        _persist_session(ws)
        return {
            "result": {"message": result.message, "applied": result.applied, "flagged": result.flagged},
            "resume": ws.chat.resume.model_dump(),
            "transcript": _transcript(ws.chat),
            "can_undo": bool(ws.chat.log),
        }

    @app.post("/api/chat/undo")
    def undo(req: UndoRequest) -> dict:
        ws = _get(req.session_id)
        result = ws.chat.undo()
        if result is not None:
            _rerender(ws)
        _persist_session(ws)
        return {
            "result": {"message": result.message if result else "Nothing to undo.", "applied": result is not None},
            "resume": ws.chat.resume.model_dump(),
            "transcript": _transcript(ws.chat),
            "can_undo": bool(ws.chat.log),
        }

    @app.get("/api/state")
    def state(session_id: str) -> dict:
        ws = _get(session_id)
        return _session_payload(ws.session_id, ws.chat, ws.result)

    def _artifact_path(session_id: str, ext: str) -> Path:
        """Artifacts are scoped per session (jobname = web_<session_id>), so a
        second run never serves its PDF over another session's."""
        ws = _get(session_id) if session_id else None
        name = ws.jobname if ws else jobname
        return out_dir / f"{name}.{ext}"

    @app.get("/api/pdf")
    def pdf(session_id: str = "") -> FileResponse:
        path = _artifact_path(session_id, "pdf")
        if not path.exists():
            raise HTTPException(404, "No PDF yet — run a job first.")
        return FileResponse(path, media_type="application/pdf", filename="resume.pdf")

    @app.get("/api/tex")
    def tex(session_id: str = "") -> FileResponse:
        path = _artifact_path(session_id, "tex")
        if not path.exists():
            raise HTTPException(404, "No .tex yet — run a job first.")
        return FileResponse(path, media_type="application/x-tex", filename="resume.tex")

    # ------------------------------------------------------------------
    # Session records: list/search/filter, tracking fields, review packet
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    def sessions_list(q: str = "", status: str = "", topic: str = "", source: str = "", difficulty: str = "") -> dict:
        records = sessions.load_records(corpus_dir)
        filtered = sessions.filter_records(
            records, q=q, status=status, topic=topic, source=source, difficulty=difficulty
        )
        topics = sorted({r.topic for r in records if r.topic})
        return {"records": [r.model_dump() for r in filtered], "topics": topics, "total": len(records)}

    def _get_record(session_id: str) -> sessions.SessionRecord:
        record = sessions.load_record(session_id, corpus_dir)
        if record is None:
            raise HTTPException(404, f"No saved session record for {session_id!r}")
        return record

    @app.get("/api/sessions/{session_id}")
    def session_detail(session_id: str) -> dict:
        return _get_record(session_id).model_dump()

    @app.post("/api/sessions/{session_id}/status")
    def session_status(session_id: str, req: StatusUpdate) -> dict:
        record = _get_record(session_id)
        if req.status is not None:
            if req.status not in sessions.STATUSES:
                raise HTTPException(400, f"status must be one of {', '.join(sessions.STATUSES)}")
            record.status = req.status
            record.status_override = True
        if req.confidence is not None:
            if not 0 <= req.confidence <= 100:
                raise HTTPException(400, "confidence must be 0-100")
            record.confidence = req.confidence
        if req.note is not None:
            record.note = req.note.strip()
        sessions.save_record(record, corpus_dir)
        return record.model_dump()

    @app.get("/api/sessions/{session_id}/packet")
    def session_packet(session_id: str) -> Response:
        record = _get_record(session_id)
        md = sessions.build_packet(record, sessions.load_evidence_index(corpus_dir))
        return Response(
            content=md,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="review_packet_{session_id}.md"'},
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run("resume_tailor.web:app", host="127.0.0.1", port=int(os.environ.get("RESUME_TAILOR_PORT", "8000")))
