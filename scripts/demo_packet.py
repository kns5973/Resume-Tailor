"""Phase 6 demo — sample review packet.

Runs the full pipeline offline (MockLLMClient, same mocks as demo_build.py),
saves the run as a session record, and writes a downloadable review packet:

    out/review_packet_sample.md

Showcases the hackathon deliverables: structured review packet with tailored
resume changes (incl. education & career fields), supporting evidence, weak
areas, key questions from verification, and recommended next actions.

Run:  .venv/bin/python scripts/demo_packet.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from resume_tailor import sessions
from resume_tailor.llm import MockLLMClient, get_client
from resume_tailor.matcher import MatchConfig
from resume_tailor.pipeline import run_full
from resume_tailor.schemas import ResumeHeader

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
    bullets = [
        {
            "text": f"Accomplished production delivery of queue-backed tooling for {real.split('#')[1] if '#' in real else 'the team'}, as measured by shipped features, by building on the cited open-source work",
            "evidence_ids": [real],
        },
        {
            "text": "Led a team of 25 engineers to scale the platform to 10M users",
            "evidence_ids": ["repo:fake#file:nonexistent.py"],  # deliberately unverifiable
        },
    ]
    if "REVISION FEEDBACK" in prompt:
        bullets = bullets[:1]
    return json.dumps({"sections": [{"title": "Experience", "entries": [{"entry_type": "job", "title": "Backend Engineer", "subtitle": "Acme", "location": "Remote", "dates": "2020 - Present", "bullets": bullets}]}]})


def _mock_verifier(prompt: str) -> str:
    ids = [int(i) for i in re.findall(r"Bullet (\d+):", prompt)]
    return json.dumps(
        {"verdicts": [{"bullet_id": i, "verdict": "pass", "reason": "claim supported by the cited evidence"} for i in ids]}
    )


def main() -> None:
    client = get_client()
    if isinstance(client, MockLLMClient):
        client.register("jd_parser", [json.dumps(DEMO_MOCK_JD)])
        client.register("matcher_reformulation", _reformulate)
        client.register("bullet_generation", _mock_builder)
        client.register("verifier", _mock_verifier)
        print("Offline mode: MockLLMClient (real Groq when GROQ_API_KEY is set)\n")

    result = run_full(
        DEMO_JD_TEXT,
        ResumeHeader(name="Jake Ryan", contact={"phone": "123-456-7890", "email": "jake@su.edu", "github": "github.com/jaker"}),
        client=client,
        corpus_dir=PROJECT_ROOT / "data",
        out_dir=PROJECT_ROOT / "out",
        jobname="packet_demo",
        match_config=MatchConfig(use_keywords=True),
    )

    # Build a session record the same way the web app does, and persist it.
    record = sessions.record_from_result("packet_demo", result, result.build_result.resume, transcript=[])
    sessions.save_record(record, PROJECT_ROOT / "data")
    print(f"Session record saved → data/sessions/{record.session_id}.json")
    print(f"Status: {record.status} · Progress: {record.progress}% · Difficulty: {record.difficulty}")

    md = sessions.build_packet(record, sessions.load_evidence_index(PROJECT_ROOT / "data"))
    out = PROJECT_ROOT / "out" / "review_packet_sample.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"Sample review packet → {out} ({out.stat().st_size:,} bytes)")
    print()
    print(md)


if __name__ == "__main__":
    main()
