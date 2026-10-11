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
    maxZoomPixelRatio: 2
  });
}

// Inside app/static/viewer.js -> loadQueue()
async function loadQueue() {
  const resp = await fetch("/api/queue");
  const data = await resp.json();
  const queueEl = document.getElementById("queue");
  queueEl.innerHTML = "";

  let totalGB = 0;
  data.slides.forEach((slide, idx) => {
    // FIX: Parse as float to ensure math works, fallback to 0
    totalGB += parseFloat(slide.gb_processed) || 0;
    
    const item = document.createElement("div");
    item.className = "slide-item" + (idx === 0 ? " active" : "");
    item.innerHTML = `
      <div style="display:flex; justify-content:space-between;">
        <strong>${slide.slide_id}</strong>
        <span class="${slide.urgency_score > 7 ? 'urgent' : ''}">Score: ${slide.urgency_score}</span>
      </div>
      <div style="font-size:0.8rem; color:#8b949e; margin-top:4px;">
        Status: ${slide.status} | <strong>${(slide.category || "UNKNOWN").toUpperCase()}</strong>
      </div>
    `;
    item.onclick = () => selectSlide(slide.slide_id, item);
    queueEl.appendChild(item);
  });

  // FIX: Force update the DOM with the summed value
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

  // Clear previous slide layers
  viewer.world.removeAll();

  // 1. Load the base "Tissue Cells" Layer
  // (Using a public sample DZI for tonight's frontend prep. 
  // Once Anant finishes, change this URL to point to his local .dzi output)
  viewer.addTiledImage({
    tileSource: "https://openseadragon.github.io/example-images/highsmith/highsmith.dzi",
    success: function() {
      
      // 2. Load the AI Heatmap as an overlay on top
      viewer.addSimpleImage({
        url: `/api/slide/${slideId}/heatmap`,
        opacity: 0.65, // 65% opacity so you can see cells underneath
        success: function(event) {
          heatmapLayer = event.item;
        }
      });
    }
  });
}

// 3. Add the toggle function
function toggleHeatmap() {
  if (heatmapLayer) {
    const currentOpacity = heatmapLayer.getOpacity();
    // Toggle between 65% visibility and 0% (hidden)
    heatmapLayer.setOpacity(currentOpacity > 0 ? 0 : 0.65);
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