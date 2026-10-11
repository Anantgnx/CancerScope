// Live pipeline page: polls /api/live, /api/events, /api/queue, /api/system and animates the run.

const STAGES = ["read", "tissue", "tile", "embed", "classify", "regions", "urgency", "report"];
const PRIO = { "Macrometastasis": "prio-stat", "Micrometastasis": "prio-urgent", "Isolated tumor cells": "prio-priority", "Negative": "prio-routine" };
const SHORT = { "Macrometastasis": "Macromet", "Micrometastasis": "Micromet", "Isolated tumor cells": "ITC", "Negative": "Negative" };

let live = { run: "idle" };
let lastSeq = 0;
let currentSlide = null;
let lastReport = null;
let injectRequested = false;   // stays true until the next reset/start, so the button can't be pressed twice
const cards = {};

// ---------- controls ----------
async function post(url) { const r = await fetch(url, { method: "POST" }); return r.json().catch(() => ({})); }

let preparing = false, prepStartedAt = 0;   // between pressing Start and the run reporting in

function setPreparing(on) {
  preparing = on;
  const btn = document.getElementById("btn-start");
  btn.classList.toggle("is-busy", on);
  btn.innerHTML = on ? `<i data-lucide="loader-circle"></i><span>Preparing…</span>` : `<i data-lucide="play"></i><span>Start tray</span>`;
  lucide.createIcons();
  ["btn-start", "btn-inject", "btn-ff", "btn-reset"].forEach(id => { document.getElementById(id).disabled = on || id === "btn-inject" || id === "btn-ff"; });
  if (on) {
    document.getElementById("k-run").innerHTML = `<i class="state-dot is-running"></i>Preparing`;
    document.getElementById("now-stage").textContent = "Preparing: starting the pipeline on the GX10…";
  }
}

async function startTray() {
  if (preparing) return;
  injectRequested = false;
  clearLog();
  prepStartedAt = Date.now();
  setPreparing(true);
  addLocalEvent("run", "Start pressed: preparing the run");
  await post("/api/demo/reset");
  await post("/api/demo/start");
  tick();
}
async function resetTray() { await post("/api/demo/reset"); clearLog(); currentSlide = null; injectRequested = false; tick(); }
async function injectUrgent() {
  injectRequested = true;
  document.getElementById("btn-inject").disabled = true;
  const r = await post("/api/demo/inject");
  addLocalEvent("inject", `Requested: ${r.slide_id} arriving from the scanner (${r.mode || "queued"})`, r.slide_id);
}

let ffRequested = false;
async function fastForward() {
  ffRequested = true;
  document.getElementById("btn-ff").disabled = true;
  document.getElementById("btn-inject").disabled = true;
  await post("/api/demo/fastforward");
  tick();
}

// ---------- polling ----------
async function tick() {
  try {
    live = await (await fetch("/api/live")).json();
    renderRun();
    const ev = await (await fetch(`/api/events?since=${lastSeq}`)).json();
    if (ev.events.length && ev.events[0].seq < lastSeq) { clearLog(); }   // new run started
    ev.events.forEach(addEvent);
  } catch {}
}
async function tickQueue() {
  try { renderQueue((await (await fetch("/api/queue")).json()).slides); } catch {}
}
async function tickSystem() {
  try {
    const s = await (await fetch("/api/system")).json();
    document.getElementById("k-net").innerHTML = s.online
      ? `<i class="state-dot is-ok"></i>Online` : `<i class="state-dot is-running"></i>Offline · still running`;
    document.getElementById("k-gpu").textContent = s.gpu && s.gpu.util != null ? `${Math.round(s.gpu.util)}%` : "–";
  } catch {}
}

// ---------- run state ----------
function fmtTime(sec) { sec = Math.max(0, Math.round(sec || 0)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`; }

function renderRun() {
  if (preparing) {
    const reported = live.run === "running" && live.started_at * 1000 >= prepStartedAt - 3000;
    if (reported || Date.now() - prepStartedAt > 120000) setPreparing(false);
    else return;   // keep the "Preparing…" view until the run reports in
  }
  const running = live.run === "running";
  const done = live.run === "done";
  const state = running ? ["is-running", "Running"] : done ? ["is-ok", "Complete"] : live.run === "stopped" ? ["is-idle", "Stopped"] : ["is-idle", "Idle"];
  document.getElementById("k-run").innerHTML = `<i class="state-dot ${state[0]}"></i>${state[1]}`;
  document.getElementById("k-elapsed").textContent = fmtTime(live.elapsed_sec);
  document.getElementById("k-slides").textContent = `${live.slides_done || 0} / ${live.slides_total || 0}`;
  document.getElementById("k-rate").textContent = running && live.tiles_per_sec ? Math.round(live.tiles_per_sec) : "–";
  document.getElementById("k-gb").textContent = `${(live.gb_done || 0).toFixed(1)} GB`;

  document.getElementById("btn-start").disabled = running;
  document.getElementById("btn-start").querySelector("span").textContent = done ? "Run again" : "Start tray";
  const injected = cards[live.holdout] && cards[live.holdout].dataset.status !== "expected";
  document.getElementById("btn-inject").disabled = !running || !live.holdout_available || injected || injectRequested || ffRequested;
  document.getElementById("btn-ff").disabled = !running || ffRequested;
  if (!running) ffRequested = false;
  document.getElementById("btn-review").classList.toggle("btn-accent", done);

  renderGraph(running, done);
  renderNow(running, done);
  renderDone(done);
}

function renderGraph(running, done) {
  const cur = STAGES.indexOf(live.stage);
  document.getElementById("graph-slide").textContent = live.slide ? `${live.slide} · stage ${cur + 1} of ${STAGES.length}`
    : done ? "tray complete" : running ? "loading models onto the GPU" : "waiting for a tray";
  document.querySelectorAll(".node").forEach(n => {
    const i = STAGES.indexOf(n.dataset.stage);
    let st = "is-idle";
    if (done) st = "is-done";
    else if (running && cur >= 0) st = i < cur ? "is-done" : i === cur ? "is-running" : "is-idle";
    if (running && live.stage === "embed" && n.dataset.stage === "classify") st = "is-running";   // scored as tiles stream
    n.className = `node ${n.classList.contains("is-gpu") ? "is-gpu " : ""}${st}`;
    n.querySelector(".node-state").textContent = st === "is-running" ? "Running" : st === "is-done" ? "Done" : "Idle";
  });
  const m = (k, v) => { document.querySelector(`[data-m="${k}"]`).textContent = v; };
  m("read", live.slide_gb ? `${live.slide_gb.toFixed(2)} GB` : "–");
  m("tissue", live.tissue_mm2 != null ? `${live.tissue_mm2} mm²` : "–");
  m("tile", live.tiles_total ? `${live.tiles_total.toLocaleString()} tiles` : "–");
  m("embed", live.tiles_total ? `${(live.tiles_done || 0).toLocaleString()} · ${Math.round(live.tiles_per_sec || 0)}/s` : "–");
  m("classify", live.tiles_done ? `${live.tiles_done.toLocaleString()} scored` : "–");
  m("regions", live.regions != null ? `${live.regions} foci` : "–");
  const last = (live.results || []).slice(-1)[0];
  m("urgency", last ? `${SHORT[last.category]} ${last.urgency_score.toFixed(1)}` : "–");
  m("report", lastReport || "–");
  drawEdges();
}

function drawEdges() {
  const svg = document.getElementById("graph-edges");
  const box = document.getElementById("graph").getBoundingClientRect();
  const nodes = Object.fromEntries([...document.querySelectorAll(".node")].map(n => [n.dataset.stage, n]));
  let html = "";
  for (let i = 0; i < STAGES.length - 1; i++) {
    const a = nodes[STAGES[i]].getBoundingClientRect(), b = nodes[STAGES[i + 1]].getBoundingClientRect();
    const sa = nodes[STAGES[i]].classList, sb = nodes[STAGES[i + 1]].classList;
    const cls = sb.contains("is-running") || (sa.contains("is-running") && sb.contains("is-running")) ? "is-active"
              : sa.contains("is-done") && (sb.contains("is-done") || sb.contains("is-running")) ? "is-done" : "";
    let d;
    if (Math.abs(a.top - b.top) < 5) {   // same row
      const y = a.top + a.height / 2 - box.top;
      d = `M${a.right - box.left},${y} L${b.left - box.left},${y}`;
    } else {                             // row change: down from UNI2-h, back to the start of row 2
      const x1 = a.left + a.width / 2 - box.left, y1 = a.bottom - box.top;
      const x2 = b.left + b.width / 2 - box.left, y2 = b.top - box.top, ym = (y1 + y2) / 2;
      d = `M${x1},${y1} L${x1},${ym} L${x2},${ym} L${x2},${y2}`;
    }
    html += `<path class="edge ${cls}" d="${d}"/>`;
  }
  svg.innerHTML = html;
}

function renderNow(running, done) {
  const thumb = document.getElementById("now-thumb"), heat = document.getElementById("now-heat");
  const slide = live.slide;
  document.getElementById("now-empty").style.display = slide || done ? "none" : "";
  if (slide && slide !== currentSlide) {
    currentSlide = slide;
    thumb.src = `/api/slide/${slide}/thumbnail?t=${Date.now()}`;
    heat.removeAttribute("src");
    if (window.gsap) gsap.from(".now-visual, .now-info > *", { opacity: 0, y: 6, duration: 0.35, stagger: 0.03 });
  }
  const scoring = ["embed", "classify", "regions", "urgency", "report"].includes(live.stage);
  if (slide && running && scoring && live.tiles_done) heat.src = `/api/slide/${slide}/heatmap_live?t=${Date.now()}`;
  if (!slide) {
    // finished: show the most urgent case instead of an empty box
    const top = done ? [...(live.results || [])].sort((a, b) => b.urgency_score - a.urgency_score)[0] : null;
    if (top && currentSlide !== `top:${top.slide_id}`) {
      currentSlide = `top:${top.slide_id}`;
      thumb.src = `/api/slide/${top.slide_id}/thumbnail`;
      heat.src = `/api/slide/${top.slide_id}/heatmap`;
    } else if (!top) { thumb.removeAttribute("src"); heat.removeAttribute("src"); currentSlide = null; }
  }

  const stageText = {
    read: "Reading the gigapixel slide image", tissue: "Finding tissue, skipping blank glass", tile: "Cutting tissue into 10× tiles",
    embed: "UNI2-h embedding tiles on the GPU, classifier scoring as they stream", classify: "Scoring every tile for tumor",
    regions: "Grouping tumor tiles into foci", urgency: "Ranking by clinical size rules", report: "Nemotron drafting the report",
  };
  const topId = (currentSlide || "").startsWith("top:") ? currentSlide.slice(4) : null;
  document.getElementById("now-id").textContent = slide || (done ? (topId ? `#1 · ${topId}` : "Tray complete") : "–");
  document.getElementById("now-stage").textContent = slide ? stageText[live.stage] || "" :
    running ? "Loading UNI2-h (681M parameters) and the classifier onto the GPU…" :
    done ? "Tray complete. This is the most urgent case: open the dashboard to review it first." : "Press Start to run this morning's tray.";
  const pct = live.tiles_total ? 100 * (live.tiles_done || 0) / live.tiles_total : 0;
  document.getElementById("now-bar").style.width = `${slide ? pct : done ? 100 : 0}%`;
  document.getElementById("now-tiles").textContent = live.tiles_total ? `${(live.tiles_done || 0).toLocaleString()} / ${live.tiles_total.toLocaleString()}` : "–";
  document.getElementById("now-rate").textContent = running && live.tiles_per_sec ? `${Math.round(live.tiles_per_sec)} tiles/s` : "–";
  document.getElementById("now-first").textContent = live.first_urgent_sec != null ? `after ${fmtTime(live.first_urgent_sec)}` : "–";
}

function renderDone(done) {
  const el = document.getElementById("done-banner");
  if (!done) { el.innerHTML = ""; return; }
  const urgent = (live.results || []).filter(r => r.category === "Macrometastasis" || r.category === "Micrometastasis").length;
  const pre = (live.results || []).filter(r => r.preloaded).length;
  const lineOne = pre
    ? `${live.slides_done} slides · ${live.slides_done - pre} analyzed live · ${pre} preloaded`
    : `${live.slides_done} slides · ${(live.gb_done || 0).toFixed(1)} GB · ${fmtTime(live.elapsed_sec)}`;
  el.innerHTML = `<span class="label">Tray complete</span>
    <div class="done-line mono">${lineOne}</div>
    <div class="done-line">${urgent} urgent case${urgent === 1 ? "" : "s"} flagged · first after ${fmtTime(live.first_urgent_sec)} · 0 bytes uploaded</div>`;
}

// ---------- queue ----------
function renderQueue(slides) {
  const q = document.getElementById("queue");
  document.getElementById("queue-count").textContent = slides.length;
  slides.forEach(s => {
    let el = cards[s.slide_id];
    if (!el) {
      el = document.createElement("div");
      el.className = "queue-card";
      el.dataset.id = s.slide_id;
      el.innerHTML = `<img class="queue-thumb" alt=""><div class="queue-id">${s.slide_id}</div><div class="queue-score"></div>
        <div class="queue-detail"><span class="chip"></span><span class="measure"></span></div>`;
      el.onclick = () => { if (el.dataset.status === "done") location.href = `/app#${s.slide_id}`; };
      cards[s.slide_id] = el;
    }
    const done = s.status === "done";
    el.dataset.status = s.status;
    el.className = `queue-card ${done ? PRIO[s.category] || "" : ""} is-${s.status}`;
    const img = el.querySelector(".queue-thumb");
    if ((done || s.status === "processing") && !img.getAttribute("src")) img.src = `/api/slide/${s.slide_id}/thumbnail?t=${Date.now()}`;
    if (s.status === "queued" || s.status === "expected") img.removeAttribute("src");
    el.querySelector(".queue-score").innerHTML = done ? s.urgency_score.toFixed(1) : `<small>${s.status === "processing" ? "···" : "—"}</small>`;
    el.querySelector(".chip").textContent = done ? SHORT[s.category] || s.category : s.status === "processing" ? "Analyzing" : s.status === "error" ? "Error" : s.status === "expected" ? "On scanner" : s.next_up ? "Queued · next" : "Queued";
  });
  Object.keys(cards).filter(id => !slides.some(s => s.slide_id === id)).forEach(id => { cards[id].remove(); delete cards[id]; });
  const want = slides.map(s => s.slide_id), have = [...q.children].map(e => e.dataset.id);
  if (want.join() !== have.join()) want.forEach(id => q.appendChild(cards[id]));
}

// ---------- activity log ----------
function clearLog() { document.getElementById("log").innerHTML = ""; lastSeq = 0; lastReport = null; updateLogCount(); }
function updateLogCount() { document.getElementById("log-count").textContent = `${document.getElementById("log").children.length} events`; }

function addEvent(e) {
  lastSeq = Math.max(lastSeq, e.seq);
  if (e.kind === "report") lastReport = e.msg.split(" · ")[0].replace("Report drafted in ", "");
  appendLog(e.ts, e.kind, e.slide_id, e.msg, e.category, e.preloaded);
}
function addLocalEvent(kind, msg, slideId) { appendLog(Date.now() / 1000, kind, slideId, msg); }

function appendLog(ts, kind, slideId, msg, category, preloaded) {
  const log = document.getElementById("log");
  const row = document.createElement("div");
  row.className = `log-row kind-${kind} ${category ? PRIO[category] || "" : ""}${preloaded ? " is-preloaded" : ""}`;
  const t = new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
  row.innerHTML = `<span class="log-time mono">${t}</span><span class="log-slide mono">${slideId || ""}</span><span class="log-msg">${msg}</span>`;
  log.appendChild(row);
  while (log.children.length > 400) log.firstChild.remove();
  log.scrollTop = log.scrollHeight;
  updateLogCount();
}

// ---------- boot ----------
document.addEventListener("DOMContentLoaded", async () => {
  lucide.createIcons();
  window.addEventListener("resize", drawEdges);
  await Promise.all([tick(), tickQueue(), tickSystem()]);
  if (new URLSearchParams(location.search).get("autostart") === "1" && live.run !== "running") {
    history.replaceState(null, "", "/pipeline");
    startTray();
  }
  setInterval(tick, 700);
  setInterval(tickQueue, 1200);
  setInterval(tickSystem, 3000);
});
