#!/usr/bin/env bash
# GX10 / laptop setup. Run from the repo root: bash setup.sh
set -euo pipefail

# OpenSlide C library (openslide-bin from pip usually covers this; apt is the backup)
if command -v apt >/dev/null; then
  sudo apt update && sudo apt install -y openslide-tools python3-venv || echo "apt install failed; relying on openslide-bin"
fi

# venv that can still see a system/NVIDIA-provided torch (DGX OS ships one)
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -U pip

if ! python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  echo ">> No CUDA torch found. On the GX10 (aarch64, CUDA 13) install the CUDA wheel, e.g.:"
  echo "   pip install torch --index-url https://download.pytorch.org/whl/cu130"
fi

pip install -r requirements.txt

python - <<'PY'
import torch, openslide, timm
print("torch", torch.__version__, "cuda", torch.cuda.is_available(),
      torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
print("openslide", openslide.__library_version__, "timm", timm.__version__)
PY
