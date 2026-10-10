"""Phase 0: load UNI2-h, embed 10 PCam tiles, sanity-check features, benchmark throughput.
    python -m pipeline.smoke_test [--batches 64,128,256]   (GX10: --batches 128,256,512)
Re-run on the GX10 for the throughput comparison."""
import argparse
import io
import time

import numpy as np
import pyarrow.parquet as pq
import torch
from PIL import Image

import config
from pipeline.models import benchmark, embed, load_uni2h


def load_pcam_tiles(n_per_class=5):
    """First n tumor and n normal tiles from the PCam test split -> (uint8 (N,96,96,3), labels)."""
    f = sorted(config.PCAM_DIR.glob("test-*.parquet"))[0]
    table = pq.ParquetFile(f).read_row_group(0).to_pylist()
    pos = [r for r in table if r["label"]][:n_per_class]
    neg = [r for r in table if not r["label"]][:n_per_class]
    rows = pos + neg
    tiles = np.stack([np.asarray(Image.open(io.BytesIO(r["image"]["bytes"])).convert("RGB")) for r in rows])
    return tiles, np.array([r["label"] for r in rows])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", default="64,128,256")
    args = ap.parse_args()

    device = config.get_device()
    print(f"device: {device}", torch.cuda.get_device_name(0) if device == "cuda" else "")

    t0 = time.perf_counter()
    model = load_uni2h(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"UNI2-h loaded in {time.perf_counter() - t0:.1f}s, {n_params / 1e6:.0f}M params")

    tiles, labels = load_pcam_tiles()
    print(f"PCam tiles: {tiles.shape} {tiles.dtype}, labels {labels.astype(int).tolist()}")
    feats = embed(model, tiles, device).float()
    assert feats.shape == (10, config.FEATURE_DIM), feats.shape
    assert torch.isfinite(feats).all(), "non-finite features"
    print(f"features: {tuple(feats.shape)}, mean {feats.mean():.3f}, std {feats.std():.3f}")

    # sanity: tiles should be more similar to their own class than to the other class
    f = torch.nn.functional.normalize(feats, dim=1)
    sim = f @ f.T
    pos, neg = labels.astype(bool), ~labels.astype(bool)
    off = ~torch.eye(10, dtype=torch.bool)
    within = torch.cat([sim[pos][:, pos][off[:5, :5]], sim[neg][:, neg][off[:5, :5]]]).mean()
    across = sim[pos][:, neg].mean()
    print(f"cosine similarity: within-class {within:.3f}, across-class {across:.3f}")

    print("benchmark (96x96 uint8 in -> 1536-d out, incl. GPU resize):")
    benchmark(model, device, [int(b) for b in args.batches.split(",")])


if __name__ == "__main__":
    main()
