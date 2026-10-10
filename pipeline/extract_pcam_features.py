"""Phase A step 2: PCam tiles -> UNI2-h features in cache/pcam_features/<tag>.npz.

    python -m pipeline.extract_pcam_features --split test             # all 32,768
    python -m pipeline.extract_pcam_features --split valid --n 8000   # class-balanced subsample
    python -m pipeline.extract_pcam_features --split train --n 30000

Resumable: features are written in shards; re-running skips finished shards.
The subsample is deterministic (--seed), so a resumed run picks the same tiles.
"""
import argparse
import io
import time

import numpy as np
import pyarrow.parquet as pq
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

import config
from pipeline.models import embed, load_uni2h

SHARD = 4096


class PNGTiles(Dataset):
    def __init__(self, pngs):
        self.pngs = pngs

    def __len__(self):
        return len(self.pngs)

    def __getitem__(self, i):
        return np.array(Image.open(io.BytesIO(self.pngs[i])).convert("RGB"))


def select_rows(files, n, seed):
    """Return {file: sorted row indices} and labels in that order. n=0 means every row.
    Subsamples are class-balanced."""
    labels = [pq.read_table(f, columns=["label"])["label"].to_numpy(zero_copy_only=False) for f in files]
    file_idx = np.concatenate([np.full(len(l), i) for i, l in enumerate(labels)])
    row_idx = np.concatenate([np.arange(len(l)) for l in labels])
    y = np.concatenate(labels).astype(np.uint8)
    if n:
        rng = np.random.default_rng(seed)
        pick = np.concatenate([rng.choice(np.flatnonzero(y == c), n // 2, replace=False) for c in (0, 1)])
        pick.sort()
        file_idx, row_idx, y = file_idx[pick], row_idx[pick], y[pick]
    return file_idx, row_idx, y


def load_pngs(files, file_idx, row_idx):
    pngs = []
    for i, f in enumerate(files):
        rows = row_idx[file_idx == i]
        if len(rows):
            col = pq.read_table(f, columns=["image"])["image"].take(rows)
            pngs.extend(d["bytes"] for d in col.to_pylist())
    return pngs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["train", "valid", "test"])
    ap.add_argument("--n", type=int, default=0, help="class-balanced subsample size; 0 = all")
    ap.add_argument("--tag", help="output name (default: split)")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    tag = args.tag or args.split

    files = sorted(config.PCAM_DIR.glob(f"{args.split}-*.parquet"))
    file_idx, row_idx, y = select_rows(files, args.n, args.seed)
    n = len(y)
    print(f"{tag}: {n} tiles from {len(files)} files ({y.mean():.1%} tumor)", flush=True)

    shard_dir = config.PCAM_FEATURES_DIR / f"{tag}_shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    todo = [s for s in range(0, n, SHARD) if not (shard_dir / f"{s:07d}.npy").exists()]

    if todo:
        device = config.get_device()
        model = load_uni2h(device)
        t0, done = time.perf_counter(), 0
        for s in todo:
            sl = slice(s, min(s + SHARD, n))
            pngs = load_pngs(files, file_idx[sl], row_idx[sl])
            loader = DataLoader(PNGTiles(pngs), batch_size=args.batch, num_workers=args.workers,
                                pin_memory=device == "cuda")
            feats = torch.cat([embed(model, batch, device) for batch in loader]).numpy()
            np.save(shard_dir / f"{s:07d}.npy", feats)
            done += len(pngs)
            rate = done / (time.perf_counter() - t0)
            left = (sum(min(SHARD, n - t) for t in todo) - done) / rate
            print(f"  shard {s // SHARD + 1}/{-(-n // SHARD)}: {rate:.1f} tiles/s, ~{left / 60:.1f} min left",
                  flush=True)

    feats = np.concatenate([np.load(shard_dir / f"{s:07d}.npy") for s in range(0, n, SHARD)])
    assert feats.shape == (n, config.FEATURE_DIM) and np.isfinite(feats).all()
    out = config.PCAM_FEATURES_DIR / f"{tag}.npz"
    np.savez(out, features=feats, labels=y, file_idx=file_idx, row_idx=row_idx)
    print(f"wrote {out} {feats.shape}", flush=True)


if __name__ == "__main__":
    main()
