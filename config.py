"""All paths and shared constants in one place.

Root comes from CANCERSCOPE_ROOT (defaults to this file's directory), so the same
code runs on the laptop and the GX10 without edits.
"""
import os
from pathlib import Path

ROOT = Path(os.environ.get("CANCERSCOPE_ROOT", Path(__file__).resolve().parent))

# --- models ---
PRETRAINED_DIR = ROOT / "models" / "pretrained"
UNI2H_DIR = PRETRAINED_DIR / "UNI2-h"
UNI_DIR = PRETRAINED_DIR / "UNI"
CONCH_DIR = PRETRAINED_DIR / "CONCH"
GIGAPATH_DIR = PRETRAINED_DIR / "prov-gigapath"
TIA_RESNET_DIR = PRETRAINED_DIR / "tiatoolbox-resnet50"  # fallback, not downloaded yet
TRAINED_DIR = ROOT / "models" / "trained"
CLASSIFIER_PATH = TRAINED_DIR / "tumor_classifier.pt"

# --- data ---
PCAM_DIR = ROOT / "data" / "pcam" / "data"  # train-*/valid-*/test-*.parquet
SLIDES_DIR = ROOT / "data" / "camelyon16" / "slides"
ANNOTATIONS_DIR = ROOT / "data" / "camelyon16" / "annotations"

# --- cache ---
PCAM_FEATURES_DIR = ROOT / "cache" / "pcam_features"
SLIDE_FEATURES_DIR = ROOT / "cache" / "slide_features"

# --- outputs (the contract with the app) ---
# CANCERSCOPE_OUTPUTS=outputs/_sample lets the app run on fake data before the pipeline exists
OUTPUTS_DIR = Path(os.environ.get("CANCERSCOPE_OUTPUTS", ROOT / "outputs"))
if not OUTPUTS_DIR.is_absolute():
    OUTPUTS_DIR = ROOT / OUTPUTS_DIR
INDEX_PATH = OUTPUTS_DIR / "index.json"
SAMPLE_DIR = ROOT / "outputs" / "_sample"
SCHEMA_VERSION = 1


def slide_output_dir(slide_id: str) -> Path:
    return OUTPUTS_DIR / slide_id


def slide_path(slide_id: str) -> Path:
    return SLIDES_DIR / f"{slide_id}.tif"


# --- device ---
def get_device() -> str:
    """cuda on the GX10 / laptop GPU, mps on a Mac, else cpu. Override with CANCERSCOPE_DEVICE."""
    if dev := os.environ.get("CANCERSCOPE_DEVICE"):
        return dev
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


# --- pipeline constants (see Data contract in CLAUDE.md) ---
TILE_SIZE = 96          # px at the tiling level, matches PCam
TILE_LEVEL = 2          # ~10x on CAMELYON16 (4x downsample from 40x); verify per slide
MODEL_INPUT_SIZE = 224  # UNI2-h input
FEATURE_DIM = 1536      # UNI2-h output
TUMOR_THRESHOLD = 0.5
MIN_DETECT_EXTENT_MM = 0.2  # tumor_detected: a reported region wider than this (micrometastasis or larger)
# Hysteresis: a region (connected tiles > TUMOR_THRESHOLD) counts only if it has at least
# MIN_REGION_TILES tiles and one tile >= REGION_SEED_THRESHOLD. Drops 1-2 tile specks.
REGION_SEED_THRESHOLD = 0.9
MIN_REGION_TILES = 2

# --- LLM: Nemotron via Ollama on the GX10 (OpenAI-compatible); disable reasoning when calling ---
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "nemotron3:33b")
