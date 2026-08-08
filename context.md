# Context — Session Working Memory

> Updated every session. Tracks what happened, current state, open decisions, and next actions. `project.md` holds the long-lived reference.

## Session Log

### Session 2 — 2026-08-08: Phase 1 Collector Agent complete ✅ (see changelog)

### Session 3 — 2026-08-08: Demo corpus seeded + expanded (see changelog)

### Session 4 — 2026-08-08: Phase 2 JD Parser + Matcher complete ✅

### Session 5 — 2026-08-08: Provider switched to Groq (Anthropic removed)

### Session 6 — 2026-08-08: Phase 3 Builder + Verifier complete ✅ (see changelog)

### Session 7 — 2026-08-08: Hybrid retrieval (keyword boost) added 🔶 default-off

### Session 8 — 2026-08-08: Hybrid retrieval wired into the pipeline (default-on) + Groq 429 retry

### Session 9 — 2026-08-08: Corpus refreshed with code-file coverage ✅

### Session 11 — 2026-08-08: Phase 5b/6 frontend scaffolded ✅ (FastAPI + no-build SPA)

- **Decision made**: professional frontend = **FastAPI + no-build static SPA** (not Streamlit — it caps the professional look; not React — build pipeline cost for a split-view scope). Backend was frontend-agnostic from day one, so the UI is a thin wrapper over `run_full` + `ChatSession`.
- **`resume_tailor/web.py`** — `create_app()` factory (injectable `client_factory`/`embedder_factory`/`corpus_dir`/`out_dir`/`jobname`): `POST /api/run` (optional live GitHub collect; 400 on empty corpus), `POST /api/chat` (re-renders PDF **only on applied ops** — rule #5), `POST /api/chat/undo`, `GET /api/state`, `GET /api/pdf` + `/api/tex` (FileResponse), static mount + root route.
- **`resume_tailor/static/`** — no-build SPA (`index.html` + `app.css` + `app.js`): split view (PDF iframe left, chat thread right), trace tabs per match (query reformulation traces + verification verdicts + evidence deep links), undo button, export links, `esc()` XSS escaping throughout.
- **`pyproject.toml`** — `[web]` extra (fastapi, uvicorn, httpx) + package-data for static assets.
- **Live smoke via curl** (real corpus + real Groq + pdflatex): run 200 (21.5s) → chat **flagged** (moment #2 through the API) → pdf 200 (22.6KB) → tex 200 (2.4KB).
- **Port 8177 not 8000** — 8000 is occupied by an unrelated process on this machine; `RESUME_TAILOR_PORT` env override added (verified: binds 8188).
- **Run it**: `./run.sh` (starts server + opens browser) or `RESUME_TAILOR_PORT=8177 .venv/bin/python -m resume_tailor.web`.
- **UX pass (user feedback)**: the SPA now has a **Use sample JD** button (pre-fills a JD matched to the seeded corpus) and a **build progress indicator** (phase text + animated bar) — the pipeline takes ~20-25s and previously showed nothing. The live server on 8177 persists via `setsid`; the preview interaction bridge is flaky in this environment (screenshots OK, click/eval time out), so the app is verified via API smoke + screenshots, not automated clicks.
- **Responsive pass (user feedback "not responsive at all")**: app.css rewritten — fluid `clamp()` typography, `dvh` viewport units, `minmax(0, 1fr)` grid (no overflow), modal `min(560px, 100vw-32px)` + scrollable, breakpoints at 1100px (single-column stack, bounded pane heights) and 640px (wrapping topbar, hidden brand tag, sticky chat input, full-width modal buttons). **Verified with headless Chrome: zero horizontal overflow at 390/768/1024/1440px.**
- **Dead-buttons fix (user feedback "clicking gives no response") — root cause found**: the SPA's `MutationObserver` watched `document.body` and its `syncBar` callback set `disabled` on buttons — a self-re-arming loop (mutation → syncBar mutates attributes → observer fires → …) that starved the main thread in the embedded preview, so every click was ignored. Replaced with explicit `syncBar()` calls after run/chat/undo + a slow 2s poll. **Verified live via the preview: modal opens on click, sample JD fills, run completes, PDF renders, chat flags unverifiable claims (moment #2).** Also confirmed the preview tool's `replace:true` cycle kills the uvicorn process (froze the page twice) — restart + fresh register avoids it.
- **Evidence-based drafting toggle (user feedback)**: the run modal now has an **Evidence-based drafting** switch (default ON). ON = full verify-and-drop loop (the product premise). OFF = quick draft: builder's draft used as-is, **no verification pass** (`run_full(evidence_based=False)` → BuildResult with empty Verification, bullets honest `verified=False`; stats gain `evidence_based`; `bullets_verified` now counts only `verified=True` bullets so draft mode reports 0). A **"draft" badge** appears in the PDF pane head for draft runs.
- **No auto-download on run (user feedback)**: the PDF pane no longer loads an iframe (embedded webviews without a PDF plugin download instead of preview). It now renders the PDF **inline with pdf.js** (CDN worker; canvas pages) and falls back to explicit **Open PDF / Download PDF / Download TEX** links when pdf.js can't load. Export stays fully user-initiated via those links + the topbar buttons. **Verified live: draft-mode run rendered 1 canvas page with real bullet text, no download.**
- **Artifacts are session-scoped** (reviewer finding): jobname = `web_{session_id}` → `/api/pdf?session_id=...` serves each session's own render — a second run never bleeds its PDF over the first. `pdf_url` in the payload carries the session param.
- **147/147 tests pass** (11 web tests: run/chat/undo/state/pdf/tex, moment #2 through the API, cross-session artifact isolation; reviewer's port + pdf_url findings fixed).

### Session 10 — 2026-08-08: Phase 5 Chat Refinement Agent complete ✅ (see changelog)

- **Typed edit ops, not LLM-mutated JSON** (the brainstorm's key design call): `chat.py` — fast-tier intent classify (`chat_intent`) → `ChatEditOp` (indices from a `document_map`) → **deterministic apply** (`_apply_rewrite/add/remove/reorder`) with full validation; everything untouched stays byte-identical; patch log → **undo**.
- **Text edits are re-verified**: rewrite_bullet/tone_change go through the strong tier (`chat_rewrites`) then the Verifier — overclaims are REFUSED with the verifier's reason (never applied).
- **add_claim routes through the build's own machinery** (efficiency rule #2: embeds only the claim): evidence found + verifier pass → added verified; else **flagged, nothing inserted** (demo moment #2).
- `ChatSession` (resume + graph/store/embedder + patch log + history + `undo()`); re-render on applied sends (efficiency rule #5) is the caller's job.
- **Live Groq demo** (`scripts/demo_chat.py`): base resume from the offline fixture (live verifier rightly refuses foreign-corpus claims → would be empty); chat runs REAL Groq — rewrite refused by real verifier, add_claim refused (moment #2), reorder applied, undo applied.
- **134/134 tests pass** (16 new chat tests: per-intent apply, re-verify accept/refuse, add_claim evidence/refusal, none intent, undo ×2, bad targets, classifier retry, embed-once).

- No `GITHUB_TOKEN` (env/.env), but the core quota had reset (59/60). `collect_github(..., refresh=True)` backfilled git-tree file listings: **30 readmes + 72 code files + 287 commits = 389 sources / 3,734 chunks**, re-embedded with 0 warnings.
- Retrieval smoke on the enriched corpus still sharp (CLI tools → oclif/svg-term-cli/chalk; webpack → file-type/type-fest).
- **BLOCKED on user input**: demo_build.py against the user's OWN GitHub profile needs their username (asked).

- `run_full()` now defaults to `MatchConfig(use_keywords=True)` (explicit config overrides); stats add `lexical_matches` + `reformulations`. `demo_build.py` passes the hybrid config and prints `[semantic|lexical / direct|reformulated]` per match.
- **Live result**: 5/7 requirements matched at 0.200 `[lexical / direct]` (Python/Redis/PostgreSQL/Docker/Node.js — zero reformulation calls); the Verifier then rejected every drafted bullet (corpus = someone else's repos, no evidence of candidate experience) → **empty honest resume** — the anti-hallucination design at full strength. Demo now explains the empty case.
- **GroqClient 429 handling**: bounded rate-limit retry (2 retries, honors `Retry-After`, exponential backoff, cap 15s) — the free tier hits TPM limits on a full pipeline run. Fail-closed still holds when retries are exhausted. `requests` moved to module-level import.
- **118/118 tests pass** (new: run_full hybrid-default stats, explicit semantic-only override, hybrid-gap-stays-gap, 429 retry-then-success, Retry-After honored).

- `EvidenceHit.retrieval_source` (`semantic`/`lexical`), `VectorStore.get_all()`, `collector/lexical.py` (`LexicalIndex` — case-insensitive whole-word scan, ranked by keyword coverage, no deps), `MatchConfig.use_keywords` + `lexical_distance` (default 0.2, validated < match_threshold). Matcher pools lexical hits per requirement (deduped by chunk_id, semantic wins ties) — default OFF, semantic-only behavior unchanged.
- **`scripts/demo_lexical.py`** before/after on the corpus: Redis/Node.js/PostgreSQL matched at 0.200 via lexical hits and **skipped their reformulation calls entirely**; distributed systems unchanged (reformulation); lawn care honest gap in both modes. 0 flips, no fabrications.
- **114/114 tests pass** (new: test_lexical.py + matcher boost/dedupe/gating + get_all).

- **Resume Builder** (`builder.py`, strong tier) — `generate_resume(jd, match_result, header, client, feedback)` → draft `Resume`; XYZ bullets prompted; bullets cite `evidence_ids` from matched hits; **header enforced from `ResumeHeader` input** (model name/contact ignored); ≤1 retry on bad JSON.
- **Verifier** (`verifier.py`, fast tier) — `verify_resume(resume, client, graph)` → `Verification`; deterministic auto-fails first (no evidence / unknown evidence ids vs the graph), then ONE batched LLM call for the rest (claim ⊆ evidence); ≤1 retry; LLM outage → fail closed.
- **Pipeline** (`pipeline.py`) — `build_verified_resume()` bounce loop (≤1 revision with verifier feedback; dropped list **cumulative across rounds**); `run_full()` chains parse→match→build→verify→render → `PipelineResult` (PDF + stats).
- **GroqClient** now sends task-aware `max_tokens` (builder 4096 vs parser 1024; env-overridable `GROQ_MAX_TOKENS_<TASK>`).
- **Live Groq demo** (`scripts/demo_build.py`) — demo moment #2 for real: verifier rejected 5/6 drafted bullets (corpus evidence doesn't support "5+ years Python", "Docker production") → honest drops; 1 bullet survived (rsmq/bee-queue); PDF rendered to `out/verified_resume.pdf`.
- **97/97 tests pass** (builder/verifier/pipeline suites; capstone build→verify→pdflatex gate).

**What happened**
- **LLM client abstraction** (`resume_tailor/llm/`) — brainstorm rec #2 delivered:
  - `LLMClient` protocol: `complete_json(task, system, prompt) -> dict`; tier resolved from `config.model_for(task)`, concrete model from env (`GROQ_MODEL_FAST`/`GROQ_MODEL_STRONG`) with stable defaults.
  - `MockLLMClient` (deterministic; queued JSON strings OR per-prompt callable handlers; records all calls) → dry-run/CI mode via `get_client()` (mock when `RESUME_TAILOR_DRY_RUN=1` or no `ANTHROPIC_API_KEY`).
- **JD Parser** (`resume_tailor/jd_parser.py`, Haiku tier) — `parse_jd(text) -> ParsedJD`; schema-validated, ≤1 retry on bad JSON, never invents requirements.
- **Matcher** (`resume_tailor/matcher.py`, guide §3 step 3):
  - Embeds ALL requirements in one call → single batched Chroma query (efficiency rule #3; verified by test).
  - Below `match_threshold` (0.62) → matched with evidence hits. Between thresholds → **reformulate-retry ≤2** (Sonnet tier, `query_trace` records each query). Hopeless gaps (≥0.75) skip reformulation to save tokens. Still nothing → **honest gap**, zero fabricated hits.
  - Schemas: `EvidenceHit`, `RequirementMatch` (status matched/gap, hits, best_distance, query_trace), `MatchResult` in `schemas/match.py`.
- **Demo** (`scripts/demo_match.py`, offline, no API key): parses a sample JD and matches against the 30-repo sindresorhus corpus. Live output shows all three behaviors:
  1. Direct matches: "Redis and message queues" → rsmq/better-queue (0.266).
  2. **Reformulation success (demo moment #1)**: "distributed systems" missed first pass → `microservices` → `message queue` → hit rsmq (0.467); trace visible.
  3. **Honest gap**: "Lawn care" → landscaping/gardening queries → 0.626 ≥ threshold → reported as gap, no fabricated evidence.
- **66/66 tests pass.**

**Decisions this session**
- Distance thresholds (0.62/0.75) tuned for all-MiniLM-L6-v2's scale; tests override with calibrated thresholds for FakeEmbedder (dim=256 — the dim=8 default caused char-bucket collisions that destroyed separation).
- `MockLLMClient` supports callable handlers so demos can do prompt-aware reformulations (a stateless shared mock steered unrelated requirements into false matches).

## Brainstorm Recommendations

| # | Recommendation | Impact | Effort | Status |
|---|---|---|---|---|
| 1 | **Offline/fixture mode** — run pipeline against canned fixtures + recorded LLM outputs | High | Moderate | 🔶 snapshot cache ✅ + MockLLMClient ✅ (LLM-output fixtures = MockLLMClient) |
| 2 | **Abstract the LLM layer** (env model tiers + mock mode) | High | Low | ✅ done (Phase 2) |
| 7 | **Hybrid retrieval (keyword boost)** — JD keywords already extracted but unused; exact-term hits pooled as lexical evidence, reformulation avoided | Med | Low | 🔶 keyword boost done (Phase 4, default-off); full BM25+RRF ⬜ if recall needs it later |
| 3 | **Evidence Graph as a first-class schema** | High | Low | ✅ done (Phase 0) |
| 4 | **Pre-filter rich demo profile before embedding** | Med | Low-Med | ✅ done (RepoFilters) |
| 5 | **Close the add_claim loop in-chat** — attach source inside chat; patch log for undo | Med | Med | ⬜ (Phase 5) |
| 6 | **Verify header fields too** (name/contact/education) | Med | Low | 🔶 header enforced from input in Builder (never from model); LLM-side header checking ⬜ (chat phase) |

## Open Decisions

- [ ] Orchestration: LangGraph vs hand-rolled asyncio DAG — still TBD (recommend hand-rolled).
- [ ] Frontend: Streamlit vs Next.js — still TBD (recommend Next.js).
- [ ] `git init` this directory? (recommend yes)
- [ ] Tesseract system install for cert OCR (needs OS-level install + permission).
- [ ] Demo GitHub profile: `sindresorhus` ✅ seeded; backfill code files with `refresh=True` after core rate-limit reset or with a token.

## Next Actions

1. **Phase 6 remaining**: design polish on the SPA (the scaffold is functional; a designer pass on the CSS), rehearse all three demo moments in the browser.
2. Export polish already live: `/api/pdf` + `/api/tex` download buttons in the UI. ⬜ Export session state as JSON (jd + resume + transcript) for continuity.
3. Optional: user's own GitHub profile corpus (verified bullets then survive live end-to-end).

## Environment Notes

- All deps installed. Real embeddings work (HF reachable). `pdflatex` fine.
- `tesseract` binary still missing (certs OCR degrades to warning).
- `.freebuff/` is Freebuff's own DB — do not touch.
- **Web app**: `RESUME_TAILOR_PORT` (default 8000) — port 8000 is taken on this machine, use e.g. 8177.

## Changelog

- 2026-08-08: Session 1 — brainstorm, context/project docs, Phase 0 (schemas + render + dummy compile), 13 tests.
- 2026-08-08: Session 2 — **Phase 1 Collector complete** (parallel readers, pre-filtering, chunking, embedding, Chroma store, EvidenceGraph); **43 tests.**
- 2026-08-08: Session 3 — **Demo corpus seeded**: sindresorhus cached offline; fetcher rewritten (search API + raw + atom feeds — rate-limit-safe, fixes ignored `sort=stars`); readme dedupe; 201 sources / 1,785 chunks; **44 tests.**
- 2026-08-08: Session 4a — **Corpus expanded to 30 repos**: 320 sources / 2,697 chunks (30 readmes, 287 commits, 3 files).
- 2026-08-08: Session 4 — **Phase 2 complete**: LLM client abstraction (mock + Anthropic, env tiers), JD Parser, Matcher (batched RAG, reformulate-retry ≤2, honest gaps); demo shows moments #1 + honest-gap live. Review fixes folded in. **70 tests.**
- 2026-08-08: Session 5 — **Provider switched to Groq** (Anthropic removed; tiers fast/strong; GroqClient via plain requests; `.env` key loading). First key invalid (401), regenerated key **verified live** — real JD parse + real reformulations + honest gap. **74 tests.**
- 2026-08-08: Session 6 — **Phase 3 Builder + Verifier complete**: XYZ builder (header enforced), verifier (deterministic auto-fail + batched LLM claim check, fail-closed), bounce loop ≤1 revision with cumulative drops, `run_full` pipeline → verified PDF. Live Groq demo dropped 5 unverifiable bullets honestly. **97 tests.**
- 2026-08-08: Session 7 — **Hybrid retrieval (keyword boost, default-off)**: `LexicalIndex` + `MatchConfig.use_keywords`; demo shows Redis/Node/PostgreSQL matching at 0.200 and skipping reformulation; no flips, no fabrications. **114 tests.**
- 2026-08-08: Session 8 — **Hybrid wired into pipeline (default-on)** in `run_full`/`demo_build`; stats `lexical_matches`/`reformulations`; **GroqClient bounded 429 retry** (Retry-After + backoff). Live demo: 5/7 lexical/direct at 0.200, verifier rejects all claims honestly. **118 tests.**
- 2026-08-08: Session 9 — **Corpus refreshed**: file trees backfilled (72 code files), 389 sources / 3,734 chunks, 0 warnings.
- 2026-08-08: Session 10 — **Phase 5 Chat Refinement Agent complete**: typed edit ops + deterministic apply + undo; rewrite/tone re-verified; add_claim flags unverifiable claims (moment #2); ChatSession; live Groq demo shows honest refusals + applied reorder/undo. **134 tests.**
- 2026-08-08: Session 11 — **Phase 5b/6 frontend scaffolded**: FastAPI backend (`/api/run|chat|undo|state|pdf|tex`) + no-build SPA (split view, trace tabs, undo, export); `[web]` extra; `RESUME_TAILOR_PORT` env; live smoke all 200s; moment #2 through the API. **146 tests.**
