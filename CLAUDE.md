# CancerScope: Hackathon Project Brief (Local Pathology Triage on ASUS GX10)

This is the shared source of truth. When a decision changes, update this file first. Flag anything that conflicts with the decisions here.

## Event, track, team
- ASUS Local AI track, UCSC AI Hackathon @ SF Tech Week, Oct 10–11, 2026.
- Hardware: ASUS Ascent GX10 (NVIDIA GB10 Grace Blackwell, **ARM CPU**, 128 GB unified memory, ~1 PFLOP FP4, DGX OS / Ubuntu, Nemotron preloaded). Borrowed; return undamaged.
- Track requirements: build and demo running locally on the GX10; complete the ASUS feedback survey before judging ends.
- Judges look for: real problems, novel use of local hardware acceleration, clear value of local AI, something that inspires.
- Team: Pranav Gaddam (embedded/firmware/DSP/FPGA) and Anant Garg (CUDA/Nsight, FPGA, YOLO CV, AI agents).

## Problem and pitch
An AI box next to a pathology lab's slide scanner that triages biopsy slides locally so the most urgent cancer cases reach a pathologist first.
- US pathologists fell >17% (2007–2017) while workload rose >41%; delays up to 60 days. Slides arrive in random order.
- Pitch: "Your mom had a biopsy. She waits two weeks to learn if it's cancer, because her slide is sitting in a stack."
- Why local: WSIs are 2–4 GB (busy lab ~3 TB/day); privacy (nothing leaves the building); labs lack GPU servers (Paige went cloud-only); Pictor Labs launched on-prem pathology AI; GX10's 128 GB holds vision model + VLM + LLM at once.
- Decided: Local AI over Physical AI; pathology over genomics (DRAGEN already does genomics locally).

## Pipeline
1. Read WSI with OpenSlide; detect tissue, skip background.
2. Tile tissue (10x–20x if 40x too slow).
3. UNI2-h features per tile (frozen, 1536-d).
4. Small classifier trained on PCam features → tumor probability per tile.
5. Stitch into heatmap; slide metrics (tumor area, # regions, max confidence).
6. Rank slides by urgency.
7. Local LLM drafts a structured report from measured metrics (stretch: Q&A).

## Demo flow (~3 min)
1. Problem in 15 s: "80 slides this morning, random order; one is metastatic."
2. Drop the tray (10–20 slides): live queue fills with thumbnails, heatmaps, urgency, re-sorting. Counter "X GB analyzed · 0 bytes uploaded".
3. Click top case: OpenSeadragon gigapixel viewer, tumor glowing, zoom slide→cells.
4. Our heatmap next to the CAMELYON16 pathologist annotation.
5. One click: draft report.
6. Unplug network, process another slide.

## Scope tiers
Get one slide end-to-end by Saturday afternoon before polishing.
- **Must (Sat night):** tile→UNI→tumor prob; heatmap overlay in OpenSeadragon; triage dashboard + counter.
- **Should (late Sat/early Sun):** LLM report from metrics; accuracy vs CAMELYON16 annotations.
- **Stretch:** live copilot Q&A; new slide processed live.
- Schedule rule: pre-compute demo slides Saturday night; process only one small slide live.

## Models and data (all downloaded, in this repo)
| Item | Path | Notes |
|---|---|---|
| UNI2-h | models/pretrained/UNI2-h | primary feature extractor |
| UNI | models/pretrained/UNI | |
| CONCH | models/pretrained/CONCH | image-text |
| Prov-GigaPath | models/pretrained/prov-gigapath | backup |
| TIAToolbox ResNet50 (PCam) | models/pretrained/tiatoolbox-resnet50 | fallback, `kaczmarj/lymphnodes-tiatoolbox-resnet50.patchcamelyon`, NOT downloaded yet |
| PCam | data/pcam/data/*.parquet | 262,144 train / 32,768 valid / 32,768 test, 96x96 @ ~10x |
| CAMELYON16 slides | data/camelyon16/slides | tumor_091, 084, 075, 082, 107, 081; normal_108, 150, 042, 019 |
| CAMELYON16 XML | data/camelyon16/annotations | all ~160 |
| LLM | Nemotron preloaded on GX10 | backup: a MoE model |

Candidates to add for large tumors: tumor_047, 031, 061, 039, 036, 034, 011, 055.
References: github.com/mahmoodlab/UNI, github.com/mahmoodlab/CLAM.

## Directory layout
Anant's pipeline writes `outputs/`; Pranav's app only reads `outputs/` and the `.tif` slides. Viewer tiles served on demand via OpenSlide `DeepZoomGenerator`. Outputs named by real slide ID. All paths come from `config.py` (root from `CANCERSCOPE_ROOT`).

```
config.py  setup.sh  requirements.txt  README.md
models/{pretrained,trained}/        data/{pcam,camelyon16/{slides,annotations}}/
cache/{pcam_features,slide_features}/
outputs/index.json   outputs/_sample/   outputs/<slide_id>/{heatmap.png,heatmap.npy,thumbnail.png,metrics.json,report.md}
pipeline/ (Anant): extract_pcam_features.py, train_classifier.py, process_slide.py, run_batch.py
eval/ (Anant): compare_annotations.py
app/ (Pranav): server.py, static/{index.html,viewer.js}, llm_copilot.py
```

## Data contract (metrics.json schema v1)
```json
{
  "schema_version": 1, "slide_id": "tumor_001", "filename": "tumor_001.tif", "status": "done",
  "tumor_detected": true, "urgency_score": 9.4,
  "total_tissue_area_mm2": 245.8, "tumor_area_mm2": 34.2, "tumor_percentage": 13.91,
  "num_tumor_regions": 3, "largest_region_mm2": 28.7, "max_confidence": 0.982, "mean_confidence": 0.841,
  "regions": [{"id": 1, "area_mm2": 28.7, "max_confidence": 0.982,
               "centroid_px": [41230, 88120], "bbox_px": [39800, 86500, 43100, 90200]}],
  "slide_width_px": 97792, "slide_height_px": 221184, "mpp": 0.243, "heatmap_downsample": 384,
  "model": "UNI2-h + linear classifier v1", "gb_processed": 1.1, "processing_time_sec": 142
}
```
Rules:
- No `rank` stored; dashboard sorts by `urgency_score` (0–10).
- `tumor_detected` = ≥1 region ≥0.2 mm² with confidence >0.5 (final rule recorded in README).
- `mean_confidence` = mean prob over tiles inside tumor regions.
- All `_px` are level-0 pixels; `mpp` = microns/px at level 0. `bbox_px` = [x0, y0, x1, y1].
- Heatmap: one pixel = one tile. 96 px tiles at level 2 (4x) → 384 level-0 px per heatmap pixel; viewer stretches heatmap.png across `slide_width_px`. Use the measured value.
- `index.json`: every slide with `slide_id`, `status` (queued/processing/done/error), `urgency_score`, output path; dashboard polls ~2 s.

## How the pipeline works
UNI2-h is frozen; classifier trained on PCam features; Nemotron only reads numbers as text.

**Phase A (training, once):** load PCam → resize 96→224 → UNI2-h features → `cache/pcam_features/` (subsample 50k–100k if slow) → logistic regression/linear layer → `models/trained/tumor_classifier.pt` → accuracy + AUC on PCam test (headline number).

**Phase B (process_slide.py):** open .tif, read dims/mpp/levels → tissue mask from thumbnail (`total_tissue_area_mm2`) → tile at level 2, 96x96, tissue only (**matching PCam magnification is the most important correctness detail**) → bf16 batches UNI2-h + classifier, cache features in `cache/slide_features/` → heatmap.npy / heatmap.png (transparent→red) / thumbnail.png → regions: threshold >0.5, smooth, connected components, area mm² = count × (384·mpp)² / 10⁶ → urgency → metrics.json + index.json. `run_batch.py` marks all queued, processes one by one, updating status.

**Urgency rule** (largest region, scaled by confidence within band):
| Largest region | Category | Urgency |
|---|---|---|
| > 2 mm | Macrometastasis | 7–10 |
| 0.2–2 mm | Micrometastasis | 4–7 |
| < 0.2 mm | Isolated tumor cells | 1–4 |
| none | Negative | 0 |

## App (Pranav)
- server.py: `/api/index`, `/api/slide/<id>`, `/slide/<id>.dzi` + tiles, `/api/slide/<id>/heatmap.png`, `/api/slide/<id>/report`.
- Queue polls `/api/index`, sorts by urgency, thumbnail, category badge, score, spinner, counter.
- Viewer: OpenSeadragon + heatmap overlay stretched to `slide_width_px`, opacity slider, region buttons zooming to `bbox_px`.
- llm_copilot.py: Nemotron via local OpenAI-compatible API; prompt includes metrics.json and "only state numbers present in this data". Demo reports pre-generated.

## Evaluation (Anant)
1. Tile: PCam test accuracy + AUC (headline). 2. Slide: tumor slides flagged, normals low (sanity, 10 slides). 3. Region: rasterize XML to heatmap resolution, overlap, side-by-side image.
**Leakage caveat:** PCam was cut from these CAMELYON16 slides; demo heatmaps may look better than true performance. Use PCam test as the official number and say so if asked.

## Roles
| Area | Owner |
|---|---|
| Model pipeline (tiling, UNI, classifier, GPU throughput, heatmap + metrics) | Anant |
| Viewer + dashboard | Pranav |
| Report/copilot wiring | Pranav (Anant assists) |
| Evaluation | Anant |

## Risks and fallbacks
| Risk | Fallback |
|---|---|
| OpenSlide/PyTorch on ARM | Install early Saturday; test one slide first |
| WSI processing too slow | 10x–20x; pre-compute overnight |
| UNI fails | TIAToolbox ResNet50 trained on PCam |
| Wi-Fi slow | Everything pre-downloaded |
| LLM too slow | MoE model, or one-click report only |
| Clinical accuracy questions | Triage/assist, never diagnosis; show held-out accuracy |

**Constraints:** public data only; non-commercial per model licenses; never redistribute or commit model weights; return GX10 undamaged; complete ASUS survey.

## Moving code and data to GX10 (decided Oct 10)
All GPU work runs on the GX10 (SSH access available). Code goes through git (models/data are gitignored and never committed). On the GX10: `git clone`, `bash setup.sh`, `hf auth login`, `bash fetch_data.sh` (downloads UNI2-h, PCam, demo slides + XML into the config.py layout). Fallback if GX10 internet is slow: rsync from Anant's laptop.

## Checklist
- [ ] Anant: load UNI2-h, run 10 PCam tiles
- [ ] Anant: extract_pcam_features.py + train_classifier.py on ~2,000 tiles
- [ ] Anant: process_slide.py on normal_108, every 10th tile
- [ ] Anant: XML annotation parser in eval/
- [ ] Pranav: server.py serving a slide via DeepZoomGenerator in OpenSeadragon
- [ ] Pranav: heatmap overlay from the sample heatmap, alignment checked
- [ ] Pranav: queue polling index.json
- [ ] Pranav: report panel + copilot against any OpenAI-compatible API
- [ ] Integration: Anant's normal_108 output in Pranav's app
- [ ] Discord: GX10 handout time, internet, whether Nemotron runs as a service
