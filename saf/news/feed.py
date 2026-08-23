"""Relevance-scored news feed (PART 11)."""
import requests
import xml.etree.ElementTree as ET
import yfinance as yf
from ..security import clean_text

SHADOW_KEYWORDS = {
    "bottleneck": 2.0, "supply shortage": 2.0, "lead time": 1.5,
    "capacity sold out": 2.0, "sole supplier": 2.5, "export control": 2.0,
    "sanctions": 1.0, "tariff": 1.0, "13f": 1.0, "cowos": 2.5,
    "hbm": 2.0, "backlog": 1.5, "price increase": 1.5, "force majeure": 2.5,
    "monopoly": 2.5, "oligopoly": 2.5, "shortage": 1.5, "chokepoint": 2.5,
    "rare earth": 2.0, "helium": 2.0, "vial": 1.5, "boron": 2.0,
    "pricing power": 2.0, "export ban": 2.0, "national security": 1.5,
}
SIGNAL_THRESHOLD = 2.0

def relevance_score(headline):
    h = headline.lower()
    hits = {k: w for k, w in SHADOW_KEYWORDS.items() if k in h}
    return round(sum(hits.values()), 1), list(hits.keys())

def fetch_news(tickers, per_ticker=4, limit=40):
    items = []
    for t in tickers:
        try:
            raw = yf.Ticker(t).news or []
            for n in raw[:per_ticker]:
                content = n.get("content", n) if isinstance(n, dict) else n
                if not isinstance(content, dict): continue
                title = content.get("title", "")
                if not title: continue
                prov = content.get("provider")
                source = prov.get("displayName", "") if isinstance(prov, dict) else ""
                title = clean_text(title, 200)
                score, kws = relevance_score(title)
                items.append({"ticker": t, "title": title, "source": source, "link": "",
                              "relevance": score, "keywords": kws, "signal": score >= SIGNAL_THRESHOLD})
        except Exception:
            continue
    items.sort(key=lambda x: x["relevance"], reverse=True)
    return items[:limit]

def fetch_news_adhoc(query, limit=12):
    """Server-side Google News RSS fetch for ad-hoc theme searches."""
    url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, headers={"User-Agent": "SkiaAlpha research contact@example.com"}, timeout=8)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall(".//item")[:limit]:
            title = item.find("title").text if item.find("title") is not None else ""
            source = item.find("source").text if item.find("source") is not None else "Google News"
            link = item.find("link").text if item.find("link") is not None else ""
            title = clean_text(title, 200)
            score, kws = relevance_score(title)
            items.append({"ticker": query.upper(), "title": title, "source": source, "link": link,
                          "relevance": score, "keywords": kws, "signal": score >= SIGNAL_THRESHOLD})
        return items
    except Exception:
        return []