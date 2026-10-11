"""Phase B: one slide -> outputs/<slide_id>/{heatmap.npy, heatmap.png, thumbnail.png, metrics.json}.

    python -m pipeline.process_slide normal_108 --stride 10   # plumbing test, every 10th tissue tile
    python -m pipeline.process_slide tumor_091                 # full slide

Tiles are 96x96 at the level closest to PCam's ~0.97 um/px (level 2 on CAMELYON16).
One heatmap pixel = one tile. Features are cached in cache/slide_features/, so a
re-run with a new classifier only re-scores (use --no-cache to re-embed).
"""
import argparse
import json
import math
import time

import numpy as np
import openslide
import torch
from torch.utils.data import DataLoader, Dataset

import config
from pipeline import live
from pipeline.index import atomic_save, atomic_write_text, update_index
from pipeline.models import embed, load_classifier, load_uni2h
from pipeline.slide_metrics import build_metrics, heatmap_rgba
from pipeline.tissue import tissue_mask

PCAM_MPP = 0.972     # PCam tiles: 96 px at ~10x
BLOCK = 16           # read 16x16 tiles (1536x1536 px) per read_region call
MODEL_NAME = "UNI2-h + logistic regression (PCam)"


def pick_level(slide, mpp):
    """Level whose downsample is closest to PCam's magnification."""
    target = PCAM_MPP / mpp
    return min(range(slide.level_count), key=lambda l: abs(math.log(slide.level_downsamples[l] / target)))


class TileBlocks(Dataset):
    """Each item: all requested tiles inside one BLOCK x BLOCK group of grid cells."""

    def __init__(self, path, level, downsample, coords):
        self.path, self.level, self.ds = str(path), level, downsample
        self.slide = None
        blocks = {}
        for r, c in coords:
            blocks.setdefault((r // BLOCK, c // BLOCK), []).append((r, c))
        self.blocks = list(blocks.items())

    def __len__(self):
        return len(self.blocks)

    def __getitem__(self, i):
        if self.slide is None:  # one handle per DataLoader worker
            self.slide = openslide.OpenSlide(self.path)
        (br, bc), cells = self.blocks[i]
        T = config.TILE_SIZE
        region = self.slide.read_region((bc * BLOCK * self.ds, br * BLOCK * self.ds), self.level,
                                        (BLOCK * T, BLOCK * T))
        arr = np.array(region)
        rgb = arr[..., :3]
        rgb[arr[..., 3] == 0] = 255  # outside the slide -> white glass, not black
        tiles = np.stack([rgb[(r - br * BLOCK) * T:(r - br * BLOCK + 1) * T,
                              (c - bc * BLOCK) * T:(c - bc * BLOCK + 1) * T] for r, c in cells])
        return torch.from_numpy(tiles), torch.tensor(cells)


def embed_tiles(path, level, downsample, coords, model, device, batch, workers, on_chunk=None):
    loader = DataLoader(TileBlocks(path, level, downsample, coords), batch_size=None, shuffle=False,
                        num_workers=workers, pin_memory=device == "cuda", prefetch_factor=4 if workers else None)
    feats, cells, buf, buf_cells = [], [], [], []
    done, t0, last = 0, time.perf_counter(), 0.0

    def flush():
        nonlocal done
        x = torch.cat(buf)
        chunk = torch.cat([embed(model, x[i:i + batch], device) for i in range(0, len(x), batch)])
        cc = torch.cat(buf_cells)
        feats.append(chunk)
        cells.append(cc)
        done += len(x)
        buf.clear()
        buf_cells.clear()
        rate = done / max(time.perf_counter() - t0, 1e-6)
        live.progress(done, len(coords), rate)
        if on_chunk:
            on_chunk(chunk, cc)

    for tiles, cc in loader:
        buf.append(tiles)
        buf_cells.append(cc)
        if sum(len(b) for b in buf) >= batch * 4:
            flush()
            if time.perf_counter() - last > 10:
                last = time.perf_counter()
                rate = done / (last - t0)
                print(f"  {done}/{len(coords)} tiles, {rate:.1f} tiles/s, ~{(len(coords) - done) / rate / 60:.1f} min left",
                      flush=True)
    if buf:
        flush()
    if not feats:
        return torch.empty(0, config.FEATURE_DIM, dtype=torch.float16), torch.empty(0, 2, dtype=torch.long)
    return torch.cat(feats), torch.cat(cells)


def process_slide(slide_id, model=None, classifier=None, device=None, stride=1, batch=64, workers=4,
                  use_cache=True):
    """Process one slide end to end; returns the metrics dict. Pass model/classifier to reuse across slides."""
    t_start = time.perf_counter()
    device = device or config.get_device()
    path = config.slide_path(slide_id)
    update_index(slide_id, status="processing", filename=path.name)
    live.slide_start(slide_id, path.stat().st_size / 1e9)
    try:
        slide = openslide.OpenSlide(str(path))
        out = config.slide_output_dir(slide_id)
        out.mkdir(parents=True, exist_ok=True)
        atomic_save(out / "thumbnail.png", lambda p: slide.get_thumbnail((512, 512)).save(p, format="PNG"))
        w, h = slide.dimensions
        mpp = float(slide.properties[openslide.PROPERTY_NAME_MPP_X])
        level = pick_level(slide, mpp)
        ds = int(round(config.TILE_SIZE * slide.level_downsamples[level]))
        gw, gh = math.ceil(w / ds), math.ceil(h / ds)
        print(f"{slide_id}: {w}x{h} px, mpp {mpp:.4f}, level {level} "
              f"(tile = {ds * mpp:.1f} um vs PCam {96 * PCAM_MPP:.1f} um), grid {gw}x{gh}", flush=True)

        tissue = tissue_mask(slide, gw, gh, ds)
        coords = np.argwhere(tissue)[::stride]
        print(f"  tissue: {int(tissue.sum())} tiles, scoring {len(coords)} (stride {stride})", flush=True)
        tile_mm2 = (ds * mpp) ** 2 / 1e6
        live.stage("tissue", slide_id, f"Tissue detected: {tissue.sum() * tile_mm2:.1f} mm², blank glass skipped",
                   tissue_mm2=round(float(tissue.sum() * tile_mm2), 1), regions=None)
        live.stage("tile", slide_id, f"{len(coords):,} tiles at 10× ({ds * mpp:.0f} µm each)", tiles_total=len(coords))

        if classifier is None:
            classifier = load_classifier(device)
        heat = np.zeros((gh, gw), dtype=np.float32)
        last_png = [0.0]

        def on_chunk(chunk_feats, chunk_cells):  # score tiles as they stream in; the page watches the heatmap grow
            heat[chunk_cells[:, 0].numpy(), chunk_cells[:, 1].numpy()] = classifier(chunk_feats).numpy()
            if time.perf_counter() - last_png[0] > 1.5:
                last_png[0] = time.perf_counter()
                atomic_save(out / "heatmap_live.png", lambda p: heatmap_rgba(heat).save(p, format="PNG"))

        cache = config.SLIDE_FEATURES_DIR / f"{slide_id}.npz"
        if use_cache and cache.exists() and np.load(cache)["stride"] == stride:
            d = np.load(cache)
            feats, cells = torch.from_numpy(d["features"]), torch.from_numpy(d["cells"])
            print(f"  loaded cached features {tuple(feats.shape)}", flush=True)
            live.stage("embed", slide_id, "UNI2-h features loaded from cache")
            live.progress(len(feats), len(feats), 0)
        else:
            if model is None:
                model = load_uni2h(device)
            live.stage("embed", slide_id, "UNI2-h (ViT-H, 681M params) embedding tiles on the GPU · classifier scoring as they stream")
            t_embed = time.perf_counter()
            feats, cells = embed_tiles(path, level, ds, coords, model, device, batch, workers, on_chunk=on_chunk)
            live.event("stage", f"{len(feats):,} tiles embedded at {len(feats) / max(time.perf_counter() - t_embed, 1e-6):.0f} tiles/s",
                       slide_id, stage="classify")
            config.SLIDE_FEATURES_DIR.mkdir(parents=True, exist_ok=True)
            atomic_save(cache, lambda p: np.savez(p, features=feats.numpy(), cells=cells.numpy(),
                                                  level=level, downsample=ds, stride=stride))

        live.stage("classify", slide_id)
        probs = classifier(feats).numpy() if len(feats) else np.zeros(0, np.float32)
        heat[cells[:, 0].numpy(), cells[:, 1].numpy()] = probs
        atomic_save(out / "heatmap_live.png", lambda p: heatmap_rgba(heat).save(p, format="PNG"))

        live.stage("regions", slide_id)
        metrics = build_metrics(
            slide_id, path.name, heat, ds, w, h, mpp,
            tissue_area_mm2=float(tissue.sum() * tile_mm2),
            model=MODEL_NAME + (f", stride {stride}" if stride > 1 else ""),
            gb_processed=path.stat().st_size / 1e9,
            processing_time_sec=time.perf_counter() - t_start,
        )
        n = metrics["num_tumor_regions"]
        live.event("stage", f"{n} suspicious region{'s' if n != 1 else ''} after filtering specks" if n else
                   "No suspicious regions", slide_id, stage="regions")
        live.stage("urgency", slide_id, regions=n)
        atomic_save(out / "heatmap.npy", lambda p: np.save(p, heat))
        atomic_save(out / "heatmap.png", lambda p: heatmap_rgba(heat).save(p, format="PNG"))
        atomic_write_text(out / "metrics.json", json.dumps(metrics, indent=2))
        update_index(slide_id, status="done", urgency_score=metrics["urgency_score"],
                     category=metrics["category"], gb_processed=metrics["gb_processed"])
        print(f"  done in {metrics['processing_time_sec']:.0f}s: {metrics['category']}, "
              f"urgency {metrics['urgency_score']}, {metrics['num_tumor_regions']} regions, "
              f"max conf {metrics['max_confidence']:.3f}", flush=True)
        return metrics
    except Exception as e:
        update_index(slide_id, status="error")
        live.event("error", f"{type(e).__name__}: {e}", slide_id)
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slide_id")
    ap.add_argument("--stride", type=int, default=1, help="score every Nth tissue tile (plumbing tests)")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-cache", action="store_true", help="re-embed even if cached features exist")
    args = ap.parse_args()
    process_slide(args.slide_id, stride=args.stride, batch=args.batch, workers=args.workers,
                  use_cache=not args.no_cache)


if __name__ == "__main__":
    main()
