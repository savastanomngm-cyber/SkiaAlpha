"""Sizing engine (improvements.txt PART 7).
Judgment proposes, math disposes: the agent pipeline decides DIRECTION,
this module owns SIZE, SLIPPAGE, and EXITS.
  - Volatility-targeted sizing (equal risk per name)
  - Liquidity cap: 5% of 20d ADV, ~1 month to build
  - Hard weight cap + 25% sector cap + crowding penalty
  - Almgren square-root slippage model
  - Risk team v2: LLM limited to [0.5x, 1.25x] multiplier + veto
  - Structural exits: 2.5 ATR stop, 3 ATR trail, 63-day time stop

PATCHED v2: RISK_V2_SYS tightened — personas told vol-targeting already
handled realized volatility. Veto must cite risks OUTSIDE that scope
(liquidity, concentration, earnings, sector exposure, negative components)."""
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .. import config, store
from ..ai import llm


def realized_vol(px: pd.DataFrame, window: int = 60):
    r = px["px"].pct_change().dropna()
    if len(r) < window:
        return None
    return float(r.tail(window).std() * np.sqrt(252))


def avg_dollar_volume(px: pd.DataFrame, window: int = 20):
    if px.empty or len(px) < window or "volume" not in px.columns:
        return None
    return float((px["px"] * px["volume"]).tail(window).mean())


def slippage_model(notional: float, adv):
    if not adv or adv <= 0 or notional <= 0:
        return None
    participation = notional / (adv * 21)
    return round(50 * np.sqrt(max(participation, 1e-6)), 1)


def _max_corr(ticker, names, limit=10):
    if not names:
        return None
    px = store.load_prices(ticker)
    if px.empty:
        return None
    r = px["px"].pct_change().dropna()
    best = None
    for t in names[:limit]:
        o = store.load_prices(t)
        if o.empty:
            continue
        ro = o["px"].pct_change().dropna()
        common = r.index.intersection(ro.index)
        if len(common) < 60:
            continue
        c = r[common].corr(ro[common])
        if c is not None and not np.isnan(c) and (best is None or c > best):
            best = float(c)
    return best


def position_size(ticker, account=None, target_vol=None, max_weight=None,
                  portfolio=None):
    cfg = config.load()["settings"]
    account = account or 100_000
    target_vol = target_vol or cfg.get("target_vol", 0.15)
    max_weight = max_weight or cfg.get("max_weight", 0.08)

    px = store.load_prices(ticker)
    if px.empty or len(px) < 60:
        return {"error": "insufficient price data"}
    vol = realized_vol(px)
    if not vol or vol < 0.05:
        return {"error": f"realized vol {vol} implausible — data problem?"}

    close = float(px["px"].iloc[-1])
    constraints = []

    notional = account * target_vol / vol
    vol_notional = notional

    adv = avg_dollar_volume(px)
    liq_cap = adv * 0.05 * 21 if adv else float("inf")

    weight_cap = account * max_weight
    notional = min(notional, weight_cap, liq_cap)
    if notional == vol_notional:
        binding = "vol_target"
    elif notional == weight_cap:
        binding = f"max_weight ({max_weight*100:.0f}%)"
    else:
        binding = "liquidity (5% ADV)"
        constraints.append("thin name — ~1 month to build position")

    if portfolio:
        my_sector = config.sector_of(ticker)
        same_sector = sum(w for t, w in portfolio.items()
                          if config.sector_of(t) == my_sector)
        room = (cfg.get("sector_cap", 0.25) - same_sector) * account
        if room < notional:
            notional = max(0.0, room)
            binding = f"sector_cap ({cfg.get('sector_cap', 0.25)*100:.0f}%)"
            constraints.append(f"sector '{my_sector}' already {same_sector*100:.0f}% deployed")
        corr_book = _max_corr(ticker, list(portfolio.keys()))
        if corr_book is not None and corr_book > 0.7:
            pen = 1 - (corr_book - 0.7) / 0.3 * 0.5
            notional *= pen
            constraints.append(f"corr {corr_book:.2f} to book -> size x{pen:.2f}")

    shares = int(notional // close) if close > 0 else 0
    return {
        "ticker": ticker, "close": round(close, 2),
        "realized_vol": round(vol, 3),
        "adv_20d": round(adv) if adv else None,
        "shares": shares,
        "notional": round(shares * close),
        "pct_account": round(shares * close / account * 100, 2),
        "binding_constraint": binding,
        "est_slippage_bps": slippage_model(shares * close, adv),
        "constraints": constraints,
        "account": account, "target_vol": target_vol, "max_weight": max_weight,
    }


def exits(ticker, entry):
    px = store.load_prices(ticker)
    if px.empty or len(px) < 20:
        return {"error": "insufficient data for exits"}
    c, h, l = px["px"], px["high"], px["low"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    entry = float(entry)
    return {
        "entry": entry, "atr14": round(atr, 3),
        "stop": round(entry - 2.5 * atr, 2),
        "trail_dist": round(3.0 * atr, 2),
        "time_stop_days": 63,
        "review_triggers": ["earnings_date", "score_decay_below_45",
                            "corr_to_spy_above_0.75"],
    }


# ── RISK TEAM v2 — tightened prompt ───────────────────────────────
RISK_V2_SYS = """You are the {persona}

You review a MATHEMATICALLY-SIZED position. The sizing math has ALREADY
handled realized volatility via vol-targeting and position caps — do NOT
re-veto based on high stock-level vol. That is double-counting.

Veto ONLY for risks the math engine did NOT price in:
- Liquidity (ADV too low to exit, thin spreads)
- Concentration (sector weight cap, single-name >8%, high correlation to book)
- Upcoming binary event (earnings, FDA, litigation)
- Negative quant component (negative trend or negative alpha_indep)
- Thesis decay (rubric total below 22, evidence contradicts thesis)

Return ONLY JSON:
{{"size_multiplier": 0.5-1.25, "veto": true/false, "reasoning": "..."}}
A veto MUST cite a specific risk fact from the DATA block."""

PERSONAS = {
    "aggressive": "AGGRESSIVE risk reviewer. You favor conviction sizing when the data supports the thesis.",
    "neutral": "NEUTRAL risk reviewer. You balance conviction against drawdown control.",
    "safe": "SAFE risk reviewer. You emphasize capital preservation. Your veto is binding.",
}


def run_risk_team(ticker, sizing, data_block):
    db = json.dumps(data_block, indent=1, default=str)
    plan = json.dumps(sizing, indent=1, default=str)

    def _review(name, desc):
        prompt = (f"DATA:\n{db}\n\nMATH-SIZED POSITION:\n{plan}\n\nGive your review.")
        out, _ = llm.complete_json(RISK_V2_SYS.replace("{persona}", desc),
                                   prompt, temperature=0.3)
        if out and "size_multiplier" in out:
            try:
                out["size_multiplier"] = float(np.clip(out.get("size_multiplier", 1.0), 0.5, 1.25))
            except Exception:
                out["size_multiplier"] = 1.0
            out["veto"] = bool(out.get("veto", False))
            return out
        return {"size_multiplier": 1.0, "veto": False,
                "reasoning": "review unavailable — neutral multiplier applied"}

    opinions = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {name: ex.submit(_review, name, desc) for name, desc in PERSONAS.items()}
        for name, fut in futs.items():
            try:
                opinions[name] = fut.result()
            except Exception as e:
                opinions[name] = {"size_multiplier": 1.0, "veto": False,
                                  "reasoning": f"error: {str(e)[:60]}"}
    return opinions


def apply_risk_team(sizing, opinions):
    mults = [o.get("size_multiplier", 1.0) for o in opinions.values()]
    final_mult = float(np.clip(np.median(mults), 0.5, 1.25))
    vetoed = [name for name, o in opinions.items() if o.get("veto")]
    if vetoed:
        return {**sizing, "shares": 0, "notional": 0, "pct_account": 0.0,
                "risk_multiplier": 0.0,
                "note": "VETOED by: " + ", ".join(vetoed) + " — " +
                        str(opinions[vetoed[0]].get("reasoning", ""))[:150]}
    shares = int(sizing["shares"] * final_mult)
    return {**sizing, "shares": shares,
            "notional": round(shares * sizing["close"]),
            "pct_account": round(shares * sizing["close"] / sizing["account"] * 100, 2),
            "risk_multiplier": final_mult}


def build_trade(ticker, action, account=None, portfolio=None, data_block=None):
    if action not in ("BUY", "SELL"):
        return None
    sizing = position_size(ticker, account=account, portfolio=portfolio)
    if "error" in sizing:
        return {"error": sizing["error"]}
    opinions = {}
    if data_block is not None:
        opinions = run_risk_team(ticker, sizing, data_block)
        sizing = apply_risk_team(sizing, opinions)
    ex = exits(ticker, sizing["close"])
    if "error" not in ex and action == "SELL":
        ex["stop"] = round(sizing["close"] + 2.5 * ex["atr14"], 2)
    return {"action": action, "sizing": sizing,
            "risk_opinions": opinions, "exits": ex}