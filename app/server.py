import os
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Local Pathology Triage Server")

# Switch easily: export DATA_SOURCE=sample OR export DATA_SOURCE=real
DATA_SOURCE = os.getenv("DATA_SOURCE", "sample").lower()

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = BASE_DIR / "outputs"
DATA_DIR = OUTPUTS_DIR / "_sample" if DATA_SOURCE == "sample" else OUTPUTS_DIR

print(f"[*] Running in [{DATA_SOURCE.upper()}] mode. Serving outputs from: {DATA_DIR}")

# Mount static web directory
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def get_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/queue")
def get_queue():
    index_path = DATA_DIR / "index.json"
    if index_path.exists():
        with open(index_path, "r") as f:
            data = json.load(f)
            # Extract the actual list from the dictionary payload
            slides = data.get("slides", [])
    else:
        slides = []
        for p in DATA_DIR.iterdir():
            if p.is_dir() and not p.name.startswith("_") and (p / "metrics.json").exists():
                with open(p / "metrics.json") as f:
                    meta = json.load(f)
                slides.append({
                    "slide_id": meta.get("slide_id", p.name),
                    "status": meta.get("status", "done"),
                    "urgency_score": meta.get("urgency_score", 0.0),
                    "tumor_detected": meta.get("tumor_detected", False),
                    "category": meta.get("category", "Unknown"),
                    "gb_processed": meta.get("gb_processed", 1.0)
                })

    # Sort descending, using 'or 0' to safely handle null/None urgency scores
    slides.sort(key=lambda s: s.get("urgency_score") or 0, reverse=True)
    return {"slides": slides, "data_source": DATA_SOURCE}


@app.get("/api/slide/{slide_id}")
def get_slide_metrics(slide_id: str):
    slide_folder = DATA_DIR / slide_id
    metrics_path = slide_folder / "metrics.json"
    if not metrics_path.exists():
        raise HTTPException(status_code=404, detail=f"Metrics for {slide_id} not found")
    with open(metrics_path, "r") as f:
        return json.load(f)


@app.get("/api/slide/{slide_id}/heatmap")
def get_slide_heatmap(slide_id: str):
    img_path = DATA_DIR / slide_id / "heatmap.png"
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Heatmap image not found")
    return FileResponse(img_path, media_type="image/png")


@app.get("/api/slide/{slide_id}/thumbnail")
def get_slide_thumbnail(slide_id: str):
    thumb_path = DATA_DIR / slide_id / "thumbnail.png"
    if not thumb_path.exists():
        # Fallback to heatmap if thumbnail isn't present
        thumb_path = DATA_DIR / slide_id / "heatmap.png"
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(thumb_path, media_type="image/png")


# ---- Full-resolution slide tiles, pathologist outline, HTML report (used by the redesigned viewer) ----
import io
import sys
from functools import lru_cache
from fastapi.responses import Response

sys.path.insert(0, str(BASE_DIR))
import config  # noqa: E402  (slide paths)


@lru_cache(maxsize=16)
def _deepzoom(slide_id: str):
    import openslide
    from openslide.deepzoom import DeepZoomGenerator
    path = config.slide_path(slide_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Slide {slide_id} not found")
    # limit_bounds=False keeps full level-0 coordinates, so heatmap and bbox_px line up
    return DeepZoomGenerator(openslide.OpenSlide(str(path)), tile_size=254, overlap=1, limit_bounds=False)


@app.get("/slide/{slide_id}.dzi")
def get_dzi(slide_id: str):
    return Response(_deepzoom(slide_id).get_dzi("jpeg"), media_type="application/xml")


@app.get("/slide/{slide_id}_files/{level}/{tile}")
def get_tile(slide_id: str, level: int, tile: str):
    col, row = (int(v) for v in tile.split(".")[0].split("_"))
    try:
        img = _deepzoom(slide_id).get_tile(level, (col, row))
    except ValueError:
        raise HTTPException(status_code=404, detail="Tile out of range")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/slide/{slide_id}/annotation")
def get_annotation(slide_id: str):
    path = DATA_DIR / slide_id / "annotation.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No annotation for this slide")
    return FileResponse(path, media_type="application/json")


@app.api_route("/api/slide/{slide_id}/report.html", methods=["GET", "HEAD"])
def get_report_html(slide_id: str):
    path = DATA_DIR / slide_id / "report.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No HTML report yet")
    return FileResponse(path, media_type="text/html")


class ReportRequest(BaseModel):
    slide_id: str

@app.post("/api/report")
def generate_or_fetch_report(req: ReportRequest):
    cached_report = DATA_DIR / req.slide_id / "report.md"
    if cached_report.exists():
        return {"report": cached_report.read_text(), "cached": True}
    
    # Import LLM generation logic
    try:
        from llm_copilot import generate_pathology_report
        metrics_path = DATA_DIR / req.slide_id / "metrics.json"
        with open(metrics_path, "r") as f:
            metrics_data = json.load(f)
        report_text = generate_pathology_report(metrics_data)
        return {"report": report_text, "cached": False}
    except Exception as e:
        return {"report": f"LLM Generation Error: {str(e)}", "cached": False}




class ChatRequest(BaseModel):
    slide_id: str
    messages: list[dict]  # Expects [{"role": "user", "content": "..."}]

# Add this endpoint
@app.post("/api/chat")
def chat_with_model(req: ChatRequest):
    try:
        from llm_copilot import chat_with_copilot
        metrics_path = DATA_DIR / req.slide_id / "metrics.json"
        
        with open(metrics_path, "r") as f:
            metrics_data = json.load(f)
            
        # Pass the metrics and the conversation history to the LLM
        response_text = chat_with_copilot(metrics_data, req.messages)
        return {"reply": response_text}
    except Exception as e:
        return {"reply": f"LLM Integration Error: {str(e)}"}



    


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)