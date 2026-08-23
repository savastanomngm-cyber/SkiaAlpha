"""Grounded rubric scoring with mandatory citations (PART 5 Step 2)."""
import json
from . import llm, evidence

RUBRIC_GROUNDED_SYS = """You are a bottleneck analyst. You will receive an
EVIDENCE PACK containing excerpts from the company's own SEC filings.
Rules:
1. Score each criterion 1-5 ONLY if the evidence pack contains direct support.
2. For each score, quote the exact supporting sentence from the pack.
3. If no support exists, score the criterion 2 (neutral) and write "INSUFFICIENT EVIDENCE".
4. Never use your own knowledge of the company to fill gaps.
5. Return ONLY valid JSON:
{"scores": {"market_concentration": N, "substitutability": N, "capital_intensity": N, "regulatory_moat": N, "demand_inelasticity": N, "cross_sector_demand": N},
 "citations": {"market_concentration": "...", "substitutability": "...", "capital_intensity": "...", "regulatory_moat": "...", "demand_inelasticity": "...", "cross_sector_demand": "..."},
 "total": N}"""

def grounded_rubric(ticker, pack):
    out, debug = llm.complete_json(RUBRIC_GROUNDED_SYS,
                                   f"EVIDENCE PACK:\n{json.dumps(pack, indent=1)}",
                                   temperature=0.2)
    if not out or "scores" not in out: return None
    ptxt = evidence.pack_text(pack)
    flagged = []
    scores = out.get("scores", {})
    for crit, quote in out.get("citations", {}).items():
        if quote and quote != "INSUFFICIENT EVIDENCE":
            if quote not in ptxt:
                scores[crit] = 2
                flagged.append(crit)
    out["scores"] = scores
    out["total"] = sum(scores.values())
    out["flagged_hallucinations"] = flagged
    out["debug"] = debug
    return out

def score_bottleneck(ticker, pack):
    """Public alias for grounded_rubric used by server endpoints."""
    return grounded_rubric(ticker, pack)