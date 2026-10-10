"""CAMELYON16 ASAP XML -> polygons -> mask at heatmap resolution."""
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from skimage.draw import polygon as draw_polygon

import config


def load_polygons(slide_id):
    """Return (tumor, exclusion) lists of (N, 2) arrays of level-0 (x, y). Empty if no XML (normal slides)."""
    path = Path(config.ANNOTATIONS_DIR) / f"{slide_id}.xml"
    tumor, exclusion = [], []
    if not path.exists():
        return tumor, exclusion
    for ann in ET.parse(path).getroot().iter("Annotation"):
        pts = np.array([[float(c.get("X")), float(c.get("Y"))]
                        for c in sorted(ann.iter("Coordinate"), key=lambda c: int(c.get("Order")))])
        if len(pts) < 3:
            continue
        group = ann.get("PartOfGroup")
        if group in ("Tumor", "_0", "_1"):
            tumor.append(pts)
        elif group in ("Exclusion", "_2"):
            exclusion.append(pts)
    return tumor, exclusion


def annotation_mask(slide_id, shape, downsample):
    """Boolean mask (H, W) at heatmap resolution: tumor polygons minus exclusions."""
    tumor, exclusion = load_polygons(slide_id)
    mask = np.zeros(shape, dtype=bool)
    for polys, value in ((tumor, True), (exclusion, False)):
        for pts in polys:
            rr, cc = draw_polygon(pts[:, 1] / downsample, pts[:, 0] / downsample, shape=shape)
            mask[rr, cc] = value
    return mask
