"""Matcher Agent — agentic RAG (guide §3 step 3).

Embeds every JD requirement in ONE batch and issues a single batched Chroma
query (efficiency rule #3). Low-confidence hits trigger reformulation via the
strong tier (≤2 retries); if nothing lands, the requirement is an honest gap —
never hallucinated (guide: "mark as real gap, do not hallucinate").
"""
from __future__ import annotations

from dataclasses import dataclass

from resume_tailor.collector.embed import Embedder
from resume_tailor.collector.lexical import LexicalIndex
from resume_tailor.collector.vector_store import VectorStore
from resume_tailor.llm import LLMClient, get_client
from resume_tailor.schemas import EvidenceHit, MatchResult, ParsedJD, RequirementMatch

REFORMULATION_SYSTEM = (
    "You improve retrieval queries for a job requirement. Given the requirement "
    "and the closest evidence snippets already retrieved, write up to 2 shorter "
    "alternative queries of core skills/technologies. Respond with ONLY JSON: "
    '{"queries": [str]}. Example: "experience with distributed systems" -> '
    '["microservices", "message queue"].'
)


@dataclass
class MatchConfig:
    """Distance thresholds (cosine distance on the embedder's space).

    all-MiniLM-L6-v2: related pairs ~0.3-0.5, unrelated ~0.6-0.8. Tune per
    embedder; tests override with their own thresholds.

    use_keywords enables hybrid retrieval: the JD parser's exact keywords are
    scanned word-by-word over the corpus and pooled into each requirement's
    evidence set at `lexical_distance` (must sit below match_threshold to count
    as matched). This MatchConfig itself defaults OFF — direct match_jd()
    calls stay semantic-only; run_full() opts in by default.

    Caveat: all lexical hits sit at `lexical_distance`, so a keyword-rich JD
    (e.g. "redis" matching many chunks) can fill the whole top-n pool with
    lexical hits and crowd out semantically-closer-but-different chunks. If
    that shows up on real corpora, reserve slots (top-k semantic + top-k
    lexical) instead of a shared ranking.
    """

    match_threshold: float = 0.62  # below this = matched
    low_conf_threshold: float = 0.75  # above this, further reformulation is pointless
    n_results: int = 5
    max_reformulations: int = 2  # guide: ≤2
    use_keywords: bool = False  # hybrid: pool exact-keyword lexical hits
    lexical_distance: float = 0.2  # synthetic distance for lexical hits (< match_threshold)

    def __post_init__(self) -> None:
        if self.low_conf_threshold <= self.match_threshold:
            raise ValueError("low_conf_threshold must be greater than match_threshold")
        if self.lexical_distance >= self.match_threshold:
            raise ValueError("lexical_distance must be below match_threshold or lexical hits never count as matched")


def _to_hit(row: dict) -> EvidenceHit:
    meta = row.get("metadata") or {}
    return EvidenceHit(
        chunk_id=row["chunk_id"],
        text=row["text"],
        distance=row["distance"],
        source_id=meta.get("source_id", ""),
        source_type=meta.get("source_type", "unknown"),
        skill_tags=[t for t in meta.get("skill_tags", "").split(",") if t],
    )


def _pool(evidence: list[EvidenceHit], extra: list[EvidenceHit], n_results: int) -> list[EvidenceHit]:
    """Merge two hit sets, deduping by chunk_id (keep the closer hit), sorted."""
    by_id: dict[str, EvidenceHit] = {}
    for h in evidence + extra:
        existing = by_id.get(h.chunk_id)
        if existing is None or h.distance < existing.distance:
            by_id[h.chunk_id] = h
    return sorted(by_id.values(), key=lambda h: h.distance)[:n_results]


def _reformulate(requirement: str, hits: list[EvidenceHit], client: LLMClient) -> list[str]:
    snippets = "\n".join(f"- ({h.distance:.2f}) {h.text[:160]}" for h in hits[:3])
    prompt = f"Requirement: {requirement}\nClosest evidence so far:\n{snippets or '(none)'}"
    data = client.complete_json(task="matcher_reformulation", system=REFORMULATION_SYSTEM, prompt=prompt)
    return [q for q in data.get("queries", []) if isinstance(q, str) and q.strip()][:2]


def match_jd(
    jd: ParsedJD,
    store: VectorStore,
    embedder: Embedder,
    client: LLMClient | None = None,
    config: MatchConfig | None = None,
) -> MatchResult:
    """Match every JD requirement against the evidence store."""
    config = config or MatchConfig()
    client = client or get_client()
    result = MatchResult()

    if not jd.requirements:
        return result

    # Batch: embed ALL requirements in one call, then a single batched query.
    req_texts = [r.requirement for r in jd.requirements]
    rows = store.query(embedder.embed(req_texts), n_results=config.n_results)

    # Hybrid: build one lexical index over the corpus when any requirement
    # carries keywords — a single get_all() + in-memory scan, no extra calls.
    lexical = LexicalIndex(store.get_all()) if config.use_keywords and any(r.keywords for r in jd.requirements) else None

    for req, hits in zip(jd.requirements, rows):
        evidence = [_to_hit(h) for h in hits]
        if lexical is not None and req.keywords:
            lexical_hits = lexical.hits_for(req.keywords, n_results=config.n_results, distance=config.lexical_distance)
            evidence = _pool(evidence, lexical_hits, config.n_results)
        trace = [req.requirement]
        best = evidence[0] if evidence else None

        if best is not None and best.distance < config.match_threshold:
            status = "matched"
        else:
            # Low confidence or no hit: reformulate-retry (≤2, strong tier).
            for _ in range(config.max_reformulations):
                if best is None or best.distance >= config.low_conf_threshold:
                    break  # hopeless — don't waste tokens
                queries = _reformulate(req.requirement, evidence, client)
                if not queries:
                    break
                trace.extend(queries)
                extra = [
                    _to_hit(h)
                    for row in store.query(embedder.embed(queries), n_results=config.n_results)
                    for h in row
                ]
                evidence = _pool(evidence, extra, config.n_results)
                best = evidence[0]
                if best.distance < config.match_threshold:
                    break
            status = "matched" if best is not None and best.distance < config.match_threshold else "gap"

        matched_hits = [h for h in evidence if h.distance < config.match_threshold] if status == "matched" else []
        match = RequirementMatch(
            requirement=req.requirement,
            priority=req.priority,
            keywords=req.keywords,
            status=status,
            hits=matched_hits,
            best_distance=best.distance if best else None,
            query_trace=trace,
        )
        if status == "matched":
            result.matches.append(match)
        else:
            result.gaps.append(match)
    return result
