#!/usr/bin/env bash
# GX10 / laptop setup. Run from the repo root: bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"

# OpenSlide C library: openslide-bin (pip) covers it; apt is only a backup and is skipped without sudo
if command -v apt >/dev/null && sudo -n true 2>/dev/null; then
  sudo apt update && sudo apt install -y openslide-tools || echo "apt install failed; relying on openslide-bin"
fi

# venv that can still see a system/NVIDIA-provided torch if one exists
[ -d .venv ] || python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -U pip

# CUDA torch: PyPI's default build may lack CUDA on aarch64, so install from PyTorch's cu130 index (GX10: CUDA 13)
if ! python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
fi

pip install -r requirements.txt

python - <<'PY'
import torch, openslide, timm
print("torch", torch.__version__, "cuda", torch.cuda.is_available(),
      torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
print("openslide", openslide.__library_version__, "timm", timm.__version__)
PY
