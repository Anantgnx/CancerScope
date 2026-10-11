import json

# Inside app/llm_copilot.py -> format_prompt()
def format_prompt(metrics: dict) -> str:
    return f"""You are a specialized clinical pathology assistant. Draft a concise, structured triage summary report based STRICTLY on the measured slide metrics below. Do not fabricate measurements.

[MEASURED METRICS]
Slide ID: {metrics.get('slide_id')}
AI Category: {metrics.get('category')}
Urgency Score: {metrics.get('urgency_score')}/10.0
Tumor Detected: {metrics.get('tumor_detected')}
Total Tissue Area: {metrics.get('total_tissue_area_mm2')} mm²
Tumor Area: {metrics.get('tumor_area_mm2')} mm² ({metrics.get('tumor_percentage')}%)
Number of Regions: {metrics.get('num_tumor_regions')}
Largest Region Extent: {metrics.get('largest_region_extent_mm')} mm
Max Probability Confidence: {metrics.get('max_confidence')}

OUTPUT FORMAT:
## Pathology Triage Summary
- **Priority Tier**: [High/Urgent/Normal based on urgency_score]
- **Diagnostic Category**: [The AI Category]
- **Key Findings**: [Tissue area, tumor burden, and largest region extent]
- **Recommendation**: [Pathologist immediate review or routine queue]
"""

def generate_pathology_report(metrics: dict) -> str:
    """Queries local vLLM/Nemotron running on GX10, or falls back to template."""
    prompt = format_prompt(metrics)
    
    # Optional: If OpenAI-compatible endpoint is running locally on GX10 (vLLM / Ollama)
    try:
        import urllib.request
        payload = json.dumps({
            "model": "nemotron",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:8000/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]
    except Exception:
        # Fallback offline template ensuring the demo never stalls
        priority = "CRITICAL / URGENT" if metrics.get("urgency_score", 0) >= 7.0 else "ROUTINE"
        return (
            f"## Pathology Triage Summary\n\n"
            f"- **Priority Tier**: {priority} (Score: {metrics.get('urgency_score')}/10.0)\n"
            f"- **Key Findings**: Suspected tumor occupies {metrics.get('tumor_area_mm2')} mm² "
            f"({metrics.get('tumor_percentage')}% of total {metrics.get('total_tissue_area_mm2')} mm² tissue across {metrics.get('num_tumor_regions')} distinct region(s)).\n"
            f"- **Confidence Assessment**: Peak probability {metrics.get('max_confidence')} (mean {metrics.get('mean_confidence')}).\n"
            f"- **Recommendation**: Prioritize for immediate manual histological confirmation by attending pathologist."
        )




def chat_with_copilot(metrics: dict, chat_history: list) -> str:
    """Handles multi-turn conversation with the local LLM."""

    if not chat_history:
        return "I have the slide metrics loaded. Ask me about the tumor burden, largest focus, or margin assessment."

    # Prepend the structured metrics as the system context
    system_prompt = format_prompt(metrics)
    messages = [{"role": "system", "content": system_prompt}] + chat_history

    try:
        import urllib.request
        payload = json.dumps({
            "model": "nemotron",
            "messages": messages,
            "temperature": 0.2
        }).encode("utf-8")

        req = urllib.request.Request(
            "http://localhost:8000/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]

    except Exception:
        last_msg = chat_history[-1]["content"].lower()
        tumor_area = float(metrics.get("tumor_area_mm2") or 0.0)
        largest_region_area = float(metrics.get("largest_region_mm2") or 0.0)
        largest_extent = float(metrics.get("largest_region_extent_mm") or 0.0)
        region_count = int(metrics.get("num_tumor_regions") or 0)

        if "margin" in last_msg or "clear" in last_msg:
            return (
                f"Based on the extracted metrics, the slide contains {tumor_area:.2f} mm² of suspicious tumor area. "
                f"The current summary does not include a direct pixel-level boundary measurement, so margin clarity is best interpreted as a review flag rather than a confirmed diagnostic statement."
            )
        if "compare" in last_msg or "largest" in last_msg:
            if largest_region_area > 0:
                secondary = max(region_count - 1, 0)
                return (
                    f"The largest region spans {largest_region_area:.2f} mm² with an extent of {largest_extent:.2f} mm, "
                    f"which is the dominant focus relative to the other {secondary} region(s) in the summary."
                )
            return (
                f"The current summary shows {region_count} suspicious region(s) and a total tumor area of {tumor_area:.2f} mm². "
                f"The largest focus is not separated into a distinct area metric in this snapshot, but the overall burden is concentrated in the dominant region."
            )

        return f"[Simulated Nemotron Response]\nContext received. Answering: '{chat_history[-1]['content']}'"