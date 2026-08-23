"""
End-to-end self-test for the Skia Alpha Fund v4 pipeline.

Run:
    python -m saf.selftest             # core checks (data, quant, API, static)
    python -m saf.selftest --full      # + LLM, SEC evidence, supply-chain, intraday, pipeline
    python -m saf.selftest --ticker NVDA

Reads the health of every subsystem and prints a pass/fail table + score.
Non-destructive: it never opens/closes real positions or mutates memory.
"""
import argparse, sys, time, json
import numpy as np
import pandas as pd
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markup import escape
from rich import box

console = Console()
TEST_TICKER = "MKSI"
RESULTS = []          # (name, ok, detail, seconds)
_CLIENT = None        # lazy FastAPI TestClient


# ── helpers ───────────────────────────────────────────────────────
def _client():
    global _CLIENT
    if _CLIENT is None:
        from fastapi.testclient import TestClient   # needs `pip install httpx`
        from . import server
        _CLIENT = TestClient(server.app)
    return _CLIENT


def _synthetic_prices(periods=320, seed=42, drift=0.0004, vol=0.015):
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=periods)
    rng = np.random.default_rng(seed)
    px = 100 * np.cumprod(1 + rng.normal(drift, vol, len(dates)))
    return pd.DataFrame({"open": px, "high": px * 1.01, "low": px * 0.99,
                         "close": px, "adj_close": px, "px": px,
                         "volume": 1_000_000}, index=dates)


def record(name, fn):
    t0 = time.time()
    try:
        ok, detail = fn()
        RESULTS.append((name, bool(ok), str(detail)[:120], round(time.time() - t0, 2)))
    except Exception as e:
        RESULTS.append((name, False, f"exception: {str(e)[:110]}", round(time.time() - t0, 2)))
    ok = RESULTS[-1][1]
    color = "green" if ok else "red"
    mark = "✓" if ok else "✗"
    console.print(f"  [{color}]{mark}[/{color}] {escape(name):<34} {escape(RESULTS[-1][2])}")


# ── CORE CHECKS (offline / fast) ─────────────────────────────────
def c_store():
    from . import store
    store.init()
    tables = {r["name"] for r in store.con().execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    need = {"prices", "fundamentals", "meta", "audit", "memory", "positions", "rubric_cache"}
    missing = need - tables
    return (not missing, f"{len(tables)} tables" + (f", missing {missing}" if missing else ""))


def c_config():
    from . import config
    cfg = config.load()
    b = config.baskets()
    n = len(config.all_tickers())
    s = config.sector_of(TEST_TICKER)
    return (len(b) > 0 and n > 10, f"{len(b)} baskets, {n} tickers, sector={s}")


def c_security():
    from . import security
    dirty = '<script>alert(1)</script>javascript:x'
    clean = security.clean_text(dirty)
    ok_clean = "<script>" not in clean and "javascript:" not in clean
    key = security.load_env()          # returns GROQ_API_KEY from .env
    return (ok_clean, f"sanitizer ok; GROQ key {'present' if key else 'MISSING'}")


def c_static_files():
    base = Path(__file__).resolve().parent / "static"
    need = {"index.html": "SKIA ALPHA FUND", "terminal.css": "--gold", "client.js": "/api/"}
    problems = []
    for f, marker in need.items():
        p = base / f
        if not p.exists():
            problems.append(f"{f} missing")
        elif marker not in p.read_text(errors="ignore"):
            problems.append(f"{f} lacks '{marker}'")
    return (not problems, "all present" if not problems else "; ".join(problems))


def c_score_math():
    from .quant import score as S
    px = _synthetic_prices()
    spy = _synthetic_prices(seed=7, drift=0.0003, vol=0.010)
    fund = {"gross_margin": 0.45, "oper_margin": 0.20, "returnOnEquity": 0.18}
    s = S.score_v2("TEST", px.index[-1], {"TEST": px}, spy, fund=fund)
    if s is None:
        return (False, "score_v2 returned None on synthetic data")
    comps = s["components"]
    comp_sum = round(sum(comps.values()), 2)
    consistent = abs(comp_sum - s["total"]) < 0.6
    has_keys = all(k in s for k in ("total", "components", "verdict", "confidence"))
    return (consistent and has_keys,
            f"total={s['total']} ({s['verdict']}/{s['confidence']}), comps_sum={comp_sum}")


def c_audit_chain():
    from . import store
    store.audit_log("selftest_probe", {"note": "integrity probe"})
    return (store.verify_audit_chain(), "hash chain intact")


def c_news_relevance():
    from .news import feed
    score, kws = feed.relevance_score("CoWoS capacity sold out, sole supplier of HBM")
    return (score >= 2.0, f"score={score}, keywords={kws}")


def c_polymarket_math():
    from .news import polymarket
    gap, interp = polymarket.divergence(0.70, 0.35)
    return (abs(gap - 0.35) < 1e-6 and "DIVERGENCE" in interp, f"gap={gap} -> {interp}")


# ── DATA + API CHECKS (need network seed) ────────────────────────
def c_data_seed():
    from . import data, store, config
    bench = config.load()["settings"]["benchmark"]
    got = []
    for t in [bench, TEST_TICKER]:
        if store.last_price_date(t) is None:
            data.refresh_ticker(t)
        if store.last_price_date(t) is not None:
            got.append(t)
    return (bench in got, f"seeded {got}")


def c_quality_report():
    from . import data
    q = data.quality_report(TEST_TICKER)
    return (q["bars"] > 0, f"bars={q['bars']}, usable={q['usable']}, stale={q['stale_days']}d")


def c_api(name, path, expect_key=None):
    def fn():
        r = _client().get(path)
        if r.status_code != 200:
            return (False, f"HTTP {r.status_code}")
        body = r.json()
        if expect_key and expect_key not in body:
            return (False, f"missing '{expect_key}'")
        return (True, f"HTTP 200")
    return fn


def c_static_served():
    r = _client().get("/static/")
    ok = r.status_code == 200 and "SKIA ALPHA FUND" in r.text
    return (ok, f"HTTP {r.status_code}, {'HTML rendered' if ok else 'bad body'}")


def c_api_screen():
    r = _client().get("/api/screen?top=5")
    if r.status_code != 200:
        return (False, f"HTTP {r.status_code}: {r.text[:60]}")
    top = r.json().get("top", [])
    return (len(top) > 0, f"{r.json().get('n_scored')} scored, top={top[0]['ticker'] if top else '?'}")


def c_api_ticker():
    r = _client().get(f"/api/ticker/{TEST_TICKER}")
    if r.status_code != 200:
        return (False, f"HTTP {r.status_code}")
    b = r.json()
    return (b.get("score_v2_core") is not None,
            f"price={b.get('price')}, score={(b.get('score_v2_core') or {}).get('total')}")


def c_api_chart():
    r = _client().get(f"/api/ticker/{TEST_TICKER}/chart?bars=60")
    if r.status_code != 200:
        return (False, f"HTTP {r.status_code}")
    n = len(r.json().get("candles", []))
    return (n > 0, f"{n} candles")


def c_sizing():
    from .exec import sizing
    out = sizing.position_size(TEST_TICKER)
    if "error" in out:
        return (False, out["error"])
    return (out["shares"] >= 0,
            f"shares={out['shares']}, {out['pct_account']}%, bind={out['binding_constraint']}")


def c_lifecycle():
    from .exec import lifecycle
    out = lifecycle.run_monitor(auto_close=False)
    ok = isinstance(out, dict) and "events" in out
    return (ok, f"{out.get('n_positions', 0)} positions, "
                f"{len(out.get('events', []))} events (read-only probe)")


# ── FULL CHECKS (LLM / heavy network) ────────────────────────────
def c_llm_complete():
    from .ai import llm
    txt, dbg = llm.complete("You are a test.", "Reply with the single word: OK", temperature=0.0)
    return (bool(txt and txt.strip()), (txt[:30] if txt else f"failed: {dbg.get('error','?')}"))


def c_llm_json():
    from .ai import llm
    out, dbg = llm.complete_json('Return ONLY JSON: {"status":"ok"}', "test", temperature=0.0)
    return (out is not None, "parses" if out else f"failed: {dbg.get('error','?')}")


def c_evidence_pack():
    from .ai import evidence
    pack = evidence.build_evidence_pack(TEST_TICKER)
    has_desc = bool(pack.get("business_desc"))
    hits = len(pack.get("concentration_hits", []))
    return (has_desc, f"desc={len(pack.get('business_desc',''))} chars, {hits} concentration hits")


def c_rubric():
    from .ai import evidence, rubric
    pack = evidence.build_evidence_pack(TEST_TICKER)
    if not pack.get("business_desc"):
        return (False, "no evidence pack")
    r = rubric.score_bottleneck(TEST_TICKER, pack)
    if not r or "error" in r:
        return (False, f"rubric failed: {str(r.get('error') if r else 'None')[:60]}")
    return (True, f"total={r.get('total')}/30, flagged={len(r.get('flagged_hallucinations',[]))}")


def c_supply_chain():
    from .ai import supply_chain
    r = supply_chain.discover("solid-state batteries for electric vehicles")
    if "error" in r:
        return (False, r["error"])
    return (len(r.get("bottlenecks", [])) > 0,
            f"{len(r.get('bottlenecks',[]))} bottlenecks, top={r.get('top_pick')}")

def c_api_supply_chain_ep():
    r = _client().post("/api/screener/supply-chain",
                       json={"trend": "solid-state batteries for EVs"})
    if r.status_code != 200:
        return (False, f"HTTP {r.status_code}: {r.text[:60]}")
    res = r.json().get("result", {})
    return (len(res.get("bottlenecks", [])) > 0,
            f"{len(res.get('bottlenecks', []))} bottlenecks, top={res.get('top_pick')}")


def c_api_screener_deep():
    r = _client().get(f"/api/screener/deep/{TEST_TICKER}")
    if r.status_code != 200:
        return (False, f"HTTP {r.status_code}")
    b = r.json()
    inv = b.get("investability", {})
    return (inv.get("passed", 0) >= 0,
            f"investability {inv.get('passed')}/4, score={b.get('score_v2', {}).get('total')}")


def c_api_news():
    r = _client().get("/api/news?q=semiconductor")
    if r.status_code != 200:
        return (False, f"HTTP {r.status_code}")
    return (len(r.json().get("items", [])) >= 0, f"{len(r.json().get('items',[]))} items")


def c_api_polymarket():
    r = _client().get("/api/polymarket?q=fed")
    if r.status_code != 200:
        return (False, f"HTTP {r.status_code}")
    return (True, f"{len(r.json().get('items',[]))} markets")


def c_intraday():
    from .intraday import leadlag
    r = leadlag.lead_lag_scan("XYL", "PHO", interval="5m", period="60d")
    if "error" in r:
        return (False, r["error"])
    return (True, f"lag={r['best_lag_bars']}, corr={r['corr_at_best']}, tradeable={r['tradeable']}")


def c_pipeline():
    from .agents import pipeline
    state = pipeline.run_pipeline(TEST_TICKER)
    a = state.get("trader", {}).get("action")
    return (a in ("BUY", "SELL", "HOLD"),
            f"action={a}, winner={state.get('verdict',{}).get('winner')}")


def c_backtest():
    from .quant import backtest
    res = backtest.run_backtest(step_days=63, eval_horizon=63)
    r = res["results"]
    spread = r.get("q5_minus_q1_annual")
    return (spread is not None, f"Q5-Q1={spread}%, IC_IR={r.get('ic_ir')}, status={r.get('status')}")


# ── runner ───────────────────────────────────────────────────────
def main():
    global TEST_TICKER
    ap = argparse.ArgumentParser(description="SAF v4 pipeline self-test")
    ap.add_argument("--full", action="store_true", help="include LLM / heavy-network checks")
    ap.add_argument("--ticker", default=TEST_TICKER)
    args = ap.parse_args()
    TEST_TICKER = args.ticker.upper().strip()

    console.print(Panel.fit(
        f"[bold magenta]SAF v4 PIPELINE SELF-TEST[/bold magenta]\n"
        f"[dim]ticker={TEST_TICKER} · mode={'FULL' if args.full else 'CORE'}[/dim]",
        box=box.DOUBLE, border_style="magenta"))

    core = [
        ("store / schema",            c_store),
        ("config / universe",         c_config),
        ("security / sanitizer+key",  c_security),
        ("static files present",      c_static_files),
        ("score v2 math (synthetic)", c_score_math),
        ("audit hash chain",          c_audit_chain),
        ("news relevance scoring",    c_news_relevance),
        ("polymarket divergence math",c_polymarket_math),
        ("data seed (SPY+ticker)",    c_data_seed),
        ("quality report",            c_quality_report),
        ("GET /api/system/health",    c_api("health", "/api/system/health", "status")),
        ("GET /api/baskets",          c_api("baskets", "/api/baskets", "baskets")),
        ("GET /api/settings",         c_api("settings", "/api/settings", "settings")),
        ("GET /api/quality",          c_api("quality", "/api/quality", "reports")),
        ("GET /api/positions",        c_api("positions", "/api/positions", "positions")),
        ("GET /api/memory",           c_api("memory", "/api/memory", "decisions")),
        ("GET /api/scorecard",        c_api("scorecard", "/api/scorecard", "scorecard")),
        ("GET /api/audit",            c_api("audit", "/api/audit", "events")),
        ("GET /api/screen",           c_api_screen),
        ("GET /api/ticker/{t}",       c_api_ticker),
        ("GET /api/ticker/{t}/chart", c_api_chart),
        ("GET /static/ (terminal)",   c_static_served),
        ("sizing engine",             c_sizing),
        ("lifecycle monitor",         c_lifecycle),
    ]
    full = [
        ("LLM complete()",            c_llm_complete),
        ("LLM complete_json()",       c_llm_json),
        ("SEC evidence pack",         c_evidence_pack),
        ("grounded rubric",           c_rubric),
        ("supply-chain discovery",    c_supply_chain),
        ("POST /api/screener/supply-chain", c_api_supply_chain_ep),
        ("GET /api/screener/deep/{t}",      c_api_screener_deep),
        ("GET /api/news",             c_api_news),
        ("GET /api/polymarket",       c_api_polymarket),
        ("intraday lead-lag scan",    c_intraday),
        ("agent pipeline (5-stage)",  c_pipeline),
        ("backtest (walk-forward)",   c_backtest),
    ]

    console.print("\n[bold cyan]CORE[/bold cyan]")
    for name, fn in core:
        record(name, fn)

    if args.full:
        console.print("\n[bold cyan]FULL (LLM / network-heavy)[/bold cyan]")
        for name, fn in full:
            record(name, fn)

    # ── report ──────────────────────────────────────────────────
    passed = sum(1 for _, ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    tbl = Table(title="Self-Test Results", box=box.HEAVY_HEAD, show_lines=True)
    tbl.add_column("Check", style="bold", min_width=28)
    tbl.add_column("Status", justify="center", width=8)
    tbl.add_column("Detail", overflow="fold")
    tbl.add_column("s", justify="right", width=7)
    for name, ok, detail, secs in RESULTS:
        status = "[bold green]PASS[/bold green]" if ok else "[bold red]FAIL[/bold red]"
        tbl.add_row(escape(name), status, escape(detail), f"{secs:.1f}")
    console.print(tbl)

    color = "green" if passed == total else ("yellow" if passed >= total - 2 else "red")
    console.print(Panel.fit(
        f"[bold {color}]HEALTH SCORE: {passed}/{total} checks passed[/bold {color}]",
        box=box.DOUBLE, border_style=color))

    # ── targeted guidance on failure ────────────────────────────
    failed = [n for n, ok, _, _ in RESULTS if not ok]
    if failed:
        console.print("[bold yellow]Troubleshooting:[/bold yellow]")
        tips = {
            "GROQ key": "set GROQ_API_KEY in .env",
            "static files": "re-create saf/static/{index.html,terminal.css,client.js}",
            "data seed": "check internet; run `python -m saf.cli fetch`",
            "httpx": "pip install httpx (needed for API tests)",
            "LLM": "verify key + model availability on Groq/Nous",
            "SEC evidence": "SEC may be rate-limiting; retry shortly",
        }
        for f in failed:
            for key, tip in tips.items():
                if any(w in f.lower() for w in key.lower().split()):
                    console.print(f"  • {escape(f)} -> {tip}")
                    break
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()