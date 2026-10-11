let viewer = null;
let currentSlideId = null;
let heatmapLayer = null;

document.addEventListener("DOMContentLoaded", () => {
  initViewer();
  loadQueue();
});

function initViewer() {
  viewer = OpenSeadragon({
    id: "viewer",
    prefixUrl: "https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/images/",
    showNavigationControl: true,
    animationTime: 0.5,
    blendTime: 0.1,
    maxZoomPixelRatio: 15, 
    minZoomImageRatio: 0.9 
  });
}

async function loadQueue() {
  const resp = await fetch("/api/queue");
  const data = await resp.json();
  const queueEl = document.getElementById("queue");
  queueEl.innerHTML = "";

  let totalGB = 0;
  data.slides.forEach((slide, idx) => {
    totalGB += parseFloat(slide.gb_processed) || 0;
    
    const item = document.createElement("div");
    item.className = "slide-item" + (idx === 0 ? " active" : "");
    
    // Purged inline styles. Exclusively using class names.
    item.innerHTML = `
      <div class="flex-between">
        <strong>${slide.slide_id}</strong>
        <span class="${slide.urgency_score > 7 ? 'urgent' : 'score'}">Score: ${slide.urgency_score}</span>
      </div>
      <div class="queue-meta">
        Status: ${slide.status} | <strong>${(slide.category || "UNKNOWN").toUpperCase()}</strong>
      </div>
    `;
    item.onclick = () => selectSlide(slide.slide_id, item);
    queueEl.appendChild(item);
  });

  document.getElementById("gb-counter").innerText = totalGB.toFixed(1);

  if (data.slides.length > 0) {
    selectSlide(data.slides[0].slide_id);
  }
}

async function selectSlide(slideId, element) {
  currentSlideId = slideId;
  if (element) {
    document.querySelectorAll(".slide-item").forEach(el => el.classList.remove("active"));
    element.classList.add("active");
  }

  const btn = document.getElementById("heatmap-toggle-btn");
  if (btn) {
    btn.innerText = "🗺️ AI Heatmap: ON";
    btn.classList.remove("off");
  }

  // Fetch the detailed metrics to get the regions array
  try {
    const metricsResp = await fetch(`/api/slide/${slideId}`);
    const metricsData = await metricsResp.json();
    renderROIs(metricsData);
  } catch (err) {
    console.error("Failed to load slide metrics:", err);
  }

  viewer.open({
    type: 'image',
    url: `/api/slide/${slideId}/thumbnail`
  });

  viewer.addHandler('open', function overlayHeatmap() {
    viewer.removeHandler('open', overlayHeatmap);
    
    viewer.addSimpleImage({
      url: `/api/slide/${slideId}/heatmap`,
      opacity: 0.65,
      success: function(event) {
        heatmapLayer = event.item;
      }
    });
  });
}

function renderROIs(metrics) {
  const panel = document.getElementById("roi-panel");
  if (!panel) return;

  if (!metrics.regions || metrics.regions.length === 0) {
    panel.innerHTML = `<div class="roi-empty">No anomalies detected.</div>`;
    return;
  }

  let html = `<h3 class="roi-title">Detected Anomalies</h3>`;
  
  metrics.regions.forEach((r) => {
    // Pass the coordinates and slide width into the click handler
    const bboxStr = JSON.stringify(r.bbox_px);
    html += `
      <div class="roi-item" onclick='zoomToROI(${bboxStr}, ${metrics.slide_width_px})'>
        <div class="flex-between">
          <strong>Region ${r.id}</strong>
          <span class="roi-confidence">${(r.max_confidence * 100).toFixed(1)}% Conf</span>
        </div>
        <div class="roi-meta">Area: ${r.area_mm2} mm²</div>
      </div>
    `;
  });
  
  panel.innerHTML = html;
}

function zoomToROI(bbox_px, slide_width_px) {
  if (!viewer.viewport) return;
  
  const [xmin, ymin, xmax, ymax] = bbox_px;
  const boxWidth = xmax - xmin;
  const boxHeight = ymax - ymin;

  // OpenSeadragon normalizes the image width to exactly 1.0.
  // We divide all pixel values by the total slide width to match OSD's coordinate space.
  const osdRect = new OpenSeadragon.Rect(
    xmin / slide_width_px,
    ymin / slide_width_px,
    boxWidth / slide_width_px,
    boxHeight / slide_width_px
  );

  // Smoothly fly to the bounding box
  viewer.viewport.fitBounds(osdRect, false);
}

function toggleHeatmap() {
  if (heatmapLayer) {
    const currentOpacity = heatmapLayer.getOpacity();
    const btn = document.getElementById("heatmap-toggle-btn");
    
    if (currentOpacity > 0) {
      heatmapLayer.setOpacity(0);
      btn.innerText = "🗺️ AI Heatmap: OFF";
      btn.classList.add("off");
    } else {
      heatmapLayer.setOpacity(0.65);
      btn.innerText = "🗺️ AI Heatmap: ON";
      btn.classList.remove("off");
    }
  }
}

async function fetchReport() {
  if (!currentSlideId) return;
  const panel = document.getElementById("report-panel");
  const content = document.getElementById("report-content");
  panel.style.display = "block";
  content.innerText = "Querying local LLM...";

  const resp = await fetch("/api/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slide_id: currentSlideId })
  });
  const data = await resp.json();
  content.innerText = data.report;
}