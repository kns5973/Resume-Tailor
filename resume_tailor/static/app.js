/* Resume Tailor — no-build SPA client. Talks to the FastAPI backend. */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  sessionId: null,
  transcript: [],
  canUndo: false,
  busy: false,
  imported: false, // read-only record view (no live artifacts/chat)
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

async function apiForm(path, formData) {
  // multipart/form-data: the browser sets the boundary header itself
  const res = await fetch(path, { method: "POST", body: formData });
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
  const fileInput = $("prev-resume");
  const fd = new FormData();
  fd.append("jd_text", jd);
  fd.append("name", $("name").value.trim() || "Candidate Name");
  fd.append("contact", JSON.stringify({ email: $("email").value.trim() }));
  fd.append("github_username", $("github").value.trim());
  fd.append("evidence_based", $("evidence-toggle").checked ? "true" : "false");
  if (fileInput.files && fileInput.files[0]) fd.append("previous_resume", fileInput.files[0]);
  try {
    const payload = await apiForm("/api/run", fd);
    state.sessionId = payload.session_id;
    state.canUndo = payload.can_undo;
    state.imported = false;
    $("run-modal").close();
    $("welcome").classList.add("hidden");
    $("main").classList.remove("hidden");
    setDraftBadge(payload.stats && payload.stats.evidence_based === false);
    applySession(payload);
    loadSessions();
    loadSessionMeta(state.sessionId);
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
    loadSessions();
    loadSessionMeta(state.sessionId);
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
    loadSessions();
    loadSessionMeta(state.sessionId);
    syncBar();
  } catch (err) {
    appendMessage("assistant", `⚠ ${err.message}`, {});
  } finally {
    setBusy(false);
  }
}

/* ---------- session records: search, filters, list ---------- */

function sessionFilterParams() {
  const params = new URLSearchParams();
  const q = $("sessions-q").value.trim();
  if (q) params.set("q", q);
  const map = [
    ["sessions-status", "status"],
    ["sessions-topic", "topic"],
    ["sessions-source", "source"],
    ["sessions-difficulty", "difficulty"],
  ];
  for (const [id, key] of map) {
    const v = $(id).value;
    if (v) params.set(key, v);
  }
  return params.toString() ? "?" + params.toString() : "";
}

function sessionsHasFilters() {
  return !!($("sessions-q").value.trim() || $("sessions-status").value || $("sessions-topic").value || $("sessions-source").value || $("sessions-difficulty").value);
}

async function loadSessions() {
  try {
    const res = await fetch("/api/sessions" + sessionFilterParams());
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    renderSessions(data);
  } catch (err) {
    $("sessions-list").innerHTML = `<div class="sessions-empty">Couldn't load sessions: ${esc(err.message)}</div>`;
  }
}

function renderSessions(data) {
  const topicSel = $("sessions-topic");
  const currentTopic = topicSel.value;
  topicSel.innerHTML = '<option value="">All topics</option>' + (data.topics || []).map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
  if (currentTopic) topicSel.value = currentTopic;

  $("sessions-count").textContent = data.total ? `${data.total} session${data.total === 1 ? "" : "s"} stored` : "";
  const records = data.records || [];
  const list = $("sessions-list");
  if (!records.length) {
    list.innerHTML = `<div class="sessions-empty">${sessionsHasFilters()
      ? "No sessions match your filters — try clearing the search or resetting the filters."
      : "No sessions yet — run a job description to create your first review session."}</div>`;
    return;
  }
  list.innerHTML = records.map(sessionRow).join("");
  list.querySelectorAll("[data-open]").forEach((btn) => btn.addEventListener("click", () => openSession(btn.dataset.open)));
  list.querySelectorAll("[data-packet]").forEach((btn) => btn.addEventListener("click", () => window.open(`/api/sessions/${btn.dataset.packet}/packet`, "_blank")));
}

function sessionRow(r) {
  const date = (r.created_at || "").replace("T", " ").slice(0, 16);
  const conf = r.confidence != null ? `${r.confidence}/100` : "—";
  const progress = Math.max(0, Math.min(100, r.progress || 0));
  return `<div class="session-row">
    <div class="session-row-main">
      <div class="session-row-title">${esc(r.jd_title || "Untitled JD")}${r.company ? " @ " + esc(r.company) : ""} <span class="chip chip-${esc(r.difficulty)}">${esc(r.difficulty)}</span></div>
      <div class="session-row-meta">${esc(date)} · ${esc(r.candidate_name || "?")} · ${esc(r.topic)}</div>
      <div class="session-row-bar"><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div><span class="progress-label">${progress}% ready</span></div>
      <div class="session-row-tags">
        <span class="badge badge-${esc(r.status)}">${esc(String(r.status).replace("_", " "))}</span>
        <span class="chip chip-mode ${r.evidence_based ? "chip-verified" : "chip-draft"}">${r.evidence_based ? "evidence-based" : "draft"}</span>
        <span class="session-conf">Confidence: <b>${esc(conf)}</b></span>
        ${r.note ? `<span class="session-note" title="${esc(r.note)}">“${esc(r.note.length > 60 ? r.note.slice(0, 60) + "…" : r.note)}”</span>` : ""}
      </div>
    </div>
    <div class="session-row-actions">
      <button class="btn" data-open="${esc(r.session_id)}">Open</button>
      <button class="btn btn-primary" data-packet="${esc(r.session_id)}">Packet</button>
    </div>
  </div>`;
}

async function openSession(id) {
  try {
    const res = await fetch(`/api/state?session_id=${id}`);
    if (res.status === 404) {
      // Not in this server's memory (e.g. created by the CLI/demo): open the
      // saved record read-only instead of failing.
      const recRes = await fetch(`/api/sessions/${id}`);
      const rec = await recRes.json();
      if (!recRes.ok) throw new Error(rec.detail || recRes.statusText);
      return openRecordOnly(rec);
    }
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.detail || res.statusText);
    state.sessionId = payload.session_id;
    state.canUndo = payload.can_undo;
    state.imported = false;
    $("welcome").classList.add("hidden");
    $("main").classList.remove("hidden");
    setDraftBadge(payload.stats && payload.stats.evidence_based === false);
    applySession(payload);
    loadSessionMeta(id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) {
    alert(`Couldn't open session: ${err.message}`);
  }
}

function resumeFromRecord(rec) {
  return { name: rec.candidate_name, contact: {}, sections: rec.sections || [] };
}

function traceFromRecord(rec) {
  const matches = (rec.requirements || []).map((r) => ({
    requirement: r.requirement,
    status: r.status === "matched" ? "matched" : "gap",
    best_distance: null,
    query_trace: [r.requirement],
    hits: [],
    retrieval_source: null,
  }));
  return {
    jd: { title: rec.jd_title, company: rec.company },
    stats: rec.stats || {},
    matches,
    verification: { verdicts: rec.verdicts || [], dropped: rec.dropped || [] },
  };
}

function openRecordOnly(rec) {
  state.sessionId = rec.session_id;
  state.canUndo = false;
  state.imported = true;
  state.transcript = [];
  $("welcome").classList.add("hidden");
  $("main").classList.remove("hidden");
  setDraftBadge(rec.evidence_based === false);
  $("chat-thread").innerHTML = '<div class="chat-empty">This session was imported from a saved record (read-only). Run a new job to chat, refine and re-render.</div>';
  $("chat-input").disabled = true;
  $("chat-input").placeholder = "Imported session — run a new job to chat & edit.";
  $("chat-send").disabled = true;
  renderTrace(traceFromRecord(rec));
  renderResume(resumeFromRecord(rec));
  const empty = $("pdf-empty");
  empty.classList.remove("hidden");
  empty.textContent = "PDF unavailable — this session was imported from a saved record.";
  $("pdf-fallback").classList.add("hidden");
  $("pdf-canvas").innerHTML = "";
  loadSessionMeta(rec.session_id);
  window.scrollTo({ top: 0, behavior: "smooth" });
  syncBar();
}

/* ---------- progress & confidence tracking (Session tab) ---------- */

async function loadSessionMeta(id) {
  try {
    const res = await fetch(`/api/sessions/${id}`);
    const rec = await res.json();
    if (!res.ok) throw new Error(rec.detail || res.statusText);
    renderSessionTab(rec);
  } catch (err) {
    $("tab-session").innerHTML = `<div class="session-pad"><div class="empty-note">${esc(err.message)}</div></div>`;
  }
}

function renderSessionTab(rec) {
  const statusOpts = [["draft", "Draft"], ["verified", "Verified"], ["refined", "Refined"], ["needs_work", "Needs work"]]
    .map(([v, l]) => `<option value="${v}"${rec.status === v ? " selected" : ""}>${l}</option>`).join("");
  const progress = Math.max(0, Math.min(100, rec.progress || 0));
  $("tab-session").innerHTML = `<div class="session-pad">
    <div class="session-card">
      <div class="session-card-title">${esc(rec.jd_title || "Untitled JD")}${rec.company ? " @ " + esc(rec.company) : ""}</div>
      <div class="session-card-meta"><code>${esc(rec.session_id)}</code> · created ${esc((rec.created_at || "").replace("T", " ").slice(0, 16))}</div>
      <div class="session-card-meta">Mode: ${rec.evidence_based ? "evidence-based" : "quick draft"} · Difficulty: <span class="chip chip-${esc(rec.difficulty)}">${esc(rec.difficulty)}</span></div>
    </div>
    <div class="session-card">
      <div class="session-card-title">Progress &amp; status</div>
      <div class="session-row-bar"><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div><span class="progress-label">${progress}% ready</span></div>
      <label class="session-label">Progress status
        <select id="ses-status" class="sessions-filter">${statusOpts}</select>
      </label>
    </div>
    <div class="session-card">
      <div class="session-card-title">Confidence</div>
      <div class="session-conf-row">
        <input id="ses-confidence" type="range" min="0" max="100" step="5" value="${rec.confidence ?? 0}">
        <span id="ses-confidence-val" class="session-conf-val">${rec.confidence ?? 0}/100</span>
      </div>
      <label class="session-label">Note
        <textarea id="ses-note" rows="3" placeholder="e.g. Strong on backend skills; needs more distributed-systems evidence…">${esc(rec.note || "")}</textarea>
      </label>
      <button id="ses-save" class="btn btn-primary">Save progress &amp; confidence</button>
      <p id="ses-saved" class="session-saved hidden">Saved ✓</p>
    </div>
    <div class="session-card">
      <div class="session-card-title">Review packet</div>
      <p class="session-card-meta">Download a structured review packet: tailored resume changes (incl. education &amp; career), supporting evidence, weak areas, key questions, and recommended next actions.</p>
      <a class="btn btn-primary" href="/api/sessions/${esc(rec.session_id)}/packet" download>Download review packet</a>
    </div>
  </div>`;

  const range = $("ses-confidence");
  const val = $("ses-confidence-val");
  range.addEventListener("input", () => { val.textContent = range.value + "/100"; });
  $("ses-save").addEventListener("click", saveSessionStatus);
}

async function saveSessionStatus() {
  const btn = $("ses-save");
  btn.disabled = true;
  try {
    await api(`/api/sessions/${state.sessionId}/status`, {
      status: $("ses-status").value,
      confidence: parseInt($("ses-confidence").value, 10) || 0,
      note: $("ses-note").value.trim(),
    });
    const saved = $("ses-saved");
    saved.classList.remove("hidden");
    setTimeout(() => saved.classList.add("hidden"), 2200);
    loadSessions();
  } finally {
    btn.disabled = false;
  }
}

function bindSessionsControls() {
  const debounce = (fn, ms) => { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; };
  $("sessions-q").addEventListener("input", debounce(loadSessions, 250));
  for (const id of ["sessions-status", "sessions-topic", "sessions-source", "sessions-difficulty"]) {
    $(id).addEventListener("change", loadSessions);
  }
  $("sessions-reset").addEventListener("click", () => {
    $("sessions-q").value = "";
    for (const id of ["sessions-status", "sessions-topic", "sessions-source", "sessions-difficulty"]) $(id).value = "";
    loadSessions();
    $("sessions-q").focus();
  });
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
$("btn-packet").addEventListener("click", () => { if (state.sessionId) window.open(`/api/sessions/${state.sessionId}/packet`, "_blank"); });
$("btn-sessions").addEventListener("click", () => {
  loadSessions();
  document.getElementById("sessions-panel").scrollIntoView({ behavior: "smooth", block: "start" });
});
bindSessionsControls();
loadSessions();  // initial empty-state render

/* keep topbar buttons in sync with session state.
   Called only from narrow, explicit points (after run/chat/undo, and a slow
   poll) — NOT via a MutationObserver on the whole body, which can starve the
   main thread in embedded webviews when every attribute change re-arms it. */
const syncBar = () => {
  $("btn-undo").disabled = !state.canUndo || state.busy;
  $("btn-export-pdf").disabled = !state.sessionId || state.imported;
  $("btn-export-tex").disabled = !state.sessionId || state.imported;
  $("btn-packet").disabled = !state.sessionId;
};
setInterval(syncBar, 2000);

function showError(msg) { const el = $("run-error"); el.textContent = msg; el.classList.remove("hidden"); }
function hideError() { $("run-error").classList.add("hidden"); }
