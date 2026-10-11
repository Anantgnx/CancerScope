// CancerScope viewer. Emits only the classes/ids in app/static/CONTRACT.md; all styling lives in style.css.

const PRIORITY = {
  "Macrometastasis":      { cls: "prio-stat",     level: "STAT",     note: "Same-day pathologist review" },
  "Micrometastasis":      { cls: "prio-urgent",   level: "URGENT",   note: "Review ahead of routine cases" },
  "Isolated tumor cells": { cls: "prio-priority", level: "PRIORITY", note: "Review with attention to highlighted area" },
  "Negative":             { cls: "prio-routine",  level: "ROUTINE",  note: "Routine queue" },
};
const OUTLINE_COLOR = "#2ee6a8";
const SHORT = { "Macrometastasis": "Macromet", "Micrometastasis": "Micromet", "Isolated tumor cells": "ITC", "Negative": "Negative" };

let viewer = null;
let currentSlideId = null;
let heatmapLayer = null;
let heatmapOpacity = 0.65;
let heatmapVisible = true;
let outlineVisible = false;
let outlineGroup = null;
let current = null;          // metrics.json of the selected slide
const metricsCache = {};     // slide_id -> metrics.json
const cards = {};            // slide_id -> queue card element


let currentChatHistory = [];



document.addEventListener("DOMContentLoaded", () => {
  initViewer();
  document.getElementById("heatmap-opacity").addEventListener("input", (e) => {
    heatmapOpacity = e.target.value / 100;
    if (heatmapLayer && heatmapVisible) heatmapLayer.setOpacity(heatmapOpacity);
  });
  loadQueue();
  setInterval(loadQueue, 2000);  // live queue: the pipeline rewrites index.json as slides finish
  lucide.createIcons();
});

function initViewer() {
  viewer = OpenSeadragon({
    id: "viewer",
    prefixUrl: "/static/vendor/openseadragon/images/",
    showNavigationControl: false,
    showNavigator: true,
    navigatorPosition: "BOTTOM_RIGHT",
    navigatorSizeRatio: 0.15,
    navigatorAutoFade: false,
    animationTime: 0.7,
    blendTime: 0.15,
    springStiffness: 8,
    maxZoomPixelRatio: 2,
    minZoomImageRatio: 0.8,
    visibilityRatio: 0.5,
    gestureSettingsMouse: { clickToZoom: false },
  });
}

// created after the first slide opens; skips repositioning while no image is loaded (avoids NaN transforms)
function outlineLayer() {
  if (!outlineGroup) {
    const ov = viewer.svgOverlay();
    const resize = ov.resize.bind(ov);
    ov.resize = () => {
      const z = viewer.viewport.getZoom(true);
      const p = viewer.viewport.pixelFromPoint(new OpenSeadragon.Point(0, 0), true);
      if (viewer.world.getItemCount() && Number.isFinite(z) && z > 0 && Number.isFinite(p.x) && Number.isFinite(p.y)) resize();
    };
    outlineGroup = ov.node();
  }
  return outlineGroup;
}

// ---------- Queue ----------

async function loadQueue() {
  let data;
  try { data = await (await fetch("/api/queue")).json(); } catch { return; }
  const slides = data.slides || [];

  // metrics for finished slides (card details, stats); refetch when a slide finishes again
  await Promise.all(slides.filter(s => s.status === "done" && metricsCache[s.slide_id]?.urgency_score !== s.urgency_score)
    .map(async s => { metricsCache[s.slide_id] = await (await fetch(`/api/slide/${s.slide_id}`)).json(); }));

  const queueEl = document.getElementById("queue");
  slides.forEach(s => updateCard(cards[s.slide_id] ||= createCard(s.slide_id), s, metricsCache[s.slide_id]));
  Object.keys(cards).filter(id => !slides.some(s => s.slide_id === id)).forEach(id => { cards[id].remove(); delete cards[id]; });

  // only move cards when the order actually changes, so the re-sort animation means something
  const want = slides.map(s => s.slide_id);
  const have = [...queueEl.children].map(el => el.dataset.id);
  if (want.join() !== have.join()) want.forEach(id => queueEl.appendChild(cards[id]));

  updateStats(slides);
  if (!currentSlideId) {
    const first = slides.find(s => s.status === "done");
    if (first) selectSlide(first.slide_id);
  }
}

function createCard(id) {
  const el = document.createElement("div");
  el.className = "queue-card";
  el.dataset.id = id;
  el.innerHTML = `
    <img class="queue-thumb" alt="">
    <div class="queue-id">${id}</div>
    <div class="queue-score"></div>
    <div class="queue-detail"><span class="chip"></span><span class="measure"></span></div>
    <div class="queue-bar"><span></span></div>`;
  el.onclick = () => { if (el.dataset.status === "done") selectSlide(id); };
  return el;
}

function updateCard(el, s, m) {
  const done = s.status === "done";
  const p = done ? PRIORITY[s.category] || PRIORITY.Negative : null;
  el.dataset.status = s.status;
  el.className = ["queue-card", p ? p.cls : "", `is-${s.status}`, s.slide_id === currentSlideId ? "is-active" : ""].join(" ");

  const thumb = el.querySelector(".queue-thumb");
  if (done && !thumb.getAttribute("src")) thumb.src = `/api/slide/${s.slide_id}/thumbnail`;

  el.querySelector(".queue-score").innerHTML = done
    ? s.urgency_score.toFixed(1) : `<small>${s.status}</small>`;
  el.querySelector(".chip").textContent = done ? (SHORT[s.category] || s.category) : (s.status === "processing" ? "Analyzing…" : s.status === "expected" ? "On scanner" : "Queued");
  el.querySelector(".measure").textContent = done && m
    ? (m.num_tumor_regions ? `${m.largest_region_extent_mm.toFixed(2)} mm · ${m.num_tumor_regions} ${m.num_tumor_regions === 1 ? "focus" : "foci"}` : "No suspicious foci")
    : "";
  el.querySelector(".queue-bar > span").style.width = done ? `${Math.max(2, s.urgency_score * 10)}%` : "0%";
}

function updateStats(slides) {
  const done = slides.filter(s => s.status === "done");
  document.getElementById("stat-slides").textContent = `${done.length} / ${slides.length}`;
  document.getElementById("gb-counter").textContent = slides.reduce((a, s) => a + (parseFloat(s.gb_processed) || 0), 0).toFixed(1);
  document.getElementById("stat-urgent").textContent =
    done.filter(s => ["Macrometastasis", "Micrometastasis"].includes(s.category)).length;
  document.getElementById("queue-count").textContent = slides.length;
  const running = slides.some(s => s.status === "processing" || s.status === "queued");
  const state = running ? ["is-running", "Running"] : done.length ? ["is-ok", "Complete"] : ["is-idle", "Idle"];
  document.getElementById("stat-pipeline").innerHTML = `<i class="state-dot ${state[0]}"></i>${state[1]}`;
}

// ---------- Viewer ----------

function openSource(src) {
  return new Promise(resolve => {
    const ok = () => { done(); resolve(true); };
    const fail = () => { done(); resolve(false); };
    const done = () => { viewer.removeHandler("open", ok); viewer.removeHandler("open-failed", fail); };
    viewer.addHandler("open", ok);
    viewer.addHandler("open-failed", fail);
    viewer.open(src);
  });
}

async function selectSlide(slideId) {
  currentSlideId = slideId;
  Object.values(cards).forEach(c => c.classList.toggle("is-active", c.dataset.id === slideId));
  document.getElementById("viewer-empty").classList.add("is-hidden");

  const m = current = metricsCache[slideId] || await (await fetch(`/api/slide/${slideId}`)).json();
  renderCase(m);

  // full-resolution slide tiles if the server offers them, else the thumbnail
  heatmapLayer = null;
  const tiled = await openSource(`/slide/${slideId}.dzi`);
  if (!tiled) await openSource({ type: "image", url: `/api/slide/${slideId}/thumbnail` });
  if (currentSlideId !== slideId) return;  // user clicked another slide meanwhile

  // Do not auto-open the report or chat when a new slide is selected.
  // The user must explicitly click the report button to open the modal.

  const base = viewer.world.getItemAt(0);
  viewer.scalebar({
    type: OpenSeadragon.ScalebarType.MICROSCOPY,
    pixelsPerMeter: (1e6 / m.mpp) * base.source.dimensions.x / m.slide_width_px,
    minWidth: "90px", location: OpenSeadragon.ScalebarLocation.BOTTOM_LEFT,
    xOffset: 16, yOffset: 18, stayInsideImage: false,
    color: "#e7ecf2", fontColor: "#e7ecf2", backgroundColor: "rgba(16,21,28,0.85)",
    fontSize: "12px", barThickness: 2,
  });

  // heatmap: one pixel per tile; spans heatmap_width * heatmap_downsample level-0 px (viewport width 1 = slide width)
  viewer.addSimpleImage({
    url: `/api/slide/${slideId}/heatmap`,
    x: 0, y: 0, width: m.heatmap_width * m.heatmap_downsample / m.slide_width_px,
    opacity: heatmapVisible ? heatmapOpacity : 0,
    success: e => { heatmapLayer = e.item; },
  });

  await drawOutline(slideId, m);
}

async function drawOutline(slideId, m) {
  outlineLayer().innerHTML = "";
  const btn = document.getElementById("outline-toggle-btn");
  let ann = null;
  try { const r = await fetch(`/api/slide/${slideId}/annotation`); if (r.ok) ann = await r.json(); } catch {}
  btn.disabled = !(ann && ann.tumor && ann.tumor.length);
  if (btn.disabled) { btn.classList.remove("is-on"); return; }
  const w = m.slide_width_px;
  for (const poly of ann.tumor) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    el.setAttribute("points", poly.map(([x, y]) => `${x / w},${y / w}`).join(" "));
    el.setAttribute("fill", "none");
    el.setAttribute("stroke", OUTLINE_COLOR);
    el.setAttribute("stroke-width", "2");
    el.setAttribute("vector-effect", "non-scaling-stroke");
    outlineGroup.appendChild(el);
  }
  outlineGroup.style.display = outlineVisible ? "" : "none";
  btn.classList.toggle("is-on", outlineVisible);
}

function toggleHeatmap() {
  heatmapVisible = !heatmapVisible;
  if (heatmapLayer) heatmapLayer.setOpacity(heatmapVisible ? heatmapOpacity : 0);
  document.getElementById("heatmap-toggle-btn").classList.toggle("is-on", heatmapVisible);
  document.getElementById("viewer-legend").style.visibility = heatmapVisible ? "" : "hidden";
}

function toggleOutline() {
  outlineVisible = !outlineVisible;
  outlineLayer().style.display = outlineVisible ? "" : "none";
  document.getElementById("outline-toggle-btn").classList.toggle("is-on", outlineVisible);
}

function resetView() { viewer.viewport.goHome(); }

// Pranav's anomaly navigator API (merged into the "Suspicious foci" panel); kept so existing callers work
function zoomToROI(bbox_px) { zoomToFocus({ bbox_px }); }

function zoomToFocus(r) {
  const w = current.slide_width_px;
  const [x0, y0, x1, y1] = r.bbox_px;
  const pad = Math.max(x1 - x0, y1 - y0) * 0.6 + 600;  // focus plus a little surrounding tissue
  viewer.viewport.fitBounds(new OpenSeadragon.Rect((x0 - pad) / w, (y0 - pad) / w, (x1 - x0 + 2 * pad) / w, (y1 - y0 + 2 * pad) / w));
}

// ---------- Case panel ----------

function renderCase(m) {
  const p = PRIORITY[m.category] || PRIORITY.Negative;
  const mpp = m.mpp;
  document.getElementById("case-id").textContent = m.slide_id;
  document.getElementById("case-meta").textContent =
    `Lymph node · H&E · ${m.slide_width_px.toLocaleString()} × ${m.slide_height_px.toLocaleString()} px · ${mpp.toFixed(3)} µm/px`;

  const banner = document.getElementById("case-priority");
  banner.className = `priority-banner ${p.cls}`;
  banner.innerHTML = `
    <div class="priority-level">${p.level}</div>
    <div class="priority-category">${m.category}</div>
    <div class="priority-note">${p.note}</div>
    <div class="priority-score">${m.urgency_score.toFixed(1)}<small>urgency / 10</small></div>`;

  const metric = (label, value, unit = "") =>
    `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}${unit ? `<small>${unit}</small>` : ""}</div></div>`;
  const has = m.num_tumor_regions > 0;
  document.getElementById("case-metrics").innerHTML = [
    metric("Largest focus", has ? m.largest_region_extent_mm.toFixed(2) : "–", has ? "mm" : ""),
    metric("Suspicious foci", m.num_tumor_regions),
    metric("Suspicious area", has ? m.tumor_area_mm2.toFixed(2) : "–", has ? "mm²" : ""),
    metric("Tissue analyzed", m.total_tissue_area_mm2.toFixed(1), "mm²"),
  ].join("");

  const foci = document.getElementById("case-foci");
  foci.innerHTML = has ? "" : `<div class="foci-empty">No suspicious foci detected</div>`;
  m.regions.forEach((r, i) => {
    const [x0, y0, x1, y1] = r.bbox_px;
    const ext = Math.max(x1 - x0, y1 - y0) * mpp / 1000;
    const row = document.createElement("div");
    row.className = "focus-row";
    row.innerHTML = `
      <span class="focus-num">${String(i + 1).padStart(2, "0")}</span>
      <div><div class="focus-size">${ext.toFixed(2)} mm</div><div class="focus-sub">${r.area_mm2.toFixed(2)} mm² area</div></div>
      <span class="focus-conf">p&nbsp;${r.max_confidence.toFixed(2)}</span>
      <i data-lucide="chevron-right"></i>`;
    row.onclick = () => zoomToFocus(r);
    foci.appendChild(row);
  });

  document.getElementById("case-footer").innerHTML =
    `Processed on-device in <b>${Math.round(m.processing_time_sec)} s</b> · <b>${m.gb_processed.toFixed(2)} GB</b> read, 0 bytes uploaded<br>` +
    `Model: UNI2-h features + linear classifier`;
  lucide.createIcons();
  if (window.gsap) gsap.from("#case-panel > *", { opacity: 0, y: 6, duration: 0.35, stagger: 0.035, ease: "power2.out" });
}

// ---------- Report + AI Chat ----------

async function fetchReport() {
  if (!currentSlideId) return;

  const panel = document.getElementById("report-panel");
  const content = document.getElementById("report-content");
  const historyEl = document.getElementById("chat-history");

  panel.classList.add("is-open");
  document.getElementById("modal-slide").textContent = currentSlideId;
  resetChat();

  let reportText = "Drafting report with local Nemotron…";

  try {
    const resp = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slide_id: currentSlideId }),
    });
    const data = await resp.json();
    reportText = data.report || reportText;

    const htmlUrl = `/api/slide/${currentSlideId}/report.html`;
    const htmlHead = await fetch(htmlUrl, { method: "HEAD" }).catch(() => null);

    if (htmlHead && htmlHead.ok) {
      content.className = "modal-body";
      content.innerHTML = `<iframe id="report-frame" src="${htmlUrl}"></iframe>`;
    } else {
      content.className = "modal-body is-text";
      content.textContent = reportText;
    }
  } catch (err) {
    content.className = "modal-body is-text";
    content.textContent = "Failed to reach local LLM.";
    reportText = "Failed to reach local LLM.";
  }

}

// chat starts on the suggestions; the report itself is shown on the left, not pasted into the chat
function resetChat() {
  currentChatHistory = [];
  document.querySelectorAll("#chat-history .chat-message").forEach(el => el.remove());
  document.getElementById("chat-empty").classList.remove("is-hidden");
}

function askSuggestion(btn) {
  document.getElementById("chat-input").value = btn.textContent;
  sendChatMessage();
}

function closeReport() {
  document.getElementById("report-panel").classList.remove("is-open");
}

function printReport() {
  document.getElementById("report-frame")?.contentWindow.print();
}

document.addEventListener("keydown", e => { if (e.key === "Escape") closeReport(); });

function handleChatEnter(event) {
  if (event.key === "Enter") sendChatMessage();
}

async function sendChatMessage() {
  const inputEl = document.getElementById("chat-input");
  const msg = inputEl.value.trim();
  if (!msg || !currentSlideId || document.querySelector(".send-btn").disabled) return;  // one question at a time

  inputEl.value = "";
  document.getElementById("chat-empty").classList.add("is-hidden");
  appendChatBubble("user", msg);
  currentChatHistory.push({ role: "user", content: msg });

  const sendBtn = document.querySelector(".send-btn");
  sendBtn.disabled = true;
  const loadingId = appendChatBubble("ai", "…");
  document.getElementById(loadingId).classList.add("is-typing");

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slide_id: currentSlideId, messages: currentChatHistory }),
    });
    const data = await resp.json();
    const reply = data.reply || "No response from the local model.";
    const el = document.getElementById(loadingId);
    el.classList.remove("is-typing");
    el.innerText = reply;
    currentChatHistory.push({ role: "assistant", content: reply });
  } catch (err) {
    const el = document.getElementById(loadingId);
    el.classList.remove("is-typing");
    el.innerText = "Connection error.";
    currentChatHistory.pop();
  } finally {
    sendBtn.disabled = false;
    document.getElementById("chat-history").scrollTop = 1e9;
  }
}

function appendChatBubble(role, text) {
  const historyEl = document.getElementById("chat-history");
  const bubble = document.createElement("div");
  bubble.className = `chat-message ${role === "user" ? "msg-user" : "msg-ai"}`;
  bubble.innerText = text;

  const id = `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  bubble.id = id;

  historyEl.appendChild(bubble);
  historyEl.scrollTop = historyEl.scrollHeight;
  return id;
}