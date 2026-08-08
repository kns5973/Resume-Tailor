"""Verifier Agent (fast tier) — guide §3 step 5.

Checks that every resume bullet's claim is supported by its cited evidence
(claim ⊆ cited evidence). Deterministic checks run first and never call the
LLM: a bullet with no evidence_ids, or with ids unknown to the EvidenceGraph,
fails immediately. Remaining bullets are checked by the LLM in ONE batch call
(one verdict list per prompt). Bullets that fail are dropped from the final
resume (or sent back to the Builder once via pipeline.py's bounce loop).
"""
from __future__ import annotations

from resume_tailor.llm import LLMClient, LLMError, get_client
from resume_tailor.schemas import BulletVerdict, EvidenceGraph, Resume, ResumeBullet, Verification

VERIFIER_SYSTEM = (
    "You verify resume bullet points against their cited evidence. A bullet "
    "passes only if every claim in it is supported by the cited evidence "
    "snippets (claim is a subset of the evidence). Rephrasing or minor "
    "formatting is fine; invented numbers, employers, skills, or outcomes are "
    "not. If the cited evidence content is unavailable, treat the claim as "
    "unverifiable (fail) rather than guessing. Respond with ONLY JSON: "
    '{"verdicts": [{"bullet_id": int, "verdict": "pass" | "fail", "reason": str}]} '
    "— exactly one verdict per bullet id."
)


def iter_bullets(resume: Resume):
    """Yield (index, ResumeBullet) in stable document order.

    sections -> entries -> bullets, matching the traversal the verifier and
    the pipeline's drop step both use, so flat bullet_ids stay consistent.
    """
    idx = 0
    for section in resume.sections:
        if section.entries:
            for entry in section.entries:
                for bullet in entry.bullets:
                    yield idx, bullet
                    idx += 1
        else:
            for bullet in section.bullets:
                yield idx, bullet
                idx += 1


def _resolve_snippets(evidence_ids: list[str], graph: EvidenceGraph | None) -> dict[str, str]:
    """Map each cited evidence id to its text, when the graph can resolve it.

    Chunks keyed by chunk_id, sources keyed by source_id — either is valid to
    cite. Without a graph (unit tests), every id counts as resolvable and the
    LLM judges from its own knowledge of the id names.
    """
    if graph is None:
        return {eid: "" for eid in evidence_ids}
    resolved: dict[str, str] = {}
    for eid in evidence_ids:
        chunk = graph.chunks.get(eid)
        if chunk is not None:
            resolved[eid] = chunk.text
            continue
        source = graph.sources.get(eid)
        if source is not None:
            resolved[eid] = source.snippet
    return resolved


def _llm_verdicts(
    candidates: list[tuple[int, ResumeBullet, dict[str, str]]],
    client: LLMClient,
    max_retries: int,
) -> list[BulletVerdict]:
    """One batched LLM verdict call over all candidates (efficiency rule #3)."""
    parts = []
    for idx, bullet, snippets in candidates:
        evidence = "\n".join(f"  - [{eid}] {snip[:200]}" for eid, snip in snippets.items()) or "  (evidence content unavailable)"
        parts.append(f"Bullet {idx}: claim: {bullet.text}\n  cited evidence:\n{evidence}")
    prompt = "Verify each bullet:\n\n" + "\n\n".join(parts)

    data: dict | None = None
    last_error: Exception | None = None
    for _attempt in range(max_retries + 1):
        try:
            data = client.complete_json(task="verifier", system=VERIFIER_SYSTEM, prompt=prompt)
            break
        except LLMError as exc:
            last_error = exc
            prompt += f"\n\nYour previous response failed validation: {exc}. Respond with valid JSON only."

    if data is None:
        # LLM unavailable after retries: fail closed (never mark unverified as
        # verified). source="operational" — this is an outage, not a judgment.
        return [
            BulletVerdict(
                bullet_id=idx,
                claim=b.text,
                evidence_ids=b.evidence_ids,
                verdict="fail",
                reason=f"verifier LLM error: {last_error}",
                source="operational",
            )
            for idx, b, _s in candidates
        ]

    returned: dict[int, tuple[str, str]] = {}
    for v in data.get("verdicts", []):
        try:
            bid = int(v.get("bullet_id"))
        except (TypeError, ValueError):
            continue
        verdict = str(v.get("verdict", "")).lower()
        if verdict in ("pass", "fail"):
            returned[bid] = (verdict, str(v.get("reason", "")))

    out: list[BulletVerdict] = []
    for idx, bullet, _snippets in candidates:
        if idx in returned:
            verdict, reason = returned[idx]
        else:
            verdict, reason = "fail", "no verdict returned for this bullet"
        out.append(
            BulletVerdict(
                bullet_id=idx,
                claim=bullet.text,
                evidence_ids=bullet.evidence_ids,
                verdict=verdict,
                reason=reason,
                source="llm",
            )
        )
    return out


def verify_resume(
    resume: Resume,
    *,
    client: LLMClient | None = None,
    graph: EvidenceGraph | None = None,
    max_retries: int = 1,
) -> Verification:
    """Verify every bullet in the resume. Returns one verdict per bullet.

    NOTE: the deterministic unknown-evidence-id auto-fail needs the graph.
    Without it, ids cannot be checked and the LLM adjudicates with unavailable
    evidence content (conservative by prompt). The full pipeline (run_full)
    always loads the graph from data/evidence_graph.json.
    """
    client = client or get_client()
    verdicts: list[BulletVerdict] = []
    llm_candidates: list[tuple[int, ResumeBullet, dict[str, str]]] = []

    for idx, bullet in iter_bullets(resume):
        if not bullet.evidence_ids:
            verdicts.append(
                BulletVerdict(
                    bullet_id=idx,
                    claim=bullet.text,
                    evidence_ids=[],
                    verdict="fail",
                    reason="no evidence cited",
                    source="deterministic",
                )
            )
            continue
        snippets = _resolve_snippets(bullet.evidence_ids, graph)
        unknown = [eid for eid in bullet.evidence_ids if eid not in snippets]
        if unknown:
            verdicts.append(
                BulletVerdict(
                    bullet_id=idx,
                    claim=bullet.text,
                    evidence_ids=bullet.evidence_ids,
                    verdict="fail",
                    reason=f"unknown evidence id(s): {', '.join(unknown)}",
                    source="deterministic",
                )
            )
            continue
        llm_candidates.append((idx, bullet, snippets))

    if llm_candidates:  # skip the LLM entirely when there is nothing to check
        verdicts.extend(_llm_verdicts(llm_candidates, client, max_retries))
    return Verification(verdicts=verdicts)
