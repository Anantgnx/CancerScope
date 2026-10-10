"""Write fake-but-realistic outputs to outputs/_sample/ so the app can be built first.

Uses real slide dimensions and puts fake tumor exactly on the pathologist's outline,
so the viewer overlay can be checked for alignment. Run:
    python -m pipeline.make_sample_outputs
Then run the app with HACKDEV_OUTPUTS=outputs/_sample.
"""
import json
import math

import numpy as np
import openslide
from scipy import ndimage

import config
from eval.annotations import annotation_mask
from pipeline.slide_metrics import build_metrics, heatmap_rgba
from pipeline.tissue import tissue_mask

# (slide_id, status) -- a mix so the queue shows every state
SAMPLES = [("tumor_091", "done"), ("tumor_075", "done"), ("normal_108", "done"),
           ("tumor_084", "processing"), ("normal_042", "queued")]
DOWNSAMPLE = config.TILE_SIZE * 4  # 96 px at level 2 (4x) = 384 level-0 px


def make_one(slide_id, rng):
    path = config.slide_path(slide_id)
    slide = openslide.OpenSlide(str(path))
    w, h = slide.dimensions
    mpp = float(slide.properties.get(openslide.PROPERTY_NAME_MPP_X, 0.243))
    gw, gh = math.ceil(w / DOWNSAMPLE), math.ceil(h / DOWNSAMPLE)

    tissue = tissue_mask(slide, gw, gh, DOWNSAMPLE)
    tumor = annotation_mask(slide_id, (gh, gw), DOWNSAMPLE).astype(np.float32)
    heat = ndimage.gaussian_filter(tumor, 1.0) * rng.uniform(0.85, 0.99)
    heat += 0.3 * rng.beta(1, 6, size=heat.shape)  # background noise, stays below threshold
    heat = np.where(tissue, np.clip(heat, 0, 1), 0).astype(np.float32)

    out = config.SAMPLE_DIR / slide_id
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "heatmap.npy", heat)
    heatmap_rgba(heat).save(out / "heatmap.png")
    slide.get_thumbnail((512, 512)).save(out / "thumbnail.png")

    tile_mm2 = (DOWNSAMPLE * mpp) ** 2 / 1e6
    metrics = build_metrics(
        slide_id, path.name, heat, DOWNSAMPLE, w, h, mpp,
        tissue_area_mm2=float(tissue.sum() * tile_mm2),
        model="SAMPLE (fake heatmap from annotation)",
        gb_processed=path.stat().st_size / 1e9,
        processing_time_sec=float(rng.uniform(60, 180)),
    )
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (out / "report.md").write_text(
        f"# Draft triage report: {slide_id}\n\n*SAMPLE placeholder, not LLM output.*\n\n"
        f"- Category: {metrics['category']}\n- Urgency: {metrics['urgency_score']}/10\n"
        f"- Tumor regions: {metrics['num_tumor_regions']}, largest {metrics['largest_region_mm2']} mm²\n")
    return metrics


def main():
    rng = np.random.default_rng(0)
    entries = []
    for slide_id, status in SAMPLES:
        m = make_one(slide_id, rng)
        print(f"{slide_id:12s} {m['category']:22s} urgency={m['urgency_score']:5.2f} "
              f"regions={m['num_tumor_regions']} heatmap={m['heatmap_width']}x{m['heatmap_height']}")
        done = status == "done"
        entries.append({
            "slide_id": slide_id,
            "filename": m["filename"],
            "status": status,
            "urgency_score": m["urgency_score"] if done else None,
            "category": m["category"] if done else None,
            "gb_processed": m["gb_processed"] if done else 0.0,
            "output_path": slide_id,  # relative to the outputs dir
        })
    index = {"schema_version": config.SCHEMA_VERSION, "slides": entries}
    (config.SAMPLE_DIR / "index.json").write_text(json.dumps(index, indent=2))
    print("wrote", config.SAMPLE_DIR)


if __name__ == "__main__":
    main()
