# Resume Tailor — Project Doc

> Living document. Kept in sync with `context.md` (session working memory). Source of truth for architecture, decisions, schemas, and build progress.

## Overview

Agentic AI system that builds a **LaTeX resume tailored to a job description**, grounded entirely in **verifiable evidence** (GitHub, certs, old resume, freeform notes) — with retrieval-based matching, claim verification, and an interactive chat refinement loop.

Pipeline: **Inputs → Collector → JD Parser → Matcher → Resume Builder → Verifier → Render → PDF preview → Chat refinement → Export (.pdf + .tex)**

## Locked Design Decisions

- [x] One LaTeX template only (Jake's Resume, single-column, ATS-friendly). Compiled locally via `pdflatex`. No network dependency for rendering.
- [x] **Resume JSON is the single source of truth.** No agent ever edits `.tex` directly; `.tex` is always regenerated from JSON.
- [x] Certs via file upload (PDF/PNG/JPG drag-and-drop) — no OAuth.
- [x] JD input accepts text or URL (scraped server-side via `trafilatura`).
- [x] Vector store: ChromaDB, in-memory/local file.
- [x] Demo GitHub profile: rich public profile (e.g. `sindresorhus`), used transparently.
- [x] Model tiering for cost/speed (§ Model Tiering).

## Phase-0/1 Schema & Design Decisions

- **Phase 0:** `ResumeEntry` + `EvidenceGraph` schema extensions; `verified` bullets must cite evidence; template preamble wrapped in `{% raw %}`.
- **Phase 1:** pre-filtering is heuristic (stars/recency/file-type), language relevance happens at MATCH time (avoids JD-Parser chicken-and-egg); GitHub snapshot cache (`data/github_{user}.json`) = offline/fixture mode; torch installed CPU-only.

## File Layout

```
resume_tailor/
├── __init__.py
├── config.py              # model tiering (guide §5) + DRY_RUN flag
├── sample_data.py         # Jake-Ryan sample resume (tests + demo)├── schemas/               # LOCKED — pipeline source of truth
│   ├── evidence.py         #   Evidence, EvidenceChunk, EvidenceGraph
│   ├── resume.py           #   Resume, ResumeSection, ResumeEntry, ResumeBullet
│   ├── jd.py               #   JDRequirement, ParsedJD
│   ├── match.py            #   EvidenceHit, RequirementMatch, MatchResult
│   ├── build.py            #   ResumeHeader, BulletVerdict, Verification, DroppedBullet, BuildResult
│   └── chat.py             #   ChatEditOp, PatchEntry, ChatResult (typed edit ops)
├── llm/                   # ✅ Phase 2 — client abstraction (rec #2)
│   └── __init__.py        #   LLMClient, GroqClient, MockLLMClient, get_client
├── jd_parser.py           # ✅ Phase 2 — parse_jd() (fast tier)
├── matcher.py             # ✅ Phase 2 — match_jd() agentic RAG (batched + reformulate ≤2)
├── builder.py             # ✅ Phase 3 — generate_resume() XYZ bullets, header enforced
├── verifier.py            # ✅ Phase 3 — verify_resume() claim ⊆ evidence (deterministic + LLM)
├── pipeline.py            # ✅ Phase 3 — build_verified_resume() bounce loop + run_full()
├── chat.py                # ✅ Phase 5 — typed edit ops, ChatSession, undo, honest refusals
├── web.py                 # ✅ Phase 5b/6 — FastAPI app: /api/run|chat|undo|state|pdf|tex + SPA serving
├── static/                # ✅ Phase 5b/6 — no-build SPA: index.html, app.css, app.js (split view)
├── collector/             # ✅ Phase 1
│   ├── __init__.py        #   CollectorInput/Result, collect()/collect_sync()
│   ├── base.py            #   SourceArtifact
│   ├── chunking.py        #   chunk_text (line-based, overlap)
│   ├── embed.py           #   Embedder / FakeEmbedder / SentenceTransformerEmbedder
│   ├── lexical.py         #   LexicalIndex — exact-keyword scan (hybrid half, no deps)
│   ├── vector_store.py    #   ChromaDB wrapper (add/upsert/batched query/count/get_all)
│   ├── github_reader.py   #   fetch via Search API + raw + atom feeds (rate-limit-safe)
│   │                      #   + RepoFilters + snapshot cache
│   ├── resume_parser.py   #   pdfplumber, per-page evidence
│   ├── jd_scraper.py      #   trafilatura (URL → plain text)
│   ├── brain_dump.py      #   freeform notes → evidence
│   └── ocr_certs.py       #   pytesseract; PNG/JPG; guarded (no tesseract → warning)
├── render/
│   ├── latex.py           # latex_escape, render_tex, compile_tex, render_resume
│   └── __init__.py
└── templates/
    └── jakes_resume.tex.j2
tests/                     # incl. fixtures/github_snapshot.json (offline profile)
scripts/
├── demo_render.py         # Phase 0: sample Resume JSON → PDF
├── demo_build.py          # Phase 3: full pipeline → verified JSON + PDF (real Groq or mocks)
├── demo_lexical.py        # Phase 4: hybrid retrieval before/after on the corpus
└── demo_chat.py           # Phase 5: chat refinement — typed edits, undo, honest refusal
data/                      # gitignored: GitHub snapshots + evidence_graph.json + Chroma
                           #  → github_sindresorhus.json, evidence_graph.json (1.8MB), chroma/ (2,697 vectors)
out/                       # demo outputs: verified_resume.json + verified_resume.pdf
```

## Pipeline Architecture

```
INPUT: JD (text|URL) · GitHub URL · Certs (upload) · Old resume / brain-dump
   │
   ▼
1. COLLECTOR AGENT  ✅ Phase 1  [PARALLEL: asyncio.gather]
   github_reader() ‖ ocr_certs()* ‖ resume_parser() ‖ jd_url_scraper() (trafilatura)
   → chunk + embed ONCE → Chroma + Evidence Graph JSON
   (* guarded: skipped with warning until tesseract binary installed)
   │
   ▼
2. JD PARSER [Haiku] → ParsedJD (requirement list)                    ✅ Phase 2
   │
   ▼
3. MATCHER AGENT [agentic RAG, batched Chroma query]                ✅ Phase 2
   embed all requirements at once → single batch query (test-verified)
   low-confidence hits → reformulate → retry (≤2)  [Sonnet]
   nothing found → mark as real gap (never hallucinate)
   │
   ▼
4. RESUME JSON BUILDER AGENT [strong tier] — XYZ bullets tagged with evidence_ids[]  ✅ Phase 3
   header enforced from ResumeHeader input (never from the model)
   │
   ▼
5. VERIFIER AGENT [fast tier, ≤1 retry] — claim ⊆ cited evidence? pass / bounce  ✅ Phase 3
   deterministic auto-fail (no evidence / unknown ids) → batched LLM check → fail-closed
   bounce loop: ≤1 revision with verifier feedback, then drop unverifiable bullets
   │
   ▼
6. RENDER AGENT — Resume JSON → Jinja2 → Jake's-Resume .tex → pdflatex → PDF   ✅ Phase 0/4 core
   │
   ▼
7. CHAT REFINEMENT AGENT [fast intent-classify → strong rewrites]           ✅ Phase 5
   intents: rewrite_bullet | add_claim | reorder_section | remove_bullet | tone_change
   typed edit ops → DETERMINISTIC apply → patch log (undo)
   text edits re-verified; add_claim: evidence found → attach, else FLAG (moment #2)
   → re-render on send (efficiency rule #5)│   ▼
FINAL EXPORT: .pdf + .tex via FastAPI /api/pdf + /api/tex + UI buttons          🔶 Phase 6

FRONTEND (Phase 5b/6): FastAPI serves the API AND the no-build SPA from one app.
  POST /api/run → PipelineResult + ChatSession (session_id) → split-view UI
  POST /api/chat → typed edit ops (re-verify; re-render PDF only on applied)
  GET /api/pdf | /api/tex | /api/state → preview, export, resume state
```

## LLM Client & Pipeline Contracts

- **`LLMClient.complete_json(task, system, prompt) -> dict`** — tier from `config.model_for(task)`; model from `GROQ_MODEL_FAST`/`GROQ_MODEL_STRONG` env (defaults `llama-3.1-8b-instant`/`llama-3.3-70b-versatile`); `max_tokens` per task from `config.max_tokens_for` (builder 4096, verifier 2048, parser 1024; `GROQ_MAX_TOKENS_<TASK>` override). `get_client()` → MockLLMClient in dry-run/no-key; GroqClient otherwise. `LLMError` on unusable output.
- **`parse_jd(text) -> ParsedJD`** — fast tier; ≤1 retry on bad JSON/schema.
- **`match_jd(jd, store, embedder, client, config) -> MatchResult`** — `MatchConfig(match_threshold=0.62, low_conf_threshold=0.75, n_results=5, max_reformulations=2, use_keywords=False, lexical_distance=0.2)`; `RequirementMatch.query_trace` = [original, reformulations…] (trace panel); gaps carry no hits, ever.
- **Hybrid retrieval (default OFF)** — with `use_keywords=True`, the JD parser's exact keywords are scanned whole-word over the corpus via `LexicalIndex` (from `VectorStore.get_all()`, one call) and pooled into each requirement's evidence set at `lexical_distance` (validated < match_threshold). Hits carry `retrieval_source="lexical"`; pooling dedupes by chunk_id (semantic wins ties). Benefits seen on the corpus: exact-tech-name requirements match immediately, skipping reformulation LLM calls.
- **`generate_resume(jd, match_result, header, client, feedback) -> Resume`** — strong tier; XYZ bullets; evidence_ids from matched hits; header taken verbatim from `ResumeHeader` (LLM name/contact ignored); ≤1 retry.
- **`verify_resume(resume, client, graph) -> Verification`** — fast tier; deterministic auto-fail for no-evidence/unknown-ids (resolved vs the EvidenceGraph: chunk_id or source_id), then ONE batched LLM call (claim ⊆ evidence); ≤1 retry; LLM outage → fail closed. `iter_bullets()` gives stable flat bullet_ids.
- **`build_verified_resume(jd, match_result, header, client, graph, max_revisions=1) -> BuildResult`** — bounce loop: failed bullets → builder revision with feedback → re-verify; drop unverifiable bullets (cumulative `dropped` ledger). Final `resume` has only `verified=True` bullets.
- **`run_full(jd_text, header, ...) -> PipelineResult`** — offline corpus at `data/` (or live `collector` if empty) → parse → match → build → verify → render; returns PDF path + stats (`requirements/matched/gaps/revisions/dropped_bullets/bullets_verified/corpus_chunks/lexical_matches/reformulations`). **Matching defaults to hybrid** (`MatchConfig(use_keywords=True)`); pass an explicit config to override.
- **`apply_chat(resume, message, client, graph, store, embedder, match_config) -> ChatResult`** — fast-tier intent classify (task `chat_intent`) → `ChatEditOp` (indices from `document_map`) → deterministic apply (`rewrite_bullet | add_claim | reorder_section | remove_bullet | tone_change | none`). Rewrites go through the strong tier (`chat_rewrites`) and are **re-verified**: overclaims are refused with the verifier's reason. `add_claim` embeds only the claim (rule #2) → Chroma → evidence found + verifier pass → added verified; otherwise **flagged, nothing inserted**.
- **`ChatSession`** — holds the working resume + graph/store/embedder; `send()` logs `PatchEntry(before)` for `undo()`; re-render on applied sends is the caller's job (rule #5).

## Chat Guarantees

- **Deterministic edits**: the model only ever produces a target + content; everything else in the Resume JSON is byte-identical, fully validated before apply, and undoable.
- **Zero fabrication in chat**: text edits must survive re-verification; `add_claim` must find + verify evidence or it is flagged (demo moment #2), never silently inserted.
- **GroqClient 429 handling** — bounded rate-limit retry (2 retries, honors `Retry-After`, exponential backoff capped at 15s). The free tier's TPM limits are easy to hit on a full pipeline run; the verifier's fail-closed still holds when retries exhaust.
- **`MockLLMClient`** — queued JSON strings or per-prompt callables; records `calls`; `.register(task, handler)`.

## Builder / Verifier Guarantees

- **Zero fabrication**: the final resume contains only bullets whose claims an LLM confirmed against the cited evidence, each citing ids resolvable in the graph. Empty-evidence or unknown-id bullets fail before any LLM call; LLM outages fail closed (never mark unverified as verified).
- **Header integrity**: `ResumeHeader` (name/contact) is the only identity source — the builder prompt forbids identity, and any model-invented name/contact is discarded.
- **Cost caps**: verifier batched (1 LLM call per draft round), bounce ≤1 revision, deterministic fails never hit the LLM.

## Collector Contracts

- **`CollectorInput`**: `jd_text`, `jd_url`, `github_username`, `resume_pdf_paths`, `cert_paths`, `brain_dump`.
- **`CollectorResult`**: `graph: EvidenceGraph`, `jd_text` (resolved), `warnings`, `stats` (`sources/chunks/embedded_chunks`); `.persist(cache_dir)` writes `evidence_graph.json`.
- **Readers** return `list[SourceArtifact]` (Evidence + pre-chunked `EvidenceChunk`s). Failures become warnings, never abort the run.
- **`RepoFilters`**: `min_stars`, `max_repos`, `max_files_per_repo`, `max_commits`, `updated_within_days`, `extensions`, `max_file_bytes`.
- **Snapshot fetcher**: Search API (star-sorted, separate quota) → raw.githubusercontent.com (READMEs/files) → atom feeds (commits); git trees API is the only core-quota call, capped to selected repos. Avoids the broken `sort=stars` on the plain repos endpoint. `refresh=True` re-fetches.

## Demo Corpus (seeded 2026-08-08, refreshed 2026-08-08)

- Profile: `sindresorhus`, **top 30 repos by stars** (awesome, awesome-nodejs, awesome-electron, type-fest, ky, …).
- **389 evidence sources** (30 readmes, **72 code files**, 287 commits) → **3,734 chunks** embedded (`all-MiniLM-L6-v2`) in `data/chroma`; graph in `data/evidence_graph.json` (0 warnings).
- First pass (unauth, quota-depleted) captured readmes+commits only (3 files). **Refresh with `refresh=True` after the core quota reset** backfilled git-tree file listings across all 30 repos (72 code files).
- A `GITHUB_TOKEN` (5k req/hr) would allow deeper coverage (`max_files_per_repo` up) for the user's own profile fetch.
- **VectorStore.query(query_embeddings, n_results, where)** — batched, one hit-list per embedding; empty store → empty lists.

## Model Tiering (provider: Groq)

Neutral tiers — no Anthropic Haiku/Sonnet dependency. Task → tier in `config.py`; tier → concrete Groq model in `resume_tailor/llm` (env-overridable `GROQ_MODEL_FAST` / `GROQ_MODEL_STRONG`).

| Task | Tier | Groq model (default) |
|---|---|---|
| JD parsing | fast | `llama-3.1-8b-instant` |
| Chat intent classification | fast | `llama-3.1-8b-instant` |
| Verifier yes/no | fast | `llama-3.1-8b-instant` |
| OCR text cleanup | fast | `llama-3.1-8b-instant` |
| Query reformulation | strong | `llama-3.3-70b-versatile` |
| Bullet generation | strong | `llama-3.3-70b-versatile` |
| Chat rewrites | strong | `llama-3.3-70b-versatile` |

`GroqClient` calls the OpenAI-compatible endpoint (`https://api.groq.com/openai/v1/chat/completions`) with `response_format: json_object` via plain `requests` — no openai/anthropic SDK. `get_client()` → Groq when `GROQ_API_KEY` is set (loaded from gitignored `.env`), else `MockLLMClient`.

## Efficiency Rules

1. Parallelize the Collector (`asyncio.gather`) — ✅ implemented.
2. Embed once, cache always — ✅ implemented (`collect()` embeds each chunk exactly once).
3. Batch the Matcher — VectorStore.query is batched; reformulate-retry only low-confidence hits (Phase 2).
4. Cap Verifier retry at 1 — ✅ (verifier ≤1 retry, bounce ≤1 revision).
5. Recompile LaTeX only on chat "send" — ✅ (demo_chat re-renders only on applied ops; ChatSession leaves rendering to the caller).

## Build Order & Status

| Phase | Deliverable | Status |
|---|---|---|
| 0. Setup | Schemas locked; Jake's Resume → Jinja2; dummy `pdflatex` compile | ✅ DONE |
| 1. Collector | Parallel fetch → chunked, embedded → Chroma + Evidence Graph JSON | ✅ DONE (39 tests; real-embedder smoke passed) |
| 2. JD Parser + Matcher | Batched RAG matching, reformulate-retry, gaps as JSON | ✅ DONE (66 tests; demo moments #1 live) |
| 3. Builder + Verifier | Verified Resume JSON with evidence-tagged bullets | ✅ DONE (97 tests; live demo drops unverifiable claims honestly) |
| 4. Render Agent | JSON → LaTeX → PDF pipeline | ✅ core done in Phase 0 |
| 5. Chat Refinement Agent | Typed edit ops, undo, honest refusal (moment #2) | ✅ DONE (134 tests; live Groq demo) |
| 5b. Frontend | Split view: PDF preview + Chat wired to run_full/ChatSession | 🔶 FastAPI + no-build SPA scaffolded (146 tests); design polish ⬜ |
| 6. Export + demo | `.pdf`/`.tex` export ✅ (API + UI); seed demo profile ✅; session-JSON export ⬜ | 🔶 |
| Buffer | Bug fixes; rehearse demo moments | ⬜ |

## Demo Moments (to rehearse)

1. **Matcher's live query reformulation** — ✅ live in `scripts/demo_match.py`: "distributed systems" missed first pass → `microservices` → `message queue` → hit rsmq; trace printed. (Phase 2)
2. **Chat agent refusing an unverified claim** — ✅ LIVE in `scripts/demo_chat.py` (Phase 5): real Groq refused "I led a team of 10 engineers" (no evidence → flagged, nothing inserted) AND refused an overclaiming rewrite ("evidence does not mention delivering production-ready queue-backed tooling").
3. **Hidden match discovery** — ✅ mechanism proven live in Phase 1: querying "Redis task queues" surfaced a forgotten commit + `queue.py` code with clickable citations.

## Running the app

```bash
./run.sh                    # starts server on 8177 + opens your browser
./run.sh --port 9000        # pick a port
./run.sh --no-browser       # server only
```
Or manually: `RESUME_TAILOR_PORT=8177 .venv/bin/python -m resume_tailor.web` → http://127.0.0.1:8177/

The UI has a **Use sample JD** button (pre-fills a backend-engineer JD that matches the seeded corpus) and a **build progress indicator** (the full pipeline takes ~20-25s: parse → match → build → verify → render).

**Evidence-based drafting toggle** (run modal, default ON): ON runs the full verify-and-drop loop; OFF is a quick draft — the builder's draft is used as-is with no verification pass (bullets marked `verified=False`, a "draft" badge shows in the PDF pane head). `POST /api/run` accepts `evidence_based`.

**PDF preview**: rendered inline via pdf.js canvas (never an iframe — webviews without a PDF plugin auto-download instead of previewing). If pdf.js can't load (offline), the pane shows explicit **Open PDF / Download PDF / Download TEX** links. Export is always user-initiated.

## Tech Stack

- Orchestration: hand-rolled (`run_full` is already the DAG; LangGraph buys nothing here) — decided 2026-08-08
- Extraction: PyGithub ✓, pdfplumber ✓, Tesseract OCR ⚠ binary missing, trafilatura ✓
- Retrieval: ChromaDB ✓ + sentence-transformers `all-MiniLM-L6-v2` ✓ (384-dim, CPU torch)
- Guardrails: Pydantic 2 ✓
- Rendering: Jinja2 → LaTeX (Jake's Resume) → local `pdflatex` ✓
- Frontend: **FastAPI + no-build static SPA** (decision made 2026-08-08) — React/Vite can replace the SPA later without touching the API contract
- Launcher: `./run.sh` (venv bootstrap + uvicorn on `RESUME_TAILOR_PORT` + auto-open browser)
- Models: Groq (`llama-3.1-8b-instant` fast / `llama-3.3-70b-versatile` strong); key in gitignored `.env` ✅ **verified live** (2026-08-08)

## Environment Status (checked 2026-08-08)

| Tool | Status |
|---|---|
| Python 3.12.3 / pdflatex / Node 18 | ✓ |
| Tesseract OCR binary | ✗ (certs degrade gracefully) |
| .venv | editable install; `[dev]` + phase1 + llm-group deps: pygithub, pdfplumber, trafilatura, pytesseract, chromadb, torch (CPU), sentence-transformers, pydantic, jinja2, pytest ✓ |

## Risks & Mitigations (from brainstorm)

See `context.md` § Brainstorm — offline/fixture ✅ (snapshots + MockLLMClient), LLM layer ✅, Evidence Graph ✅, pre-filtering ✅, in-chat claim sourcing ⬜ (Phase 5), header-field verification ⬜ (Phase 3).
