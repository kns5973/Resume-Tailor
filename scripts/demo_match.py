"""Phase 2 demo — JD Parser + Matcher against the offline sindresorhus corpus.

Run:  .venv/bin/python scripts/demo_match.py
Uses real Groq when GROQ_API_KEY is set (loaded from .env); otherwise falls
back to offline mocks. Shows demo moment #1 mechanics: requirements that miss
on first retrieval get reformulated (query_trace), and requirements with no
supporting evidence are reported as honest gaps.
"""
from __future__ import annotations

import json
from pathlib import Path

from resume_tailor.collector.embed import SentenceTransformerEmbedder
from resume_tailor.collector.vector_store import VectorStore
from resume_tailor.jd_parser import parse_jd
from resume_tailor.llm import MockLLMClient, get_client
from resume_tailor.matcher import match_jd

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
    """Prompt-aware mock: a real LLM reformulation would do exactly this.

    Keeps unrelated requirements (e.g. lawn care) from being steered into the
    redis/node chunks by a shared generic query.
    """
    lower = prompt.lower()
    if "lawn" in lower or "landscaping" in lower:
        return json.dumps({"queries": ["landscaping services", "gardening equipment"]})
    if "distributed" in lower:
        return json.dumps({"queries": ["microservices", "message queue"]})
    return json.dumps({"queries": ["redis queue", "node.js cli tools"]})


def main() -> None:
    client = get_client()
    if isinstance(client, MockLLMClient):
        # Offline mode: seed the mock with fixtures (real Groq needs none).
        client.register("jd_parser", [json.dumps(DEMO_MOCK_JD)])
        client.register("matcher_reformulation", _reformulate)
    jd = parse_jd(DEMO_JD_TEXT, client=client)
    print("Parsed JD:")
    for r in jd.requirements:
        print(f"  [{r.priority}] {r.requirement}")

    store = VectorStore(path=PROJECT_ROOT / "data/chroma")
    embedder = SentenceTransformerEmbedder()
    result = match_jd(jd, store, embedder, client=client)

    print(f"\n=== MATCHED ({len(result.matches)}) ===")
    for m in result.matches:
        print(f"  {m.requirement}  [best {m.best_distance:.3f}]")
        print(f"    trace: {' -> '.join(m.query_trace)}")
        for h in m.hits[:2]:
            print(f"    - {h.distance:.3f} {h.source_id}: {h.text[:60]!r}")

    print(f"\n=== GAPS ({len(result.gaps)}) — honest, never fabricated ===")
    for g in result.gaps:
        print(f"  {g.requirement}  [best {g.best_distance}]")
        print(f"    trace: {' -> '.join(g.query_trace)}")


if __name__ == "__main__":
    main()
