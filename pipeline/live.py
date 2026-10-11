"""Live run state for the pipeline page.

    outputs/live.json     current state (atomic, throttled): run, slide, stage, tile progress, totals
    outputs/events.jsonl  append-only event log, one JSON object per line with an increasing `seq`

The server streams both to the browser; the pipeline only calls the functions below.
"""
import json
import time

import config
from pipeline.index import atomic_write_text

STAGES = ["read", "tissue", "tile", "embed", "classify", "regions", "urgency", "report"]
URGENT = ("Macrometastasis", "Micrometastasis")

_state = {}
_seq = 0
_last_write = 0.0


def _paths():
    return config.OUTPUTS_DIR / "live.json", config.OUTPUTS_DIR / "events.jsonl"


def _write(force=False):
    global _last_write
    now = time.time()
    if not force and now - _last_write < 0.4:
        return
    _last_write = now
    _state["updated_at"] = now
    if _state.get("started_at"):
        _state["elapsed_sec"] = round(now - _state["started_at"], 1)
    atomic_write_text(_paths()[0], json.dumps(_state))


def event(kind, msg, slide_id=None, **data):
    """kind: run | slide | stage | result | report | inject | error"""
    global _seq
    _seq += 1
    line = {"seq": _seq, "ts": time.time(), "kind": kind, "slide_id": slide_id, "msg": msg, **data}
    with open(_paths()[1], "a") as f:
        f.write(json.dumps(line) + "\n")


def run_start(slide_ids):
    global _state, _seq
    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    _paths()[1].write_text("")  # new run, new log
    _seq = 0
    _state = {"run": "running", "started_at": time.time(), "slides_total": len(slide_ids), "slides_done": 0,
              "gb_done": 0.0, "tiles_done_total": 0, "first_urgent_sec": None, "slide": None, "stage": None,
              "tiles_done": 0, "tiles_total": 0, "tiles_per_sec": 0.0, "results": []}
    event("run", f"Tray received: {len(slide_ids)} slides, processing on-device")
    _write(force=True)


def slide_start(slide_id, gb):
    _state.update(slide=slide_id, slide_gb=round(gb, 2), stage="read", tiles_done=0, tiles_total=0, tiles_per_sec=0.0)
    event("slide", f"Reading {gb:.2f} GB whole-slide image", slide_id)
    _write(force=True)


def stage(name, slide_id, msg=None, **info):
    _state["stage"] = name
    _state.update(info)
    if msg:
        event("stage", msg, slide_id, stage=name)
    _write(force=True)


def progress(tiles_done, tiles_total, tiles_per_sec):
    _state.update(tiles_done=tiles_done, tiles_total=tiles_total, tiles_per_sec=round(tiles_per_sec, 1))
    _write()


def slide_done(m, rank=None):
    _state["slides_done"] = _state.get("slides_done", 0) + 1
    _state["gb_done"] = round(_state.get("gb_done", 0) + m["gb_processed"], 2)
    _state["tiles_done_total"] = _state.get("tiles_done_total", 0) + _state.get("tiles_total", 0)
    if m["category"] in URGENT and _state.get("first_urgent_sec") is None and _state.get("started_at"):
        _state["first_urgent_sec"] = round(time.time() - _state["started_at"], 1)
    _state.setdefault("results", []).append({"slide_id": m["slide_id"], "category": m["category"],
                                             "urgency_score": m["urgency_score"]})
    size = f"{m['largest_region_extent_mm']:.2f} mm, " if m["num_tumor_regions"] else ""
    where = f" → queue position #{rank}" if rank else ""
    event("result", f"{m['category']} · {size}urgency {m['urgency_score']:.2f}{where}", m["slide_id"],
          category=m["category"], urgency=m["urgency_score"], rank=rank)
    _write(force=True)


def run_done():
    s = _state
    secs = time.time() - s.get("started_at", time.time())
    pre = sum(1 for r in s.get("results", []) if r.get("preloaded"))
    live_n = s.get("slides_done", 0) - pre
    _state.update(run="done", slide=None, stage=None, slides_live=live_n, slides_preloaded=pre)
    if pre:
        event("run", f"Tray complete: {s.get('slides_done', 0)} slides ({live_n} analyzed live in {secs / 60:.1f} min, "
                     f"{pre} preloaded from earlier full runs) · 0 bytes uploaded")
    else:
        event("run", f"Tray complete: {s.get('slides_done', 0)} slides, {s.get('gb_done', 0):.1f} GB in "
                     f"{secs / 60:.1f} min · 0 bytes uploaded")
    _write(force=True)


def set_idle():
    """Reset to an idle state (demo reset)."""
    global _state, _seq
    _state, _seq = {"run": "idle"}, 0
    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    _paths()[1].write_text("")
    _write(force=True)


def resume():
    """Continue an existing run's state and event numbering from disk (used by the server's fast-forward)."""
    global _state, _seq
    live_path, events_path = _paths()
    _state = json.loads(live_path.read_text()) if live_path.exists() else {"run": "idle"}
    _seq = 0
    if events_path.exists():
        for line in events_path.read_text().splitlines():
            try:
                _seq = max(_seq, json.loads(line).get("seq", 0))
            except json.JSONDecodeError:
                pass
    if _state.get("run") != "running":   # fast-forward from idle: start a fresh run record
        _state.update(run="running", started_at=time.time(), slides_total=0, slides_done=0, gb_done=0.0,
                      tiles_done_total=0, first_urgent_sec=None, results=[])
    _state.update(slide=None, stage=None, tiles_done=0, tiles_total=0, tiles_per_sec=0.0)


def preloaded(m, rank=None):
    """Record a result loaded from disk (computed in an earlier run on this machine), clearly labeled."""
    _state["slides_done"] = _state.get("slides_done", 0) + 1
    _state["gb_done"] = round(_state.get("gb_done", 0) + m["gb_processed"], 2)
    if m["category"] in URGENT and _state.get("first_urgent_sec") is None and _state.get("started_at"):
        _state["first_urgent_sec"] = round(time.time() - _state["started_at"], 1)
    _state.setdefault("results", []).append({"slide_id": m["slide_id"], "category": m["category"],
                                             "urgency_score": m["urgency_score"], "preloaded": True})
    size = f"{m['largest_region_extent_mm']:.2f} mm, " if m["num_tumor_regions"] else ""
    where = f" → #{rank}" if rank else ""
    event("result", f"{m['category']} · {size}urgency {m['urgency_score']:.2f}{where} (preloaded)", m["slide_id"],
          category=m["category"], urgency=m["urgency_score"], rank=rank, preloaded=True)
    _write(force=True)
