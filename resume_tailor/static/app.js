/* Resume Tailor — no-build SPA client. Talks to the FastAPI backend. */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  sessionId: null,
  transcript: [],
  canUndo: false,
  busy: false,
  progressTimer: null,
};

/* ---------- helpers ---------- */

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON */ }
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function setBusy(b) {
  state.busy = b;
  $("chat-send").disabled = b || !state.sessionId;
  $("chat-input").disabled = b || !state.sessionId;
  $("run-submit").disabled = b;
  $("run-submit").textContent = b ? "Building…" : "Build verified resume";
  if (!b) $("chat-input").focus();
}

const SAMPLE_JD = `Senior Backend Engineer @ Acme
We build distributed systems for learning platforms at scale. You will own services that power millions of daily users.

Requirements:
- 5+ years of Python
- Redis and message queues
- PostgreSQL and Docker
- Node.js tooling
- Distributed systems experience
- Strong testing culture (CI, unit + integration tests)`;

const BUILD_PHASES = ["Parsing job description…", "Matching evidence…", "Drafting verified bullets…", "Verifying claims…", "Rendering PDF…"];

function startProgress() {
  const box = $("build-progress");
  const phase = $("build-phase");
  const fill = $("build-fill");
  box.classList.remove("hidden");
  let i = 0;
  phase.textContent = BUILD_PHASES[0];
  fill.style.width = "4%";
  clearInterval(state.progressTimer);
  state.progressTimer = setInterval(() => {
    i = Math.min(i + 1, BUILD_PHASES.length - 1);
    phase.textContent = BUILD_PHASES[i];
    fill.style.width = Math.min(88, 6 + i * 20 + Math.random() * 10) + "%";
  }, 4000);
}

function stopProgress() {
  clearInterval(state.progressTimer);
  state.progressTimer = null;
  const box = $("build-progress");
  if (box) box.classList.add("hidden");
}

/* ---------- run pipeline ---------- */

async function runPipeline() {
  const jd = $("jd").value.trim();
  if (!jd) { showError("Paste a job description first."); return; }
  hideError();
  setBusy(true);
  startProgress();
  try {
    const payload = await api("/api/run", {
      jd_text: jd,
      name: $("name").value.trim() || "Candidate Name",
      contact: { email: $("email").value.trim() },
      github_username: $("github").value.trim(),
      evidence_based: $("evidence-toggle").checked,
    });
    state.sessionId = payload.session_id;
    state.canUndo = payload.can_undo;
    $("run-modal").close();
    $("welcome").classList.add("hidden");
    $("main").classList.remove("hidden");
    setDraftBadge(payload.stats && payload.stats.evidence_based === false);
    applySession(payload);
  } catch (err) {
    showError(err.message);
  } finally {
    stopProgress();
    setBusy(false);
    syncBar();
  }
}

function applySession(payload) {
  state.transcript = payload.transcript || [];
  state.canUndo = payload.can_undo;
  renderTranscript();
  renderTrace(payload);
  renderResume(payload.resume);
  refreshPdf();
  setBusy(false);
  syncBar();
}

/* ---------- chat ---------- */

async function sendChat() {
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text || state.busy || !state.sessionId) return;
  input.value = "";
  appendTyping();
  setBusy(true);
  try {
    const data = await api("/api/chat", { session_id: state.sessionId, message: text });
    state.transcript = data.transcript;
    state.canUndo = data.can_undo;
    removeTyping();
    renderTranscript();
    renderResume(data.resume);
    if (data.result.applied) await refreshPdf();
    syncBar();
  } catch (err) {
    removeTyping();
    appendMessage("assistant", `⚠ ${err.message}`, {});
  } finally {
    setBusy(false);
  }
}

async function undo() {
  if (!state.sessionId || state.busy) return;
  setBusy(true);
  try {
    const data = await api("/api/chat/undo", { session_id: state.sessionId });
    state.transcript = data.transcript;
    state.canUndo = data.can_undo;
    renderTranscript();
    renderResume(data.resume);
    if (data.result.applied) await refreshPdf();
    syncBar();
  } catch (err) {
    appendMessage("assistant", `⚠ ${err.message}`, {});
  } finally {
    setBusy(false);
  }
}

/* ---------- rendering ---------- */

function renderTranscript() {
  const thread = $("chat-thread");
  thread.innerHTML = "";
  for (const item of state.transcript) {
    if (item.role === "user") {
      thread.appendChild(bubble("msg-user", esc(item.text)));
    } else {
      const meta = [];
      if (item.applied) meta.push('<span class="tag tag-applied">✓ applied</span>');
      if (item.flagged) meta.push('<span class="tag tag-flagged">⚠ flagged</span>');
      if (!item.applied && !item.flagged && item.text.startsWith("Undid")) meta.push('<span class="tag tag-undo">undo</span>');
      const el = bubble("msg-assistant" + (item.flagged ? " flagged" : ""), esc(item.text));
      if (meta.length) el.insertAdjacentHTML("beforeend", `<div class="msg-meta">${meta.join("")}</div>`);
      thread.appendChild(el);
    }
  }
  thread.scrollTop = thread.scrollHeight;
  const empty = $("chat-empty") || null;
  if (empty) empty.remove();
}

function bubble(cls, innerHtml) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = innerHtml;
  return div;
}

function appendTyping() {
  const thread = $("chat-thread");
  const div = document.createElement("div");
  div.id = "typing";
  div.className = "msg msg-assistant typing";
  div.textContent = "thinking…";
  thread.appendChild(div);
  thread.scrollTop = thread.scrollHeight;
}

function removeTyping() { const t = $("typing"); if (t) t.remove(); }

function appendMessage(role, text) {
  state.transcript = state.transcript.concat([{ role: "user", text: "" }, { role: "assistant", text, applied: false, flagged: false }]);
  renderTranscript();
}

function renderTrace(payload) {
  const panel = $("tab-trace");
  const parts = [];

  parts.push(`<div class="trace-pad"><h3 style="margin:0 0 2px;font-size:15px">JD requirements</h3>`);
  parts.push(`<div style="color:var(--muted);font-size:12.5px;margin-bottom:6px">${esc(payload.jd.title || "(untitled)")}${payload.jd.company ? " @ " + esc(payload.jd.company) : ""} — ${payload.stats.matched} matched / ${payload.stats.gaps} gaps</div>`);

  for (const m of payload.matches) {
    const status = m.status === "matched" ? '<span class="chip chip-matched">matched</span>' : '<span class="chip chip-gap">gap</span>';
    const src = m.retrieval_source ? `<span class="chip chip-${m.retrieval_source}">${m.retrieval_source}</span>` : "";
    const reform = m.query_trace.length > 1 ? '<span class="chip chip-reformulated">reformulated</span>' : "";
    const trace = m.query_trace.map((q, i) => (i ? '<span class="trace-arrow">→</span>' : "") + `<span class="trace-query">${esc(q)}</span>`).join("");

    parts.push(`<div class="trace-block">
      <div class="trace-block-head">${status}${src}${reform}<span style="color:var(--muted);font-weight:500">best ${m.best_distance != null ? m.best_distance.toFixed(3) : "—"}</span></div>
      <div class="trace-block-body">
        <div style="font-weight:600">${esc(m.requirement)}</div>
        <div class="trace-trace">${trace}</div>
        ${m.hits.length ? m.hits.map((h) => `<div class="evidence">
          <div class="evidence-source">${esc(h.source_id)} <span style="color:var(--muted)">· ${esc(h.source_type)} · ${h.distance}</span></div>
          <div class="evidence-text">${esc(h.text)}</div>
        </div>`).join("") : '<div class="empty-note">No evidence found — honest gap, never fabricated.</div>'}
      </div>
    </div>`);
  }

  const v = payload.verification;
  parts.push(`<h3 style="margin:14px 0 4px;font-size:15px">Verification</h3>`);
  for (const vd of v.verdicts) {
    const chip = vd.verdict === "pass" ? '<span class="chip chip-pass">pass</span>' : '<span class="chip chip-fail">fail</span>';
    parts.push(`<div class="verdict-row">${chip}<div><div>${esc(vd.claim)}</div><div class="verdict-reason">${esc(vd.reason)} <span style="color:#94a3b8">[${esc(vd.source)}]</span></div></div></div>`);
  }
  for (const d of v.dropped) {
    parts.push(`<div class="verdict-row"><span class="tag tag-flagged">dropped</span><div><div><s>${esc(d.claim)}</s></div><div class="verdict-reason">${esc(d.reason)}</div></div></div>`);
  }
  if (!v.verdicts.length && !v.dropped.length) parts.push('<div class="empty-note">Nothing to verify yet.</div>');
  parts.push("</div>");
  panel.innerHTML = parts.join("");
}

function renderResume(resume) {
  const panel = $("tab-resume");
  const parts = ['<div class="resume-pad">'];
  parts.push(`<div style="font-size:16px;font-weight:700">${esc(resume.name || "(no name)")}</div>`);
  if (resume.contact && resume.contact.email) parts.push(`<div style="color:var(--muted);font-size:12.5px">${esc(resume.contact.email)}</div>`);
  for (const section of resume.sections || []) {
    parts.push('<div class="section-card"><div class="section-title">' + esc(section.title) + "</div><div class='section-body'>");
    if (section.entries && section.entries.length) {
      for (const entry of section.entries) {
        if (entry.title) parts.push(`<div class="entry-title">${esc(entry.title)}${entry.subtitle ? " — " + esc(entry.subtitle) : ""}</div>`);
        for (const b of entry.bullets || []) parts.push(bulletHtml(b));
      }
    } else {
      for (const b of section.bullets || []) parts.push(bulletHtml(b));
    }
    parts.push("</div></div>");
  }
  if (!(resume.sections || []).length) parts.push('<div class="empty-note">No verified bullets yet — run a job description against an evidence corpus.</div>');
  parts.push("</div>");
  panel.innerHTML = parts.join("");
}

function setDraftBadge(isDraft) {
  const badge = $("draft-badge");
  if (badge) badge.classList.toggle("hidden", !isDraft);
}

function bulletHtml(b) {
  const mark = b.verified ? '<span class="bullet-check">✓</span>' : '<span class="bullet-unverified">!</span>';
  const evid = (b.evidence_ids || []).map((e) => `<span class="bullet-evid">${esc(e)}</span>`).join(" ");
  return `<div class="resume-bullet"><span>${mark}</span><div><div>${esc(b.text)}</div>${evid ? `<div>${evid}</div>` : ""}</div></div>`;
}

/* PDF preview via pdf.js canvas — never an iframe, because embedded webviews
   without a PDF plugin auto-download the file instead of previewing it.
   If pdf.js can't load (offline/CDN blocked), show manual Open/Download links. */
const PDFJS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

async function refreshPdf() {
  const wrap = $("pdf-wrap");
  const canvasBox = $("pdf-canvas");
  const fallback = $("pdf-fallback");
  const empty = $("pdf-empty");
  canvasBox.innerHTML = "";
  fallback.classList.add("hidden");
  empty.textContent = "Rendering PDF preview…";
  empty.classList.remove("hidden");

  const pdfUrl = `/api/pdf?session_id=${state.sessionId}&t=${Date.now()}`;
  const texUrl = `/api/tex?session_id=${state.sessionId}`;
  $("pdf-open").href = pdfUrl;
  $("pdf-dl").href = pdfUrl;
  $("tex-dl").href = texUrl;

  try {
    if (!window.pdfjsLib) throw new Error("pdf.js not available");
    pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_CDN;
    const pdf = await pdfjsLib.getDocument({ url: pdfUrl }).promise;
    const base = Math.max(1, (wrap.clientWidth - 28) / 612);
    const scale = Math.min(2, base);
    for (let p = 1; p <= pdf.numPages; p++) {
      const page = await pdf.getPage(p);
      const viewport = page.getViewport({ scale });
      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.className = "pdf-page";
      canvasBox.appendChild(canvas);
      empty.classList.add("hidden");
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    }
  } catch (_) {
    empty.classList.add("hidden");
    fallback.classList.remove("hidden");
  }
}

/* ---------- tabs ---------- */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tabpanel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $("tab-" + tab.dataset.tab).classList.add("active");
  });
});

/* ---------- wiring ---------- */

$("btn-run").addEventListener("click", () => { hideError(); stopProgress(); $("run-modal").showModal(); });
$("btn-run-welcome").addEventListener("click", () => { hideError(); stopProgress(); $("run-modal").showModal(); });
$("modal-close").addEventListener("click", () => $("run-modal").close());
$("btn-sample").addEventListener("click", () => {
  $("jd").value = SAMPLE_JD;
  hideError();
  $("jd").focus();
});
$("run-form").addEventListener("submit", (e) => { e.preventDefault(); runPipeline(); });
$("chat-send").addEventListener("click", sendChat);
$("chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } });
$("btn-undo").addEventListener("click", undo);
$("btn-export-pdf").addEventListener("click", () => window.open(`/api/pdf?session_id=${state.sessionId}`, "_blank"));
$("btn-export-tex").addEventListener("click", () => window.open(`/api/tex?session_id=${state.sessionId}`, "_blank"));

/* keep topbar buttons in sync with session state.
   Called only from narrow, explicit points (after run/chat/undo, and a slow
   poll) — NOT via a MutationObserver on the whole body, which can starve the
   main thread in embedded webviews when every attribute change re-arms it. */
const syncBar = () => {
  $("btn-undo").disabled = !state.canUndo || state.busy;
  $("btn-export-pdf").disabled = !state.sessionId;
  $("btn-export-tex").disabled = !state.sessionId;
};
setInterval(syncBar, 2000);

function showError(msg) { const el = $("run-error"); el.textContent = msg; el.classList.remove("hidden"); }
function hideError() { $("run-error").classList.add("hidden"); }
