"""AI Supply Chain Discovery — grounded bottleneck mapping.

Restores the v3 screener's AI supply-chain discovery (sfascreener.py option 8),
rebuilt on v4 principles (PART 5): the LLM proposes the physical supply-chain map
and the candidate choke points, then every suggested ticker is immediately
validated against the real quant stack — data-quality gate, Score v2, and the
grounded rubric cache.

Provenance is explicit: the MAP is AI-generated (EST); every validation number
is CACHED/LIVE from the local store. The LLM never gets to assert a score.
"""
from .. import store, config, data
from ..ai import llm
from ..quant import score as S

SUPPLY_CHAIN_SYS = """You are a SHADOW ALPHA SUPPLY CHAIN ANALYST at a macro hedge fund.
Your philosophy: Don't buy the end product. Buy the physical bottleneck that enables it.
You identify oligopolies, monopolies, and choke points in supply chains.
You think in second-order effects: "If X scales 100x, what physically breaks first?"

Given a trend or demand signal, you must:
1. Map the complete physical supply chain (raw materials -> components -> equipment -> logistics)
2. Identify the TOP 3 choke points (who controls the bottleneck?)
3. Suggest specific publicly-traded tickers for each bottleneck
4. Explain WHY each is a bottleneck (market concentration, no substitutes, high capex barriers)

Return ONLY valid JSON:
{"trend": "...",
 "supply_chain": ["layer1", "layer2"],
 "bottlenecks": [
   {"name": "...", "why_bottleneck": "...", "tickers": ["TICK1", "TICK2"],
    "market_concentration": "high/medium/low", "substitutability": "none/limited/many"}
 ],
 "top_pick": "TICKER",
 "thesis_summary": "..."}"""


def discover_supply_chain(trend: str) -> dict:
    """Map a supply chain for a trend; validate every suggested ticker."""
    out, debug = llm.complete_json(
        SUPPLY_CHAIN_SYS,
        "Map the supply chain for this trend and find the Shadow Alpha bottlenecks:\nTREND: " + trend,
        temperature=0.5,
    )
    if not out or "bottlenecks" not in out:
        return {"error": "AI returned no usable supply-chain map", "debug": debug}

    # Collect every suggested ticker
    suggested = set()
    for b in out.get("bottlenecks", []):
        for t in b.get("tickers", []):
            if t:
                suggested.add(str(t).upper().strip())
    if out.get("top_pick"):
        suggested.add(str(out["top_pick"]).upper().strip())

    # Grounding pass: validate each against the real quant stack
    validation = {t: _validate_ticker(t) for t in sorted(suggested)}

    # Attach per-bottleneck validation for the UI
    for b in out.get("bottlenecks", []):
        b["validation"] = {
            str(t).upper().strip(): validation.get(str(t).upper().strip(), {})
            for t in b.get("tickers", [])
        }

    out["validation"] = validation
    out["n_suggested"] = len(suggested)
    out["provenance"] = {
        "map": "EST (AI-generated, unvalidated thesis)",
        "validation": "CACHED/LIVE (real quant stack)",
    }
    return out


def _validate_ticker(ticker: str) -> dict:
    """Cross-reference one suggested ticker against the real data + quant stack."""
    res = {"ticker": ticker, "in_store": False, "usable": False}

    px = store.load_prices(ticker)
    if px.empty:
        res["note"] = "not in price store — add to universe.yaml and run fetch"
        return res

    res["in_store"] = True
    q = data.quality_report(ticker)
    res["usable"] = bool(q.get("usable"))
    res["bars"] = q.get("bars")
    res["last_date"] = str(q.get("last_date") or "")

    cfg = config.load()
    spy = store.load_prices(cfg["settings"]["benchmark"])

    fund = store.get_fundamentals(ticker)
    fund_norm = None
    if fund:
        fund_norm = {
            "gross_margin": fund.get("grossMargins"),
            "oper_margin": fund.get("operatingMargins"),
            "returnOnEquity": fund.get("returnOnEquity"),
        }
        res["sector"] = fund.get("sector")

    if not spy.empty and len(px) >= 250:
        s = S.score_v2(ticker, px.index[-1], {ticker: px}, spy, fund=fund_norm)
        if s:
            res["score_v2"] = s.get("total")
            res["verdict"] = s.get("verdict")
            res["bottleneck_prior"] = s.get("components", {}).get("bottleneck_prior")

    cached = store.get_cached_rubric(ticker)
    if cached:
        res["rubric_total"] = cached.get("raw", {}).get("total")
        res["rubric_age_days"] = cached.get("age_days")
    else:
        res["rubric_total"] = None

    return res