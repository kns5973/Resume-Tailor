# Verified-Proof Resume Tailor — Implementation Guide

Agentic AI system that builds a LaTeX resume tailored to a job description, grounded entirely in verifiable evidence (GitHub, certs, old resume, freeform notes) — with retrieval-based matching, claim verification, and an interactive chat refinement loop.

---

## 1. Locked Design Decisions

These are decided — don't relitigate mid-build:

- **One LaTeX template only**: Jake's Resume style (single-column, ATS-friendly, no exotic fonts). Compiled locally via `pdflatex` — no network dependency, no template-switching logic.
- **Resume JSON is the single source of truth.** No agent, including the chat agent, ever edits `.tex` directly. All edits mutate the JSON; `.tex` is always regenerated from it.
- **Certs via file upload** (PDF/PNG/JPG drag-and-drop) — not Google Drive OAuth. Simpler, no auth flow.
- **JD input accepts text or a URL** (scraped server-side via `trafilatura`).
- **Vector store: ChromaDB**, in-memory/local file — no external DB service.
- **Demo GitHub profile: a real, rich public profile** (e.g. `sindresorhus`) rather than the user's own sparse one — used transparently, narrated as "running against a public profile to show extraction depth."
- **Model tiering** for cost/speed — see §5.

---

## 2. Input Layer

| Input | Required? | Ingestion method |
|---|---|---|
| Job description | Yes (text or URL) | Pasted text, or scraped via `trafilatura` if URL |
| GitHub profile URL | Optional | GitHub REST API (PyGithub) |
| Certification files | Optional | Multi-file upload (PDF/PNG/JPG) |
| Old resume / LinkedIn PDF export | Recommended | PDF upload |
| Freeform brain-dump text | Optional fallback | Text box |

**Why not scrape GitHub itself:** the REST API already returns structured JSON (file trees, manifests, commit messages, READMEs) at 5,000 req/hr authenticated — strictly better than HTML scraping for this use case. Scraping is reserved for the JD-URL input only, where no structured API exists.

---

## 3. Full Architecture

```
INPUT: JD (text or URL) · GitHub URL · Certs (upload) · Old resume/brain-dump
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│ 1. COLLECTOR AGENT  [PARALLEL: asyncio.gather]            │
│    github_reader() ‖ ocr_certs() ‖ resume_parser() ‖      │
│    jd_url_scraper() (trafilatura, if URL given)           │
│    → chunk + embed ONCE → Chroma + Evidence Graph JSON    │
└───────────────────────────┬───────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────┐
│ 2. JD PARSER  [Haiku]  → requirement list                 │
│    [{requirement, priority, keywords}]                    │
└───────────────────────────┬───────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────┐
│ 3. MATCHER AGENT  [agentic RAG, batched Chroma query]     │
│    - embed all requirements at once, single batch query   │
│    - low-confidence hits → reformulate query → retry (≤2) │
│    - still nothing → mark as real gap (do not hallucinate)│
│    [Sonnet for reformulation reasoning]                   │
└───────────────────────────┬───────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────┐
│ 4. RESUME JSON BUILDER AGENT  [Sonnet]                    │
│    XYZ-format bullets, each tagged with evidence_ids[]    │
└───────────────────────────┬───────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────┐
│ 5. VERIFIER AGENT  [Haiku, ≤1 retry]                       │
│    claim ⊆ cited evidence? pass / bounce to Builder        │
└───────────────────────────┬───────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────┐
│ 6. RENDER AGENT                                            │
│    Resume JSON → Jinja2 → Jake's-Resume .tex → pdflatex    │
└───────────────────────────┬───────────────────────────────┘
                             ▼
                   [ PDF PREVIEW shown to user ]
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│ 7. CHAT REFINEMENT AGENT  [Haiku intent-classify →         │
│    Sonnet for rewrites]                                    │
│    intents: rewrite_bullet | add_claim | reorder_section |  │
│             remove_bullet | tone_change                     │
│    add_claim → embed NEW claim only → query Chroma →        │
│      evidence found: attach + accept                        │
│      not found: flag "unverified", ask user for a source    │
│    → mutate Resume JSON → re-render (step 6) on send        │
└───────────────────────────┬───────────────────────────────┘
                             ▼
                  FINAL EXPORT: .pdf + .tex
```

---

## 4. Core Schemas (lock these first)

```python
from pydantic import BaseModel
from typing import Literal

class Evidence(BaseModel):
    source_id: str          # e.g. "repo:backend-api#file:queue.py#L45"
    source_type: Literal["code", "commit", "cert", "readme", "resume", "brain_dump"]
    skill_tags: list[str]   # e.g. ["redis", "bullmq", "async"]
    snippet: str            # the actual proof text/code
    confidence: float
    url: str                # deep link for the citation

class ResumeBullet(BaseModel):
    text: str
    evidence_ids: list[str] # must be non-empty & schema-validated
    verified: bool

class ResumeSection(BaseModel):
    title: str
    bullets: list[ResumeBullet]

class Resume(BaseModel):
    name: str
    contact: dict
    sections: list[ResumeSection]
```

`evidence_ids` must never be empty for a generated bullet — this is what makes the zero-hallucination claim structurally enforced rather than just asserted.

---

## 5. Model Tiering (speed & cost)

| Task | Model | Reason |
|---|---|---|
| JD parsing | Haiku | Simple structured extraction |
| Chat intent classification | Haiku | Simple routing task |
| Verifier yes/no check | Haiku | Simple binary check |
| OCR text cleanup | Haiku | Simple extraction |
| Query reformulation (Matcher) | Sonnet | Needs real reasoning |
| Bullet generation (Builder) | Sonnet | Needs quality writing |
| Chat rewrites | Sonnet | Needs quality writing |

---

## 6. Efficiency Rules

1. **Parallelize the Collector.** GitHub fetch, OCR, resume parsing, JD scraping are independent — run concurrently via `asyncio.gather`, not sequentially.
2. **Embed once, cache always.** Evidence chunks are embedded exactly once at ingestion. Chat's `add_claim` only embeds the new claim text, never re-embeds the corpus.
3. **Batch the Matcher.** Embed all JD requirements in one call and issue a single batched Chroma query; only reformulate-retry the specific low-confidence hits, not everything.
4. **Cap the Verifier retry at 1.** A second retry rarely changes the outcome and just burns time/tokens.
5. **Recompile LaTeX only on chat "send"**, not per keystroke. Local `pdflatex` compiles in ~1–2s, so no caching layer needed there.

---

## 7. Build Order

| Phase | Hours | Deliverable |
|---|---|---|
| 0. Setup | 0–2 | Schemas locked; Jake's Resume template converted to Jinja2; dummy `pdflatex` compile working |
| 1. Collector Agent | 2–7 | Parallel fetch (GitHub/OCR/resume/JD-URL) → chunked, embedded → Chroma + Evidence Graph JSON |
| 2. JD Parser + Matcher | 7–13 | Batched RAG matching with reformulate-retry loop; matches + real gaps as JSON |
| 3. Resume Builder + Verifier | 13–18 | Verified Resume JSON with evidence-tagged bullets |
| 4. Render Agent | 18–22 | Working JSON → LaTeX → PDF pipeline |
| 5. Frontend (preview + chat) | 22–28 | Split view: PDF preview + Chat Refinement Agent wired to Chroma |
| 6. Export + demo prep | 28–33 | `.pdf`/`.tex` export button; seed/select demo GitHub profile; polish |
| Buffer | 33–36 | Bug fixes; rehearse demo moments (below) |

---

## 8. Demo Moments to Rehearse

1. **Matcher's live query reformulation** — show the trace panel when a JD term ("distributed systems") misses on first retrieval, gets reformulated ("microservices," "message queue"), and succeeds.
2. **Chat agent refusing an unverified claim** — user asks to add "led a team of 10," system finds no evidence, responds in chat and flags it instead of silently inserting it.
3. **Hidden match discovery** — a skill the user forgot they had (e.g., a Redis import from months ago) surfacing automatically as a matched bullet with a clickable citation.

---

## 9. Suggested Tech Stack Summary

- **Orchestration:** LangGraph or CrewAI
- **Data extraction:** PyGithub, `pdfplumber`, Tesseract OCR, `trafilatura` (JD URL scraping)
- **Retrieval:** ChromaDB + `sentence-transformers` (`all-MiniLM-L6-v2`)
- **Schema/guardrails:** Pydantic
- **Rendering:** Jinja2 → LaTeX (Jake's Resume template) → local `pdflatex`
- **Frontend:** Streamlit (fastest for hackathon) or Next.js if team is React-fast
- **Models:** Haiku (structured/simple tasks), Sonnet (reasoning/writing tasks)
