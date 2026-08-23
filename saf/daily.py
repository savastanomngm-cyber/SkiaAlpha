"""
saf/daily.py — Daily operations loop. The capstone that turns SAF v4 into a usable fund.

Run:  python -m saf.daily

Stages (each isolated — one failure won't kill the run):
  1. Data refresh      (incremental prices + fundamentals)
  2. Quality gate      (exclude stale/thin/unusable names)
  3. Screen            (Score v2, backtest-validated)
  4. Lifecycle monitor (open positions: stops, trails, time stops — dry run)
  5. Memory grading    (grade past signals vs realized returns) + scorecard
  6. Audit-log the run (hash-chained)
"""
import time
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from . import config, store, data

console = Console()


def stage_refresh():
    console.print(Panel.fit("[bold cyan]1. DATA REFRESH[/bold cyan]", box=box.SIMPLE))
    summary = data.refresh_universe(with_fundamentals=True)
    console.print(f"  prices: [green]{summary['ok']}[/green]/{summary['tickers']} ok, "
                  f"[red]{summary['fail']}[/red] failed")
    if summary["failures"]:
        console.print(f"  [dim]failed: {', '.join(summary['failures'][:12])}[/dim]")
    return summary


def stage_quality():
    console.print(Panel.fit("[bold cyan]2. QUALITY GATE[/bold cyan]", box=box.SIMPLE))
    reports = [data.quality_report(t) for t in config.all_tickers()]
    usable = [r for r in reports if r["usable"]]
    bad = [r for r in reports if not r["usable"]]
    console.print(f"  usable: [green]{len(usable)}[/green] | excluded: [red]{len(bad)}[/red]")
    if bad:
        console.print(f"  [dim]excluded: {', '.join(r['ticker'] for r in bad[:12])}[/dim]")
    return usable


def stage_screen():
    console.print(Panel.fit("[bold cyan]3. SCREEN — Score v2[/bold cyan]", box=box.SIMPLE))
    from .quant import score as S
    cfg = config.load(); bench = cfg["settings"]["benchmark"]
    spy = store.load_prices(bench)
    if spy.empty:
        console.print("  [red]benchmark missing — run fetch first[/red]")
        return []
    upto = spy.index[-1]; rows = []
    for t in config.all_tickers(cfg):
        if t == bench:
            continue
        px = store.load_prices(t)
        if px.empty or len(px) < 250:
            continue
        fund = store.get_fundamentals(t)
        fund_norm = {"gross_margin": (fund or {}).get("grossMargins"),
                     "oper_margin": (fund or {}).get("operatingMargins"),
                     "returnOnEquity": (fund or {}).get("returnOnEquity")} if fund else None
        s = S.score_v2(t, upto, {t: px}, spy, fund=fund_norm)
        if s:
            rows.append(s)
    rows.sort(key=lambda r: r["total"], reverse=True)
    tbl = Table(box=box.SIMPLE_HEAVY)
    tbl.add_column("#", width=3)
    tbl.add_column("Ticker", style="bold cyan")
    tbl.add_column("Total", justify="right")
    tbl.add_column("Verdict")
    tbl.add_column("Trend", justify="right")
    tbl.add_column("α-Indep", justify="right")
    tbl.add_column("Bottleneck", justify="right")
    for i, r in enumerate(rows[:12], 1):
        c = r["components"]
        tbl.add_row(str(i), r["ticker"], f"{r['total']:.1f}", r["verdict"],
                    f"{c.get('trend', 0):.0f}", f"{c.get('alpha_indep', 0):.0f}",
                    f"{c.get('bottleneck_prior', 0):.0f}")
    console.print(tbl)
    store.audit_log("daily_screen", {"asof": str(upto.date()), "n": len(rows)})
    return rows


def stage_monitor():
    console.print(Panel.fit("[bold cyan]4. LIFECYCLE MONITOR (open positions)[/bold cyan]",
                            box=box.SIMPLE))
    from .exec import lifecycle
    out = lifecycle.run_monitor(auto_close=False)
    if out["n_positions"] == 0:
        console.print("  [dim]no open positions[/dim]")
        return out
    for e in out["events"]:
        if e.get("status") == "triggered":
            console.print(f"  [red]⚠ {e['ticker']} → {e['state']} ({e['reason']})[/red]")
        else:
            console.print(f"  [green]● {e['ticker']} open[/green] @ ${e.get('price', 0):.2f}")
    trig = [e for e in out["events"] if e.get("status") == "triggered"]
    if trig:
        console.print(f"  [yellow]{len(trig)} trigger(s) — run "
                      f"`python -m saf.cli monitor --close` to execute[/yellow]")
    return out


def stage_scorecard():
    console.print(Panel.fit("[bold cyan]5. MEMORY GRADING + SCORECARD[/bold cyan]",
                            box=box.SIMPLE))
    n = store.grade_memory()
    console.print(f"  graded {n} signal(s) against realized returns")
    sc = store.scorecard()
    if sc["n_signals"] == 0:
        console.print("  [dim]no graded signals yet (signals grade 30d after issuance)[/dim]")
    else:
        hr = sc["hit_rate"]
        line = (f"  signals: {sc['n_signals']} | wins: [green]{sc['n_wins']}[/green] | "
                f"losses: [red]{sc['n_losses']}[/red] | hit rate: "
                f"[bold]{hr*100:.1f}%[/bold]" if hr is not None else "  —")
        console.print(line)
    return sc


def main():
    store.init()
    t0 = time.time()
    console.print(Panel.fit(
        f"[bold magenta]🦅 SKIA ALPHA FUND — DAILY OPS[/bold magenta]\n"
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M')} · full pipeline loop[/dim]",
        box=box.DOUBLE, border_style="magenta"))
    for stage in (stage_refresh, stage_quality, stage_screen, stage_monitor, stage_scorecard):
        try:
            stage()
        except Exception as e:
            console.print(f"  [red]stage failed: {e}[/red]")
            store.audit_log("daily_stage_error",
                            {"stage": stage.__name__, "err": str(e)[:120]})
    chain_ok = store.verify_audit_chain()
    store.audit_log("daily_run",
                    {"elapsed_s": round(time.time() - t0, 1), "audit_chain_ok": chain_ok})
    console.print(Panel.fit(
        f"[bold green]✅ DAILY RUN COMPLETE[/bold green] in {time.time()-t0:.1f}s\n"
        f"[dim]audit chain: {'intact' if chain_ok else 'BROKEN'}[/dim]",
        box=box.DOUBLE, border_style="green"))


if __name__ == "__main__":
    main()