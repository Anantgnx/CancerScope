"""Triage reports: outputs/<slide_id>/report.md and report.html from metrics.json.

    python -m pipeline.report                       # every processed slide
    python -m pipeline.report --slides tumor_081    # one slide
    python -m pipeline.report --no-llm              # skip Nemotron, deterministic impression

Every number in the header, findings and priority is rendered by code from metrics.json.
Nemotron (local Ollama) only writes the short impression, which is rejected and
regenerated if it contains any number not in the findings, or falls back to a
deterministic impression.
"""
import argparse
import html
import json
import re
import time
import urllib.request
from datetime import datetime

import config
from pipeline.index import atomic_write_text

PCAM_AUC = "0.988"  # PCam test AUC, see README

# category -> (priority, priority line, recommendation, size definition)
TIERS = {
    "Macrometastasis": ("STAT", "Review first: same-day pathologist review",
                        "Prioritize for same-day review. Examine the highlighted foci at high magnification; "
                        "consider cytokeratin immunohistochemistry to confirm.",
                        "focus > 2 mm"),
    "Micrometastasis": ("URGENT", "Review ahead of routine cases",
                        "Review ahead of routine cases. Examine the highlighted focus at high magnification; "
                        "consider cytokeratin immunohistochemistry if morphology is equivocal.",
                        "focus > 0.2 mm and ≤ 2 mm"),
    "Isolated tumor cells": ("PRIORITY", "Review with attention to the highlighted area",
                             "Routine-priority review with attention to the highlighted area; isolated tumor "
                             "cell-sized foci may be artifacts or mimics.",
                             "focus ≤ 0.2 mm"),
    "Negative": ("ROUTINE", "Routine queue",
                 "No suspicious foci detected by AI triage. Routine review. A negative AI result does not "
                 "exclude metastasis.",
                 "no suspicious focus"),
}

DISCLAIMER = ("Research prototype. AI triage aid, not a diagnostic device; all findings require confirmation by a "
              "pathologist. Model trained and validated on public CAMELYON16 / PatchCamelyon data.")


def fmt(v, nd=2):
    return f"{v:,.{nd}f}"


def build_facts(m, analyzed_at):
    """Everything shown in the report except the impression, as (label, value) rows."""
    cat = m["category"]
    priority, priority_line, recommendation, size_def = TIERS[cat]
    mpp = m["mpp"]
    regions = m["regions"]

    def extent(r):
        x0, y0, x1, y1 = r["bbox_px"]
        return max(x1 - x0, y1 - y0) * mpp / 1000

    rows = [("AI category", f"{cat} ({size_def})")]
    if regions:
        rows += [
            ("Largest focus, greatest dimension", f"{fmt(m['largest_region_extent_mm'])} mm"),
            ("Suspicious foci", str(m["num_tumor_regions"])),
            ("Suspicious area", f"{fmt(m['tumor_area_mm2'])} mm² ({fmt(m['tumor_percentage'])}% of tissue analyzed)"),
            ("Peak / mean tumor probability", f"{fmt(m['max_confidence'])} / {fmt(m['mean_confidence'])}"),
        ]
    rows += [("Tissue analyzed", f"{fmt(m['total_tissue_area_mm2'], 1)} mm² at 10× (~{fmt(m['heatmap_downsample'] * mpp, 0)} µm tiles)")]

    foci = [{
        "n": r["id"],
        "extent": fmt(extent(r)),
        "area": fmt(r["area_mm2"]),
        "conf": fmt(r["max_confidence"]),
        "x": fmt(r["centroid_px"][0] * mpp / 1000, 1),
        "y": fmt(r["centroid_px"][1] * mpp / 1000, 1),
    } for r in regions[:5]]

    return {
        "slide_id": m["slide_id"], "category": cat, "priority": priority, "priority_line": priority_line,
        "urgency": fmt(m["urgency_score"]), "recommendation": recommendation, "rows": rows, "foci": foci,
        "more_foci": max(0, len(regions) - 5),
        "meta": [("Slide", m["slide_id"]), ("Specimen", "Lymph node, H&E whole-slide image"),
                 ("Analyzed", analyzed_at), ("Model", f"UNI2-h features + linear classifier (PCam test AUC {PCAM_AUC})"),
                 ("Compute", f"On-device, ASUS GX10 · {fmt(m['gb_processed'])} GB analyzed · 0 bytes uploaded")],
    }


LLM_ROWS = ("AI category", "Largest focus, greatest dimension", "Suspicious foci", "Suspicious area", "Tissue analyzed")


def facts_text(f):
    """The only facts the LLM sees: no probabilities or per-focus details, fewer chances to misstate."""
    lines = [f"{k}: {v}" for k, v in f["rows"] if k in LLM_ROWS]
    lines.append(f"Triage priority: {f['priority']}")
    return "\n".join(lines)


NUM = re.compile(r"\d+(?:[.,]\d+)*")
WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}
BANNED = re.compile(r"additional|other \w+ foci|carcinoma|likelihood|likely|extensive|diagnos|probabilit|confirm(s|ed) (the )?(presence|metast)",
                    re.IGNORECASE)


def numbers_ok(text, allowed_text):
    """Every number in text (digits or number words) must appear as a value in allowed_text,
    and no banned interpretive phrase may appear. Returns (ok, offending token)."""
    allowed = {float(n.replace(",", "")) for n in NUM.findall(allowed_text)}
    found = [n.replace(",", "") for n in NUM.findall(text)]
    found += [str(WORDS[w]) for w in re.findall(r"[a-z]+", text.lower()) if w in WORDS]
    for n in found:
        if not any(abs(float(n) - a) < 1e-9 for a in allowed):
            return False, n
    if m := BANNED.search(text):
        return False, m.group(0)
    return True, None


def fallback_impression(f):
    cat = f["category"]
    if cat == "Negative":
        return "No tumor-like regions were identified by AI triage on the tissue analyzed. Routine review is suggested."
    n = int(f["rows"][2][1])
    largest = f["rows"][1][1]
    what = "1 suspicious focus" if n == 1 else f"{n} suspicious foci, the largest"
    return (f"AI triage identified {what} measuring {largest} in greatest dimension, in the size range of "
            f"{cat.lower()}. Pathologist review of the highlighted {'region' if n == 1 else 'regions'} is recommended.")


def llm_impression(f, retries=3):
    """Nemotron writes 2-3 sentences; returns (text, source)."""
    allowed = facts_text(f) + " 0.2 2"  # category size definitions may be quoted
    prompt = (
        "You are assisting a pathologist by summarizing an AI triage result for a lymph node slide.\n"
        "Write the IMPRESSION section: 2 to 3 short sentences of plain clinical prose.\n"
        "Rules:\n"
        "- Use ONLY the facts below. Write numbers as digits, exactly as they appear below.\n"
        "- State the total number of suspicious foci exactly as given; never say 'additional' or 'other' foci.\n"
        "- Describe what the AI flagged and what review is suggested. Do not state or imply a diagnosis,\n"
        "  and do not interpret beyond the facts (no 'extensive', 'likely', 'carcinoma', probabilities).\n"
        "- Do not mention the slide ID. No markdown, no headings, no bullet points.\n\n"
        f"FACTS:\n{facts_text(f)}"
    )
    url = config.LLM_BASE_URL + "/chat/completions"
    body = {"model": config.LLM_MODEL, "temperature": 0.1, "max_tokens": 220,
            "reasoning_effort": "none", "messages": [{"role": "user", "content": prompt}]}
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                text = json.load(r)["choices"][0]["message"]["content"].strip()
        except Exception as e:  # noqa: BLE001 -- LLM down: deterministic text keeps the demo going
            return fallback_impression(f), f"template (LLM unavailable: {type(e).__name__})"
        text = re.sub(r"[*#`]", "", text).strip()
        ok, bad = numbers_ok(text, allowed)
        if ok and 0 < len(text) < 900:
            return text, "Nemotron (nemotron3:33b, local), numbers verified against measurements"
        last = bad
    return fallback_impression(f), f"template (LLM output rejected: unsupported number {last!r})"


def render_md(f, impression, source):
    out = [f"# CancerScope AI Triage Report", ""]
    out += [" · ".join(f"**{k}:** {v}" for k, v in f["meta"][:3]), ""]
    out += [f"## Triage priority: {f['priority']}", f"{f['priority_line']} (urgency {f['urgency']} / 10)", ""]
    out += ["## Findings", "", "| Measurement | Value |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in f["rows"]]
    if f["foci"]:
        out += ["", "**Suspicious foci** (location in mm from the slide's top-left corner)", "",
                "| # | Greatest dimension | Area | Peak probability | Location (x, y) |", "|---|---|---|---|---|"]
        out += [f"| {c['n']} | {c['extent']} mm | {c['area']} mm² | {c['conf']} | {c['x']}, {c['y']} mm |" for c in f["foci"]]
        if f["more_foci"]:
            out += [f"", f"_+{f['more_foci']} smaller foci not listed._"]
    out += ["", "## Impression", impression, "", "## Recommendation", f["recommendation"], ""]
    out += ["---", " · ".join(f"{k}: {v}" for k, v in f["meta"][3:]), f"Impression: {source}.", "", f"_{DISCLAIMER}_", ""]
    return "\n".join(out)


PRIORITY_COLOR = {"STAT": "#b42318", "URGENT": "#c4320a", "PRIORITY": "#b54708", "ROUTINE": "#067647"}


def render_html(f, impression, source):
    e = html.escape
    color = PRIORITY_COLOR[f["priority"]]
    rows = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in f["rows"])
    foci = ""
    if f["foci"]:
        foci = ("<h2>Suspicious foci</h2><p class='note'>Location in mm from the slide's top-left corner.</p><table class='foci'>"
                "<tr><th>#</th><th>Greatest dimension</th><th>Area</th><th>Peak probability</th><th>Location (x, y)</th></tr>"
                + "".join(f"<tr><td>{c['n']}</td><td>{c['extent']} mm</td><td>{c['area']} mm²</td><td>{c['conf']}</td>"
                          f"<td>{c['x']}, {c['y']} mm</td></tr>" for c in f["foci"])
                + "</table>" + (f"<p class='note'>+{f['more_foci']} smaller foci not listed.</p>" if f["more_foci"] else ""))
    meta = "".join(f"<div><span>{e(k)}</span>{e(v)}</div>" for k, v in f["meta"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>CancerScope report · {e(f['slide_id'])}</title>
<style>
body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1d2939;max-width:760px;margin:32px auto;padding:0 24px}}
header{{display:flex;justify-content:space-between;align-items:baseline;border-bottom:2px solid #1d2939;padding-bottom:8px}}
header h1{{font-size:20px;margin:0}} header .brand{{color:#667085;font-size:12px;letter-spacing:.08em;text-transform:uppercase}}
.meta{{display:grid;grid-template-columns:1fr 1fr;gap:4px 24px;margin:14px 0 18px;font-size:13px}}
.meta span{{display:block;color:#667085;font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
.priority{{border-left:6px solid {color};background:#f9fafb;padding:12px 16px;margin:8px 0 20px}}
.priority b{{color:{color};font-size:18px;letter-spacing:.04em}} .priority div{{color:#475467}}
h2{{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#475467;margin:22px 0 8px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #eaecf0;vertical-align:top}}
table:not(.foci) th{{width:42%;color:#475467;font-weight:500}} table.foci th{{color:#475467;font-weight:500;background:#f9fafb}}
.note{{color:#667085;font-size:12px;margin:4px 0}} p.text{{margin:0}}
footer{{margin-top:28px;padding-top:10px;border-top:1px solid #eaecf0;color:#667085;font-size:11px}}
@media print{{body{{margin:0}}}}
</style></head><body>
<header><h1>AI Triage Report</h1><div class="brand">CancerScope</div></header>
<div class="meta">{meta}</div>
<div class="priority"><b>{e(f['priority'])}</b> &nbsp;{e(f['category'])}<div>{e(f['priority_line'])} · urgency {e(f['urgency'])} / 10</div></div>
<h2>Findings</h2><table>{rows}</table>{foci}
<h2>Impression</h2><p class="text">{e(impression)}</p>
<h2>Recommendation</h2><p class="text">{e(f['recommendation'])}</p>
<footer>Impression: {e(source)}.<br>{e(DISCLAIMER)}</footer>
</body></html>"""


def write_report(slide_id, use_llm=True):
    out = config.slide_output_dir(slide_id)
    path = out / "metrics.json"
    m = json.loads(path.read_text())
    analyzed_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    f = build_facts(m, analyzed_at)
    impression, source = llm_impression(f) if use_llm else (fallback_impression(f), "template (LLM disabled)")
    atomic_write_text(out / "report.md", render_md(f, impression, source))
    atomic_write_text(out / "report.html", render_html(f, impression, source))
    return f, impression, source


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slides", help="comma-separated ids (default: every slide with metrics.json)")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()
    ids = args.slides.split(",") if args.slides else sorted(
        p.parent.name for p in config.OUTPUTS_DIR.glob("*/metrics.json") if not p.parent.name.startswith("_"))
    for sid in ids:
        t0 = time.perf_counter()
        f, impression, source = write_report(sid, use_llm=not args.no_llm)
        print(f"{sid:12s} {f['priority']:8s} {time.perf_counter() - t0:5.1f}s  [{source}]\n    {impression}", flush=True)


if __name__ == "__main__":
    main()
