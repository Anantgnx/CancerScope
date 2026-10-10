#!/usr/bin/env bash
# Download models and data into the layout config.py expects. Resumable; safe to re-run.
#   bash fetch_data.sh            # uni2h + pcam + slides
#   bash fetch_data.sh slides     # one part: uni2h | models | pcam | slides
# Gated models need `hf auth login` first (token with access to MahmoodLab repos).
# Run after setup.sh (uses the venv's `hf`).
set -euo pipefail
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && source .venv/bin/activate

S3=https://camelyon-dataset.s3.amazonaws.com/CAMELYON16
# Demo slides from CLAUDE.md. Override: SLIDES="tumor_047 tumor_031" bash fetch_data.sh slides
SLIDES=${SLIDES:-"tumor_091 tumor_084 tumor_075 tumor_082 tumor_107 tumor_081 normal_108 normal_150 normal_042 normal_019"}

uni2h() {
  hf download MahmoodLab/UNI2-h --local-dir models/pretrained/UNI2-h
}

models() {  # everything else; not needed for the must-haves
  uni2h
  hf download MahmoodLab/UNI --local-dir models/pretrained/UNI
  hf download MahmoodLab/CONCH --local-dir models/pretrained/CONCH
  hf download prov-gigapath/prov-gigapath --local-dir models/pretrained/prov-gigapath
}

pcam() {
  hf download 1aurent/PatchCamelyon --repo-type dataset --local-dir data/pcam
}

slides() {
  mkdir -p data/camelyon16/slides data/camelyon16/annotations
  for s in $SLIDES; do
    local f="data/camelyon16/slides/$s.tif"
    local remote local_size
    remote=$(curl -fsSIL "$S3/images/$s.tif" | grep -i '^content-length' | tail -1 | tr -dc '0-9')
    local_size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [[ "$local_size" == "$remote" ]]; then
      echo ">> $s already complete ($((remote / 1000000)) MB)"
    else
      echo ">> $s ($((remote / 1000000)) MB)"
      curl -fL -C - --retry 5 -o "$f" "$S3/images/$s.tif"
    fi
    if [[ $s == tumor_* ]]; then
      curl -fsSL --retry 5 -o "data/camelyon16/annotations/$s.xml" "$S3/annotations/$s.xml"
    fi
  done
}

case "${1:-all}" in
  all) uni2h; pcam; slides ;;
  uni2h|models|pcam|slides) "$1" ;;
  *) echo "usage: $0 [all|uni2h|models|pcam|slides]"; exit 1 ;;
esac
du -sh models/pretrained/* data/* 2>/dev/null
