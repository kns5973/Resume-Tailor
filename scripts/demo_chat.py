"""Phase 5 demo — Chat Refinement Agent: typed edit ops, undo, honest refusal.

Run:  .venv/bin/python scripts/demo_chat.py
Real Groq when GROQ_API_KEY is set; offline mocks otherwise
(RESUME_TAILOR_DRY_RUN=1 for full determinism).

1. Build a verified resume from the demo JD + corpus (hybrid matching).
2. Chat over it: rewrite a bullet (re-verified), add an unverifiable claim
   (refused — demo moment #2), reorder a section, undo.
3. Re-render the PDF only on applied sends (efficiency rule #5).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from resume_tailor.chat import ChatSession, document_map
from resume_tailor.collector.embed import SentenceTransformerEmbedder
from resume_tailor.collector.vector_store import VectorStore
from resume_tailor.llm import MockLLMClient, get_client
from resume_tailor.matcher import MatchConfig
from resume_tailor.pipeline import run_full
from resume_tailor.render.latex import render_resume
from resume_tailor.schemas import EvidenceGraph, ResumeHeader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA = PROJECT_ROOT / "data"
OUT = PROJECT_ROOT / "out"

DEMO_JD_TEXT = """Senior Backend Engineer @ Acme
We build distributed systems for learning platforms at scale.
Requirements:
- 5+ years of Python
- Experience with Redis and message queues
- Node.js / developer tooling (nice to have)
- PostgreSQL and Docker
- Lawn care and landscaping experience (bonus)
"""

DEMO_MOCK_JD = {
    "title": "Senior Backend Engineer",
    "company": "Acme",
    "requirements": [
        {"requirement": "Experience with Redis and message queues", "priority": "must_have", "keywords": ["redis", "queue"]},
        {"requirement": "Proficiency in Node.js and developer tooling", "priority": "must_have", "keywords": ["node", "cli"]},
        {"requirement": "Knowledge of distributed systems", "priority": "nice_to_have", "keywords": ["distributed systems"]},
        {"requirement": "Lawn care and landscaping experience", "priority": "nice_to_have", "keywords": ["lawn", "landscaping"]},
    ],
}


def _reformulate(prompt: str) -> str:
    lower = prompt.lower()
    if "lawn" in lower or "landscaping" in lower:
        return json.dumps({"queries": ["landscaping services", "gardening equipment"]})
    if "distributed" in lower:
        return json.dumps({"queries": ["microservices", "message queue"]})
    return json.dumps({"queries": ["redis queue", "node.js cli tools"]})


def _mock_builder(prompt: str) -> str:
    ids = re.findall(r"evidence_id=([^\s]+)", prompt)
    real = ids[0] if ids else "repo:evidence#file:default.py"
    return json.dumps(
        {
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
                                    "text": "Accomplished production delivery of queue-backed tooling, by building on the cited open-source work",
                                    "evidence_ids": [real],
                                }
                            ],
                        }
                    ],
                },
                {"title": "Technical Skills", "bullets": [{"text": "Redis, Node.js, CLI tooling", "evidence_ids": [real]}]},
            ]
        }
    )


def _verifier_handler(prompt: str) -> str:
    ids = [int(i) for i in re.findall(r"Bullet (\d+):", prompt)]
    return json.dumps(
        {"verdicts": [{"bullet_id": i, "verdict": "pass", "reason": "claim supported by the cited evidence"} for i in ids]}
    )


def _chat_intent_handler(prompt: str) -> str:
    msg = prompt.split("User message:")[-1].lower()
    if "led a team" in msg or ("add" in msg and "claim" in msg):
        return json.dumps(
            {
                "intent": "add_claim",
                "section": 0,
                "entry": 0,
                "bullet": -1,
                "text": "I led a team of 10 engineers",
                "reply": "Checking the evidence for that claim...",
            }
        )
    if "rewrite" in msg or "confident" in msg:
        return json.dumps(
            {
                "intent": "rewrite_bullet",
                "section": 0,
                "entry": 0,
                "bullet": 0,
                "instruction": "make it sound more confident while staying strictly factual",
                "reply": "Rewriting it now...",
            }
        )
    if "move" in msg and "section" in msg:
        return json.dumps({"intent": "reorder_section", "section": 1, "to": 0, "reply": "Moving it to the top."})
    return json.dumps({"intent": "none", "reply": "What would you like to change?"})


def _chat_rewrite_handler(prompt: str) -> str:
    return json.dumps({"text": "Delivered a production Redis-backed job queue, by shipping retries and dead-letter handling"})


def _fixture_client() -> MockLLMClient:
    """Deterministic builder fixture — the base resume always comes from here.

    The LIVE verifier refuses every claim backed only by the foreign
    sindresorhus corpus (it correctly demands evidence of the candidate's own
    work), which would leave the chat demo with an empty resume. So the base
    resume is built with the offline fixture (verified bullets citing real
    corpus evidence ids), while the CHAT layer below runs with real Groq when
    a key is present.
    """
    client = MockLLMClient()
    client.register("jd_parser", [json.dumps(DEMO_MOCK_JD)])
    client.register("matcher_reformulation", _reformulate)
    client.register("bullet_generation", _mock_builder)
    client.register("verifier", _verifier_handler)
    return client


def main() -> None:
    result = run_full(
        DEMO_JD_TEXT,
        ResumeHeader(name="Jake Ryan", contact={"phone": "123-456-7890", "email": "jake@su.edu", "github": "github.com/jaker"}),
        client=_fixture_client(),
        corpus_dir=DATA,
        out_dir=OUT,
        jobname="chat_base",
        match_config=MatchConfig(use_keywords=True),
    )
    print(f"Base verified resume (offline fixture): {result.stats}")
    for section in result.build_result.resume.sections:
        print(f"  {section.title}")

    client = get_client()
    offline = isinstance(client, MockLLMClient)
    if offline:
        client.register("chat_intent", _chat_intent_handler)
        client.register("chat_rewrites", _chat_rewrite_handler)
        client.register("verifier", _verifier_handler)
        print("\nChat: offline mode (MockLLMClient — real Groq when GROQ_API_KEY is set)")
    else:
        print("\nChat: LIVE Groq mode")

    session = ChatSession(
        result.build_result.resume,
        client=client,
        graph=EvidenceGraph.model_validate_json((DATA / "evidence_graph.json").read_text(encoding="utf-8")),
        store=VectorStore(path=DATA / "chroma"),
        embedder=SentenceTransformerEmbedder(),
    )

    messages = [
        "Rewrite the first bullet in Experience to sound more confident.",
        "Add a claim: I led a team of 10 engineers.",
        "Move the Technical Skills section to the top.",
    ]
    for message in messages:
        r = session.send(message)
        status = "APPLIED" if r.applied else ("FLAGGED" if r.flagged else "replied")
        print(f"\n> {message}\n  [{status}] {r.message}")
        if r.applied:
            pdf = render_resume(session.resume, OUT, jobname="chat_resume")
            print(f"  re-rendered PDF -> {pdf}")

    undo = session.undo()
    if undo is not None:
        print(f"\n> undo\n  [APPLIED] {undo.message}")
        pdf = render_resume(session.resume, OUT, jobname="chat_resume")
        print(f"  re-rendered PDF -> {pdf}")
    else:
        print("\n> undo\n  [replied] nothing to undo")

    print("\n=== RESUME AFTER CHAT (document map) ===")
    print(document_map(session.resume))


if __name__ == "__main__":
    main()
