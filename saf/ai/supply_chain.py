"""AI Supply Chain Discovery — restored to original working behavior.
Uses the same prompt and approach from sfascreener.py that worked.
Explicit task='deep' ensures Nous Ox Alpha is always first."""
from . import llm

# This is the EXACT prompt from sfascreener.py that was working
SUPPLY_CHAIN_SYS = """You are a SHADOW ALPHA SUPPLY CHAIN ANALYST at a macro hedge fund.
Your philosophy: Don't buy the end product. Buy the physical bottleneck that enables it.
You identify oligopolies, monopolies, and choke points in supply chains.
You think in second-order effects: "If X scales 100x, what physically breaks first?"
Given a trend or demand signal, you must:
1. Map the complete physical supply chain (raw materials → components → equipment → logistics)
2. Identify the TOP 3 choke points (who controls the bottleneck?)
3. Suggest specific publicly-traded tickers for each bottleneck
4. Explain WHY each is a bottleneck (market concentration, no substitutes, high capex barriers)
Return ONLY valid JSON:
{
  "trend": "...",
  "supply_chain": ["layer1", "layer2", ...],
  "bottlenecks": [
    {
      "name": "...",
      "why_bottleneck": "...",
      "tickers": ["TICK1", "TICK2"],
      "market_concentration": "high/medium/low",
      "substitutability": "none/limited/many"
    }
  ],
  "top_pick": "TICKER",
  "thesis_summary": "..."
}"""


def discover(trend: str):
    """Map a trend to its supply chain bottlenecks.
    
    Uses task='deep' which now ALWAYS routes to DEEP_CHAIN (Nous first),
    regardless of UI mode. This restores the original working behavior."""
    print(f"[supply_chain] Discovering for: {trend} (task=deep, Nous first)")

    # Same call pattern as the working sfascreener.py version
    out, debug = llm.complete_json(
        SUPPLY_CHAIN_SYS,
        f"Map the supply chain for this trend and find the Shadow Alpha bottlenecks:\nTREND: {trend}",
        temperature=0.5,
        max_tokens=4000,
        task="deep",       # ALWAYS gets DEEP_CHAIN (Nous first) now
        timeout=300,
    )

    if not out:
        err_msg = debug.get("error", "unknown") if isinstance(debug, dict) else str(debug)
        print(f"[supply_chain] LLM returned nothing. Error: {err_msg}")
        return {"error": f"AI returned no usable supply-chain map: {err_msg}", "debug": debug}

    if "bottlenecks" not in out:
        print(f"[supply_chain] No 'bottlenecks' key. Got keys: {list(out.keys())}")
        for alt in ["choke_points", "chokepoints", "bottleneck", "constraints"]:
            if alt in out:
                out["bottlenecks"] = out[alt]
                break
        if "bottlenecks" not in out:
            return {"error": f"AI returned JSON but no 'bottlenecks' key. Got: {list(out.keys())}",
                    "debug": debug}

    if not isinstance(out["bottlenecks"], list) or len(out["bottlenecks"]) == 0:
        return {"error": "AI returned empty bottlenecks list", "debug": debug}

    # Collect tickers
    all_tickers = set()
    for b in out.get("bottlenecks", []):
        if isinstance(b, dict):
            for t in b.get("tickers", []):
                if t and isinstance(t, str):
                    all_tickers.add(t.upper().strip())
    if out.get("top_pick") and isinstance(out["top_pick"], str):
        all_tickers.add(out["top_pick"].upper().strip())

    out["all_tickers"] = sorted(all_tickers)
    out["provenance"] = {"map": "EST (AI-generated thesis, unvalidated)"}
    print(f"[supply_chain] SUCCESS: {len(out['bottlenecks'])} bottlenecks, {len(all_tickers)} tickers")
    return out