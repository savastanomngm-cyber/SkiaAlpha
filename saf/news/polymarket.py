"""Polymarket intel. Returns markets with clickable links, end dates, and 24h volume."""
import json, requests

GAMMA = "https://gamma-api.polymarket.com"
HEADERS = {"User-Agent": "SkiaAlpha research contact@example.com"}
TIMEOUT = 12


def _extract(m, ev_slug=None):
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
        prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
    except Exception:
        return None
    if not outcomes or len(outcomes) != len(prices):
        return None
    yes = next((p for o, p in zip(outcomes, prices)
                if str(o).strip().lower() == "yes"), None)
    if yes is None:
        yes = prices[0]
    slug = m.get("slug") or ev_slug or ""
    return {
        "question": m.get("question") or m.get("title") or "",
        "yes": round(yes, 3),
        "volume": float(m.get("volumeNum") or m.get("volume") or 0),
        "volume24hr": float(m.get("volume24hr") or 0),
        "endDate": (m.get("endDate") or "")[:10],
        "link": f"https://polymarket.com/event/{slug}" if slug else "",
    }


def search_markets(query, limit=10, sort="relevance"):
    results = []
    # Primary: public search
    try:
        r = requests.get(f"{GAMMA}/public-search",
                         params={"q": query, "limit_per_type": limit},
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json() or {}
        for ev in (data.get("events") or [])[:limit]:
            ev_slug = ev.get("slug")
            mkts = ev.get("markets") or []
            if mkts:
                for m in mkts:
                    x = _extract(m, ev_slug)
                    if x:
                        results.append(x)
            else:
                x = _extract(ev, ev_slug)
                if x:
                    results.append(x)
    except Exception:
        pass
    # Fallback: browse active events sorted by 24h volume
    if not results:
        try:
            r = requests.get(f"{GAMMA}/events",
                             params={"closed": "false", "limit": limit,
                                     "order": "volume24hr", "ascending": "false"},
                             headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            for ev in (r.json() or []):
                ev_slug = ev.get("slug")
                mkts = ev.get("markets") or []
                if mkts:
                    for m in mkts:
                        x = _extract(m, ev_slug)
                        if x:
                            results.append(x)
                else:
                    x = _extract(ev, ev_slug)
                    if x:
                        results.append(x)
        except Exception:
            pass
    if sort == "volume24hr":
        results.sort(key=lambda x: x.get("volume24hr", 0), reverse=True)
    else:
        results.sort(key=lambda x: x.get("volume", 0), reverse=True)
    return results[:limit]