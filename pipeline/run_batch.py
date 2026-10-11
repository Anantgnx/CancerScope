"""Process a tray of slides one by one, driving the live queue in outputs/index.json.

    python -m pipeline.run_batch                          # every .tif in data/camelyon16/slides, random order
    python -m pipeline.run_batch --slides tumor_091,normal_108 --order name
    python -m pipeline.run_batch --skip-done              # restartable overnight run / live demo add-on

All slides are marked `queued` up front (one index write), then each goes
processing -> done (or error). The model and classifier are loaded once.
"""
import argparse
import random
import time

import torch

import config
from pipeline.index import set_queued
from pipeline.models import load_classifier, load_uni2h
from pipeline.process_slide import process_slide


def all_slide_ids():
    return sorted(p.stem for p in config.SLIDES_DIR.glob("*.tif"))


def is_done(slide_id):
    return (config.slide_output_dir(slide_id) / "metrics.json").exists()


def run(slide_ids, model, classifier, device, stride=1, batch=64, workers=4, reports=False):
    """Process slides in the given order; one failure doesn't stop the batch. Returns per-slide results."""
    set_queued(slide_ids)
    results, t0 = [], time.perf_counter()
    for i, sid in enumerate(slide_ids, 1):
        print(f"\n[{i}/{len(slide_ids)}] {sid}", flush=True)
        try:
            m = process_slide(sid, model=model, classifier=classifier, device=device,
                              stride=stride, batch=batch, workers=workers)
            if reports:
                from pipeline.report import write_report
                _, _, source = write_report(sid)
                print(f"  report: {source}", flush=True)
            results.append({"slide_id": sid, "ok": True, **{k: m[k] for k in
                            ("category", "urgency_score", "gb_processed", "processing_time_sec")}})
        except Exception as e:  # noqa: BLE001 -- keep the tray moving; status is already "error"
            print(f"  FAILED: {type(e).__name__}: {e}", flush=True)
            results.append({"slide_id": sid, "ok": False, "error": f"{type(e).__name__}: {e}"})
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
    model = load_uni2h(device)
    classifier = load_classifier(device)
    run(ids, model, classifier, device, stride=args.stride, batch=args.batch, workers=args.workers,
        reports=args.reports)


if __name__ == "__main__":
    main()
