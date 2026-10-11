"""Re-apply the current category / urgency / tumor_detected rules to existing outputs,
without re-running the slides (keeps the original processing times).

    python -m pipeline.rescore              # every processed slide, then rewrite reports
    python -m pipeline.rescore --no-reports
"""
import argparse
import json

import config
from pipeline.index import atomic_write_text, update_index
from pipeline.slide_metrics import extent_mm, urgency


def rescore(slide_id):
    path = config.slide_output_dir(slide_id) / "metrics.json"
    m = json.loads(path.read_text())
    old = (m["category"], m["urgency_score"])
    m["category"], m["urgency_score"] = urgency(m["regions"], m["mpp"])
    m["tumor_detected"] = any(extent_mm(r, m["mpp"]) > config.MIN_DETECT_EXTENT_MM for r in m["regions"])
    atomic_write_text(path, json.dumps(m, indent=2))
    update_index(slide_id, urgency_score=m["urgency_score"], category=m["category"])
    return old, (m["category"], m["urgency_score"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-reports", action="store_true")
    args = ap.parse_args()
    ids = sorted(p.parent.name for p in config.OUTPUTS_DIR.glob("*/metrics.json") if not p.parent.name.startswith("_"))
    for sid in ids:
        (oc, ou), (nc, nu) = rescore(sid)
        print(f"{sid:12s} {oc:22s} {ou:5.2f} -> {nc:22s} {nu:5.2f}")
    if not args.no_reports:
        from pipeline.report import main as report_main
        import sys
        sys.argv = [sys.argv[0]]
        report_main()


if __name__ == "__main__":
    main()
