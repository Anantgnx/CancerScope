"""outputs/index.json updates and atomic file writes.

The dashboard polls index.json every ~2 s, so every write goes to a temp file and is
swapped in with os.replace (readers never see half a file). A lock file serializes
concurrent writers (run_batch + a manual process_slide).
"""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

import config

ENTRY_FIELDS = ("slide_id", "filename", "status", "urgency_score", "category", "gb_processed", "output_path")


def atomic_write_text(path, text):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def atomic_save(path, save_fn):
    """save_fn(tmp_path) writes the file (e.g. img.save, np.save); then it is swapped in."""
    path = Path(path)
    tmp = path.with_name(f".tmp.{path.name}")  # keep the extension so PIL/numpy pick the format
    save_fn(tmp)
    os.replace(tmp, path)


@contextmanager
def _locked_index():
    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.OUTPUTS_DIR / ".index.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            index = json.loads(config.INDEX_PATH.read_text()) if config.INDEX_PATH.exists() else {}
        except json.JSONDecodeError:
            index = {}
        index.setdefault("schema_version", config.SCHEMA_VERSION)
        index.setdefault("slides", [])
        yield index
        atomic_write_text(config.INDEX_PATH, json.dumps(index, indent=2))


def update_index(slide_id, **fields):
    """Create or update one slide's entry. Unknown slides are appended."""
    with _locked_index() as index:
        entry = next((e for e in index["slides"] if e["slide_id"] == slide_id), None)
        if entry is None:
            entry = {k: None for k in ENTRY_FIELDS}
            entry.update(slide_id=slide_id, filename=f"{slide_id}.tif", output_path=slide_id, gb_processed=0.0)
            index["slides"].append(entry)
        entry.update(fields)


def set_queued(slide_ids):
    """Mark slides queued (clearing old results) in one write, so the dashboard sees the whole tray at once."""
    with _locked_index() as index:
        by_id = {e["slide_id"]: e for e in index["slides"]}
        for sid in slide_ids:
            e = by_id.get(sid)
            if e is None:
                e = {"slide_id": sid, "filename": f"{sid}.tif", "output_path": sid}
                index["slides"].append(e)
            e.update(status="queued", urgency_score=None, category=None, gb_processed=0.0)
