# Resume Tailor

An agentic system that builds a **LaTeX resume tailored to a job description**, grounded entirely in **verifiable evidence** — your GitHub, an old resume, certs, or freeform notes. Every claim is matched against your sources, verified line-by-line by an LLM, and anything that can't be proven is **dropped, never fabricated**. Then you refine the result in plain English with an interactive chat agent (with undo).

## Why it exists

Resume builders that "just write" content hallucinate. Resume Tailor takes a different stance:

- **Nothing enters the resume unless evidence exists for it.** Claims are cited back to specific evidence (repo files, commits, parsed resume lines, certs).
- **A verifier checks every bullet** — "does the cited evidence support this claim?" — and unverifiable bullets are dropped with the reason shown.
- **Hybrid retrieval** matches JD requirements to evidence fast: exact tech-name keywords land immediately (skipping LLM calls), and semantic search + query reformulation handle the fuzzy stuff.
- **Honest gaps**: if a requirement has no evidence anywhere, it's reported as a gap — never padded with a fake bullet.

## Features

- **Evidence Graph** — a first-class schema tying every source artifact to chunks and citations.
- **Agentic matcher** — batched semantic retrieval + keyword boost + reformulate-and-retry (≤2), with the full query trace exposed.
- **Verified resume JSON** — the single source of truth; LaTeX is always regenerated from it.
- **Chat refinement with typed edit ops** — rewrite / add claim / reorder / remove / tone-change intents applied *deterministically* (the model never mutates the resume JSON directly), each applied edit undoable.
- **Refusal by design** — text edits are re-verified; an overclaiming rewrite is refused. `add_claim` with no supporting evidence is flagged, never inserted.
- **Professional web app** — FastAPI + a no-build responsive SPA: split PDF preview (pdf.js canvas) + chat, trace tabs, undo, session-scoped PDF/TEX export, and an **evidence-based drafting toggle** (full verification vs. quick draft).
- **Offline / mock mode** — no API key? The pipeline runs on recorded fixtures and a deterministic mock LLM.
- **Model tiers** — cheap fast-tier models for parsing/verification/intent, strong-tier for rewrites/bullets; provider: Groq.

## Architecture

```
INPUT: JD (text|URL) · GitHub profile · old resume / certs / notes
   │
   ▼
1. COLLECTOR AGENT           [PARALLEL — asyncio.gather]
   github_reader ‖ resume_parser ‖ jd_url_scraper ‖ ocr_certs ‖ brain_dump
   → chunk + embed ONCE → Chroma (vector store) + Evidence Graph JSON
   │
   ▼
2. JD PARSER (fast tier)     → requirement list with priority + keywords
   │
   ▼
3. MATCHER AGENT             hybrid retrieval: keyword hits (0.200, no LLM call)
                             + batched semantic query → reformulate-retry ≤2
                             → honest gaps (no fabricated evidence, ever)
   │
   ▼
4. RESUME BUILDER (strong tier)  → XYZ bullets, each citing evidence_ids
   │
   ▼
5. VERIFIER (fast tier)      claim ⊆ cited evidence?  deterministic fails first
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

### 3. Seed an evidence corpus (one time)

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

This fetches repos → readmes/code files/commits, chunks them, embeds them (`all-MiniLM-L6-v2`), and writes `data/chroma/` + `data/evidence_graph.json`. **Use your own GitHub profile** — that's what makes verified bullets actually survive (the pipeline only certifies claims it can prove from *your* evidence).

### 4. Run the web app

```bash
./run.sh            # starts the server + opens your browser
./run.sh --port 9000
```

Open **http://127.0.0.1:8177/** (or your chosen port). Paste a job description (or hit **Use sample JD**), toggle **Evidence-based drafting** on/off, build, read the **Trace** tab, chat, undo, and export PDF/TEX when you're ready.

### 5. CLI demos

```bash
.venv/bin/python scripts/demo_build.py     # full pipeline: JD → verified resume + PDF (real Groq or mocks)
.venv/bin/python scripts/demo_chat.py      # chat refinement: typed edits, undo, honest refusal
.venv/bin/python scripts/demo_lexical.py   # hybrid-retrieval before/after on the corpus
```

### 6. Tests

```bash
.venv/bin/pytest -q
# 151 tests: schemas, collector, matcher (incl. hybrid), builder/verifier bounce loop,
# chat edit ops + refusals, render → pdflatex gate, and the FastAPI web layer.
```

## The three demo moments

1. **Matcher's live query reformulation** — a requirement like *"distributed systems"* misses on the first pass, gets reformulated (`microservices` → `message queue`), and lands real evidence — the full trace is visible in the Trace tab.
2. **Chat agent refusing an unverified claim** — *"Add a claim: I led a team of 10 engineers"* → flagged, nothing inserted, asked for a source. An overclaiming *rewrite* is refused with the verifier's reason and left unapplied.
3. **Hidden match discovery** — querying *"Redis task queues"* surfaces a forgotten commit or `queue.py` file with clickable citations — evidence you forgot you had.

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | Hand-rolled (collector parallelizes via `asyncio.gather`) |
| Extraction | PyGithub, pdfplumber, trafilatura, pytesseract (guarded) |
| Retrieval | ChromaDB + sentence-transformers `all-MiniLM-L6-v2` + keyword index (no deps) |
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
├── builder.py           # XYZ bullets from matched evidence (strong tier)
├── verifier.py          # claim ⊆ evidence, deterministic + LLM, fail-closed
├── pipeline.py          # build_verified_resume bounce loop + run_full()
├── chat.py              # typed edit ops, ChatSession, undo, honest refusals
├── web.py               # FastAPI app: /api/run|chat|undo|state|pdf|tex + SPA
├── static/              # no-build SPA (index.html, app.css, app.js)
├── collector/           # github_reader, resume_parser, jd_scraper, ocr_certs,
│                        #   chunking, embed, lexical, vector_store
├── llm/                 # LLMClient / GroqClient / MockLLMClient
├── render/              # Resume JSON → Jinja2 → LaTeX → PDF
└── schemas/             # locked pydantic contracts (resume, evidence, jd, match, build, chat)
scripts/                 # demo_build / demo_chat / demo_lexical / demo_match / demo_render
tests/                   # 151 tests incl. offline fixtures
run.sh                   # one-command launcher
```

## Notes

- **`data/`** (corpus + Chroma), **`out/`** (rendered artifacts), and **`.env`** (secrets) are gitignored — nothing sensitive gets committed.
- With a **foreign** corpus (e.g. a demo profile that isn't yours), verification honestly produces few or zero surviving bullets — that's the anti-hallucination design working. Point it at your own evidence to see it shine.
- The web app's PDF pane renders with pdf.js and falls back to explicit Open/Download links in webviews without PDF support; export is always user-initiated.
