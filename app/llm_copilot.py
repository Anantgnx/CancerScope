"""Copilot chat + report drafting against the local Nemotron (Ollama on the GX10).

Grounding: the model sees only this slide's measurements, and every number in a reply is
checked against them (pipeline.report.numbers_ok). A reply that fails is retried once with
a correction, then replaced by an honest deterministic answer. If the local model is down,
the user is told so; nothing is ever presented as model output when it is not.
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from pipeline.report import build_facts, fallback_impression, numbers_ok, write_report  # noqa: E402

MAX_TURNS = 8  # recent messages sent to the model
# chat may discuss probabilities and say "not a diagnosis"; still block speculative or diagnostic wording
CHAT_BANNED = re.compile(r"carcinoma|likelihood|\blikely\b|\badditional\b", re.IGNORECASE)


def generate_pathology_report(metrics: dict) -> str:
    """Write report.md/.html for the slide (Nemotron impression, verified) and return the markdown."""
    write_report(metrics["slide_id"])
    return (config.slide_output_dir(metrics["slide_id"]) / "report.md").read_text()


def _facts(metrics: dict):
    """Everything the chat may state: findings, per-focus details, priority."""
    f = build_facts(metrics, analyzed_at="")
    lines = [f"Slide: {metrics['slide_id']}", f"Triage priority: {f['priority']} ({f['priority_line']})",
             f"Urgency score: {metrics['urgency_score']} out of 10"]
    lines += [f"{k}: {v}" for k, v in f["rows"]]
    for c in f["foci"]:
        lines.append(f"Focus {c['n']}: {c['extent']} mm greatest dimension, {c['area']} mm², peak probability "
                     f"{c['conf']}, located {c['x']} mm from the left and {c['y']} mm from the top of the slide")
    if f["more_foci"]:
        lines.append(f"Plus {f['more_foci']} smaller foci not listed")
    lines.append(f"Recommendation: {f['recommendation']}")
    lines.append("Size categories: macrometastasis > 2 mm; micrometastasis > 0.2 mm and up to 2 mm; "
                 "isolated tumor cells up to 0.2 mm.")
    return "\n".join(lines), f


SYSTEM = """You are CancerScope's assistant, helping a pathologist triage a lymph node slide.
Answer in 1 to 3 short sentences of plain prose. No markdown, no lists, no headings.
Use ONLY the measurements below. Never invent a number; write numbers exactly as they appear below.
Probabilities are the model's tumor scores, not certainty: quote them as given (e.g. 0.98), never as
percentages or as "confident". You assist triage; do not give a diagnosis or treatment advice. If the measurements cannot answer
the question, say so briefly and suggest what the pathologist could check on the slide.

MEASUREMENTS:
{facts}"""


def _call(messages: list) -> str:
    body = {"model": config.LLM_MODEL, "messages": messages, "temperature": 0.1, "max_tokens": 260,
            "reasoning_effort": "none"}  # nemotron3 is a reasoning model; otherwise content comes back empty
    req = urllib.request.Request(config.LLM_BASE_URL + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:  # first call after idle loads the model (~60 s)
        return json.load(r)["choices"][0]["message"]["content"].strip()


def chat_with_copilot(metrics: dict, chat_history: list) -> str:
    """Multi-turn chat grounded in metrics.json; returns the assistant reply text."""
    facts, f = _facts(metrics)
    if not chat_history:
        return "I have this slide's measurements loaded. Ask about the foci, their size, or why it was prioritized."

    history = [m for m in chat_history if m.get("role") in ("user", "assistant")][-MAX_TURNS:]
    messages = [{"role": "system", "content": SYSTEM.format(facts=facts)}] + history
    allowed = facts + " 0.2 2 1 3 10"  # category limits and the 0-10 scale may be quoted
    try:
        for _ in range(2):
            text = _call(messages).replace("**", "").replace("#", "").strip()
            ok, bad = numbers_ok(text, allowed, banned=CHAT_BANNED)
            if ok and text:
                return text
            messages = messages + [{"role": "assistant", "content": text}, {"role": "user", "content":
                f"That answer contained '{bad}', which is not supported by the measurements. "
                "Answer again using only the measurements, in 1 to 3 sentences."}]
    except Exception as e:  # noqa: BLE001
        return (f"The local model is not reachable right now ({type(e).__name__}). "
                f"From the measurements: {fallback_impression(f)}")
    return f"I can only answer from this slide's measurements. {fallback_impression(f)}"
