# CancerScope demo: talking points

Bullets to cover on each page, not a word-for-word script. Pages in order: hero (`/`) → live pipeline (`/pipeline`) → dashboard (`/app`).

## Before you go on stage
- **Warm up Nemotron:** open any report and ask one question (the first call after idle takes ~60 s).
- **Reset:** pipeline page → Reset, so the tray starts unprocessed. Keep the hero page open in one tab.
- **Record a backup video** of one clean run in case anything fails live.
- **Fill in the ASUS feedback survey before judging ends** (track requirement).

## 1. Hero page (~40 s): the problem and the product
- **Hook:** "Your mom had a biopsy. She waits two weeks, because her slide is sitting in a stack." Point at the headline: 80 slides, random order, one is cancer.
- **Real problem:** US pathologists fell ~17% while caseload per pathologist rose ~41% (2007–2017). Slides are read in arrival order, not urgency order; understaffed labs can delay results by weeks.
- **What CancerScope does (one sentence):** reads every slide on a box under the desk, ranks the tray by urgency and drafts the report, so the most urgent patient is seen first.
- **Models and data (one sentence):** "UNI2-h, a 681M-parameter pathology foundation model, turns each tissue tile into a numeric fingerprint; our classifier, trained on 30,000 expert-labeled tiles from PatchCamelyon (PCam), turns that into a tumor probability; and NVIDIA's Nemotron, running locally, drafts the report from the measurements."
- **Headline accuracy:** 95.0% accuracy, AUC 0.988 on 32,768 held-out PCam test tiles, cut from separate CAMELYON16 test slides the model never saw.
- **Data:** public research data only, no real patient data. CAMELYON16 is a lymph-node metastasis benchmark of whole slides with pathologist-drawn tumor outlines; PCam is a set of small labeled tiles cut from it.
- **Why local (the track's core question):**
  - **Size:** a slide is 0.3–4 GB and a busy lab makes terabytes a day; uploading all of it is impractical.
  - **Privacy:** patient tissue never leaves the building, so IT and security reviews are shorter.
  - **Hospitals have storage, not GPU servers:** a desk-sized GX10 fills that gap.
- **Why the GX10:** 128 GB of unified memory holds the vision model and the LLM at the same time.
- Click **Run this morning's tray**.

## 2. Live pipeline page (~75 s): how it works, on the hardware
- **Set the scene:** "This tray is 10 real CAMELYON16 lymph-node slides, ~1 GB each."
- **Walk the 8 steps as they light up:**
  1. Read the gigapixel slide (OpenSlide).
  2. Find tissue, skip blank glass.
  3. Cut 10× tiles (87 µm), the same magnification as the PCam tiles the classifier learned from (matching it matters most for accuracy).
  4. **UNI2-h** on the GX10 GPU.
  5. PCam-trained classifier scores each tile as it streams in; point at the heatmap filling in.
  6. Group tumor tiles into foci.
  7. Rank by clinical size categories: macrometastasis (> 2 mm), micrometastasis, isolated tumor cells.
  8. **Nemotron** drafts the report locally; numbers are checked.
- **Hardware ("novel use of local acceleration"):** ~135 tiles/s, ~100 slides/hour on one box, GPU ~96%. UNI2-h (~6 GB) and Nemotron (~33 GB) both loaded in the 128 GB unified memory at once. 0 bytes uploaded.
- **Interactive moment:** at ~25 s press **Drop in urgent case** ("a STAT case just came off the scanner"). It is analyzed next and jumps to #1 (Macrometastasis, 3.91 mm, urgency 9.19). Mention "first urgent case found after N seconds" in the status strip.
- **Fast-forward, said honestly:** "For time, the rest of the tray loads results this box computed in full earlier today; they are labeled 'preloaded' in the log." Live, the full tray takes ~5 min.
- Click **Review cases**.

## 3. Dashboard (~75 s): what a pathologist gets, and the evidence
- **Queue:** sorted by urgency, colored by priority (STAT, urgent, priority, routine); tumor_082 on top.
- **Gigapixel viewer:** zoom from the whole slide to cells; point at the scale bar and the overview map.
- **Proof moment:** toggle **Pathologist outline**, the real expert-drawn tumor outline that ships with CAMELYON16 as ground truth; our red heatmap sits inside it.
- **Click a focus** to fly to it; each shows its size and peak probability.
- **Needle in a haystack:** open tumor_081: a 0.35 mm tumor in a large slide, found exactly where the pathologist marked it.
- **Evidence on the 10 CAMELYON16 demo slides:** every tumor slide ranks above every normal slide; 6/6 tumors detected, 0/4 normals flagged; sensitivity 0.86–1.00 against the pathologist outlines. Honest caveat: PCam was cut from these same CAMELYON16 slides, so the classifier may have seen tiles from them, which is why the held-out PCam test score (95.0%) is the headline.
- **Report:** structured findings, foci locations, priority and recommendation are filled in by code from the measurements; Nemotron writes only the impression, and every number in it is checked against the measurements.
- **Chat with Nemotron:** ask "Where should I look first?" (it answers with a location in mm); ask something it shouldn't answer ("Will she need chemo?") and show it declines.

## 4. Close (~15 s)
- **Inspiring:** "Every slide gets read. The urgent one just gets read first, on a box any hospital can put under a desk."
- **What's next:** more tissue types, scanner and lab-information-system integration, a clinical study.
- **Thank you.**

## How this maps to the ASUS judging criteria
| Criterion | Where you show it |
|---|---|
| Real problem | Hero: pathologist shortage, waiting patients |
| Novel use of local hardware acceleration | Pipeline: 681M-param model on the GPU live, tiles/s, GPU %, both models in 128 GB at once |
| Clear value of local AI | Hero (size, privacy, no GPU servers); "0 B uploaded" throughout |
| Inspiring | Urgent case jumping to #1; the 0.35 mm tumor found |
| Runs locally on the GX10 (track rule) | Everything, including Nemotron, served from the GX10 |

## Likely judge questions
- **"Was it trained on these slides?"** Partly: PCam, our training set, was cut from CAMELYON16, including the slides in the demo tray, so demo results are optimistic. The held-out number (AUC 0.988) is on PCam test tiles from separate CAMELYON16 test slides.
- **"Is the 95% your model?"** It is our classifier on UNI2-h features, trained and tested on PCam: a standard "linear probe" of a foundation model. What we built is everything around it: the whole-slide pipeline, triage, verified reports and chat, and the live run on the GX10.
- **"Is this real patient data?"** It is public, de-identified research data (CAMELYON16 and PCam); no private patient data was used.
- **"False positives?"** Yes: a spare normal slide came out as a micrometastasis, which is why this is triage with a pathologist always in the loop. The extra region on tumor_091 sits near the node's edge and looks like a known mimic.
- **"Why not the cloud?"** Data size, privacy and approval friction; hospitals lack GPU servers; it keeps working without internet.
- **"How fast at scale?"** ~100 slides/hour on one box today, with known optimizations left (e.g. smaller UNI2-h input; we measured it as compute-bound).

## Sources (verify starred items before quoting)
- Pathologist workforce −17.5%, workload per pathologist +41.7% (2007–2017): Metter et al., *JAMA Network Open*, 2019.
- \*"Up to 60 days" delay and \*"~3 TB per lab per day" come from our brief: find a citation, or say "can be weeks" and "terabytes per day".
- CAMELYON16: Ehteshami Bejnordi et al., *JAMA*, 2017 (399 lymph-node whole slides with pathologist-drawn tumor outlines). PatchCamelyon (PCam): Veeling et al., *MICCAI*, 2018 (327,680 labeled 96×96 tiles cut from CAMELYON16).
- UNI / UNI2-h: Chen et al., *Nature Medicine*, 2024 (Mahmood Lab). Nemotron: NVIDIA.
