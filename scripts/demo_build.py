"""Phase 3 demo — full pipeline: JD -> match -> build -> verify -> PDF.

Run:  .venv/bin/python scripts/demo_build.py
Uses real Groq when GROQ_API_KEY is set (loaded from .env); otherwise falls
back to offline mocks (RESUME_TAILOR_DRY_RUN=1 for full determinism).

Shows demo moment #2 mechanics: the Verifier checks every bullet against its
cited evidence (claim ⊆ evidence); bullets that overclaim are dropped — and
the resume that reaches the PDF contains only verified, evidence-backed lines.
Outputs: out/verified_resume.json + out/verified_resume.pdf
"""
from __future__ import annotations

import json
import re
from pathlib import Path

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
    """Offline builder: cites real evidence ids from the prompt, plus one
    deliberate overclaim the Verifier will reject (unknown evidence id)."""
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
    if "REVISION FEEDBACK" in prompt:  # verifier pushed back — drop the overclaim
        bullets = bullets[:1]
    return json.dumps({"sections": [{"title": "Experience", "entries": [{"entry_type": "job", "title": "Backend Engineer", "subtitle": "Acme", "location": "Remote", "dates": "2020 - Present", "bullets": bullets}]}]})


def _mock_verifier(prompt: str) -> str:
    """Offline verifier: pass every bullet the LLM is asked about (the fake
    evidence id never reaches us — it fails deterministically first)."""
    ids = [int(i) for i in re.findall(r"Bullet (\d+):", prompt)]
    return json.dumps(
        {
            "verdicts": [
                {"bullet_id": i, "verdict": "pass", "reason": "claim supported by the cited evidence"} for i in ids
            ]
        }
    )


def _flat(section):
    if section.entries:
        for e in section.entries:
            yield from e.bullets
    else:
        yield from section.bullets


def main() -> None:
    client = get_client()
    offline = isinstance(client, MockLLMClient)
    if offline:
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
        jobname="verified_resume",
        match_config=MatchConfig(use_keywords=True),  # hybrid retrieval (also run_full's default)
    )

    print("=== JD REQUIREMENTS ===")
    for r in result.jd.requirements:
        print(f"  [{r.priority}] {r.requirement}")

    print(f"\n=== MATCHED ({len(result.match_result.matches)}) — gaps: {len(result.match_result.gaps)} ===")
    for m in result.match_result.matches:
        src = m.hits[0].retrieval_source if m.hits else "-"
        how = "reformulated" if len(m.query_trace) > 1 else "direct"
        print(f"  {m.requirement}  [best {m.best_distance:.3f}] [{src} / {how}]")

    print(f"\n=== VERIFICATION ({len(result.build_result.verification.verdicts)} bullets, "
          f"{result.stats['revisions']} revision(s)) ===")
    for v in result.build_result.verification.verdicts:
        mark = "✅" if v.verdict == "pass" else "❌"
        print(f"  {mark} [{v.source}] {v.claim[:70]}")
        print(f"      {v.reason}")

    if result.build_result.dropped:
        print("\n=== DROPPED (unverifiable — never rendered) ===")
        for d in result.build_result.dropped:
            print(f"  ✂️  {d.claim[:70]}  — {d.reason}")

    print("\n=== FINAL VERIFIED RESUME ===")
    for section in result.build_result.resume.sections:
        print(f"\n  {section.title}")
        for bullet in _flat(section):
            print(f"    [verified] {bullet.text}")
            for eid in bullet.evidence_ids:
                print(f"        ↳ {eid}")
    if not any(_flat(s) for s in result.build_result.resume.sections):
        print("\n  (no verified bullets — this corpus holds someone else's repos, not evidence of")
        print("   the candidate's own experience, so verification refused every claim. An empty")
        print("   honest resume beats a fabricated one — with the user's own repos this fills in.)")

    out_json = PROJECT_ROOT / "out" / "verified_resume.json"
    out_json.write_text(result.build_result.resume.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nVerified Resume JSON:  {out_json}")
    print(f"Rendered PDF:          {result.pdf} ({result.pdf.stat().st_size:,} bytes)")
    print(f"Stats: {result.stats}")


if __name__ == "__main__":
    main()
