"""
saf/fulltest.py — Full-stack pipeline diagnostic.
Tests every layer and reports WHICH LLM PROVIDER/MODEL is actually answering.

Run:
    python -m saf.fulltest            # full test (calls real LLMs, ~1-3 min)
    python -m saf.fulltest --no-llm   # skip live LLM calls (fast, offline-safe)
"""
import sys, time, json, traceback
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()
RUN_LLM = "--no-llm" not in sys.argv

RESULTS = []   # (layer, check, ok, detail, seconds)

def record(layer, check, ok, detail, secs):
    RESULTS.append((layer, check, bool(ok), str(detail)[:110], round(secs, 2)))
    mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
    console.print(f"  {mark} {check:<38} {str(detail)[:70]}  "
                  f"[{'green' if ok else 'red'}]{secs:.2f}s[/{'green' if ok else 'red'}]")


# ═══════════════════════════════════════════════════════════
# 1. DEPENDENCIES & CORE
# ═══════════════════════════════════════════════════════════
def test_deps():
    console.print("\n[bold cyan]━━━ 1. DEPENDENCIES & CORE ━━━[/bold cyan]")
    t0 = time.time()
    missing = []
    for m in ("yfinance", "pandas", "numpy", "rich", "openai", "requests"):
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    record("deps", "python imports", not missing,
           "all present" if not missing else f"MISSING: {missing}", time.time() - t0)

    t0 = time.time()
    try:
        from . import config, store, data
        cfg = config.load()
        nb = len(cfg["baskets"]); nt = len(config.all_tickers(cfg))
        record("core", "config + universe load", nb > 0,
               f"{nb} baskets, {nt} tickers", time.time() - t0)
    except Exception as e:
        record("core", "config + universe load", False, str(e), time.time() - t0)
        return False

    t0 = time.time()
    try:
        store.init()
        tables = {r["name"] for r in store.con().execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        need = {"prices", "fundamentals", "meta", "audit", "memory",
                "positions", "rubric_cache"}
        ok = need.issubset(tables)
        record("store", "sqlite schema", ok, f"{len(tables)} tables", time.time() - t0)
    except Exception as e:
        record("store", "sqlite schema", False, str(e), time.time() - t0)
    return True


# ═══════════════════════════════════════════════════════════
# 2. DATA LAYER
# ═══════════════════════════════════════════════════════════
def test_data():
    console.print("\n[bold cyan]━━━ 2. DATA LAYER ━━━[/bold cyan]")
    from . import store, data, config
    bench = config.load()["settings"]["benchmark"]

    for t in (bench, "MKSI"):
        t0 = time.time()
        try:
            if store.last_price_date(t) is None:
                data.refresh_ticker(t)
            px = store.load_prices(t)
            ok = len(px) >= 100
            record("data", f"price history [{t}]", ok,
                   f"{len(px)} bars", time.time() - t0)
        except Exception as e:
            record("data", f"price history [{t}]", False, str(e), time.time() - t0)

    t0 = time.time()
    try:
        q = data.quality_report(bench)
        record("data", "quality report", q.get("usable", False),
               f"bars={q.get('bars')}, stale={q.get('stale_days')}d", time.time() - t0)
    except Exception as e:
        record("data", "quality report", False, str(e), time.time() - t0)


# ═══════════════════════════════════════════════════════════
# 3. QUANT ENGINE
# ═══════════════════════════════════════════════════════════
def test_quant():
    console.print("\n[bold cyan]━━━ 3. QUANT ENGINE (Score v2) ━━━[/bold cyan]")
    import numpy as np, pandas as pd
    from .quant import score as S
    from . import store, config

    t0 = time.time()
    try:
        dates = pd.bdate_range(end=pd.Timestamp.today(), periods=320)
        rng = np.random.default_rng(42)
        px = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, len(dates)))
        df = pd.DataFrame({"px": px, "open": px, "high": px * 1.01,
                           "low": px * 0.99, "close": px}, index=dates)
        fund = {"gross_margin": 0.45, "oper_margin": 0.2, "returnOnEquity": 0.18}
        s = S.shadow_alpha_v2(df, df, fund)
        comp_sum = round(sum(s["components"].values()), 1)
        ok = abs(comp_sum - s["total"]) < 0.6 and s["total"] > 0
        record("quant", "score_v2 math (synthetic)", ok,
               f"total={s['total']} ({s['verdict']}), Σ={comp_sum}", time.time() - t0)
    except Exception as e:
        record("quant", "score_v2 math (synthetic)", False, str(e), time.time() - t0)

    t0 = time.time()
    try:
        bench = config.load()["settings"]["benchmark"]
        px, spy = store.load_prices("MKSI"), store.load_prices(bench)
        if px.empty or spy.empty:
            record("quant", "score_v2 (real MKSI)", False,
                   "missing price data", time.time() - t0)
        else:
            s = S.score_v2("MKSI", px.index[-1], {"MKSI": px}, spy,
                           fund=store.get_fundamentals("MKSI"))
            ok = s is not None and s["total"] > 0
            record("quant", "score_v2 (real MKSI)", ok,
                   f"total={s['total']} ({s['verdict']})", time.time() - t0)
    except Exception as e:
        record("quant", "score_v2 (real MKSI)", False, str(e), time.time() - t0)


# ═══════════════════════════════════════════════════════════
# 4. LLM LAYER — WHICH PROVIDER IS ACTUALLY ANSWERING
# ═══════════════════════════════════════════════════════════
def test_llm():
    console.print("\n[bold magenta]━━━ 4. LLM LAYER — PROVIDER RESOLUTION ━━━[/bold magenta]")
    from .security import get_key

    nous_key = get_key("NOUS_API_KEY")
    groq_key = get_key("GROQ_API_KEY")
    provider_env = get_key("SAF_LLM_PROVIDER") or "(default: nous)"

    record("llm-config", "NOUS_API_KEY present", bool(nous_key),
           "set" if nous_key else "MISSING", 0.0)
    record("llm-config", "GROQ_API_KEY present", bool(groq_key),
           "set" if groq_key else "MISSING", 0.0)
    record("llm-config", "SAF_LLM_PROVIDER", True, provider_env, 0.0)

    from .ai import llm
    order = llm._provider_order()
    order_str = " → ".join(f"{p}:{m}" for p, m in order)
    record("llm-config", "resolved provider chain", bool(order), order_str, 0.0)

    primary = order[0] if order else ("none", "none")
    console.print(f"\n  [bold yellow]PRIMARY PROVIDER → "
                  f"{primary[0].upper()} :: {primary[1]}[/bold yellow]")

    if not RUN_LLM:
        record("llm-live", "live LLM calls", True, "SKIPPED (--no-llm)", 0.0)
        return

    # ── Ping every model using a JSON prompt (reasoning-model safe) ──
    console.print("\n  [dim]Pinging each model individually (JSON prompt)...[/dim]")
    for prov, model in order:
        t0 = time.time()
        try:
            out, dbg = llm.complete_json(
                'Return ONLY this JSON object: {"status": "ready"}',
                "Respond with the JSON object.",
                temperature=0.0, max_tokens=64,
                force_provider=prov, force_model=model)
            ok = out is not None and out.get("status") == "ready"
            answered_by = f"{dbg.get('provider', '?')}:{dbg.get('model', '?')}"
            detail = (f"answered by {answered_by}" if ok
                      else dbg.get("error", "no response"))
            record("llm-live", f"ping {prov}:{model}", ok,
                   detail, time.time() - t0)
        except Exception as e:
            record("llm-live", f"ping {prov}:{model}", False,
                   str(e)[:80], time.time() - t0)

    # ── Full-chain JSON test (shows which model actually wins) ──
    t0 = time.time()
    try:
        out, dbg = llm.complete_json(
            'Return ONLY JSON: {"status":"ok"}', "test",
            temperature=0.0, max_tokens=64)
        ok = out is not None and out.get("status") == "ok"
        answered_by = f"{dbg.get('provider', '?')}:{dbg.get('model', '?')}"
        record("llm-live", "JSON mode (full chain)", ok,
               f"answered by [bold]{answered_by}[/bold]", time.time() - t0)
        console.print(f"\n  [bold green]★ JSON call actually answered by: "
                      f"{answered_by}[/bold green]")
    except Exception as e:
        record("llm-live", "JSON mode (full chain)", False,
               str(e)[:80], time.time() - t0)


# ═══════════════════════════════════════════════════════════
# 5. GROUNDED AI (evidence + rubric + supply chain)
# ═══════════════════════════════════════════════════════════
def test_grounded():
    console.print("\n[bold cyan]━━━ 5. GROUNDED AI (SEC evidence + rubric) ━━━[/bold cyan]")
    if not RUN_LLM:
        record("grounded", "grounded AI", True, "SKIPPED (--no-llm)", 0.0)
        return
    from .ai import evidence, rubric, supply_chain

    t0 = time.time()
    try:
        pack = evidence.build_evidence_pack("MKSI")
        ok = bool(pack.get("business_desc"))
        record("grounded", "SEC evidence pack", ok,
               f"{len(pack.get('business_desc', ''))} chars, "
               f"{len(pack.get('concentration_hits', []))} hits", time.time() - t0)
    except Exception as e:
        record("grounded", "SEC evidence pack", False, str(e)[:80], time.time() - t0)
        pack = None

    if pack and pack.get("business_desc"):
        t0 = time.time()
        try:
            r = rubric.score_bottleneck("MKSI", pack)
            ok = r is not None and "total" in r
            record("grounded", "grounded rubric", ok,
                   f"total={r.get('total')}/30, "
                   f"flagged={len(r.get('flagged_hallucinations', []))}",
                   time.time() - t0)
        except Exception as e:
            record("grounded", "grounded rubric", False, str(e)[:80], time.time() - t0)

    t0 = time.time()
    try:
        sc = supply_chain.discover("solid-state batteries for electric vehicles")
        ok = "bottlenecks" in sc and len(sc["bottlenecks"]) > 0
        record("grounded", "supply chain discovery", ok,
               f"{len(sc.get('bottlenecks', []))} bottlenecks, "
               f"top={sc.get('top_pick')}", time.time() - t0)
    except Exception as e:
        record("grounded", "supply chain discovery", False,
               str(e)[:80], time.time() - t0)


# ═══════════════════════════════════════════════════════════
# 6. AGENTS PIPELINE (5-stage)
# ═══════════════════════════════════════════════════════════
def test_pipeline():
    console.print("\n[bold cyan]━━━ 6. AGENTS PIPELINE (5-stage) ━━━[/bold cyan]")
    if not RUN_LLM:
        record("agents", "pipeline", True, "SKIPPED (--no-llm)", 0.0)
        return
    from .agents import pipeline

    t0 = time.time()
    try:
        state = pipeline.run_pipeline("MKSI")
        ok = state.get("trader", {}).get("action") in ("BUY", "SELL", "HOLD")
        verdict = state.get("verdict", {}).get("winner", "?")
        record("agents", "5-stage pipeline [MKSI]", ok,
               f"action={state.get('trader', {}).get('action')}, debate={verdict}",
               time.time() - t0)
    except Exception as e:
        record("agents", "5-stage pipeline", False, str(e)[:90], time.time() - t0)


# ═══════════════════════════════════════════════════════════
# 7. SERVER ENDPOINTS (via TestClient, no live server needed)
# ═══════════════════════════════════════════════════════════
def test_server():
    console.print("\n[bold cyan]━━━ 7. SERVER ENDPOINTS ━━━[/bold cyan]")
    t0 = time.time()
    try:
        from fastapi.testclient import TestClient
        from . import server
        client = TestClient(server.app)
        record("server", "TestClient init", True, "app loaded", time.time() - t0)
    except Exception as e:
        record("server", "TestClient init", False, str(e)[:80], time.time() - t0)
        return

    endpoints = [
        ("GET", "/api/system/health", None),
        ("GET", "/api/baskets", None),
        ("GET", "/api/ticker/MKSI", None),
        ("GET", "/api/ticker/MKSI/chart?bars=30", None),
        ("GET", "/api/screen?top=5", None),
        ("GET", "/api/screener/universe", None),
        ("GET", "/api/memory", None),
        ("GET", "/api/positions", None),
        ("GET", "/api/audit", None),
        ("GET", "/api/news?q=semiconductor", None),
        ("GET", "/static/", None),
    ]
    for method, path, body in endpoints:
        t0 = time.time()
        try:
            r = (client.get(path) if method == "GET"
                 else client.post(path, json=body))
            ok = r.status_code == 200
            record("server", f"{method} {path}", ok,
                   f"HTTP {r.status_code}", time.time() - t0)
        except Exception as e:
            record("server", f"{method} {path}", False,
                   str(e)[:80], time.time() - t0)


# ═══════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════
def summary():
    console.print("\n")
    tbl = Table(title="FULL PIPELINE DIAGNOSTIC RESULTS",
                box=box.HEAVY_HEAD, show_lines=True)
    tbl.add_column("Layer", style="bold cyan", width=12)
    tbl.add_column("Check", width=34)
    tbl.add_column("Status", justify="center", width=8)
    tbl.add_column("Detail", overflow="fold")
    tbl.add_column("s", justify="right", width=7)
    for layer, check, ok, detail, secs in RESULTS:
        st = "[green]✅ PASS[/green]" if ok else "[red]❌ FAIL[/red]"
        tbl.add_row(layer, check, st, detail, f"{secs:.2f}")
    console.print(tbl)

    passed = sum(1 for _, _, ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    color = ("green" if passed == total
             else "yellow" if passed >= total - 3 else "red")
    console.print(Panel.fit(
        f"[bold {color}]HEALTH SCORE: {passed}/{total} checks passed[/bold {color}]",
        box=box.DOUBLE, border_style=color))

    fails = [(l, c, d) for l, c, ok, d, _ in RESULTS if not ok]
    if fails:
        console.print("\n[bold red]FAILED CHECKS:[/bold red]")
        for l, c, d in fails:
            console.print(f"  • [{l}] {c} → {d}")
    else:
        console.print("\n[bold green]🎉 Full pipeline operational.[/bold green]")


def main():
    console.print(Panel.fit(
        "[bold magenta]🧪 SAF v4 — FULL PIPELINE DIAGNOSTIC[/bold magenta]\n"
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        f"mode={'FULL (live LLM)' if RUN_LLM else 'FAST (no LLM calls)'}[/dim]",
        box=box.DOUBLE, border_style="magenta"))
    try:
        if not test_deps():
            summary()
            return
        test_data()
        test_quant()
        test_llm()
        test_grounded()
        test_pipeline()
        test_server()
    except Exception:
        console.print("[red]Fatal error:[/red]")
        console.print(traceback.format_exc())
    summary()


if __name__ == "__main__":
    main()