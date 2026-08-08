"""Session records — persisted resume-tailoring runs.

Each completed web run is snapshotted to ``data/sessions/<session_id>.json`` so
it can be listed, searched, filtered, tracked (status / progress / confidence)
and summarized into a downloadable review packet after the fact. Records are
self-contained: they carry the JD, match/verification summary, the final resume
sections (incl. education & career fields), the chat transcript, and derived
status/progress/difficulty signals.

The web app is the only writer; these helpers keep persistence + derivation
pure and testable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from resume_tailor.schemas import EvidenceGraph

SessionStatus = Literal["draft", "verified", "refined", "needs_work"]
Difficulty = Literal["easy", "medium", "hard"]

STATUSES: tuple[SessionStatus, ...] = ("draft", "verified", "refined", "needs_work")
DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard")

RECORDS_DIR_NAME = "sessions"


class SessionRecord(BaseModel):
    """Everything a completed run produced, persisted as one JSON file."""

    session_id: str
    created_at: str
    candidate_name: str = ""
    jd_title: str = ""
    company: str = ""
    topic: str = ""
    evidence_based: bool = True
    status: SessionStatus = "draft"
    status_override: bool = Field(default=False, description="True once the user set the status manually")
    confidence: int = Field(default=0, ge=0, le=100, description="user-set confidence score (0-100)")
    note: str = Field(default="", description="user-set confidence note")
    progress: int = Field(default=0, ge=0, le=100, description="derived readiness score")
    difficulty: Difficulty = "medium"
    stats: dict = Field(default_factory=dict)
    requirements: list[dict] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    dropped: list[dict] = Field(default_factory=list)
    verdicts: list[dict] = Field(default_factory=list)
    sections: list[dict] = Field(default_factory=list, description="final resume sections (education/career included)")
    transcript: list[dict] = Field(default_factory=list)
    has_chat_edits: bool = False


# --------------------------------------------------------------------------
# Derivation helpers
# --------------------------------------------------------------------------

def derive_status(evidence_based: bool, bullets_verified: int, has_chat_edits: bool) -> SessionStatus:
    """Auto status: refined after chat edits, verified when the verifier kept
    bullets, otherwise a draft (incl. honest empty results)."""
    if has_chat_edits:
        return "refined"
    if evidence_based and bullets_verified > 0:
        return "verified"
    return "draft"


def derive_progress(stats: dict, bullets_verified: int, has_chat_edits: bool) -> int:
    """Readiness 0-100: base for completing a run + matched share + verified
    bullets + chat polish. Explainable and monotonic with the product story."""
    total = max(int(stats.get("requirements", 0) or 0), 1)
    matched = max(0, min(int(stats.get("matched", 0) or 0), total))
    matched_pct = matched / total
    score = 20 + 40 * matched_pct + (30 if bullets_verified > 0 else 0) + (10 if has_chat_edits else 0)
    return max(0, min(100, int(round(score))))


def derive_difficulty(stats: dict) -> Difficulty:
    """How hard the JD was to satisfy: share of requirements matched."""
    total = max(int(stats.get("requirements", 0) or 0), 1)
    matched = int(stats.get("matched", 0) or 0)
    ratio = matched / total
    if ratio >= 0.7:
        return "easy"
    if ratio >= 0.4:
        return "medium"
    return "hard"


# --------------------------------------------------------------------------
# Building records from pipeline results (duck-typed: needs .stats/.jd/.match_result)
# --------------------------------------------------------------------------

def _count_verified(sections: list[dict]) -> int:
    """Verified bullets in the CURRENT resume (not the run-time stat, which
    goes stale after chat edits / undo)."""
    return sum(1 for _, _, bullets in _iter_bullets(sections) for b in bullets if b.get("verified"))


def record_from_result(
    session_id: str,
    result,
    resume,
    transcript: list[dict],
    *,
    created_at: str | None = None,
    has_chat_edits: bool | None = None,
) -> SessionRecord:
    """Snapshot a finished run into a SessionRecord.

    ``result`` is a PipelineResult (duck-typed so this stays import-light);
    ``resume`` is the current Resume (post-chat edits); ``transcript`` is the
    chat history in the web payload shape. ``has_chat_edits`` defaults to the
    transcript's applied flags but callers can pass the authoritative signal
    (e.g. the chat patch log, which empties after undoing every edit).
    """
    stats = dict(result.stats)
    bullets_verified = _count_verified([s.model_dump() for s in resume.sections])
    evidence_based = bool(stats.get("evidence_based", True))
    status_map = {m.requirement: m.status for m in result.match_result.all}
    has_chat = bool(has_chat_edits) if has_chat_edits is not None else any(t.get("applied") for t in transcript)

    return SessionRecord(
        session_id=session_id,
        created_at=created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        candidate_name=resume.name or "",
        jd_title=result.jd.title or "",
        company=result.jd.company or "",
        topic=(result.jd.title or "").strip() or "Untitled JD",
        evidence_based=evidence_based,
        status=derive_status(evidence_based, bullets_verified, has_chat),
        progress=derive_progress(stats, bullets_verified, has_chat),
        difficulty=derive_difficulty(stats),
        stats=stats,
        requirements=[
            {
                "requirement": r.requirement,
                "priority": r.priority,
                "status": status_map.get(r.requirement, "unknown"),
            }
            for r in result.jd.requirements
        ],
        gaps=[m.requirement for m in result.match_result.all if m.status == "gap"],
        dropped=[{"claim": d.claim, "reason": d.reason} for d in result.build_result.dropped],
        verdicts=[
            {"bullet_id": v.bullet_id, "claim": v.claim, "verdict": v.verdict, "reason": v.reason, "source": v.source}
            for v in result.build_result.verification.verdicts
        ],
        sections=[s.model_dump() for s in resume.sections],
        transcript=transcript,
        has_chat_edits=has_chat,
    )


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def records_dir(corpus_dir: str | Path) -> Path:
    return Path(corpus_dir) / RECORDS_DIR_NAME


def save_record(record: SessionRecord, corpus_dir: str | Path) -> Path:
    d = records_dir(corpus_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{record.session_id}.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_record(session_id: str, corpus_dir: str | Path) -> SessionRecord | None:
    path = records_dir(corpus_dir) / f"{session_id}.json"
    if not path.exists():
        return None
    return SessionRecord.model_validate_json(path.read_text(encoding="utf-8"))


def load_records(corpus_dir: str | Path) -> list[SessionRecord]:
    """All records, newest first (created_at desc, ties by session id)."""
    d = records_dir(corpus_dir)
    out: list[SessionRecord] = []
    if not d.exists():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            out.append(SessionRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — a corrupt record must not break the list
            continue
    out.sort(key=lambda r: (r.created_at, r.session_id), reverse=True)
    return out


# --------------------------------------------------------------------------
# Search / filter
# --------------------------------------------------------------------------

def _searchable(record: SessionRecord) -> str:
    parts = [record.candidate_name, record.jd_title, record.company, record.topic]
    parts += [r.get("requirement", "") for r in record.requirements]
    parts += list(record.gaps)
    parts += [d.get("claim", "") for d in record.dropped]
    for s in record.sections:
        parts.append(s.get("title", ""))
        for e in s.get("entries") or []:
            parts.append(e.get("title", ""))
            for b in e.get("bullets") or []:
                parts.append(b.get("text", ""))
        for b in s.get("bullets") or []:
            parts.append(b.get("text", ""))
    return "\n".join(parts).lower()


def filter_records(
    records: list[SessionRecord],
    *,
    q: str = "",
    status: str = "",
    topic: str = "",
    source: str = "",
    difficulty: str = "",
) -> list[SessionRecord]:
    """Keyword search + facet filters. Empty facets mean 'any'. source is
    evidence|draft (the drafting mode)."""
    out = records
    if q.strip():
        needle = q.strip().lower()
        out = [r for r in out if needle in _searchable(r)]
    if status:
        out = [r for r in out if r.status == status]
    if topic:
        out = [r for r in out if r.topic.lower() == topic.lower()]
    if source == "evidence":
        out = [r for r in out if r.evidence_based]
    elif source == "draft":
        out = [r for r in out if not r.evidence_based]
    if difficulty:
        out = [r for r in out if r.difficulty == difficulty]
    return out


# --------------------------------------------------------------------------
# Review packet
# --------------------------------------------------------------------------

def load_evidence_index(corpus_dir: str | Path) -> dict:
    """{source_id: {snippet, url, source_type, skill_tags}} from the graph."""
    path = Path(corpus_dir) / "evidence_graph.json"
    if not path.exists():
        return {}
    graph = EvidenceGraph.model_validate_json(path.read_text(encoding="utf-8"))
    return {sid: ev.model_dump() for sid, ev in graph.sources.items()}


def _iter_bullets(sections: list[dict]):
    """Yield (section_title, entry-or-None, bullets). Section-level tuples are
    only yielded when the section actually has bullets (entry-based sections
    are covered by their entries, no spurious bare headings)."""
    for s in sections:
        for e in s.get("entries") or []:
            yield s.get("title", ""), e, e.get("bullets") or []
        if s.get("bullets"):
            yield s.get("title", ""), None, s["bullets"]


def recommended_actions(record: SessionRecord) -> list[str]:
    """Deterministic next-action suggestions derived from the record."""
    actions: list[str] = []
    if record.gaps:
        names = "; ".join(record.gaps[:5]) + ("…" if len(record.gaps) > 5 else "")
        actions.append(f"Add evidence for {len(record.gaps)} unmet requirement(s): {names}.")
    if record.stats.get("bullets_verified", 0) == 0 and record.evidence_based:
        actions.append("No verified bullets survived — re-run against your own evidence (own GitHub/repos), not the demo corpus.")
    if not record.evidence_based:
        actions.append("Re-run with evidence-based drafting ON so every claim is verified before it lands.")
    if record.has_chat_edits:
        actions.append("Review the chat edit log and undo any edit that doesn't match the cited evidence.")
    if record.confidence < 50:
        actions.append("Set a confidence score + note after reviewing the weak areas below (currently below 50/100).")
    actions.append("Export the final PDF (.pdf) and LaTeX (.tex) and send the tailored resume.")
    return actions


def build_packet(record: SessionRecord, evidence: dict | None = None) -> str:
    """Structured Markdown review packet for one session."""
    evidence = evidence or {}
    lines: list[str] = []
    add = lines.append

    add("# Resume Review Packet")
    add("")
    add(f"**Candidate:** {record.candidate_name or '—'}")
    add(f"**Role:** {record.jd_title or '—'}{' @ ' + record.company if record.company else ''}")
    add(f"**Session:** `{record.session_id}` · created {record.created_at}")
    add(f"**Status:** {record.status} · **Progress:** {record.progress}% · **Confidence:** {record.confidence}/100 · **Difficulty:** {record.difficulty}")
    add(f"**Mode:** {'evidence-based (verified)' if record.evidence_based else 'quick draft (no verification pass)'}")

    # 1. Overview
    add("")
    add("## 1. Session overview")
    add("")
    st = record.stats
    add(f"- Requirements: **{st.get('requirements', 0)}** · matched **{st.get('matched', 0)}** · gaps **{st.get('gaps', 0)}**")
    add(f"- Bullets verified: **{st.get('bullets_verified', 0)}** · dropped: **{st.get('dropped_bullets', 0)}** · revisions: **{st.get('revisions', 0)}**")
    add(f"- Corpus chunks: {st.get('corpus_chunks', '—')} · lexical matches: {st.get('lexical_matches', '—')} · reformulations: {st.get('reformulations', '—')}")
    if record.requirements:
        add("")
        add("Requirements:")
        for r in record.requirements:
            mark = "✅ matched" if r.get("status") == "matched" else ("❌ gap" if r.get("status") == "gap" else "?")
            add(f"- [{mark}] {r.get('requirement', '')} ({r.get('priority', '')})")

    # 2. Tailored resume changes (education + career fields)
    # (kept as a separate heading so the spec's education/career fields are explicit)
    add("")
    add("## 2. Tailored resume changes")
    add("")
    if not record.sections:
        add("- (no sections yet)")
    for section_title, entry, bullets in _iter_bullets(record.sections):
        if entry is not None:
            head = f"- **{entry.get('title', '')}**" + (f" — {entry.get('subtitle', '')}" if entry.get("subtitle") else "")
            head += f" [{section_title}]" + (f" · {entry.get('dates', '')}" if entry.get("dates") else "")
            add(head)
        else:
            add(f"- **{section_title}**")
        for b in bullets:
            mark = "✓" if b.get("verified") else "!"
            add(f"  - {mark} {b.get('text', '')}")
            for eid in b.get("evidence_ids") or []:
                add(f"    - `{eid}`")

    # 3. Supporting evidence
    add("")
    add("## 3. Supporting evidence")
    add("")
    seen: set[str] = set()
    for _, entry, bullets in _iter_bullets(record.sections):
        for b in bullets:
            for eid in b.get("evidence_ids") or []:
                if eid in seen:
                    continue
                seen.add(eid)
                ev = evidence.get(eid)
                if ev:
                    snippet = (ev.get("snippet") or "")[:220].replace("\n", " ")
                    add(f"- **{eid}** ({ev.get('source_type', '?')})")
                    add(f"  - _{snippet}_")
                    if ev.get("url"):
                        add(f"  - <{ev['url']}>")
                else:
                    add(f"- **{eid}** (evidence not in graph)")
    if not seen:
        add("- No evidence citations on record.")

    # 4. Weak areas
    add("")
    add("## 4. Weak areas")
    add("")
    if record.gaps:
        add("Unmet requirements (no evidence found — honest gaps):")
        for g in record.gaps:
            add(f"- {g}")
    else:
        add("- No unmet requirements.")
    if record.dropped:
        add("")
        add("Dropped bullets (verifier rejected the claim):")
        for d in record.dropped:
            add(f"- ~~{d.get('claim', '')}~~ — {d.get('reason', '')}")
    else:
        add("- Nothing dropped by the verifier.")

    # 5. Key questions raised by verification
    add("")
    add("## 5. Key questions raised by verification")
    add("")
    if record.verdicts:
        for v in record.verdicts:
            add(f"- [{v.get('verdict', '?')}] {v.get('claim', '')} — {v.get('reason', '')} `[{v.get('source', '')}]`")
    else:
        add("- No bullets were LLM-verified (draft mode or nothing to verify).")

    # 6. Recommended next actions
    add("")
    add("## 6. Recommended next actions")
    add("")
    for a in recommended_actions(record):
        add(f"- {a}")

    # 7. Chat refinement log
    add("")
    add("## 7. Chat refinement log")
    add("")
    applied = [t for t in record.transcript if t.get("role") == "assistant" and t.get("applied")]
    flagged = [t for t in record.transcript if t.get("role") == "assistant" and t.get("flagged")]
    if applied:
        for t in applied:
            add(f"- ✓ {t.get('text', '')}")
    if flagged:
        add("")
        add("Refused requests (nothing was applied):")
        for t in flagged:
            add(f"- ⚠ {t.get('text', '')}")
    if not applied and not flagged and record.transcript:
        add("- No applied chat edits.")
    if not record.transcript:
        add("- No chat activity.")

    return "\n".join(lines)
