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