"""SAF v4 CLI — foundation + backtest + rubric cache + pipeline + sizing + lifecycle + scorecard + intraday."""
import argparse, json, sys, time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from . import config, store, data

console = Console()

def cmd_init(_): store.init(); console.print("[green]✅[/green] saf.db initialized")

def cmd_validate(_):
    cfg = config.load(); th = cfg["settings"]["score_thresholds"]
    console.print(f"[green]✅[/green] Config valid — {len(cfg['baskets'])} baskets, "
                  f"{len(config.all_tickers(cfg))} unique tickers")
    console.print(f"   thresholds: candidate≥{th['candidate']}, watch≥{th['watch']}")

def cmd_fetch(args):
    store.init()
    if args.ticker:
        ok = data.refresh_ticker(args.ticker.upper())
        _print_quality([data.quality_report(args.ticker.upper())])
        sys.exit(0 if ok else 1)
    summary = data.refresh_universe(with_fundamentals=args.fundamentals)
    console.print(f"[green]✅ {summary['ok']}/{summary['tickers']} refreshed, "
                  f"{summary['fail']} failed[/green]")
    if summary["failures"]:
        console.print(f"[red]Failed: {', '.join(summary['failures'])}[/red]")

def cmd_quality(_):
    store.init()
    reports = [data.quality_report(t) for t in config.all_tickers()]
    _print_quality(reports)
    bad = [r for r in reports if not r["usable"]]
    if bad:
        console.print(f"[yellow]⚠️ {len(bad)} tickers excluded by quality gate[/yellow]")

def _parse_flags(raw):
    if isinstance(raw, list): return raw
    try:
        parsed = json.loads(raw or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception: return []

def _print_quality(reports):
    tbl = Table(title="Data Quality Gate")
    tbl.add_column("Ticker", style="bold cyan")
    tbl.add_column("Usable", justify="center")
    tbl.add_column("Bars", justify="right")
    tbl.add_column("Last", justify="right")
    tbl.add_column("Stale d", justify="right")
    tbl.add_column("0-vol d", justify="right")
    tbl.add_column("Flags", overflow="fold")
    for r in sorted(reports, key=lambda x: x["ticker"]):
        usable = "[green]✅[/green]" if r["usable"] else "[red]❌[/red]"
        flags = _parse_flags(r["flags"])
        flag_txt = ", ".join(flags) if flags else "[dim]—[/dim]"
        tbl.add_row(r["ticker"], usable, str(r["bars"]), str(r["last_date"] or "—"),
                    str(r["stale_days"] if r["stale_days"] is not None else "—"),
                    str(r["zero_vol_days"] if r["zero_vol_days"] is not None else "—"),
                    flag_txt)
    console.print(tbl)

def cmd_prices(args):
    df = store.load_prices(args.ticker.upper())
    if df.empty:
        console.print(f"[red]No stored prices for {args.ticker}[/red]")
        sys.exit(1)
    console.print(f"[bold]{args.ticker.upper()}[/bold] — {len(df)} bars stored")
    console.print(df.tail(args.tail)[["px", "volume"]].to_string())

def cmd_verify_audit(_):
    ok = store.verify_audit_chain()
    console.print("[green]✅ Audit chain intact[/green]" if ok else "[red]❌ AUDIT CHAIN BROKEN[/red]")
    sys.exit(0 if ok else 1)

def cmd_backtest(args):
    store.init()
    from .quant import backtest
    console.print(f"[dim]Walk-forward validation — Score v2, "
                  f"{args.eval_horizon}d horizon, net of costs...[/dim]")
    try:
        out = backtest.run_backtest(step_days=args.step, cost_bps=args.cost_bps,
                                    eval_horizon=args.eval_horizon)
    except RuntimeError as e:
        console.print(f"[red]❌ {e}[/red]")
        sys.exit(1)
    _render_backtest(out["results"])

def _render_backtest(res):
    q = res.get("quintiles_annual_pct")
    if q is None:
        console.print(f"[yellow]{res['verdict_text']}[/yellow]")
        return
    tbl = Table(title=f"Score v2 — annualized fwd return by quintile "
                      f"({res.get('eval_horizon')}d horizon, net of costs)")
    tbl.add_column("Quintile")
    tbl.add_column("Ann. return %", justify="right")
    for i, v in q.items():
        tag = " LOW" if i == 0 else (" HIGH" if i == len(q) - 1 else "")
        tbl.add_row(f"Q{i+1}{tag}", f"{v:+.2f}")
    console.print(tbl)
    c = "green" if res["q5_minus_q1_annual"] >= 3 else "red"
    console.print(f"  Q5−Q1 annualized: [{c}]{res['q5_minus_q1_annual']:+.2f}%[/{c}] (bar: ≥3.0)")
    console.print(f"  IC mean: {res['ic_mean']} | IC IR: {res['ic_ir']} (bar: ≥0.30) | "
                  f"monotonic: {'✅' if res['monotonic'] else '❌'} | turnover: {res['turnover_est']}")
    console.print(f"  sample: {res['n_dates']} rebalances, {res['n_obs']} ticker-dates")
    d = res.get("decay", {})
    console.print("  decay curve (Q5−Q1 %): " + "  ".join(
        f"{h}d: {v:+.2f}%" if v is not None else f"{h}d: n/a" for h, v in d.items()))
    for reg, r in res.get("by_regime", {}).items():
        if r.get("q5_minus_q1_annual") is not None:
            cc = "green" if r["q5_minus_q1_annual"] >= 3 else "red"
            console.print(f"  regime {reg}: spread [{cc}]{r['q5_minus_q1_annual']:+.2f}%[/{cc}], "
                          f"IC IR {r['ic_ir']}")
    ok = res["status"] == "VALIDATED"
    style = "bold green" if ok else "bold red"
    console.print(Panel(
        f"[{style}]{'✅ VALIDATED' if ok else '❌ ' + res['status']}[/{style}]\n" + res["verdict_text"],
        title="Scorecard verdict", box=box.DOUBLE, border_style="green" if ok else "red"))

def cmd_cache_rubric(args):
    store.init()
    from .ai import evidence, rubric
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        cfg = config.load()
        tickers = config.all_tickers(cfg)
        if args.top:
            tickers = tickers[:args.top]
    console.print(f"[dim]Warming rubric cache for {len(tickers)} tickers "
                  f"(delay {args.delay}s between calls)...[/dim]")
    saved = failed = skipped = 0
    for i, t in enumerate(tickers, 1):
        if store.get_cached_rubric(t) and not args.force:
            console.print(f"  [{i}/{len(tickers)}] {t}: [green]cached[/green]")
            skipped += 1
            continue
        console.print(f"  [{i}/{len(tickers)}] {t}: scoring...", end=" ")
        try:
            pack = evidence.build_evidence_pack(t)
        except Exception as e:
            console.print(f"[red]evidence failed: {str(e)[:60]}[/red]")
            failed += 1
            continue
        if not pack.get("business_desc"):
            console.print("[yellow]no evidence (skipped)[/yellow]")
            failed += 1
            continue
        res = rubric.score_bottleneck(t, pack)
        if "error" not in res:
            store.save_rubric(t, res["total"], res)
            meta = res.get("llm_meta", {})
            console.print(f"[green]saved ({res['total']}/30)[/green] "
                          f"[dim]via {meta.get('model', '?')}[/dim]")
            saved += 1
        else:
            dbg = res.get("debug", {})
            console.print("[red]FAILED[/red]")
            console.print(f"      model: {dbg.get('model', '?')}")
            console.print(f"      error: {dbg.get('error', res.get('error', 'unknown'))}")
            failed += 1
        time.sleep(args.delay)
    console.print(f"\n[bold]Summary:[/bold] [green]{saved} saved[/green], "
                  f"[dim]{skipped} already cached[/dim], [red]{failed} failed[/red]")

def cmd_pipeline(args):
    store.init()
    from .agents import pipeline
    t = args.ticker.upper().strip()
    console.print(Panel.fit(
        f"[bold magenta]⚛️ AGENT PIPELINE v2 — {t}[/bold magenta]\n"
        f"[dim]Evidence-anchored debate · claim-graded judge · math sizing · position open[/dim]",
        box=box.DOUBLE, border_style="magenta"))
    with console.status("[cyan]Running pipeline (analysts → debate → trader → math → position)...[/cyan]"):
        state = pipeline.run_pipeline(t)

    console.print("\n[bold cyan]I. ANALYST TEAM[/bold cyan]")
    for k, v in state["analysts"].items():
        console.print(f"  [green]ok[/green] {k} ({len(v)} chars)")

    v = state["verdict"]
    console.print("\n[bold cyan]II. CLAIM-GRADED DEBATE[/bold cyan]")
    console.print(f"  bull claims: {v.get('bull_verified')} verified / {v.get('bull_fabricated')} fabricated")
    console.print(f"  bear claims: {v.get('bear_verified')} verified / {v.get('bear_fabricated')} fabricated")
    wcolor = {"BULL": "green", "BEAR": "red", "ABSTAIN": "yellow"}.get(v.get("winner"), "white")
    console.print(f"  judge: [{wcolor}]{v.get('winner')}[/{wcolor}] (confidence {v.get('confidence')})")
    if v.get("judge_debug"):
        console.print("  [yellow]⚠ judge returned no usable verdict (fallback ABSTAIN). Debug:[/yellow]")
        console.print(f"    [dim]{json.dumps(v['judge_debug'], default=str)[:280]}[/dim]")

    tr = state["trader"]
    color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(tr.get("action"), "white")
    sc = state.get("score") or {}
    console.print(Panel.fit(
        f"[bold {color}]SIGNAL: {tr.get('action')}[/bold {color}]\n"
        f"Confidence: {tr.get('confidence')} | Quant score: {sc.get('total', '?')} ({sc.get('verdict', '?')})\n"
        f"[italic]{str(tr.get('rationale', ''))[:400]}[/italic]",
        title=f"Trader verdict — {t} (direction only)", box=box.DOUBLE, border_style=color))

    console.print("\n[bold cyan]III. MATH ENGINE — judgment proposes, math disposes[/bold cyan]")
    _render_trade(state.get("trade"))

    if state.get("position_opened"):
        console.print(Panel.fit(
            f"[bold green]📌 POSITION OPENED[/bold green] — {t} now in the daily lifecycle monitor.\n"
            f"Run [bold]python -m saf.cli monitor[/bold] each day to check stops, trailing ratchets, time stops, and thesis decay.",
            box=box.SQUARE, border_style="green"))
    elif state.get("trade") and "sizing" in state["trade"] and state["trade"]["sizing"].get("shares", 0) == 0:
        console.print(Panel.fit(
            "[yellow]No position opened — trade was sized to 0 shares (veto or rounding).[/yellow]",
            box=box.SQUARE, border_style="yellow"))

    console.print("[dim]Decision saved to memory + audit log. Grade later with: grade-memory[/dim]")

def _render_trade(trade):
    if not trade:
        console.print("  [dim]HOLD — no position sized.[/dim]")
        return
    if "error" in trade:
        console.print(f"  [red]Sizing error: {trade['error']}[/red]")
        return
    s, e = trade["sizing"], trade["exits"]
    color = "green" if trade["action"] == "BUY" else "red"
    tbl = Table(title="Trade Construction", box=box.HEAVY_HEAD)
    tbl.add_column("Component", style="bold")
    tbl.add_column("Value", justify="right")
    tbl.add_column("Note", overflow="fold")
    tbl.add_row("Action", f"[{color}]{trade['action']}[/{color}]", "direction from trader")
    tbl.add_row("Shares", str(s["shares"]), f"@ ${s['close']}")
    tbl.add_row("Notional", f"${s['notional']:,}", f"{s['pct_account']}% of ${s['account']:,} account")
    tbl.add_row("Realized vol", f"{s['realized_vol']*100:.1f}%", f"target {s['target_vol']*100:.0f}%")
    tbl.add_row("Binding constraint", s["binding_constraint"],
                "; ".join(s.get("constraints", [])) or "—")
    slip = s.get("est_slippage_bps")
    tbl.add_row("Est. slippage", f"{slip} bps" if slip is not None else "—",
                f"vs 20d ADV ${s['adv_20d']:,}" if s.get("adv_20d") else "no ADV data")
    if s.get("risk_multiplier") is not None:
        tbl.add_row("Risk team", f"{s['risk_multiplier']}×",
                    s.get("note") or "median of 3 personas, capped [0.5×, 1.25×]")
    if "error" not in e:
        sign = "+" if trade["action"] == "SELL" else "−"
        tbl.add_row("Stop", f"${e['stop']}", f"entry {sign} 2.5 ATR (${e['atr14']})")
        tbl.add_row("Trail", f"${e['trail_dist']}", "3.0 ATR ratchet (enforced daily)")
        tbl.add_row("Time stop", f"{e['time_stop_days']} days", "thesis must work in a quarter")
    console.print(tbl)

def cmd_positions(_):
    store.init()
    from .exec import lifecycle
    rows = lifecycle.positions_table()
    if not rows:
        console.print("[dim]No open positions.[/dim]")
        return
    tbl = Table(title="Open Positions", box=box.HEAVY_HEAD)
    tbl.add_column("Ticker", style="bold cyan")
    tbl.add_column("Dir", justify="center")
    tbl.add_column("Shares", justify="right")
    tbl.add_column("Entry", justify="right")
    tbl.add_column("Last", justify="right")
    tbl.add_column("P/L %", justify="right")
    tbl.add_column("Days", justify="right")
    tbl.add_column("Stop", justify="right")
    tbl.add_column("Trail", justify="right")
    tbl.add_column("Time stop", justify="right")
    for r in rows:
        pl = r.get("unrealized_pct")
        color = "green" if pl is not None and pl > 0 else "red" if pl is not None and pl < 0 else "white"
        tbl.add_row(r["ticker"], r["direction"],
                    str(r["shares"]),
                    f"${r['entry']:.2f}",
                    f"${r['last_price']:.2f}" if r["last_price"] else "—",
                    f"[{color}]{pl:+.2f}%[/{color}]" if pl is not None else "—",
                    str(r.get("days_held") or 0),
                    f"${r['stop']:.2f}" if r["stop"] else "—",
                    f"${r['trail_stop']:.2f}" if r.get("trail_stop") else "—",
                    str(r["time_stop_date"] or "—"))
    console.print(tbl)

def cmd_monitor(args):
    store.init()
    from .exec import lifecycle
    out = lifecycle.run_monitor(auto_close=args.close)
    if out["n_positions"] == 0:
        console.print("[dim]No open positions to monitor.[/dim]")
        return
    tbl = Table(title=f"Position Monitor — {len(out['events'])} positions checked",
                box=box.HEAVY_HEAD)
    tbl.add_column("Ticker", style="bold cyan")
    tbl.add_column("Status", justify="center")
    tbl.add_column("Price", justify="right")
    tbl.add_column("Reason", overflow="fold")
    for e in out["events"]:
        if e.get("status") == "triggered":
            status = f"[bold red]{e['state']}[/bold red]"
            reason = e["reason"]
            if e.get("realized_pct") is not None:
                reason += f" -> {e['realized_pct']:+.2f}%"
        else:
            status = "[green]open[/green]"
            reason = "—"
        tbl.add_row(e["ticker"], status,
                    f"${e.get('new_price') or e.get('price') or 0:.2f}",
                    reason)
    console.print(tbl)
    if not args.close:
        triggered = [e for e in out["events"] if e.get("status") == "triggered"]
        if triggered:
            console.print(f"[yellow]Run with --close to execute {len(triggered)} close(s) and log to audit.[/yellow]")

def cmd_scorecard(_):
    store.init()
    store.grade_memory()
    sc = store.scorecard()
    if sc["n_signals"] == 0:
        console.print("[dim]No graded signals yet. Run: grade-memory[/dim]")
        return
    console.print(Panel.fit(
        f"[bold magenta]🎯 SIGNAL SCORECARD — the honesty anchor[/bold magenta]\n"
        f"[dim]Every signal graded against realized returns. "
        f"The system cannot lie to itself.[/dim]",
        box=box.DOUBLE, border_style="magenta"))
    tbl = Table(title="Overall Performance", box=box.SIMPLE)
    tbl.add_column("Metric", style="bold")
    tbl.add_column("Value", justify="right")
    tbl.add_row("Signals graded", str(sc["n_signals"]))
    tbl.add_row("Wins", f"[green]{sc['n_wins']}[/green]")
    tbl.add_row("Losses", f"[red]{sc['n_losses']}[/red]")
    tbl.add_row("Flat", str(sc["n_flat"]))
    hr = sc["hit_rate"]
    tbl.add_row("Hit rate", f"[bold]{hr*100:.1f}%[/bold]" if hr is not None else "—")
    avg = sc["avg_return"]
    tbl.add_row("Avg realized return",
                f"[bold]{avg:+.2f}%[/bold]" if avg is not None else "—")
    console.print(tbl)

    if sc["by_action"]:
        tbl2 = Table(title="By Action")
        tbl2.add_column("Action", style="bold")
        tbl2.add_column("N", justify="right")
        tbl2.add_column("Hit rate", justify="right")
        tbl2.add_column("Avg return", justify="right")
        for a, d in sc["by_action"].items():
            hr = f"{d['hit_rate']*100:.0f}%" if d["hit_rate"] is not None else "—"
            ar = f"{d['avg_ret']:+.2f}%" if d["avg_ret"] is not None else "—"
            tbl2.add_row(a, str(d["n"]), hr, ar)
        console.print(tbl2)

    if sc["positions_summary"]:
        ps = sc["positions_summary"]
        console.print(f"\n[bold cyan]Positions:[/bold cyan] {ps.get('n_open', 0)} open | "
                      f"avg realized: {ps.get('avg_realized_pct', 0):+.2f}%")
        if ps.get("states"):
            console.print("  closed by: " + ", ".join(f"{k}={v}" for k, v in ps["states"].items()))

def cmd_grade_memory(_):
    store.init()
    n = store.grade_memory()
    console.print(f"[green]✅ Graded {n} signal(s) against realized returns.[/green]")

def cmd_intraday(args):
    """Phase 8: Computed lead-lag scanner (PART 12)."""
    from .intraday import leadlag
    eq, px = args.equity.upper(), args.proxy.upper()
    console.print(f"[dim]Scanning {eq} -> {px} (interval={args.interval}, period={args.period})...[/dim]")
    res = leadlag.lead_lag_scan(eq, px, interval=args.interval, period=args.period)
    
    if "error" in res:
        console.print(f"[red]❌ Scan failed: {res['error']}[/red]")
        sys.exit(1)

    color = "green" if res["tradeable"] else "red"
    console.print(Panel.fit(
        f"[bold {color}]TRADEABLE: {res['tradeable']}[/bold {color}]\n"
        f"[dim]Pair: {res['pair']} | Bars: {res['bars']}[/dim]",
        box=box.DOUBLE, border_style=color))

    tbl = Table(title="Statistical Gates", box=box.HEAVY_HEAD)
    tbl.add_column("Gate", style="bold")
    tbl.add_column("Value", justify="right")
    tbl.add_column("Pass?", justify="center")
    
    tbl.add_row("Best Lag (bars)", str(res["best_lag_bars"]), 
                "[green]✅[/green]" if res["best_lag_bars"] > 0 else "[red]❌[/red]")
    tbl.add_row("Corr @ Best Lag", f"{res['corr_at_best']:.4f}",
                "[green]✅[/green]" if abs(res["corr_at_best"]) >= 0.15 else "[red]❌[/red]")
    tbl.add_row("Min Overlap (30d)", str(res["min_overlap_ok"]),
                "[green]✅[/green]" if res["min_overlap_ok"] else "[red]❌[/red]")
    tbl.add_row("Spread Stationary (ADF)", f"p={res['spread_pval']:.4f}",
                "[green]✅[/green]" if res["spread_stationary"] else "[red]❌[/red]")
    tbl.add_row("Cointegrated (Engle-Granger)", f"p={res['coint_pval']:.4f}",
                "[green]✅[/green]" if res["cointegrated"] else "[yellow]⚠️[/yellow]")
    console.print(tbl)

def main():
    p = argparse.ArgumentParser(prog="saf", description="Skia Alpha Fund v4")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)

    f = sub.add_parser("fetch")
    f.add_argument("--ticker")
    f.add_argument("--fundamentals", action="store_true")
    f.set_defaults(fn=cmd_fetch)

    sub.add_parser("quality").set_defaults(fn=cmd_quality)

    pr = sub.add_parser("prices")
    pr.add_argument("ticker")
    pr.add_argument("--tail", type=int, default=10)
    pr.set_defaults(fn=cmd_prices)

    sub.add_parser("verify-audit").set_defaults(fn=cmd_verify_audit)

    bt = sub.add_parser("backtest")
    bt.add_argument("--step", type=int, default=21)
    bt.add_argument("--cost-bps", type=float, default=10.0)
    bt.add_argument("--eval-horizon", type=int, default=63, choices=[21, 63, 126])
    bt.set_defaults(fn=cmd_backtest)

    cr = sub.add_parser("cache-rubric")
    cr.add_argument("--tickers")
    cr.add_argument("--top", type=int)
    cr.add_argument("--force", action="store_true")
    cr.add_argument("--delay", type=float, default=3.0)
    cr.set_defaults(fn=cmd_cache_rubric)

    pl = sub.add_parser("pipeline", help="run Agent Pipeline v2 + sizing + position open")
    pl.add_argument("ticker")
    pl.set_defaults(fn=cmd_pipeline)

    sub.add_parser("grade-memory").set_defaults(fn=cmd_grade_memory)

    sub.add_parser("positions", help="list open positions").set_defaults(fn=cmd_positions)

    m = sub.add_parser("monitor", help="daily lifecycle check on open positions")
    m.add_argument("--close", action="store_true", help="execute the closes and audit-log them")
    m.set_defaults(fn=cmd_monitor)

    sub.add_parser("scorecard", help="the honesty anchor — self-grading table").set_defaults(fn=cmd_scorecard)

    # Phase 8: Intraday Scanner
    il = sub.add_parser("intraday", help="computed lead-lag scanner (PART 12)")
    il.add_argument("equity", help="The equity to trade (e.g., XYL)")
    il.add_argument("proxy", help="The proxy/basket ETF (e.g., PHO)")
    il.add_argument("--interval", default="5m", choices=["1m", "5m", "15m", "30m", "1h"])
    il.add_argument("--period", default="60d", help="Lookback period (max 60d for 5m)")
    il.set_defaults(fn=cmd_intraday)

    args = p.parse_args()
    args.fn(args)

if __name__ == "__main__":
    main()