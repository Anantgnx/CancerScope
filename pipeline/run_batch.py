"""Process a tray of slides one by one, driving the live queue in outputs/index.json.

    python -m pipeline.run_batch                          # every .tif in data/camelyon16/slides, random order
    python -m pipeline.run_batch --slides tumor_091,normal_108 --order name
    python -m pipeline.run_batch --skip-done              # restartable overnight run / live demo add-on

All slides are marked `queued` up front (one index write), then each goes
processing -> done (or error). The model and classifier are loaded once.
"""
import argparse
import json
import random
import time

import torch

import config
from pipeline import live
from pipeline.index import set_queued
from pipeline.models import load_classifier, load_uni2h
from pipeline.process_slide import process_slide


def all_slide_ids():
    return sorted(p.stem for p in config.SLIDES_DIR.glob("*.tif"))


def is_done(slide_id):
    return (config.slide_output_dir(slide_id) / "metrics.json").exists()


INJECT = config.OUTPUTS_DIR / "inject.json"


def take_injected(queue):
    """Slides dropped into the tray mid-run (POST /api/demo/inject writes outputs/inject.json)."""
    if not INJECT.exists():
        return []
    try:
        ids = json.loads(INJECT.read_text())
    except json.JSONDecodeError:
        return []
    INJECT.unlink(missing_ok=True)
    new = [s for s in ids if s not in queue and config.slide_path(s).exists()]
    if new:
        set_queued(new)
        for s in new:
            live.event("inject", "New slide arrived from the scanner; analyzing it next", s)
    return new


def queue_rank(slide_id):
    """1-based position of slide_id among finished slides, most urgent first."""
    from pipeline.index import _locked_index
    with _locked_index() as index:
        done = sorted((e for e in index["slides"] if e.get("status") == "done"), key=lambda e: -(e.get("urgency_score") or 0))
        ids = [e["slide_id"] for e in done]
    return ids.index(slide_id) + 1 if slide_id in ids else None


def run(slide_ids, model, classifier, device, stride=1, batch=64, workers=4, reports=False, use_cache=True,
        started=False):
    """Process slides in order; one failure doesn't stop the batch; picks up injected slides. Returns results."""
    slide_ids = list(slide_ids)
    if not started:   # main() already announced the run before loading models
        set_queued(slide_ids)
        live.run_start(slide_ids)
    results, t0 = [], time.perf_counter()
    i = 0
    while True:
        # injected slides (an urgent case from the scanner) go next, not to the back of the tray
        slide_ids[i:i] = take_injected(slide_ids)
        live._state["slides_total"] = len(slide_ids)
        if i >= len(slide_ids):
            break
        sid = slide_ids[i]
        i += 1
        print(f"\n[{i}/{len(slide_ids)}] {sid}", flush=True)
        try:
            m = process_slide(sid, model=model, classifier=classifier, device=device,
                              stride=stride, batch=batch, workers=workers, use_cache=use_cache)
            live.slide_done(m, rank=queue_rank(sid))
            if reports:
                from pipeline.report import write_report
                live.stage("report", sid)
                t_r = time.perf_counter()
                _, _, source = write_report(sid)
                print(f"  report: {source}", flush=True)
                live.event("report", f"Report drafted in {time.perf_counter() - t_r:.1f} s · "
                           + ("Nemotron, numbers verified" if source.startswith("Nemotron") else source), sid)
            results.append({"slide_id": sid, "ok": True, **{k: m[k] for k in
                            ("category", "urgency_score", "gb_processed", "processing_time_sec")}})
        except Exception as e:  # noqa: BLE001 -- keep the tray moving; status is already "error"
            print(f"  FAILED: {type(e).__name__}: {e}", flush=True)
            results.append({"slide_id": sid, "ok": False, "error": f"{type(e).__name__}: {e}"})
    live.run_done()
    total = time.perf_counter() - t0

    print(f"\n{'slide':14s} {'category':22s} {'urgency':>7s} {'GB':>6s} {'sec':>6s}")
    for r in sorted(results, key=lambda r: -(r.get("urgency_score") or -1)):
        if r["ok"]:
            print(f"{r['slide_id']:14s} {r['category']:22s} {r['urgency_score']:7.2f} "
                  f"{r['gb_processed']:6.2f} {r['processing_time_sec']:6.0f}")
        else:
            print(f"{r['slide_id']:14s} ERROR  {r['error']}")
    gb = sum(r.get("gb_processed", 0) for r in results)
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} slides, {gb:.2f} GB in {total / 60:.1f} min "
          f"({gb / total * 60:.2f} GB/min) · 0 bytes uploaded", flush=True)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slides", help="comma-separated slide ids (default: all in data/camelyon16/slides)")
    ap.add_argument("--order", choices=["random", "name"], default="random",
                    help="random = an unsorted tray, like the demo story")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--skip-done", action="store_true", help="skip slides that already have metrics.json")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--reports", action="store_true", help="write report.md/.html (Nemotron) after each slide")
    ap.add_argument("--no-cache", action="store_true", help="re-embed every slide (live demo: really run UNI2-h)")
    args = ap.parse_args()

    ids = args.slides.split(",") if args.slides else all_slide_ids()
    missing = [s for s in ids if not config.slide_path(s).exists()]
    if missing:
        raise SystemExit(f"slides not found in {config.SLIDES_DIR}: {', '.join(missing)}")
    if not config.CLASSIFIER_PATH.exists():
        raise SystemExit(f"no classifier at {config.CLASSIFIER_PATH}; run pipeline.train_classifier first")
    if args.skip_done:
        ids = [s for s in ids if not is_done(s)]
    if args.order == "random":
        random.Random(args.seed).shuffle(ids)
    else:
        ids.sort()
    if not ids:
        print("nothing to do")
        return

    device = config.get_device()
    print(f"device {device}" + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else "")
          + f", {len(ids)} slides: {', '.join(ids)}", flush=True)
    # announce the run before the slow model load, so the page shows progress immediately
    set_queued(ids)
    live.run_start(ids)
    live.stage("load", None, "Loading UNI2-h (681M parameters) and the tumor classifier onto the GPU")
    t_load = time.perf_counter()
    model = load_uni2h(device)
    classifier = load_classifier(device)
    live.event("run", f"Models ready in {time.perf_counter() - t_load:.0f} s")
    run(ids, model, classifier, device, stride=args.stride, batch=args.batch, workers=args.workers,
        reports=args.reports, use_cache=not args.no_cache, started=True)


if __name__ == "__main__":
    main()
