"""Hybrid retrieval before/after — does keyword boosting actually help?

Run:  .venv/bin/python scripts/demo_lexical.py
Matches the demo JD against the offline sindresorhus corpus TWICE:
  - semantic-only  (MatchConfig() default — today's behavior)
  - hybrid         (MatchConfig(use_keywords=True))
and prints per-requirement status/best-hit/trace + any matched↔gap flips.
Real embeddings; reformulation uses real Groq when GROQ_API_KEY is set,
else prompt-aware mocks (fully offline with RESUME_TAILOR_DRY_RUN=1).
"""
from __future__ import annotations

import json
from pathlib import Path

from resume_tailor.collector.embed import SentenceTransformerEmbedder
from resume_tailor.collector.vector_store import VectorStore
from resume_tailor.llm import MockLLMClient, get_client
from resume_tailor.matcher import MatchConfig, match_jd
from resume_tailor.schemas import JDRequirement, ParsedJD

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REQUIREMENTS = [
    ("Experience with Redis and message queues", ["redis", "message queue", "queue"]),
    ("Proficiency in Node.js and developer tooling", ["node.js", "cli"]),
    ("Knowledge of distributed systems", ["distributed systems"]),
    ("Lawn care and landscaping experience", ["lawn", "landscaping"]),
    ("Experience with PostgreSQL databases", ["postgresql"]),
]


def _jd() -> ParsedJD:
    return ParsedJD(requirements=[JDRequirement(requirement=r, keywords=k) for r, k in REQUIREMENTS])


def _reformulate(prompt: str) -> str:
    lower = prompt.lower()
    if "lawn" in lower or "landscaping" in lower:
        return json.dumps({"queries": ["landscaping services", "gardening equipment"]})
    if "distributed" in lower:
        return json.dumps({"queries": ["microservices", "message queue"]})
    return json.dumps({"queries": ["redis queue", "node.js cli tools"]})


def _run(client, config: MatchConfig) -> dict:
    store = VectorStore(path=PROJECT_ROOT / "data/chroma")
    embedder = SentenceTransformerEmbedder()
    result = match_jd(_jd(), store, embedder, client=client, config=config)
    rows = {}
    for m in result.all:
        best = m.hits[0] if m.hits else None
        rows[m.requirement] = {
            "status": m.status,
            "best": f"{m.best_distance:.3f}" if m.best_distance is not None else "None",
            "src": best.retrieval_source if best else "-",
            "trace": " -> ".join(m.query_trace),
        }
    return rows


def main() -> None:
    client = get_client()
    if isinstance(client, MockLLMClient):
        client.register("matcher_reformulation", _reformulate)

    plain = _run(client, MatchConfig())
    print("=== SEMANTIC-ONLY (default) ===")
    for req, row in plain.items():
        print(f"  [{row['status']:<7}] best={row['best']:<6} {req}")
        print(f"      trace: {row['trace']}")

    hybrid = _run(client, MatchConfig(use_keywords=True))
    print("\n=== HYBRID (use_keywords=True) ===")
    for req, row in hybrid.items():
        print(f"  [{row['status']:<7}] best={row['best']:<6} src={row['src']:<8} {req}")
        print(f"      trace: {row['trace']}")

    print("\n=== FLIPS ===")
    flips = 0
    lexical_evid = 0
    for req in plain:
        p, h = plain[req], hybrid[req]
        if p["status"] != h["status"]:
            flips += 1
            print(f"  {req}: {p['status']} -> {h['status']} (best {p['best']} -> {h['best']})")
        if h["src"] == "lexical":
            lexical_evid += 1
    print(f"\nRequirements flipped: {flips}/{len(plain)} | requirements whose best hit is lexical: {lexical_evid}")


if __name__ == "__main__":
    main()
