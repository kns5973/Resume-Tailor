# Resume Tailor

An agentic **RAG** system that builds a **LaTeX resume tailored to a job description**, grounded entirely in **verifiable evidence** — your GitHub, an old resume, certs, or freeform notes. Every claim is *retrieved* from your sources, *generated* with citations, then *verified* line-by-line by an LLM; anything that can't be proven is **dropped, never fabricated**. Then you refine the result in plain English with an interactive chat agent (with undo).

## Why it exists

Resume builders that "just write" content hallucinate. Resume Tailor takes a different stance:

- **Nothing enters the resume unless evidence exists for it.** Claims are cited back to specific evidence (repo files, commits, parsed resume lines, certs).
- **A verifier checks every bullet** — "does the cited evidence support this claim?" — and unverifiable bullets are dropped with the reason shown.
- **Hybrid retrieval** matches JD requirements to evidence fast: exact tech-name keywords land immediately (skipping LLM calls), and semantic search + query reformulation handle the fuzzy stuff.
- **Honest gaps**: if a requirement has no evidence anywhere, it's reported as a gap — never padded with a fake bullet.

## How RAG works here

This is a retrieval-augmented generation system end to end: the LLM never writes from memory — every generation step is *augmented* with evidence retrieved from your own sources.

```
                         ┌────────────────────────── RETRIEVAL ──────────────────────────┐
                         │                                                              │
  EVIDENCE CORPUS        │    JD REQUIREMENTS                                           │
  ────────────────       │    ────────────────                                          │
  GitHub / old resume /  │      "Redis and message queues"                              │
  certs / brain dump     │          │                                                   │
      │                  │          ▼                                                   │
  chunk_text()  ───────► │   hybrid retrieval ──────────────────────┐                   │
      │                  │   • lexical: exact keyword scan over     │                   │
  embed ONCE (all-       │     the corpus (microseconds, no LLM)    │                   │
  MiniLM-L6-v2)          │   • semantic: batched Chroma cosine      │                   │
      ▼                  │     query (5 hits / requirement)         │                   │
  ┌─────────────┐        │   • pool + dedupe, rank by distance      │                   │
  │ Chroma (vec)│        │   • low confidence? LLM reformulates     │                   │
  │ + Evidence  │        │     the query (≤2 retries)               │                   │
  │ Graph JSON  │        │   • nothing lands → HONEST GAP           │                   │
  └─────────────┘        │          │                               │                   │
                         └──────────┼───────────────────────────────┘                   │
                                    ▼                                                  │
                         ┌────────────────────────── AUGMENT ──────────────────────────┐
                         │  retrieved snippets + evidence_ids + previous resume        │
                         │  (format reference / fallback) fed into the builder prompt  │
                         └──────────────────────────┬──────────────────────────────────┘
                                                    ▼
                         ┌────────────────────────── GENERATE ─────────────────────────┐
                         │  strong-tier LLM writes XYZ bullets, each citing the        │
                         │  evidence_ids it used (every claim traceable)               │
                         └──────────────────────────┬──────────────────────────────────┘
                                                    ▼
                         ┌────────────────────────── VERIFY ───────────────────────────┐
                         │  fast-tier LLM checks claim ⊆ cited evidence;               │
                         │  deterministic fails first (no evidence / unknown ids);     │
                         │  bounce loop ≤1 revision; unverifiable bullets dropped      │
                         └──────────────────────────┬──────────────────────────────────┘
                                                    ▼
                                            RENDER → PDF (.pdf + .tex)
```

The three RAG stages mapped to the code:

| Stage | What runs | Files |
|---|---|---|
| **Index** | chunk → embed once → store | `collector/chunking.py`, `collector/embed.py`, `collector/vector_store.py`, `collector/lexical.py` |
| **Retrieve** | hybrid lexical + semantic, reformulate-retry ≤2, honest gaps | `matcher.py`, `collector/lexical.py` |
| **Augment + Generate** | snippets + evidence_ids + previous resume in the prompt; cited bullets out | `builder.py` |
| **Verify** | claim ⊆ evidence; drop what can't be proven | `verifier.py` |

Key retrieval details:

- **Hybrid, not just vectors.** JD requirements are full of exact tech names (Redis, Docker, C++). A word-boundary keyword scan over the corpus catches them in microseconds and pools the hits at a synthetic distance of `0.20` — *below* the match threshold, so they count as matched evidence without an LLM call. This is why exact-tech requirements never need reformulation.
- **One batched query.** All JD requirements are embedded in one call and queried in a single Chroma batch (efficiency rule: embed once, batch everything).
- **Reformulate-and-retry.** Semantic hits above the match threshold (`0.62`) but below `0.75` get the strong tier to suggest up to 2 alternative queries; the results are pooled and re-ranked. Above `0.75` it's hopeless — the system saves tokens and marks the requirement an honest gap.
- **Provenance everywhere.** Every hit carries `source_id` + `source_type`, and every bullet keeps its `evidence_ids`, so the **Trace tab** shows the full retrieval trace and the **Verifier** can re-check claims against the graph.
- **Previous resume = more evidence.** Upload an old resume and it's parsed into sections (`resume:old#career-objective`, …), ingested into Chroma + the evidence graph, and handed to the builder as a *format reference + fallback content* for sections with no matched evidence (e.g. a career objective) — still cited and still verifiable.

## Features

- **Evidence Graph** — a first-class schema tying every source artifact to chunks and citations.
- **Agentic RAG matcher** — batched hybrid retrieval (lexical boost + semantic) + reformulate-and-retry (≤2), with the full query trace exposed in the UI.
- **Verified resume JSON** — the single source of truth; LaTeX is always regenerated from it.
- **Chat refinement with typed edit ops** — rewrite / add claim / reorder / remove / tone-change intents applied *deterministically* (the model never mutates the resume JSON directly), each applied edit undoable.
- **Refusal by design** — text edits are re-verified; an overclaiming rewrite is refused. `add_claim` with no supporting evidence is flagged, never inserted.
- **One-click retailoring** — candidate profile (name/email/GitHub) remembered across runs, every session stores its raw JD, and **↻ Re-tailor** buttons + a "reuse a previous job" picker pre-fill the run modal for a new role in one click.
- **Session records + review packets** — every run is persisted (`data/sessions/*.json`) with search/filters, progress/confidence tracking, and a downloadable **review packet** (tailored changes incl. education/career, supporting evidence, weak areas, key questions, next actions).
- **Professional web app** — FastAPI + a no-build responsive SPA: split PDF preview (pdf.js canvas) + chat, trace tabs, undo, session-scoped PDF/TEX export, and an **evidence-based drafting toggle** (full verification vs. quick draft).
- **Offline / mock mode** — no API key? The pipeline runs on recorded fixtures and a deterministic mock LLM.
- **Model tiers** — cheap fast-tier models for parsing/verification/intent, strong-tier for rewrites/bullets; provider: Groq.

## Architecture

```
INPUT: JD (text|URL) · GitHub profile · old resume / certs / notes
   │
   ▼
1. COLLECTOR AGENT           [PARALLEL — asyncio.gather]         ← RAG: INDEX
   github_reader ‖ resume_parser ‖ jd_url_scraper ‖ ocr_certs ‖ brain_dump
   → chunk + embed ONCE → Chroma (vector store) + Evidence Graph JSON
   │
   ▼
2. JD PARSER (fast tier)     → requirement list with priority + keywords
   │
   ▼
3. MATCHER AGENT             hybrid retrieval: keyword hits (0.200, no LLM call)   ← RAG: RETRIEVE
                             + batched semantic query → reformulate-retry ≤2
                             → honest gaps (no fabricated evidence, ever)
   │
   ▼
4. RESUME BUILDER (strong tier)  → XYZ bullets, each citing evidence_ids            ← RAG: AUGMENT+GENERATE
   │
   ▼
5. VERIFIER (fast tier)      claim ⊆ cited evidence?  deterministic fails first   ← RAG: VERIFY
   │                         (no evidence / unknown ids), then ONE batched LLM
   │                         call; bounce loop ≤1 revision; drop the rest
   ▼
6. RENDER AGENT              Resume JSON → Jinja2 → Jake's-Resume .tex → pdflatex → PDF
   │
   ▼
7. CHAT REFINEMENT AGENT     fast intent classify → typed edit ops (rewrite_bullet |
                             add_claim | reorder_section | remove_bullet | tone_change)
                             → deterministic apply → patch log → undo
   │
   ▼
EXPORT:  .pdf + .tex   (web: /api/pdf, /api/tex — per-session, user-initiated)
```

**Key invariant:** the model never writes the resume JSON directly. It proposes *edit operations* against a generated `document_map`; Python applies them with full validation. Everything the model didn't touch stays byte-identical, and every applied edit lands in the patch log for undo.

## Quickstart

### Prerequisites

- Python 3.12+
- `pdflatex` on your PATH (renders the PDF locally)
- (optional) a [Groq](https://console.groq.com) API key for live LLM calls — without one, the pipeline runs in deterministic mock mode

### 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[web]"        # app + web extras
```

### 2. Configure

```bash
cp .env.example .env                     # then add your key:
# GROQ_API_KEY=gsk_...
```

| Env var | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | Live LLM calls (else `MockLLMClient`) |
| `GROQ_MODEL_FAST` | `llama-3.1-8b-instant` | Parsing, verification, intent |
| `GROQ_MODEL_STRONG` | `llama-3.3-70b-versatile` | Bullet generation, rewrites, reformulation |
| `RESUME_TAILOR_DRY_RUN` | unset | `1` = force mock mode (no network) |
| `RESUME_TAILOR_PORT` | `8000` | Web app port |

### 3. Seed an evidence corpus (one time) — the RAG index

```bash
.venv/bin/python - <<'EOF'
from resume_tailor.collector import CollectorInput, collect_sync
from resume_tailor.collector.github_reader import RepoFilters

collect_sync(
    CollectorInput(github_username="YOUR_GITHUB_USERNAME"),
    cache_dir="data",
    repo_filters=RepoFilters(max_repos=20),
)
EOF
```

This fetches repos → readmes/code files/commits, **chunks** them, **embeds** them once (`all-MiniLM-L6-v2`, ~2 GB torch stack), and writes the retrieval index: `data/chroma/` (vector store) + `data/evidence_graph.json` (provenance). **Use your own GitHub profile** — that's what makes verified bullets actually survive (the pipeline only certifies claims it can prove from *your* evidence). You can also upload a previous resume in the UI (no corpus needed) and it's ingested into the same index.

### 4. Run the web app

```bash
./run.sh            # starts the server + opens your browser
./run.sh --port 9000
```

Open **http://127.0.0.1:8177/** (or your chosen port). Paste a job description (or hit **Use sample JD**), toggle **Evidence-based drafting** on/off, build, read the **Trace** tab (the RAG retrieval trace), chat, undo, and export PDF/TEX when you're ready. For a new role: hit **↻ Re-tailor** on any past session — your profile and the job description carry over.

### 5. CLI demos

```bash
.venv/bin/python scripts/demo_build.py     # full pipeline: JD → verified resume + PDF (real Groq or mocks)
.venv/bin/python scripts/demo_chat.py      # chat refinement: typed edits, undo, honest refusal
.venv/bin/python scripts/demo_lexical.py   # hybrid-retrieval before/after on the corpus
.venv/bin/python scripts/demo_packet.py    # generates a sample review packet
```

### 6. Tests

```bash
.venv/bin/pytest -q
# 180 tests: schemas, collector, matcher (incl. hybrid RAG), builder/verifier bounce loop,
# chat edit ops + refusals, render → pdflatex gate, section-aware resume parsing, and the
# FastAPI web layer (sessions, packets, retailoring, multipart upload).
```

## The three demo moments

1. **Matcher's live query reformulation** — a requirement like *"distributed systems"* misses on the first pass, gets reformulated (`microservices` → `message queue`), and lands real evidence — the full RAG retrieval trace is visible in the Trace tab.
2. **Chat agent refusing an unverified claim** — *"Add a claim: I led a team of 10 engineers"* → flagged, nothing inserted, asked for a source. An overclaiming *rewrite* is refused with the verifier's reason and left unapplied.
3. **Hidden match discovery** — querying *"Redis task queues"* surfaces a forgotten commit or `queue.py` file with clickable citations — evidence you forgot you had.

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | Hand-rolled (collector parallelizes via `asyncio.gather`) |
| Extraction | PyGithub, pdfplumber (incl. section-aware resume parsing), trafilatura, pytesseract (guarded) |
| RAG — index | chunk_text (line-based, ~500 chars + overlap), sentence-transformers `all-MiniLM-L6-v2`, ChromaDB (cosine) |
| RAG — retrieve | hybrid: LexicalIndex keyword scan (no deps) + Chroma semantic batch + LLM reformulate-retry |
| LLM | Groq (OpenAI-compatible endpoint, plain `requests`), env-configured tiers, mock mode |
| Guardrails | Pydantic v2 schemas everywhere |
| Rendering | Jinja2 → LaTeX (Jake's Resume template) → local `pdflatex` |
| Frontend | FastAPI + no-build responsive SPA (vanilla HTML/CSS/JS, pdf.js preview) |

## Project layout

```
resume_tailor/
├── config.py            # model tiering + dry-run flag
├── jd_parser.py         # JD → requirements (fast tier)
├── matcher.py           # hybrid RAG matching, reformulate-retry ≤2, honest gaps
├── builder.py           # XYZ bullets from retrieved evidence (strong tier) — RAG augment/generate
├── verifier.py          # claim ⊆ evidence, deterministic + LLM, fail-closed — RAG verify
├── pipeline.py          # build_verified_resume bounce loop + run_full()
├── chat.py              # typed edit ops, ChatSession, undo, honest refusals
├── sessions.py          # persisted session records, filters, review packets
├── web.py               # FastAPI app: /api/run|chat|undo|state|pdf|tex|sessions + SPA
├── static/              # no-build SPA (index.html, app.css, app.js)
├── collector/           # github_reader, resume_parser, jd_scraper, ocr_certs,
│                        #   chunking, embed, lexical, vector_store
├── llm/                 # LLMClient / GroqClient / MockLLMClient
├── render/              # Resume JSON → Jinja2 → LaTeX → PDF
└── schemas/             # locked pydantic contracts (resume, evidence, jd, match, build, chat)
scripts/                 # demo_build / demo_chat / demo_lexical / demo_packet / demo_match / demo_render
tests/                   # 180 tests incl. offline fixtures
run.sh                   # one-command launcher
requirements.txt         # PaaS-friendly dependency list (mirrors pyproject.toml extras)
```

## Notes

- **`data/`** (corpus + Chroma + sessions), **`out/`** (rendered artifacts), and **`.env`** (secrets) are gitignored — nothing sensitive gets committed.
- With a **foreign** corpus (e.g. a demo profile that isn't yours), verification honestly produces few or zero surviving bullets — that's the anti-hallucination design working. Point it at your own evidence to see it shine.
- The web app's PDF pane renders with pdf.js and falls back to explicit Open/Download links in webviews without PDF support; export is always user-initiated.
