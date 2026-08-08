"""Chat Refinement Agent (Phase 5: guide §3 step 7).

Flow: fast-tier intent classification -> typed edit op -> DETERMINISTIC apply.
The model never returns mutated resume JSON — only a target + new content, so
everything it didn't touch stays byte-identical and every applied edit lands
in the patch log (undoable).

Honesty guarantees:
- rewrite_bullet / tone_change keep the cited evidence and are re-verified:
  an edit that overclaims is REFUSED with the verifier's reason (never applied).
- add_claim routes through the same retrieval + verification machinery as the
  build: embed the claim once (efficiency rule #2) -> Chroma -> evidence found?
  attach + verify; not found -> flagged in chat, nothing inserted — the user is
  asked for a source (demo moment #2).

Re-rendering LaTeX is the caller's job and must happen only on applied sends
(efficiency rule #5).
"""
from __future__ import annotations

from pydantic import ValidationError

from resume_tailor.llm import LLMClient, LLMError, get_client
from resume_tailor.matcher import MatchConfig
from resume_tailor.schemas import (
    ChatEditOp,
    ChatResult,
    EvidenceGraph,
    PatchEntry,
    Resume,
    ResumeBullet,
    ResumeEntry,
    ResumeSection,
)
from resume_tailor.verifier import verify_resume

CHAT_INTENT_SYSTEM = (
    "You route a chat message about the user's resume to exactly one edit "
    "operation. The Document Map below lists every section, entry, and bullet "
    "with its index. Respond with ONLY JSON matching: "
    '{"intent": "rewrite_bullet" | "add_claim" | "reorder_section" | '
    '"remove_bullet" | "tone_change" | "none", "section": int, "entry": int, '
    '"bullet": int, "to": int, "text": str, "instruction": str, "reply": str}. '
    "Target via the map's indices; use -1 where not applicable. "
    "add_claim: put the exact claim sentence in text and leave bullet=-1 "
    "(it appends). rewrite_bullet / tone_change: put the user's edit "
    "instruction verbatim in instruction. reorder_section: section = index of "
    "the section to move, to = destination index. remove_bullet: target the "
    "bullet precisely. For non-edit messages (greetings, questions, thanks) "
    "use intent none with a helpful reply. reply is a short assistant message "
    "shown to the user."
)

CHAT_REWRITE_SYSTEM = (
    "You rewrite one resume bullet. Keep every fact strictly inside the cited "
    "evidence snippet — never add numbers, skills, employers, or outcomes the "
    "evidence does not show. Apply the user's instruction (confidence, tone, "
    "brevity, XYZ format) while staying true to the evidence. Respond with "
    'ONLY JSON: {"text": str}.'
)


class ChatEditError(ValueError):
    """Raised when an edit op cannot be applied (bad target, etc.)."""


# --------------------------------------------------------------------------
# Document map + classification
# --------------------------------------------------------------------------

def document_map(resume: Resume) -> str:
    """Indexed view of the resume the classifier targets (exact indices)."""
    lines: list[str] = []
    for si, section in enumerate(resume.sections):
        if section.entries:
            lines.append(f'[section {si}] "{section.title}" (entries)')
            for ei, entry in enumerate(section.entries):
                head = f'  [entry {ei}] "{entry.title}"'
                if entry.subtitle:
                    head += f" ({entry.subtitle})"
                lines.append(head + " bullets:")
                for bi, bullet in enumerate(entry.bullets):
                    lines.append(f"    [bullet {bi}] {bullet.text[:140]}")
        else:
            lines.append(f'[section {si}] "{section.title}" (bullets)')
            for bi, bullet in enumerate(section.bullets):
                lines.append(f"  [bullet {bi}] {bullet.text[:140]}")
    return "\n".join(lines)


def _classify(resume: Resume, message: str, client: LLMClient, max_retries: int = 1) -> ChatEditOp:
    prompt = f"Document map:\n{document_map(resume)}\n\nUser message:\n{message}"
    last_error: Exception | None = None
    for _attempt in range(max_retries + 1):
        try:
            data = client.complete_json(task="chat_intent", system=CHAT_INTENT_SYSTEM, prompt=prompt)
            return ChatEditOp.model_validate(data)
        except (LLMError, ValidationError) as exc:
            last_error = exc
            prompt += f"\n\nYour previous response failed validation: {exc}. Respond with valid JSON only."
    raise LLMError(f"chat_intent failed after {max_retries + 1} attempts: {last_error}")


def _rewrite_text(bullet_text: str, snippet: str, instruction: str, client: LLMClient) -> str:
    prompt = (
        f"Original bullet: {bullet_text}\n"
        f"Cited evidence: {snippet or '(none — stay minimal and honest)'}\n"
        f"Instruction: {instruction}"
    )
    data = client.complete_json(task="chat_rewrites", system=CHAT_REWRITE_SYSTEM, prompt=prompt)
    text = str(data.get("text", "")).strip()
    if not text:
        raise LLMError("chat_rewrites returned empty text")
    return text


# --------------------------------------------------------------------------
# Targeting + deterministic application
# --------------------------------------------------------------------------

def _locate(resume: Resume, op: ChatEditOp) -> tuple[ResumeSection, ResumeEntry | None, int]:
    """(section, entry-or-None, entry_index_or_-1); raises ChatEditError."""
    if op.intent in ("rewrite_bullet", "tone_change", "remove_bullet") and op.bullet < 0:
        # -1 means "append" only for add_claim; for the other bullet intents it
        # would silently target the wrong bullet (or remove nothing) — reject it.
        raise ChatEditError(f"{op.intent} requires a bullet index (got -1) — nothing changed.")
    if not (0 <= op.section < len(resume.sections)):
        raise ChatEditError(f"Unknown section index {op.section} — nothing changed.")
    section = resume.sections[op.section]
    if section.entries:
        if not (0 <= op.entry < len(section.entries)):
            raise ChatEditError(f"Unknown entry index {op.entry} in section {op.section} — nothing changed.")
        entry = section.entries[op.entry]
        if op.bullet >= 0 and op.bullet >= len(entry.bullets):
            raise ChatEditError(f"Unknown bullet index {op.bullet} in entry {op.entry} — nothing changed.")
        return section, entry, op.entry
    if op.bullet >= 0 and op.bullet >= len(section.bullets):
        raise ChatEditError(f"Unknown bullet index {op.bullet} in section {op.section} — nothing changed.")
    return section, None, -1


def _rebuild(
    resume: Resume,
    section_idx: int,
    entry_idx: int,
    mutate: callable,
) -> Resume:
    """Rebuild the resume with `mutate(bullets) -> bullets` applied at the
    targeted list, pruning entries/sections that end up empty."""
    sections: list[ResumeSection] = []
    for si, section in enumerate(resume.sections):
        if si != section_idx:
            sections.append(section)
            continue
        if section.entries:
            entries: list[ResumeEntry] = []
            for ei, entry in enumerate(section.entries):
                updated = entry.model_copy(update={"bullets": mutate(entry.bullets)}) if ei == entry_idx else entry
                if updated.bullets:
                    entries.append(updated)
            if entries:
                sections.append(section.model_copy(update={"entries": entries}))
        else:
            bullets = mutate(section.bullets)
            if bullets:
                sections.append(section.model_copy(update={"bullets": bullets}))
    return resume.model_copy(update={"sections": sections})


def _summarize(op: ChatEditOp) -> str:
    where = f"section {op.section}"
    if op.entry >= 0:
        where += f" entry {op.entry}"
    if op.bullet >= 0:
        where += f" bullet {op.bullet}"
    return f"{op.intent} in {where}"


def _apply_rewrite(resume: Resume, op: ChatEditOp, new_text: str) -> Resume:
    section, _entry, entry_idx = _locate(resume, op)

    def mutate(bullets: list[ResumeBullet]) -> list[ResumeBullet]:
        out = list(bullets)
        old = out[op.bullet]
        out[op.bullet] = ResumeBullet(text=new_text, evidence_ids=old.evidence_ids, verified=old.verified)
        return out

    return _rebuild(resume, op.section, entry_idx, mutate)


def _apply_add(resume: Resume, op: ChatEditOp, bullet: ResumeBullet) -> Resume:
    section, _entry, entry_idx = _locate(resume, op)

    def mutate(bullets: list[ResumeBullet]) -> list[ResumeBullet]:
        out = list(bullets)
        if op.bullet >= 0:
            out.insert(min(op.bullet, len(out)), bullet)
        else:
            out.append(bullet)
        return out

    return _rebuild(resume, op.section, entry_idx, mutate)


def _apply_remove(resume: Resume, op: ChatEditOp) -> Resume:
    section, _entry, entry_idx = _locate(resume, op)

    def mutate(bullets: list[ResumeBullet]) -> list[ResumeBullet]:
        return [b for i, b in enumerate(bullets) if i != op.bullet]

    return _rebuild(resume, op.section, entry_idx, mutate)


def _apply_reorder(resume: Resume, op: ChatEditOp) -> Resume:
    n = len(resume.sections)
    if not (0 <= op.section < n and 0 <= op.to < n):
        raise ChatEditError(f"Section index out of range (0..{n - 1}) — nothing changed.")
    if op.section == op.to:
        return resume
    sections = list(resume.sections)
    moved = sections.pop(op.section)
    sections.insert(op.to, moved)
    return resume.model_copy(update={"sections": sections})


# --------------------------------------------------------------------------
# Verification helpers
# --------------------------------------------------------------------------

def _evidence_snippet(bullet: ResumeBullet, graph: EvidenceGraph | None) -> str:
    if graph is None:
        return ""
    parts: list[str] = []
    for eid in bullet.evidence_ids:
        chunk = graph.chunks.get(eid)
        if chunk is not None:
            parts.append(chunk.text)
            continue
        source = graph.sources.get(eid)
        if source is not None:
            parts.append(source.snippet)
    return " | ".join(p[:200] for p in parts)[:600]


def _verify_bullet(bullet: ResumeBullet, client: LLMClient, graph: EvidenceGraph | None):
    """Verify one bullet via the shared Verifier (claim ⊆ cited evidence)."""
    mini = Resume(name="", contact={}, sections=[ResumeSection(title="", bullets=[bullet])])
    verification = verify_resume(mini, client=client, graph=graph)
    return verification.verdicts[0] if verification.verdicts else None


# --------------------------------------------------------------------------
# Intent handlers
# --------------------------------------------------------------------------

def _handle_rewrite(resume: Resume, op: ChatEditOp, client: LLMClient, graph: EvidenceGraph | None) -> ChatResult:
    try:
        section, entry, _entry_idx = _locate(resume, op)
        bullets = entry.bullets if entry is not None else section.bullets
        bullet = bullets[op.bullet]
        snippet = _evidence_snippet(bullet, graph)
        new_text = _rewrite_text(bullet.text, snippet, op.instruction, client)
    except (ChatEditError, LLMError) as exc:
        return ChatResult(message=f"Couldn't apply that edit: {exc}", resume=resume)

    candidate = ResumeBullet(text=new_text, evidence_ids=bullet.evidence_ids, verified=bullet.verified)
    verdict = _verify_bullet(candidate, client, graph)
    if verdict is not None and verdict.verdict == "fail":
        return ChatResult(
            message=f"I can't apply that rewrite — {verdict.reason}",
            resume=resume,
            flagged=True,
            op=op,
        )
    new_resume = _apply_rewrite(resume, op, new_text)
    return ChatResult(message=op.reply or _summarize(op), resume=new_resume, applied=True, op=op)


def _handle_add_claim(
    resume: Resume,
    op: ChatEditOp,
    client: LLMClient,
    graph: EvidenceGraph | None,
    store,
    embedder,
    config: MatchConfig,
) -> ChatResult:
    claim = (op.text or "").strip()
    if not claim:
        return ChatResult(message="What claim would you like to add?", resume=resume)
    if store is None or embedder is None:
        return ChatResult(
            message="I can't verify new claims without an evidence store, so nothing was added.",
            resume=resume,
            flagged=True,
            op=op,
        )

    if graph is None:
        return ChatResult(
            message="I can't verify new claims without the evidence graph, so nothing was added.",
            resume=resume,
            flagged=True,
            op=op,
        )

    # Efficiency rule #2: embed ONLY the new claim, never the corpus.
    rows = store.query(embedder.embed([claim]), n_results=config.n_results)
    hit = rows[0][0] if rows and rows[0] else None
    if hit is None or hit["distance"] >= config.match_threshold:
        return ChatResult(
            message=(
                f'I couldn\'t find evidence for "{claim[:80]}" in your sources — '
                "nothing was added. Can you point me to where you did this?"
            ),
            resume=resume,
            flagged=True,
            op=op,
        )

    eid = str((hit.get("metadata") or {}).get("source_id") or hit["chunk_id"])
    candidate = ResumeBullet(text=claim, evidence_ids=[eid], verified=False)
    verdict = _verify_bullet(candidate, client, graph)
    if verdict is not None and verdict.verdict == "fail":
        return ChatResult(
            message=f"I found related evidence, but it doesn't support that claim — {verdict.reason} Nothing was added.",
            resume=resume,
            flagged=True,
            op=op,
        )
    accepted = ResumeBullet(text=claim, evidence_ids=[eid], verified=True)
    new_resume = _apply_add(resume, op, accepted)
    return ChatResult(message=op.reply or _summarize(op), resume=new_resume, applied=True, op=op)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def apply_chat(
    resume: Resume,
    message: str,
    *,
    client: LLMClient | None = None,
    graph: EvidenceGraph | None = None,
    store=None,
    embedder=None,
    match_config: MatchConfig | None = None,
) -> ChatResult:
    """Route one chat message to a typed edit and apply it deterministically.

    add_claim needs store + embedder + graph: the claim is embedded once, looked
    up in Chroma, and only accepted when the verifier confirms it against the
    graph. Without a graph it is refused (nothing can be verified).
    """
    client = client or get_client()
    config = match_config or MatchConfig()

    try:
        op = _classify(resume, message, client)
    except LLMError as exc:
        return ChatResult(message=f"Sorry, I couldn't understand that: {exc}", resume=resume)

    if op.intent == "none":
        return ChatResult(message=op.reply or "Got it — anything else you'd like to change?", resume=resume, op=op)

    if op.intent == "add_claim":
        return _handle_add_claim(resume, op, client, graph, store, embedder, config)

    if op.intent in ("rewrite_bullet", "tone_change"):
        return _handle_rewrite(resume, op, client, graph)

    # remove_bullet / reorder_section: fully deterministic after classification.
    try:
        if op.intent == "remove_bullet":
            new_resume = _apply_remove(resume, op)
        elif op.intent == "reorder_section":
            new_resume = _apply_reorder(resume, op)
        else:  # pragma: no cover — ChatIntent is closed
            raise ChatEditError(f"Unhandled intent {op.intent}")
    except ChatEditError as exc:
        return ChatResult(message=str(exc), resume=resume, op=op)
    return ChatResult(message=op.reply or _summarize(op), resume=new_resume, applied=True, op=op)


class ChatSession:
    """Holds the working resume + edit context; send() applies edits via
    apply_chat and logs every applied op for undo. Re-rendering the PDF is the
    caller's job, on applied sends only (efficiency rule #5)."""

    def __init__(
        self,
        resume: Resume,
        *,
        client: LLMClient | None = None,
        graph: EvidenceGraph | None = None,
        store=None,
        embedder=None,
        match_config: MatchConfig | None = None,
    ) -> None:
        self.resume = resume
        self.client = client or get_client()
        self.graph = graph
        self.store = store
        self.embedder = embedder
        self.match_config = match_config
        self.log: list[PatchEntry] = []
        self.history: list[tuple[str, ChatResult]] = []

    def send(self, message: str) -> ChatResult:
        result = apply_chat(
            self.resume,
            message,
            client=self.client,
            graph=self.graph,
            store=self.store,
            embedder=self.embedder,
            match_config=self.match_config,
        )
        if result.applied and result.op is not None:
            self.log.append(PatchEntry(op=result.op, before=self.resume, summary=_summarize(result.op)))
            self.resume = result.resume
        self.history.append((message, result))
        return result

    def undo(self) -> ChatResult | None:
        """Revert the last applied edit (patch log snapshot restore)."""
        if not self.log:
            return None
        entry = self.log.pop()
        self.resume = entry.before
        result = ChatResult(message=f"Undid: {entry.summary}", resume=self.resume, applied=True)
        self.history.append(("<undo>", result))
        return result
