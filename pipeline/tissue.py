"""Tissue detection: Otsu on HSV saturation of a low-res view (CLAM-style)."""
import numpy as np
from scipy import ndimage
from skimage.filters import threshold_otsu


def tissue_mask(slide, grid_w, grid_h, downsample):
    """Boolean (grid_h, grid_w): True where a heatmap cell (downsample x downsample level-0 px) is tissue."""
    from PIL import Image
    level = slide.get_best_level_for_downsample(downsample / 4)
    ds = slide.level_downsamples[level]
    lw, lh = slide.level_dimensions[level]
    img = slide.read_region((0, 0), level, (lw, lh)).convert("RGB")
    canvas = Image.new("RGB", (round(grid_w * downsample / ds), round(grid_h * downsample / ds)), "white")
    canvas.paste(img, (0, 0))
    hsv = np.asarray(canvas.resize((grid_w, grid_h), Image.BILINEAR).convert("HSV"))
    sat, val = hsv[..., 1], hsv[..., 2]
    thr = max(threshold_otsu(sat), 15)
    mask = (sat > thr) & (val > 30)  # val>30 drops black scanner borders
    mask = ndimage.binary_opening(mask, iterations=1)
    return ndimage.binary_fill_holes(mask)
