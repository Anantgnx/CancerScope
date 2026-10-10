"""Heatmap -> regions -> urgency -> metrics.json dict.

Shared by process_slide.py and make_sample_outputs.py so the schema has one source.
"""
import numpy as np
from PIL import Image
from scipy import ndimage

import config

# (category, min longest extent in mm, urgency band). Checked in order.
CATEGORIES = [
    ("Macrometastasis", 2.0, (7.0, 10.0)),
    ("Micrometastasis", 0.2, (4.0, 7.0)),
    ("Isolated tumor cells", 0.0, (1.0, 4.0)),
]


def find_regions(heatmap, downsample, mpp, threshold=config.TUMOR_THRESHOLD):
    """Threshold, close small gaps, 8-connected components. Coordinates in level-0 px."""
    mask = heatmap > threshold
    closed = ndimage.binary_closing(mask, structure=np.ones((3, 3)))
    labels, n = ndimage.label(closed, structure=np.ones((3, 3)))
    tile_mm2 = (downsample * mpp) ** 2 / 1e6
    regions = []
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        region = (labels[sl] == i) & mask[sl]  # only tiles actually above threshold
        if not region.any():
            continue
        probs = heatmap[sl][region]
        ys, xs = np.nonzero(region)
        y0, x0 = sl[0].start, sl[1].start
        regions.append({
            "area_mm2": round(float(region.sum() * tile_mm2), 4),
            "max_confidence": round(float(probs.max()), 4),
            "mean_confidence": float(probs.mean()),
            "n_tiles": int(region.sum()),
            "centroid_px": [int((x0 + xs.mean() + 0.5) * downsample),
                            int((y0 + ys.mean() + 0.5) * downsample)],
            "bbox_px": [int(sl[1].start * downsample), int(sl[0].start * downsample),
                        int(sl[1].stop * downsample), int(sl[0].stop * downsample)],
        })
    regions.sort(key=lambda r: r["area_mm2"], reverse=True)
    for i, r in enumerate(regions, start=1):
        r["id"] = i
    return regions


def extent_mm(region, mpp):
    x0, y0, x1, y1 = region["bbox_px"]
    return max(x1 - x0, y1 - y0) * mpp / 1000


def urgency(regions, mpp):
    """Category from the largest region's longest extent (clinical size rule), score scaled
    within the band by max confidence mapped from (threshold, 1] to [0, 1]."""
    if not regions:
        return "Negative", 0.0
    largest = max(regions, key=lambda r: extent_mm(r, mpp))
    ext = extent_mm(largest, mpp)
    conf = max(r["max_confidence"] for r in regions)
    t = np.clip((conf - config.TUMOR_THRESHOLD) / (1 - config.TUMOR_THRESHOLD), 0, 1)
    for name, min_mm, (lo, hi) in CATEGORIES:
        if ext > min_mm:
            break
    return name, round(float(lo + (hi - lo) * t), 2)


def build_metrics(slide_id, filename, heatmap, downsample, slide_w, slide_h, mpp,
                  tissue_area_mm2, model, gb_processed, processing_time_sec):
    regions = find_regions(heatmap, downsample, mpp)
    category, score = urgency(regions, mpp)
    tumor_area = sum(r["area_mm2"] for r in regions)
    n_tiles = sum(r["n_tiles"] for r in regions)
    detected = any(r["area_mm2"] >= config.MIN_REGION_MM2 and r["max_confidence"] > config.TUMOR_THRESHOLD
                   for r in regions)
    mean_conf = sum(r["mean_confidence"] * r["n_tiles"] for r in regions) / n_tiles if n_tiles else 0.0
    out_regions = [{k: r[k] for k in ("id", "area_mm2", "max_confidence", "centroid_px", "bbox_px")}
                   for r in regions]
    return {
        "schema_version": config.SCHEMA_VERSION,
        "slide_id": slide_id,
        "filename": filename,
        "status": "done",
        "tumor_detected": bool(detected),
        "urgency_score": score,
        "category": category,
        "total_tissue_area_mm2": round(tissue_area_mm2, 2),
        "tumor_area_mm2": round(tumor_area, 2),
        "tumor_percentage": round(100 * tumor_area / tissue_area_mm2, 2) if tissue_area_mm2 else 0.0,
        "num_tumor_regions": len(regions),
        "largest_region_mm2": regions[0]["area_mm2"] if regions else 0.0,
        "largest_region_extent_mm": round(max((extent_mm(r, mpp) for r in regions), default=0.0), 3),
        "max_confidence": max((r["max_confidence"] for r in regions), default=0.0),
        "mean_confidence": round(mean_conf, 4),
        "regions": out_regions,
        "slide_width_px": int(slide_w),
        "slide_height_px": int(slide_h),
        "mpp": round(mpp, 5),
        "heatmap_downsample": int(downsample),
        "heatmap_width": int(heatmap.shape[1]),
        "heatmap_height": int(heatmap.shape[0]),
        "model": model,
        "gb_processed": round(gb_processed, 3),
        "processing_time_sec": round(processing_time_sec, 1),
    }


def heatmap_rgba(heatmap, floor=0.2):
    """Transparent below `floor`, then yellow -> red with rising alpha."""
    p = np.clip((heatmap - floor) / (1 - floor), 0, 1)
    rgba = np.zeros((*heatmap.shape, 4), dtype=np.uint8)
    rgba[..., 0] = 255
    rgba[..., 1] = (200 * (1 - p)).astype(np.uint8)
    rgba[..., 3] = (230 * p).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")
