// Simulated Alexa+ front end: plain JS, no build step. Talks only to the simulator's
// /api routes; the simulator is the MCP client.
"use strict";

const BUDGET_MS = 500; // Alexa+ round-trip budget per tool call
const $ = (id) => document.getElementById(id);
const els = {
  conversation: $("conversation"), screen: document.querySelector(".screen"), state: $("state-label"),
  form: $("ask-form"), input: $("text-input"), mic: $("mic-btn"), mute: $("mute-btn"), hint: $("hint"),
  trace: $("trace"), memory: $("memory"), memoryRaw: $("memory-raw"), memMeta: $("mem-meta"),
  brainChip: $("brain-chip"), serverChip: $("server-chip"), reset: $("reset-btn"), clock: $("clock"),
  linkChip: $("link-chip"), linkBtn: $("link-btn"), unlinkBtn: $("unlink-btn"), driveLink: $("drive-link"),
  exportBtn: $("export-btn"), exportBox: $("export-box"), exportPreviewBtn: $("export-preview-btn"),
  exportDownload: $("export-download"), exportCopy: $("export-copy"), exportStatus: $("export-status"),
  exportPreview: $("export-preview"), exportHistory: $("export-history"),
};

let sessionId = newSessionId();
let busy = false;
let lastMemory = null; // id -> serialized record, for change highlighting

function newSessionId() {
  return (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random();
}

// Tiny DOM helper: el("div", {class: "x"}, "text", child...) - text is never parsed as HTML.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v; else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

function store(kind, key, value) {
  try {
    const s = kind === "session" ? sessionStorage : localStorage;
    if (value === undefined) return s.getItem(key);
    if (value === null) s.removeItem(key); else s.setItem(key, value);
  } catch { return null; }
}

// ---------------------------------------------------------------- device state
function setState(state) {
  els.screen.classList.remove("listening", "thinking", "speaking");
  if (state !== "idle") els.screen.classList.add(state);
  els.state.textContent = { idle: "Ready", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" }[state];
}

function bubble(kind, text) {
  const b = el("div", { class: `bubble ${kind}` }, text);
  els.conversation.append(b);
  els.conversation.scrollTop = els.conversation.scrollHeight;
  return b;
}

function tickClock() {
  els.clock.textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// ---------------------------------------------------------------- speech output
let muted = store("local", "sim-muted") === "1";
let voice = null;

function pickVoice() {
  if (!("speechSynthesis" in window)) return;
  const voices = speechSynthesis.getVoices().filter((v) => v.lang && v.lang.startsWith("en"));
  voice = voices.find((v) => /Google US English|Samantha|Aria|Jenny/.test(v.name))
    || voices.find((v) => v.lang === "en-US") || voices[0] || null;
}

function renderMute() {
  els.mute.textContent = muted ? "🔇" : "🔊";
  els.mute.setAttribute("aria-pressed", String(muted));
  els.mute.title = muted ? "Spoken replies are off" : "Mute spoken replies";
}

function speak(text) {
  if (muted || !("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel(); // Chrome queues utterances; never let replies pile up
  const u = new SpeechSynthesisUtterance(text);
  if (voice) u.voice = voice;
  u.lang = (voice && voice.lang) || "en-US";
  u.onstart = () => setState("speaking");
  u.onend = u.onerror = () => { if (!busy && !listening) setState("idle"); };
  speechSynthesis.speak(u);
}

// ---------------------------------------------------------------- speech input
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;
let interim = null;

function setupRecognition() {
  if (!SR) {
    els.mic.disabled = true;
    els.mic.title = "Speech recognition isn't available in this browser. Type instead (Chrome supports it).";
    return;
  }
  recognition = new SR();
  recognition.lang = "en-US";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => { listening = true; els.mic.classList.add("on"); setState("listening"); hint(""); };
  recognition.onresult = (event) => {
    let text = "", final = false;
    for (let i = event.resultIndex; i < event.results.length; i++) {
      text += event.results[i][0].transcript;
      final = final || event.results[i].isFinal;
    }
    if (!interim) interim = bubble("user interim", "");
    interim.textContent = text;
    if (final) {
      interim.remove(); interim = null;
      send(text);
    }
  };
  recognition.onerror = (event) => {
    const messages = {
      "not-allowed": "Microphone access is blocked. Allow it in the address bar, or type instead.",
      "service-not-allowed": "Microphone access is blocked. Allow it in the address bar, or type instead.",
      "network": "Chrome's speech service couldn't be reached. Type instead.",
      "audio-capture": "No microphone found. Type instead.",
      "no-speech": "I didn't hear anything. Tap the mic and try again.",
    };
    hint(messages[event.error] || `Speech recognition stopped (${event.error}).`);
  };
  recognition.onend = () => {
    listening = false;
    els.mic.classList.remove("on");
    if (interim) { interim.remove(); interim = null; }
    if (!busy) setState("idle");
  };
}

function toggleMic() {
  if (!recognition || busy) return;
  if (listening) { recognition.stop(); return; }
  if ("speechSynthesis" in window) speechSynthesis.cancel(); // don't listen to ourselves
  try { recognition.start(); } catch { /* already starting */ }
}

function hint(text) { els.hint.textContent = text; }

// ---------------------------------------------------------------- talking to the simulator
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (res.status === 401 && data && data.gate) location.href = "/gate";  // passcode session expired
  return { ok: res.ok, status: res.status, data: data || {} };
}

async function send(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  busy = true;
  els.input.value = "";
  bubble("user", text);
  setState("thinking");
  try {
    const { ok, data } = await api("/api/utterance", {
      method: "POST", body: JSON.stringify({ session_id: sessionId, text }),
    });
    if (!ok) {
      bubble("error", data.error || "Something went wrong.");
      return;
    }
    bubble(data.error ? "error" : "alexa", data.reply_text);
    renderTurn(text, data);
    const exported = (data.trace || []).find((s) => s.tool === "export_memory" && s.file);
    if (exported) showExport({ ...exported.result, markdown: exported.file }, exported.arguments || {});  // voice export lands in the panel
    busy = false;
    if (!data.error) speak(data.reply_text);
    await loadMemory(true);
  } catch (err) {
    bubble("error", "The simulator isn't reachable. Is it still running?");
  } finally {
    busy = false;
    if (!listening && !(window.speechSynthesis && speechSynthesis.speaking)) setState("idle");
  }
}

// ---------------------------------------------------------------- under the hood
function renderTurn(utterance, data) {
  const empty = els.trace.querySelector(".empty");
  if (empty) empty.remove();
  const turn = el("article", { class: "turn" + (data.error ? " error" : "") },
    el("div", { class: "turn-head" },
      el("span", { class: "turn-utterance" }, `“${utterance}”`),
      el("span", { class: "model-chip", title: "Time spent in the model (brain), separate from MCP" },
        `brain ${fmtMs(data.model_latency_ms)}`)));

  if (!data.trace.length) turn.append(el("p", { class: "empty" }, "No MCP tool calls for this turn."));
  for (const call of data.trace) {
    if (call.kind === "skill") {  // Agent Skill loaded or read: local, no MCP round trip
      turn.append(el("div", { class: "call skill" },
        el("div", { class: "call-name" }, call.tool),
        call.arguments && call.arguments.path ? el("code", { class: "call-args" }, call.arguments.path) : "",
        el("div", { class: "budget-label" }, "Agent Skill · local, no MCP call")));
      continue;
    }
    const ms = call.mcp_latency_ms;
    const pct = Math.min(100, (ms / BUDGET_MS) * 100);
    const level = ms >= BUDGET_MS ? "bad" : ms >= BUDGET_MS * 0.6 ? "warn" : "";
    turn.append(el("div", { class: "call" },
      el("div", { class: "call-name" }, call.tool),
      el("code", { class: "call-args" }, JSON.stringify(call.arguments)),
      el("div", { class: "budget", title: `${fmtMs(ms)} of the ${BUDGET_MS} ms budget` },
        el("div", { class: "budget-track" },
          el("div", { class: `budget-fill ${level}`, style: `width:${pct.toFixed(2)}%` })),
        el("span", { class: "budget-label" }, `${fmtMs(ms)} / ${BUDGET_MS} ms`)),
      el("details", { class: "result" },
        el("summary", {}, "Result"),
        el("pre", {}, JSON.stringify(call.result, null, 2)))));
  }
  turn.append(el("div", { class: "turn-reply" }, `Reply: ${data.reply_text}`));
  els.trace.append(turn);
  els.trace.scrollTop = els.trace.scrollHeight;
}

function fmtMs(ms) {
  if (ms === undefined || ms === null) return "–";
  return ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`;
}

// ---------------------------------------------------------------- memory file
function parseMemory(markdown) {
  // Mirrors memory/schema.py: "## ID" headings, "- field: value" lines.
  const records = [];
  let current = null;
  for (const raw of markdown.split("\n")) {
    const line = raw.trim();
    const head = line.match(/^##\s+(\S+)\s*$/);
    const field = line.match(/^-\s+([a-z_]+):\s*(.*)$/);
    if (head) { current = { id: head[1] }; records.push(current); }
    else if (current && field) current[field[1]] = field[2];
  }
  return records.filter((r) => r.fact);
}

async function loadMemory(highlight) {
  const { ok, data } = await api("/api/memory");
  if (!ok) {
    els.memMeta.textContent = data.error || "memory file unavailable";
    if (data.linked === false) {
      els.driveLink.hidden = true;
      lastMemory = null;
      els.memoryRaw.textContent = "";
      els.memory.replaceChildren(el("p", { class: "empty" },
        "Link your account to let this assistant read your memory file."));
    }
    return;
  }
  const where = data.backend === "google-drive" ? "Google Drive" : "local file";
  els.memMeta.textContent = `memory://memories.md (${where}) · read over MCP in ${fmtMs(data.mcp_latency_ms)}`;
  els.driveLink.hidden = !data.web_view_link;
  if (data.web_view_link) els.driveLink.href = data.web_view_link;
  els.memoryRaw.textContent = data.markdown;
  const records = parseMemory(data.markdown);
  const next = Object.fromEntries(records.map((r) => [r.id, JSON.stringify(r)]));
  const changed = new Map();
  if (highlight && lastMemory) {
    for (const r of records) {
      if (!(r.id in lastMemory)) changed.set(r.id, "new");
      else if (lastMemory[r.id] !== next[r.id]) changed.set(r.id, "changed");
    }
  }
  lastMemory = next;
  renderMemory(records, changed);
}

function renderMemory(records, changed) {
  els.memory.replaceChildren();
  if (!records.length) els.memory.append(el("p", { class: "empty" }, "The memory file is empty."));
  let lastChanged = null; // new records land at the end of the file; show the latest change
  for (const r of records) {
    const status = r.status || "pending";
    const meta = [
      r.category,
      r.source_type === "user_explicit" ? "confirmed by you" : "not confirmed yet",
      r.review_after && `review after ${r.review_after}`,
      r.supersedes && `replaces ${r.supersedes}`,
      r.superseded_by && `replaced by ${r.superseded_by}`,
      r.updated_at && `updated ${r.updated_at.replace("T", " ").replace("+00:00", " UTC")}`,
    ].filter(Boolean).join(" · ");
    const tag = changed.get(r.id);
    const card = el("div", { class: `record status-${status}` + (tag ? " flash" : "") },
      el("div", { class: "record-head" },
        el("span", { class: "record-id" }, r.id),
        el("span", { class: `badge status-${status}` }, status),
        tag ? el("span", { class: "change-tag" }, tag === "new" ? "new" : "just changed") : ""),
      el("div", { class: "record-fact" }, r.fact),
      el("div", { class: "record-meta" }, meta));
    if (tag) lastChanged = card;
    els.memory.append(card);
  }
  if (lastChanged) lastChanged.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function setMemoryView(view) {
  for (const b of document.querySelectorAll(".seg-btn")) {
    const active = b.dataset.view === view;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", String(active));
  }
  els.memory.hidden = view !== "cards";
  els.memoryRaw.hidden = view !== "raw";
}

// ---------------------------------------------------------------- status + reset
async function loadStatus() {
  const { data } = await api("/api/status");
  const brain = data.brain === "bedrock" ? `brain: Bedrock ${data.model_id || "(no model id)"}` : `brain: ${data.brain}`;
  els.brainChip.textContent = brain;
  els.brainChip.className = "chip " + (data.brain_error ? "bad" : "ok");
  els.brainChip.title = data.brain_error || "The model that plays Alexa";
  const account = data.account || {};
  els.linkBtn.hidden = !!account.linked || !account.configured;
  els.unlinkBtn.hidden = !account.linked;
  if (account.linked) {
    const canWrite = (account.scopes || []).includes("memory.write");
    els.linkChip.textContent = canWrite ? "account: linked" : "account: linked (read only)";
    els.linkChip.className = "chip ok";
    els.linkChip.title = `Scopes: ${(account.scopes || []).join(", ")}. Tokens stay in the simulator, never in this page.`;
  } else {
    els.linkChip.textContent = account.configured ? "account: not linked" : "account: linking not configured";
    els.linkChip.className = "chip bad";
    els.linkChip.title = account.configured ? "Link your account to use your memory." : "Set OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET.";
  }
  if (data.server && data.server.tools) {
    els.serverChip.textContent = `server: ${data.server.name} · ${data.server.tools.length} tools`;
    els.serverChip.className = "chip ok";
    els.serverChip.title = data.server_url;
  } else if (data.server) {
    els.serverChip.textContent = "server: reachable";
    els.serverChip.className = "chip ok";
    els.serverChip.title = data.server_url;
  } else {
    els.serverChip.textContent = "server: not reachable";
    els.serverChip.className = "chip bad";
    els.serverChip.title = data.server_error || "";
  }
  if (data.brain_error) bubble("error", data.brain_error);
}

async function resetDemo() {
  if (busy) return;
  const token = store("session", "sim-admin-token");
  let res = await api("/api/reset", {
    method: "POST", body: JSON.stringify({ session_id: sessionId }),
    headers: token ? { "X-Admin-Token": token } : {},
  });
  if (res.status === 403) {
    const entered = window.prompt("Admin token for Reset demo:");
    if (!entered) return;
    store("session", "sim-admin-token", entered);
    res = await api("/api/reset", {
      method: "POST", body: JSON.stringify({ session_id: sessionId }), headers: { "X-Admin-Token": entered },
    });
  }
  if (!res.ok) { hint(res.data.error || "Reset failed."); return; }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  sessionId = newSessionId();
  els.conversation.replaceChildren(el("div", { class: "bubble system" }, "Demo data restored. Try: “When is my sister's birthday?”"));
  els.trace.replaceChildren(el("p", { class: "empty" }, "Every MCP tool call made for your request shows up here with its arguments, result and round-trip time."));
  lastMemory = null;
  hint("");
  await loadMemory(false);
}

// ---------------------------------------------------------------- wire up
els.form.addEventListener("submit", (e) => { e.preventDefault(); send(els.input.value); });
els.mic.addEventListener("click", toggleMic);
els.mute.addEventListener("click", () => {
  muted = !muted;
  store("local", "sim-muted", muted ? "1" : "0");
  if (muted && "speechSynthesis" in window) speechSynthesis.cancel();
  renderMute();
});
els.reset.addEventListener("click", resetDemo);
for (const b of document.querySelectorAll(".seg-btn")) b.addEventListener("click", () => setMemoryView(b.dataset.view));
if ("speechSynthesis" in window) {
  pickVoice();
  speechSynthesis.addEventListener("voiceschanged", pickVoice); // Chrome loads voices asynchronously
}

renderMute();
setupRecognition();
tickClock();
setInterval(tickClock, 15000);
// ---------------------------------------------------------------- export
let exportMarkdown = "";

function exportChoices() {
  const cats = [...els.exportBox.querySelectorAll('input[name="cat"]:checked')].map((c) => c.value);
  return { categories: cats, include_history: els.exportHistory.checked };
}

function showExport(result, args) {
  els.exportBox.hidden = false;
  els.exportBtn.setAttribute("aria-expanded", "true");
  if (args && Array.isArray(args.categories) && args.categories.length) {
    for (const c of els.exportBox.querySelectorAll('input[name="cat"]')) c.checked = args.categories.includes(c.value);
  }
  if (args && "include_history" in args) els.exportHistory.checked = !!args.include_history;
  exportMarkdown = result.markdown;
  els.exportPreview.textContent = exportMarkdown;
  els.exportPreview.hidden = false;
  els.exportDownload.disabled = els.exportCopy.disabled = false;
  const topic = args && args.topic ? `, topic "${args.topic}"` : "";
  els.exportStatus.textContent = `${result.count} ${result.count === 1 ? "memory" : "memories"}${topic}`;
  els.exportBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function previewExport() {
  const choice = exportChoices();
  if (!choice.categories.length) { els.exportStatus.textContent = "Tick at least one category."; return; }
  els.exportStatus.textContent = "Preparing…";
  const { ok, data } = await api("/api/export", { method: "POST", body: JSON.stringify(choice) });
  if (!ok) { els.exportStatus.textContent = data.error || "Export failed."; return; }
  showExport(data, {});
}

function downloadExport() {
  const blob = new Blob([exportMarkdown], { type: "text/markdown" });
  const a = el("a", { href: URL.createObjectURL(blob),
    download: `portable-memory-export-${new Date().toISOString().slice(0, 10)}.md` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function copyExport() {
  const done = "Copied. Paste it into any AI assistant or message.";
  try {
    await navigator.clipboard.writeText(exportMarkdown);
    els.exportStatus.textContent = done;
    return;
  } catch { /* clipboard API blocked (permissions policy, embedded view): try the classic way */ }
  const area = el("textarea", { "aria-hidden": "true", style: "position:fixed;left:-9999px;top:0" });
  area.value = exportMarkdown;
  document.body.append(area);
  area.select();
  let copied = false;
  try { copied = document.execCommand("copy"); } catch { copied = false; }
  area.remove();
  if (copied) { els.exportStatus.textContent = done; return; }
  // last resort: select the preview so the user can press Cmd/Ctrl+C
  const range = document.createRange();
  range.selectNodeContents(els.exportPreview);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  els.exportStatus.textContent = "Copy is blocked here: the text is selected, press Cmd+C (or use Download).";
}

els.exportBtn.addEventListener("click", () => {
  const open = els.exportBox.hidden;
  els.exportBox.hidden = !open;
  els.exportBtn.setAttribute("aria-expanded", String(open));
});
els.exportPreviewBtn.addEventListener("click", previewExport);
els.exportDownload.addEventListener("click", downloadExport);
els.exportCopy.addEventListener("click", copyExport);
// changing the choice invalidates the preview
els.exportBox.addEventListener("change", () => { els.exportDownload.disabled = els.exportCopy.disabled = true; });

async function unlinkAccount() {
  const token = store("session", "sim-admin-token");
  const res = await api("/api/unlink", { method: "POST", headers: token ? { "X-Admin-Token": token } : {} });
  if (!res.ok) { hint(res.data.error || "Unlink failed."); return; }
  sessionId = newSessionId();
  bubble("system", "Account unlinked. Link it again to use your memory.");
  await loadStatus();
  await loadMemory(false);
}
els.unlinkBtn.addEventListener("click", unlinkAccount);

// Coming back from the server's sign-in page: say how linking went, then tidy the URL.
(function linkResult() {
  const params = new URLSearchParams(location.search);
  if (params.has("linked")) bubble("system", "Account linked. Try: “When is my sister's birthday?”");
  if (params.has("link_error")) bubble("error", params.get("link_error"));
  if (params.has("linked") || params.has("link_error")) history.replaceState(null, "", "/");
})();

loadStatus();
loadMemory(false);
