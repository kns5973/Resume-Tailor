"""Phase 3 orchestration — build, verify, revise, render (guide §3 steps 4-6).

build_verified_resume() runs the builder/verifier bounce loop: a draft whose
bullets fail verification is sent back to the builder once (≤1 revision) with
the verifier's feedback; bullets that still fail are dropped. The final resume
contains only verified, evidence-backed bullets — nothing fabricated.

run_full() chains collector (optional) -> JD parser -> matcher -> builder ->
verifier -> render: the complete pipeline the demo and app entry points use.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from resume_tailor.builder import generate_resume
from resume_tailor.collector import CollectorInput, SentenceTransformerEmbedder, VectorStore, collect_sync
from resume_tailor.collector.embed import Embedder
from resume_tailor.jd_parser import parse_jd
from resume_tailor.llm import LLMClient, get_client
from resume_tailor.matcher import MatchConfig, match_jd
from resume_tailor.render.latex import render_resume
from resume_tailor.schemas import (
    BuildResult,
    DroppedBullet,
    EvidenceGraph,
    MatchResult,
    ParsedJD,
    Resume,
    ResumeBullet,
    ResumeHeader,
    ResumeSection,
    Verification,
)
from resume_tailor.verifier import iter_bullets, verify_resume

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_MAX_REVISIONS = 1  # guide: the builder bounce happens at most once


def _feedback(verification: Verification) -> str:
    """Human-readable revision feedback from the verifier to the builder."""
    lines = []
    for v in verification.failed:
        lines.append(f"- bullet {v.bullet_id}: {v.claim[:120]!r} — {v.reason}")
    return "\n".join(lines)


def _apply_verdicts(draft: Resume, verification: Verification) -> Resume:
    """Keep only bullets that passed verification; drop the rest.

    Passed bullets are marked verified=True (schema requires evidence_ids —
    guaranteed: bullets without evidence always fail deterministically).
    Empty entries/sections are pruned so the rendered resume stays clean.

    Index semantics come from iter_bullets (same traversal the verifier uses);
    the manual walk below mirrors it structurally, and the runtime assertion
    catches any drift between the two.
    """
    verdicts = {v.bullet_id: v for v in verification.verdicts}

    def keep(idx: int) -> bool:
        verdict = verdicts.get(idx)
        return verdict is not None and verdict.verdict == "pass"

    sections: list[ResumeSection] = []
    idx = 0
    for section in draft.sections:
        if section.entries:
            kept_entries = []
            for entry in section.entries:
                kept_bullets = []
                for bullet in entry.bullets:
                    if keep(idx):
                        kept_bullets.append(ResumeBullet(text=bullet.text, evidence_ids=bullet.evidence_ids, verified=True))
                    idx += 1
                if kept_bullets:
                    kept_entries.append(entry.model_copy(update={"bullets": kept_bullets}))
            if kept_entries:
                sections.append(section.model_copy(update={"entries": kept_entries}))
        else:
            kept_bullets = []
            for bullet in section.bullets:
                if keep(idx):
                    kept_bullets.append(ResumeBullet(text=bullet.text, evidence_ids=bullet.evidence_ids, verified=True))
                idx += 1
            if kept_bullets:
                sections.append(section.model_copy(update={"bullets": kept_bullets}))
    assert idx == sum(1 for _ in iter_bullets(draft)), "bullet traversal drifted from iter_bullets"
    return Resume(name=draft.name, contact=draft.contact, sections=sections)


def build_verified_resume(
    jd: ParsedJD,
    match_result: MatchResult,
    header: ResumeHeader,
    *,
    client: LLMClient | None = None,
    graph: EvidenceGraph | None = None,
    max_revisions: int = _MAX_REVISIONS,
) -> BuildResult:
    """Build a verified Resume via the bounce loop (≤ max_revisions revisions).

    Draft -> verify; if any bullet fails and revisions remain, rebuild with the
    verifier's feedback; finally drop unverifiable bullets. The result never
    contains fabricated content: every bullet's claim is LLM-checked against
    its cited evidence and its evidence_ids resolve in the graph (or are LLM-
    adjudicated when no graph is supplied).
    """
    client = client or get_client()
    draft = generate_resume(jd, match_result, header, client=client)
    verification = verify_resume(draft, client=client, graph=graph)
    revisions = 0
    dropped: list[DroppedBullet] = []
    while verification.failed and revisions < max_revisions:
        _record_dropped(dropped, verification)  # cuts from this round stay in the trace
        revisions += 1
        draft = generate_resume(jd, match_result, header, client=client, feedback=_feedback(verification))
        verification = verify_resume(draft, client=client, graph=graph)
    _record_dropped(dropped, verification)  # final round's cuts
    final = _apply_verdicts(draft, verification)
    return BuildResult(resume=final, draft=draft, verification=verification, revisions=revisions, dropped=dropped)


def _record_dropped(ledger: list[DroppedBullet], verification: Verification) -> None:
    """Append a round's cuts to the dropped ledger, deduped by claim.

    A bullet the builder failed to fix can fail in round 1 AND round 2; it
    must appear once in the trace, not twice.
    """
    seen = {d.claim for d in ledger}
    for d in verification.dropped:
        if d.claim not in seen:
            ledger.append(d)
            seen.add(d.claim)


def _load_graph(corpus_dir: Path) -> EvidenceGraph:
    path = corpus_dir / "evidence_graph.json"
    if not path.exists():
        return EvidenceGraph()
    return EvidenceGraph.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass
class PipelineResult:
    """Everything produced by a full run, for the CLI/UI to display."""

    pdf: Path | None
    jd: ParsedJD
    match_result: MatchResult
    build_result: BuildResult
    stats: dict


def run_full(
    jd_text: str,
    header: ResumeHeader,
    *,
    client: LLMClient | None = None,
    collector: CollectorInput | None = None,
    corpus_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    jobname: str = "verified_resume",
    embedder: Embedder | None = None,
    match_config: MatchConfig | None = None,
    evidence_based: bool = True,
) -> PipelineResult:
    """Full pipeline: (collect corpus) -> parse JD -> match -> build -> verify -> PDF.

    Uses the offline corpus at data/ (built in Phase 1) by default. Pass a
    CollectorInput to build the corpus live when it does not exist yet.

    Matching defaults to hybrid retrieval (use_keywords=True): the JD parser's
    exact keywords are pooled as lexical evidence, so exact-tech-name
    requirements match directly and skip reformulation LLM calls. Pass an
    explicit MatchConfig to override (e.g. use_keywords=False for semantic-only).

    `evidence_based=True` (default) runs the full verify-and-drop loop: the
    final resume contains only bullets whose claims were confirmed against
    their cited evidence. With `evidence_based=False` the builder's draft is
    used as-is (no verification pass) — a quick draft mode for iterating on
    content before the strict pass.
    """
    client = client or get_client()
    corpus_dir = Path(corpus_dir) if corpus_dir else PROJECT_ROOT / "data"
    out_dir = Path(out_dir) if out_dir else PROJECT_ROOT / "out"

    store = VectorStore(path=corpus_dir / "chroma")
    graph = _load_graph(corpus_dir)
    if collector is not None and store.count() == 0:
        collect_sync(collector, store=store, cache_dir=corpus_dir)
        graph = _load_graph(corpus_dir)

    embedder = embedder or SentenceTransformerEmbedder()
    match_config = match_config or MatchConfig(use_keywords=True)  # hybrid retrieval on by default
    jd = parse_jd(jd_text, client=client)
    match_result = match_jd(jd, store, embedder, client=client, config=match_config)
    if evidence_based:
        build = build_verified_resume(jd, match_result, header, client=client, graph=graph)
    else:
        draft = generate_resume(jd, match_result, header, client=client)
        build = BuildResult(
            resume=draft,
            draft=draft,
            verification=Verification(),  # empty: no verification pass in draft mode
            revisions=0,
            dropped=[],
        )
    pdf = render_resume(build.resume, out_dir, jobname=jobname)

    stats = {
        "requirements": len(jd.requirements),
        "matched": len(match_result.matches),
        "gaps": len(match_result.gaps),
        "revisions": build.revisions,
        "dropped_bullets": len(build.dropped),
        "bullets_verified": sum(1 for _, b in iter_bullets(build.resume) if b.verified),
        "corpus_chunks": store.count(),
        "lexical_matches": sum(1 for m in match_result.matches if m.hits and m.hits[0].retrieval_source == "lexical"),
        "reformulations": sum(1 for m in match_result.all if len(m.query_trace) > 1),
        "evidence_based": evidence_based,
    }
    return PipelineResult(pdf=pdf, jd=jd, match_result=match_result, build_result=build, stats=stats)
