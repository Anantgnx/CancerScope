"""Evaluation: PCam tile metrics + slide-level ranking + heatmap vs. pathologist outline.

    python -m eval.compare_annotations                     # every slide in outputs/
    python -m eval.compare_annotations --slides tumor_091  # one slide

Writes to outputs/eval/: <slide>_compare.png (outline | heatmap | overlay), slide_eval.json.
Also writes outputs/<slide>/annotation.json (pathologist polygons, level-0 px) for the viewer.

Leakage caveat: PCam tiles were cut from these CAMELYON16 slides, so region scores here
are optimistic. The official number is PCam test (from train_classifier.py).
"""
import argparse
import json

import numpy as np
from PIL import Image, ImageDraw
from sklearn.metrics import roc_auc_score

import config
from eval.annotations import annotation_mask, load_polygons
from pipeline.slide_metrics import heatmap_rgba

OUTLINE = (0, 200, 0, 255)


def region_scores(heat, truth, threshold):
    """Tile-level agreement on tissue tiles (heat > 0 marks scored tissue)."""
    tissue = heat > 0
    pred = (heat > threshold) & tissue
    t = truth & tissue
    tp, fp, fn = int((pred & t).sum()), int((pred & ~t).sum()), int((~pred & t).sum())
    out = {
        "tumor_tiles_truth": int(t.sum()), "tumor_tiles_pred": int(pred.sum()),
        "dice": round(2 * tp / (2 * tp + fp + fn), 4) if tp + fp + fn else None,
        "iou": round(tp / (tp + fp + fn), 4) if tp + fp + fn else None,
        "sensitivity": round(tp / (tp + fn), 4) if tp + fn else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
    }
    if 0 < t.sum() < tissue.sum():
        out["tile_auc"] = round(float(roc_auc_score(t[tissue], heat[tissue])), 4)
    return out


def compare_figure(out_dir, metrics, slide_id, title):
    """Three panels on the thumbnail: pathologist outline | our heatmap | both."""
    thumb = Image.open(out_dir / "thumbnail.png").convert("RGBA")
    sx = thumb.width / metrics["slide_width_px"]
    span = metrics["heatmap_downsample"] * sx
    heat = Image.open(out_dir / "heatmap.png").resize(
        (round(metrics["heatmap_width"] * span), round(metrics["heatmap_height"] * span)), Image.NEAREST)
    heat = heat.crop((0, 0, thumb.width, thumb.height))

    def outlined(img):
        img = img.copy()
        draw = ImageDraw.Draw(img)
        tumor, _ = load_polygons(slide_id)
        for pts in tumor:
            draw.line([tuple(p) for p in (pts * sx)] + [tuple(pts[0] * sx)], fill=OUTLINE, width=2)
        return img

    with_heat = thumb.copy()
    with_heat.alpha_composite(heat)
    panels = [("Pathologist", outlined(thumb)), ("CancerScope", with_heat), ("Overlay", outlined(with_heat))]
    pad, head = 8, 22
    fig = Image.new("RGBA", (3 * thumb.width + 4 * pad, thumb.height + head + 2 * pad + 18), "white")
    draw = ImageDraw.Draw(fig)
    for i, (label, img) in enumerate(panels):
        x = pad + i * (thumb.width + pad)
        draw.text((x, pad), label, fill="black")
        fig.paste(img, (x, pad + head))
    draw.text((pad, fig.height - 20), title, fill="black")
    return fig.convert("RGB")


def write_annotation_json(out_dir, slide_id):
    tumor, exclusion = load_polygons(slide_id)
    doc = {"slide_id": slide_id, "coordinates": "level-0 px, [[x, y], ...]",
           "tumor": [np.round(p, 1).tolist() for p in tumor],
           "exclusion": [np.round(p, 1).tolist() for p in exclusion]}
    (out_dir / "annotation.json").write_text(json.dumps(doc))


def evaluate_slide(slide_id, eval_dir, threshold):
    out_dir = config.slide_output_dir(slide_id)
    metrics = json.loads((out_dir / "metrics.json").read_text())
    heat = np.load(out_dir / "heatmap.npy")
    has_xml = (config.ANNOTATIONS_DIR / f"{slide_id}.xml").exists()
    row = {"slide_id": slide_id, "truth": "tumor" if has_xml else "normal",
           **{k: metrics[k] for k in ("category", "urgency_score", "tumor_detected", "max_confidence",
                                      "num_tumor_regions", "tumor_area_mm2")}}
    if has_xml:
        truth = annotation_mask(slide_id, heat.shape, metrics["heatmap_downsample"])
        row.update(region_scores(heat, truth, threshold))
        write_annotation_json(out_dir, slide_id)
        title = (f"{slide_id}: Dice {row['dice']}, sensitivity {row['sensitivity']}, "
                 f"precision {row['precision']} (tiles > {threshold})")
    else:
        title = f"{slide_id}: normal (no tumor annotation); predicted {metrics['category']}"
    if (out_dir / "thumbnail.png").exists():
        compare_figure(out_dir, metrics, slide_id, title).save(eval_dir / f"{slide_id}_compare.png")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slides", help="comma-separated ids (default: every slide with outputs)")
    ap.add_argument("--threshold", type=float, default=config.TUMOR_THRESHOLD)
    ap.add_argument("--out", help="eval output dir (default: <outputs>/eval)")
    args = ap.parse_args()

    eval_dir = config.OUTPUTS_DIR / "eval" if args.out is None else config.ROOT / args.out
    eval_dir.mkdir(parents=True, exist_ok=True)
    ids = args.slides.split(",") if args.slides else sorted(
        p.parent.name for p in config.OUTPUTS_DIR.glob("*/metrics.json") if not p.parent.name.startswith("_"))
    rows = [evaluate_slide(s, eval_dir, args.threshold) for s in ids]

    tumor = [r for r in rows if r["truth"] == "tumor"]
    normal = [r for r in rows if r["truth"] == "normal"]
    summary = {"n_slides": len(rows), "n_tumor": len(tumor), "n_normal": len(normal)}
    if tumor and normal:
        summary["slide_auc_urgency"] = round(roc_auc_score(
            [r["truth"] == "tumor" for r in rows], [r["urgency_score"] for r in rows]), 4)
        summary["all_tumor_ranked_above_all_normal"] = (
            min(r["urgency_score"] for r in tumor) > max(r["urgency_score"] for r in normal))
    summary["tumor_flagged"] = f"{sum(r['tumor_detected'] for r in tumor)}/{len(tumor)}"
    summary["normal_flagged"] = f"{sum(r['tumor_detected'] for r in normal)}/{len(normal)}"
    dice = [r["dice"] for r in tumor if r.get("dice") is not None]
    if dice:
        summary["mean_dice"] = round(float(np.mean(dice)), 4)
    pcam = config.ROOT / "outputs" / "eval" / "pcam_metrics.json"
    if pcam.exists():
        summary["pcam"] = json.loads(pcam.read_text())

    (eval_dir / "slide_eval.json").write_text(json.dumps({"summary": summary, "slides": rows}, indent=2))
    print(f"{'slide':12s} {'truth':7s} {'category':22s} {'urg':>5s} {'dice':>6s} {'sens':>6s} {'prec':>6s} {'auc':>6s}")
    fmt = lambda v: f"{v:6.3f}" if isinstance(v, float) else f"{'-':>6s}"  # noqa: E731
    for r in sorted(rows, key=lambda r: -r["urgency_score"]):
        print(f"{r['slide_id']:12s} {r['truth']:7s} {r['category']:22s} {r['urgency_score']:5.2f} "
              f"{fmt(r.get('dice'))} {fmt(r.get('sensitivity'))} {fmt(r.get('precision'))} {fmt(r.get('tile_auc'))}")
    print(json.dumps({k: v for k, v in summary.items() if k != "pcam"}, indent=1))
    if "pcam" in summary:
        print("PCam test:", summary["pcam"].get("test"))
    print(f"wrote {eval_dir}")


if __name__ == "__main__":
    main()
