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
def get_intro():
    return FileResponse(STATIC_DIR / "intro.html")


@app.get("/app")
def get_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/pipeline")
def get_pipeline():
    return FileResponse(STATIC_DIR / "pipeline.html")


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



    


# ---- Live run + demo controls (pipeline page) ----
import socket
import subprocess
import time as _time
from fastapi.responses import JSONResponse

DEMO_HOLDOUT = os.getenv("DEMO_HOLDOUT", "tumor_082")   # arrives mid-run via "Drop in an urgent case"
DEMO_BATCH = os.getenv("DEMO_BATCH", "256")
DEMO_WORKERS = os.getenv("DEMO_WORKERS", "8")
_demo_proc = None
_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (_time.time(), val)
    return val


def _tray():
    return sorted(p.stem for p in config.SLIDES_DIR.glob("*.tif") if p.stem != DEMO_HOLDOUT)


RUN_PID = OUTPUTS_DIR / "run.pid"   # survives server reloads, so a run is never orphaned


def _run_pid():
    try:
        pid = int(RUN_PID.read_text())
        os.kill(pid, 0)          # raises if the process is gone
        return pid
    except (OSError, ValueError):
        return None


def _running():
    if _demo_proc is not None and _demo_proc.poll() is None:
        return True
    return _run_pid() is not None


def _stop_run(timeout=15):
    """Stop the current run and its DataLoader workers (whole process group), wherever it was started from."""
    import signal
    pid = _demo_proc.pid if _demo_proc is not None and _demo_proc.poll() is None else _run_pid()
    if pid is None:
        RUN_PID.unlink(missing_ok=True)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        pass
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            os.kill(pid, 0)
            _time.sleep(0.2)
        except OSError:
            break
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass
    RUN_PID.unlink(missing_ok=True)


def _spawn(slides, no_cache=True, holdout=None):
    global _demo_proc
    cmd = [sys.executable, "-m", "pipeline.run_batch", "--slides", ",".join(slides), "--order", "random",
           "--batch", DEMO_BATCH, "--workers", DEMO_WORKERS, "--reports"] + (["--no-cache"] if no_cache else []) \
          + (["--holdout", holdout] if holdout else [])
    log = open(OUTPUTS_DIR / "demo_run.log", "w")
    _stop_run()   # never two runs at once
    _demo_proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True)   # own process group: workers stop with it
    RUN_PID.write_text(str(_demo_proc.pid))


@app.get("/api/live")
def get_live():
    p = DATA_DIR / "live.json"
    state = json.loads(p.read_text()) if p.exists() else {"run": "idle"}
    ff_active = "_ff_thread" in globals() and _ff_thread is not None and _ff_thread.is_alive()
    if state.get("run") == "running" and not _running() and not ff_active:
        state["run"] = "stopped"
    state["holdout"] = DEMO_HOLDOUT
    state["holdout_available"] = config.slide_path(DEMO_HOLDOUT).exists()
    return state


@app.get("/api/events")
def get_events(since: int = 0):
    p = DATA_DIR / "events.jsonl"
    if not p.exists():
        return {"events": []}
    out = []
    for line in p.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue  # line being written
        if e.get("seq", 0) > since:
            out.append(e)
    return {"events": out[-300:]}


@app.get("/api/slide/{slide_id}/heatmap_live")
def get_heatmap_live(slide_id: str):
    for name in ("heatmap_live.png", "heatmap.png"):
        p = DATA_DIR / slide_id / name
        if p.exists():
            return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="No heatmap yet")


class DemoStart(BaseModel):
    no_cache: bool = True


@app.post("/api/demo/start")
def demo_start(req: DemoStart = DemoStart()):
    if _running():
        return JSONResponse({"ok": False, "detail": "A run is already in progress"}, status_code=409)
    holdout = DEMO_HOLDOUT if config.slide_path(DEMO_HOLDOUT).exists() else None
    if holdout:   # show it in the queue as still on the scanner, so the tray reads 10 from the start
        from pipeline.index import update_index
        update_index(holdout, status="expected", urgency_score=None, category=None, gb_processed=0.0)
    _spawn(_tray(), no_cache=req.no_cache, holdout=holdout)
    return {"ok": True, "slides": _tray(), "holdout": holdout}


@app.post("/api/demo/reset")
def demo_reset():
    """Back to the 'tray just arrived' state: tray slides queued, held-out slide 'expected' (still on the scanner)."""
    from pipeline import live
    from pipeline.index import atomic_write_text
    _stop_run()
    tray = _tray()
    index = {"schema_version": config.SCHEMA_VERSION, "slides": [
        {"slide_id": s, "filename": f"{s}.tif", "status": "queued", "urgency_score": None, "category": None,
         "gb_processed": 0.0, "output_path": s} for s in tray]}
    if config.slide_path(DEMO_HOLDOUT).exists():
        index["slides"].append({"slide_id": DEMO_HOLDOUT, "filename": f"{DEMO_HOLDOUT}.tif", "status": "expected",
                                "urgency_score": None, "category": None, "gb_processed": 0.0, "output_path": DEMO_HOLDOUT})
    atomic_write_text(DATA_DIR / "index.json", json.dumps(index, indent=2))
    (DATA_DIR / "inject.json").unlink(missing_ok=True)
    live.set_idle()
    return {"ok": True, "slides": tray}


@app.post("/api/demo/restore")
def demo_restore():
    """Safety net: rebuild index.json from the metrics.json files already on disk (every slide 'done')."""
    from pipeline.index import atomic_write_text
    if _running():
        return JSONResponse({"ok": False, "detail": "A run is in progress"}, status_code=409)
    slides = []
    for p in sorted(DATA_DIR.glob("*/metrics.json")):
        if p.parent.name.startswith("_"):
            continue
        m = json.loads(p.read_text())
        slides.append({"slide_id": m["slide_id"], "filename": m["filename"], "status": "done",
                       "urgency_score": m["urgency_score"], "category": m["category"],
                       "gb_processed": m["gb_processed"], "output_path": m["slide_id"]})
    atomic_write_text(DATA_DIR / "index.json", json.dumps({"schema_version": config.SCHEMA_VERSION, "slides": slides}, indent=2))
    return {"ok": True, "slides": len(slides)}


class DemoInject(BaseModel):
    slide_id: str | None = None


@app.post("/api/demo/inject")
def demo_inject(req: DemoInject = DemoInject()):
    sid = req.slide_id or DEMO_HOLDOUT
    if not config.slide_path(sid).exists():
        raise HTTPException(status_code=404, detail=f"{sid} not on this machine")
    if _running():
        (DATA_DIR / "inject.json").write_text(json.dumps([sid]))
        return {"ok": True, "slide_id": sid, "mode": "joins the running tray"}
    _spawn([sid])
    return {"ok": True, "slide_id": sid, "mode": "processing now"}


_offline_strikes = 0


def _online():
    """Online if any probe connects; reports offline only after two consecutive failed checks (no flicker)."""
    global _offline_strikes
    for host in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection(host, timeout=0.8).close()
            _offline_strikes = 0
            return True
        except OSError:
            pass
    _offline_strikes += 1
    return _offline_strikes < 2


def _gpu():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,power.draw,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2).stdout
        name, util, power, temp = [v.strip() for v in out.splitlines()[0].split(",")]
        num = lambda v: float(v) if v.replace(".", "", 1).isdigit() else None  # noqa: E731
        return {"name": name, "util": num(util), "power_w": num(power), "temp_c": num(temp)}
    except Exception:  # noqa: BLE001
        return None


def _models():
    try:
        import urllib.request
        base = config.LLM_BASE_URL.rsplit("/v1", 1)[0]
        ps = json.load(urllib.request.urlopen(base + "/api/ps", timeout=1))
        return [{"name": m["name"], "gb": round(m.get("size_vram", m.get("size", 0)) / 1e9, 1)} for m in ps.get("models", [])]
    except Exception:  # noqa: BLE001
        return []


@app.get("/api/system")
def get_system():
    return {"online": _cached("online", 3, _online), "gpu": _cached("gpu", 1.5, _gpu),
            "llm": _cached("models", 3, _models), "run_active": _running()}


# ---- Fast-forward: finish the tray from results computed earlier on this machine ----
import random as _random
import threading

_ff_thread = None


def _rank(slide_id):
    idx = json.loads((DATA_DIR / "index.json").read_text())
    done = sorted((e for e in idx["slides"] if e.get("status") == "done"), key=lambda e: -(e.get("urgency_score") or 0))
    ids = [e["slide_id"] for e in done]
    return ids.index(slide_id) + 1 if slide_id in ids else None


def _fast_forward(remaining, stagger):
    from pipeline import live
    from pipeline.index import update_index
    live.resume()
    live._state["slides_total"] = live._state.get("slides_done", 0) + len(remaining)
    live.event("run", f"Fast-forward: loading results for the remaining {len(remaining)} slides, "
                      "analyzed in full earlier today on this GX10")
    for sid in remaining:
        m = json.loads((DATA_DIR / sid / "metrics.json").read_text())
        update_index(sid, status="done", urgency_score=m["urgency_score"], category=m["category"],
                     gb_processed=m["gb_processed"], filename=m["filename"], output_path=sid)
        live.preloaded(m, rank=_rank(sid))
        _time.sleep(stagger)
    live.run_done()


class DemoFastForward(BaseModel):
    include_holdout: bool = True
    stagger: float = 0.3


@app.post("/api/demo/fastforward")
def demo_fastforward(req: DemoFastForward = DemoFastForward()):
    global _ff_thread
    if _ff_thread is not None and _ff_thread.is_alive():
        return JSONResponse({"ok": False, "detail": "Fast-forward already running"}, status_code=409)
    _stop_run()
    (DATA_DIR / "inject.json").unlink(missing_ok=True)
    idx = json.loads((DATA_DIR / "index.json").read_text()) if (DATA_DIR / "index.json").exists() else {"slides": []}
    status = {e["slide_id"]: e.get("status") for e in idx["slides"]}
    wanted = _tray() + ([DEMO_HOLDOUT] if req.include_holdout and config.slide_path(DEMO_HOLDOUT).exists() else [])
    remaining = [s for s in wanted if status.get(s) != "done" and (DATA_DIR / s / "metrics.json").exists()]
    _random.shuffle(remaining)  # scanner order, so the queue visibly re-sorts
    missing = [s for s in wanted if status.get(s) != "done" and s not in remaining]
    _ff_thread = threading.Thread(target=_fast_forward, args=(remaining, req.stagger), daemon=True)
    _ff_thread.start()
    return {"ok": True, "preloading": remaining, "no_stored_result": missing}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)