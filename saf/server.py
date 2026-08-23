"""FastAPI research server (v4.2.1). Browser is read-only client over this API.
Adds: on-demand fetching for AI-discovered tickers, dossier memo, basket membership, 
add/remove tickers, 3-news & 3-polymarket dossier feeds."""
import json, time, signal, os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from . import config, store, data
from .security import get_key
from .quant import score as S
from .ai import llm

store.init()
app = FastAPI(title="Skia Alpha Fund v4.2", version="4.2.1")


@asynccontextmanager
async def lifespan(_app):
    store.audit_log("server_start",
                    {"ai_key_present": bool(get_key("NOUS_API_KEY") or get_key("GROQ_API_KEY"))})
    yield


app.router.lifespan_context = lifespan
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded,
                          lambda r, e: JSONResponse(429, {"detail": f"Rate limit: {e.detail}"}))
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])


def provenance(source, asof=None, note=None):
    return {"source": source, "asof": asof, "note": note,
            "served_at": datetime.now().isoformat(timespec="seconds")}


_PX = {"cache": {}, "built": 0.0}


def _px(ticker):
    if time.time() - _PX["built"] > 900:
        _PX["cache"], _PX["built"] = {}, time.time()
    if ticker not in _PX["cache"]:
        _PX["cache"][ticker] = store.load_prices(ticker)
    return _PX["cache"][ticker]


def _ensure_prices(ticker: str) -> pd.DataFrame:
    """Load prices from cache; if missing, attempt live fetch from Yahoo.
    This fixes 404s for AI-discovered tickers that aren't in the local DB yet."""
    px = _px(ticker)
    if px.empty:
        try:
            print(f"[info] {ticker} not in cache, attempting on-demand fetch...")
            ok = data.refresh_ticker(ticker)
            if ok:
                _PX["cache"].pop(ticker, None)  # invalidate cache
                return _px(ticker)
        except Exception as e:
            print(f"[warn] on-demand fetch for {ticker} failed: {e}")
    return px


def _period_return(series, days):
    series = series.dropna()
    if len(series) < 2:
        return None
    if days == "YTD":
        yp = series[series.index.year == series.index[-1].year]
        if len(yp) < 2:
            return None
        base = yp.iloc[0]
    else:
        valid = series[series.index >= series.index[-1] - pd.Timedelta(days=days)]
        if len(valid) < 2:
            return None
        base = valid.iloc[0]
    return round((series.iloc[-1] / base - 1) * 100, 2)


def _f(v):
    try:
        return None if pd.isna(float(v)) else round(float(v), 4)
    except Exception:
        return None


def _baskets_for(ticker):
    """All baskets (YAML + custom) that contain this ticker."""
    merged = config.baskets()
    return [name for name, holdings in merged.items() if ticker in holdings]


# ═══════════════════ SYSTEM ═══════════════════
@app.get("/api/system/health")
def system_health(request: Request):
    cfg = config.load(); bench = cfg["settings"]["benchmark"]; spy = _px(bench)
    return {"status": "ok",
            "ai_key_present": bool(get_key("NOUS_API_KEY") or get_key("GROQ_API_KEY")),
            "primary_provider": "nous" if get_key("NOUS_API_KEY") else "groq",
            "benchmark": bench, "benchmark_bars": int(len(spy)),
            "benchmark_last": str(spy.index[-1].date()) if not spy.empty else None,
            "baskets": len(cfg["baskets"]),
            "universe_tickers": len(config.all_tickers(cfg)),
            "audit_chain_ok": store.verify_audit_chain(),
            "provenance": provenance("live")}


@app.get("/api/settings")
def api_settings(request: Request):
    cfg = config.load()
    return {"settings": cfg["settings"], "n_baskets": len(cfg["baskets"]),
            "n_universe": len(config.all_tickers(cfg)),
            "provenance": provenance("cached")}


@app.get("/api/audit")
def api_audit(request: Request, limit: int = 25):
    rows = store.con().execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    events = [{"ts": r["ts"], "kind": r["kind"],
               "payload": json.loads(r["payload"]), "hash": r["hash"]} for r in rows]
    return {"events": events, "chain_ok": store.verify_audit_chain(),
            "provenance": provenance("live")}


@app.get("/api/quality")
def api_quality(request: Request):
    reports = [data.quality_report(t) for t in config.all_tickers()]
    return {"reports": reports, "provenance": provenance("cached")}


# ═══════════════════ BASKETS ═══════════════════
@app.get("/api/baskets")
def api_baskets(request: Request):
    merged = config.baskets()
    sections = config.basket_sections()
    out = []
    for name, holdings in merged.items():
        total_w = sum(holdings.values()) or 1
        rets = {}
        for label, days in (("1d", 1), ("1w", 7), ("1m", 30), ("ytd", "YTD")):
            w_ret, count = 0.0, 0
            for t, w in holdings.items():
                px = _px(t)
                if px.empty:
                    continue
                r = _period_return(px["px"], days)
                if r is not None:
                    w_ret += r * (w / total_w); count += 1
            if count:
                rets[label] = round(w_ret, 2)
        out.append({"name": name, "section": sections.get(name, "OTHER"),
                    "timing_class": "hold_only",
                    "n_holdings": len(holdings), "returns_pct": rets})
    return {"baskets": out, "provenance": provenance("cached")}


@app.get("/api/baskets/names")
def api_basket_names(request: Request):
    return {"names": config.basket_names(), "provenance": provenance("cached")}


@app.get("/api/basket/{name}")
def api_basket(request: Request, name: str):
    merged = config.baskets()
    holdings_dict = merged.get(name)
    if holdings_dict is None:
        raise HTTPException(404, f"Basket not found: {name}")
    holdings = []
    for t, w in holdings_dict.items():
        px = _px(t)
        holdings.append({"ticker": t, "weight": w,
                         "price": _f(px["px"].iloc[-1]) if not px.empty else None,
                         "ytd_pct": _period_return(px["px"], "YTD") if not px.empty else None})
    holdings.sort(key=lambda h: h["weight"], reverse=True)
    sections = config.basket_sections()
    return {"name": name, "section": sections.get(name, "OTHER"),
            "holdings": holdings, "provenance": provenance("cached")}


@app.post("/api/basket/{name}/add")
def api_basket_add(request: Request, name: str, ticker: str, weight: float = 1.0):
    ticker = ticker.upper().strip()
    if not ticker:
        raise HTTPException(400, "Missing ticker")
    if not (0 < weight <= 5.0):
        raise HTTPException(400, "Weight must be between 0 and 5")
    store.add_custom_holding(name, ticker, weight)
    return {"ok": True, "basket": name, "ticker": ticker, "weight": weight,
            "provenance": provenance("live")}


@app.post("/api/basket/{name}/remove")
def api_basket_remove(request: Request, name: str, ticker: str):
    ticker = ticker.upper().strip()
    store.remove_custom_holding(name, ticker)
    return {"ok": True, "basket": name, "ticker": ticker,
            "provenance": provenance("live")}


# ═══════════════════ TICKER / DOSSIER ═══════════════════
@app.get("/api/ticker/{t}")
def api_ticker(request: Request, t: str, mode: str = "auto"):
    llm.set_mode(mode)
    t = t.upper().strip()
    
    # Use on-demand fetch for AI-discovered tickers
    px = _ensure_prices(t)
    if px.empty:
        raise HTTPException(404, f"No data for {t} (ticker may be delisted or invalid)")
        
    cfg = config.load(); spy = _px(cfg["settings"]["benchmark"])
    fund = store.get_fundamentals(t)
    if not fund:
        fund = data.refresh_fundamentals(t)
    q = data.quality_report(t)
    rub = store.get_cached_rubric(t)
    s = S.score_v2(t, px.index[-1], {t: px}, spy, fund=fund,
                   rubric=rub["raw"] if rub else None)
    return {"ticker": t, "price": _f(px["px"].iloc[-1]),
            "asof": str(px.index[-1].date()),
            "quality": {"usable": q["usable"], "bars": q["bars"],
                        "flags": q.get("flags", []) if isinstance(q.get("flags"), list) else []},
            "fundamentals": {k: v for k, v in (fund or {}).items() if not k.startswith("_")},
            "score_v2_core": s,
            "in_baskets": _baskets_for(t),
            "provenance": provenance("cached")}


@app.get("/api/ticker/{t}/memo")
def api_ticker_memo(request: Request, t: str):
    """Company memo: what it is, what it does, Shadow Alpha angle, basket membership."""
    t = t.upper().strip()
    fund = store.get_fundamentals(t)
    if not fund:
        fund = data.refresh_fundamentals(t)
    fund = fund or {}

    rub = store.get_cached_rubric(t)
    bottleneck = None
    if rub:
        total = rub.get("total", 0)
        bottleneck = {"is_bottleneck": total >= 22, "rubric_total": total}

    memo = {
        "ticker": t,
        "name": fund.get("longName", t),
        "sector": fund.get("sector", "Unknown"),
        "industry": fund.get("industry", "Unknown"),
        "business_summary": fund.get("longBusinessSummary", ""),
        "market_cap": fund.get("marketCap"),
        "in_baskets": _baskets_for(t),
        "bottleneck": bottleneck,
        "key_metrics": {
            "gross_margin": fund.get("grossMargins"),
            "oper_margin": fund.get("operatingMargins"),
            "forward_pe": fund.get("forwardPE"),
            "revenue_growth": fund.get("revenueGrowth"),
        },
    }
    return {"memo": memo, "provenance": provenance("live")}


@app.get("/api/ticker/{t}/chart")
def api_chart(request: Request, t: str, bars: int = 252):
    t = t.upper().strip()
    px = _ensure_prices(t)
    if px.empty:
        raise HTTPException(404, f"No data for {t}")
    tail = px.tail(max(2, min(int(bars), len(px))))
    candles = [{"time": str(idx.date()), "open": _f(r.get("open")), "high": _f(r.get("high")),
                "low": _f(r.get("low")), "close": _f(r.get("close"))}
               for idx, r in tail.iterrows()]
    return {"ticker": t, "candles": candles, "provenance": provenance("cached")}


@app.get("/api/ticker/{t}/rubric")
def api_rubric(request: Request, t: str, mode: str = "auto"):
    llm.set_mode(mode)
    from .ai import evidence, rubric
    t = t.upper().strip(); cached = store.get_cached_rubric(t)
    if cached and not request.query_params.get("force"):
        return {"ticker": t, "ok": True, "rubric": cached["raw"], "cached": True,
                "age_days": cached["age_days"], "provenance": provenance("cached")}
    pack = evidence.build_evidence_pack(t)
    if not pack.get("business_desc"):
        raise HTTPException(404, f"No evidence for {t}")
    result = rubric.score_bottleneck(t, pack)
    if not result:
        raise HTTPException(502, "Rubric scoring failed")
    store.save_rubric(t, result["total"], result)
    return {"ticker": t, "ok": True, "rubric": result, "cached": False,
            "provenance": provenance("live")}


@app.get("/api/ticker/{t}/polymarket")
def api_ticker_polymarket(request: Request, t: str, limit: int = 3):
    """3 most relevant Polymarket markets for this ticker's sector/theme."""
    from .news import polymarket
    t = t.upper().strip()
    fund = store.get_fundamentals(t) or {}
    sector = fund.get("sector", "")
    query = sector if sector else t
    items = polymarket.search_markets(query, limit=limit, sort="volume24hr")
    if len(items) < limit:
        seen = {i.get("question") for i in items}
        for ti in polymarket.search_markets(t, limit=limit, sort="volume24hr"):
            if ti.get("question") not in seen:
                items.append(ti); seen.add(ti.get("question"))
    return {"items": items[:limit], "provenance": provenance("live")}


# ═══════════════════ SCREENER ═══════════════════
@app.get("/api/screen")
def api_screen(request: Request, top: int = 50):
    cfg = config.load(); bench = cfg["settings"]["benchmark"]; spy = _px(bench)
    if spy.empty:
        raise HTTPException(503, "Benchmark not fetched")
    rows = []
    for t in config.all_tickers(cfg):
        if t == bench:
            continue
        px = _px(t)
        if px.empty or len(px) < 250:
            continue
        fund = store.get_fundamentals(t); rub = store.get_cached_rubric(t)
        s = S.score_v2(t, px.index[-1], {t: px}, spy, fund=fund,
                       rubric=rub["raw"] if rub else None)
        if s:
            s["ticker"] = t; rows.append(s)
    rows.sort(key=lambda r: r["total"], reverse=True)
    return {"n_scored": len(rows), "top": rows[:top], "provenance": provenance("cached")}


@app.get("/api/screener/universe")
def api_screener_universe(request: Request):
    cfg = config.load()
    return {"universe": cfg.get("screening_universe", {}),
            "total_tickers": len(config.all_tickers(cfg)),
            "provenance": provenance("cached")}


@app.post("/api/screener/supply-chain")
async def api_discover_supply_chain(request: Request, mode: str = "auto"):
    llm.set_mode(mode)
    from .ai import supply_chain
    body = await request.json(); trend = (body or {}).get("trend", "").strip()
    if not trend:
        raise HTTPException(400, "Missing 'trend'")
    result = supply_chain.discover(trend)
    if "error" in result:
        raise HTTPException(502, result["error"])
    store.audit_log("supply_chain_discovery", {"trend": trend, "mode": mode})
    return {"result": result, "provenance": provenance("live", note="AI map (EST)")}


@app.get("/api/screener/deep/{t}")
def api_screener_deep(request: Request, t: str, mode: str = "auto"):
    llm.set_mode(mode)
    t = t.upper().strip()
    px = _ensure_prices(t)
    if px.empty:
        raise HTTPException(404, f"No data for {t}")
    cfg = config.load(); spy = _px(cfg["settings"]["benchmark"])
    fund = store.get_fundamentals(t); q = data.quality_report(t)
    inv = {"market_cap": (fund or {}).get("marketCap"),
           "price": float(px["px"].iloc[-1]),
           "avg_volume": (fund or {}).get("averageVolume"),
           "checks": {"market_cap_ok": ((fund or {}).get("marketCap") or 0) > 500_000_000,
                      "price_ok": float(px["px"].iloc[-1]) > 5.0,
                      "volume_ok": ((fund or {}).get("averageVolume") or 0) > 100_000}}
    inv["passed"] = sum(inv["checks"].values())
    inv["status"] = "PASS" if inv["passed"] >= 2 else "REVIEW"
    s = S.score_v2(t, px.index[-1], {t: px}, spy, fund=fund) if not spy.empty else None
    return {"ticker": t, "investability": inv, "score_v2": s,
            "in_baskets": _baskets_for(t), "provenance": provenance("cached")}


@app.post("/api/screener/bottleneck-ai/{t}")
def api_screener_bottleneck_ai(request: Request, t: str, mode: str = "auto"):
    llm.set_mode(mode)
    from .ai import evidence, rubric
    t = t.upper().strip()
    pack = evidence.build_evidence_pack(t)
    if not pack.get("business_desc"):
        raise HTTPException(404, f"No evidence for {t}")
    r = rubric.score_bottleneck(t, pack)
    if not r:
        raise HTTPException(502, "Bottleneck analysis failed")
    store.save_rubric(t, r["total"], r)
    return {"ticker": t, "rubric": r, "provenance": provenance("live")}


@app.get("/api/screener/deep-report/{t}")
def api_screener_deep_report(request: Request, t: str, mode: str = "auto"):
    llm.set_mode(mode)
    from .agents import pipeline as agents_pipeline
    t = t.upper().strip()
    px = _ensure_prices(t)
    if px.empty:
        raise HTTPException(404, f"No data for {t}")
    state = agents_pipeline.run_pipeline(t)
    return {"ticker": t, "memo": state.get("trader", {}).get("rationale", ""),
            "action": state.get("trader", {}).get("action", "HOLD"),
            "confidence": state.get("trader", {}).get("confidence", 0),
            "verdict": state.get("verdict", {}), "provenance": provenance("live")}


# ═══════════════════ NEWS & POLYMARKET ═══════════════════
@app.get("/api/news")
def api_news(request: Request, q: str = "", limit: int = 10):
    from .news import feed
    if q:
        items = feed.fetch_news_adhoc(q, limit=limit)
    else:
        items = feed.fetch_news(config.all_tickers()[:30])
    return {"items": items[:limit],
            "threshold": getattr(feed, "SIGNAL_THRESHOLD", 2.0),
            "provenance": provenance("live")}


@app.get("/api/polymarket")
def api_polymarket(request: Request, q: str = "fed rate cut",
                   limit: int = 10, sort: str = "relevance"):
    from .news import polymarket
    return {"items": polymarket.search_markets(q, limit=limit, sort=sort),
            "provenance": provenance("live")}


@app.get("/api/calibration")
def api_calibration(request: Request):
    from .news import polymarket
    return {"rows": polymarket.calibration_rows(), "provenance": provenance("live")}


# ═══════════════════ POSITIONS / MONITOR / MEMORY ═══════════════════
@app.get("/api/positions")
def api_positions(request: Request):
    from .exec import lifecycle
    return {"positions": lifecycle.positions_table(), "provenance": provenance("cached")}


@app.get("/api/monitor")
def api_monitor(request: Request):
    from .exec import lifecycle
    return {"actions": lifecycle.daily_monitor(), "provenance": provenance("live")}


@app.get("/api/memory")
def api_memory(request: Request):
    store.grade_memory()
    return {"decisions": store.recent_memory(50), "provenance": provenance("cached")}


@app.get("/api/scorecard")
def api_scorecard(request: Request):
    store.grade_memory()
    rows = store.con().execute(
        """SELECT action, COUNT(*) n,
                  AVG(CASE WHEN outcome='WIN' THEN 1.0 ELSE 0.0 END) hit_rate,
                  AVG(realized_ret) avg_ret
           FROM memory WHERE outcome IS NOT NULL GROUP BY action""").fetchall()
    return {"scorecard": [dict(r) for r in rows], "provenance": provenance("cached")}


# ═══════════════════ INTRADAY ═══════════════════
@app.get("/api/intraday/{equity}/{proxy}")
def api_intraday(request: Request, equity: str, proxy: str,
                 interval: str = "5m", period: str = "60d"):
    from .intraday import leadlag
    res = leadlag.lead_lag_scan(equity.upper(), proxy.upper(),
                                interval=interval, period=period)
    if "error" in res:
        raise HTTPException(400, res["error"])
    return {"result": res, "provenance": provenance("live")}


# ═══════════════════ PIPELINE ═══════════════════
@app.post("/api/pipeline/{t}")
def api_pipeline(request: Request, t: str, mode: str = "auto"):
    llm.set_mode(mode)
    t = t.upper().strip()
    px = _ensure_prices(t)
    if px.empty:
        raise HTTPException(404, f"No data for {t}")
    try:
        from .agents import pipeline
        state = pipeline.run_pipeline(t)
        slim = {"ticker": state["ticker"],
                "analysts": {k: v[:600] for k, v in state["analysts"].items()},
                "bull": state["bull"][:800], "bear": state["bear"][:800],
                "verdict": state["verdict"], "trader": state["trader"],
                "trade": state.get("trade"), "score": state.get("score"),
                "position_opened": state.get("position_opened", False)}
        return {"state": slim, "provenance": provenance("live")}
    except Exception as e:
        raise HTTPException(500, f"Pipeline failed: {str(e)[:200]}")


# ═══════════════════ STATIC ═══════════════════
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _force_exit(sig, frame):
    print("\n[!] Force quitting server (Ctrl+C)...")
    os._exit(1)


if __name__ == "__main__":
    import uvicorn
    signal.signal(signal.SIGINT, _force_exit)
    signal.signal(signal.SIGTERM, _force_exit)
    print("SAF v4.2.1 server  → http://127.0.0.1:8000/static/")
    print("AI Provider:", "NOUS (Ox Alpha)" if get_key("NOUS_API_KEY") else "GROQ")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")