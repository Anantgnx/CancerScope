"""UNI2-h loading, preprocessing and embedding. Shared by PCam training and slide scoring,
so both see identical preprocessing. Loads weights from disk only (works offline)."""
import time

import numpy as np
import timm
import torch
import torch.nn.functional as F

import config

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
DTYPE = torch.bfloat16


def load_uni2h(device=None, compile=False):
    """Build UNI2-h (ViT-H/14, 1536-d) from MahmoodLab's timm kwargs and load local weights."""
    device = device or config.get_device()
    model = timm.create_model(
        "vit_giant_patch14_224",
        pretrained=False,
        img_size=224,
        patch_size=14,
        depth=24,
        num_heads=24,
        init_values=1e-5,
        embed_dim=1536,
        mlp_ratio=2.66667 * 2,
        num_classes=0,
        no_embed_class=True,
        mlp_layer=timm.layers.SwiGLUPacked,
        act_layer=torch.nn.SiLU,
        reg_tokens=8,
        dynamic_img_size=True,
    )
    state = torch.load(config.UNI2H_DIR / "pytorch_model.bin", map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model = model.eval().to(device=device, dtype=DTYPE)
    if compile:
        model = torch.compile(model)
    return model


def preprocess(tiles, device):
    """uint8 tiles (N, H, W, 3) as numpy or tensor -> normalized (N, 3, 224, 224) bf16 on device.
    Resize happens on the GPU: 96 -> 224 bilinear, same as for PCam training."""
    if isinstance(tiles, np.ndarray):
        tiles = torch.from_numpy(tiles)
    x = tiles.to(device, non_blocking=True).permute(0, 3, 1, 2).float().div_(255)
    if x.shape[-1] != config.MODEL_INPUT_SIZE:
        x = F.interpolate(x, size=config.MODEL_INPUT_SIZE, mode="bilinear", align_corners=False)
    x = (x - MEAN.to(device)) / STD.to(device)
    return x.to(DTYPE)


@torch.inference_mode()
def embed(model, tiles, device):
    """uint8 tiles (N, H, W, 3) -> features (N, 1536) float16 on CPU."""
    return model(preprocess(tiles, device)).to(torch.float16).cpu()


def benchmark(model, device, batch_sizes=(64, 128, 256), n_batches=10):
    """Tiles/sec for 96x96 uint8 input at each batch size. Prints and returns {bs: tiles_per_sec}."""
    results = {}
    for bs in batch_sizes:
        tiles = torch.randint(0, 256, (bs, 96, 96, 3), dtype=torch.uint8)
        try:
            embed(model, tiles, device)  # warmup
            if device == "cuda":
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
            t0 = time.perf_counter()
            for _ in range(n_batches):
                embed(model, tiles, device)
            if device == "cuda":
                torch.cuda.synchronize()
            dt = time.perf_counter() - t0
        except torch.OutOfMemoryError:
            print(f"batch {bs:4d}: out of memory")
            torch.cuda.empty_cache()
            break
        results[bs] = bs * n_batches / dt
        peak = torch.cuda.max_memory_allocated() / 1e9 if device == "cuda" else float("nan")
        print(f"batch {bs:4d}: {results[bs]:7.1f} tiles/s, peak GPU mem {peak:.2f} GB")
    return results


def load_classifier(device=None):
    """Logistic-regression head saved by train_classifier.py -> callable features -> tumor probability."""
    device = device or config.get_device()
    ck = torch.load(config.CLASSIFIER_PATH, map_location=device, weights_only=True)
    w, b, mean, std = ck["weight"], ck["bias"], ck["mean"], ck["std"]

    def predict(features):
        x = (features.to(device).float() - mean) / std
        return torch.sigmoid(x @ w + b).cpu()

    return predict
