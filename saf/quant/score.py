"""Score v2 — five sub-scores, sign-aware, regime-aware (PART 3)."""
import numpy as np
import pandas as pd

def t_score(px):
    if px.empty or len(px) < 130: return 0.0
    c = px["px"]
    r6 = c.pct_change(126).iloc[-1]
    vol = c.pct_change().std() * np.sqrt(252)
    sharpe6 = r6 / (vol * np.sqrt(0.5)) if vol > 0 else 0
    above50 = int(c.iloc[-1] > c.rolling(50).mean().iloc[-1])
    return float(np.clip(12.5 * sharpe6 + 6 * above50, 0, 25))

def a_score(px, spy):
    common = px.index.intersection(spy.index)
    rt = px["px"][common].pct_change().dropna()
    rs = spy["px"][common].pct_change().dropna()
    common2 = rt.index.intersection(rs.index)
    rt, rs = rt[common2], rs[common2]
    if len(rt) < 120: return 0.0
    beta = rt.cov(rs) / rs.var() if rs.var() > 0 else 0
    resid = rt - beta * rs
    ir = (resid.mean() / resid.std()) * np.sqrt(252) if resid.std() > 0 else 0
    corr = rt.corr(rs)
    gate = np.tanh(resid.sum() * 10)
    raw = (1 - abs(corr)) * 15 + np.clip(ir, -2, 2) * 7.5
    return float(np.clip(gate * raw, -15, 30))

def r_score(px, spy):
    common = px.index.intersection(spy.index)
    if len(common) < 70: return 0.0
    rt = px["px"][common].pct_change(63).iloc[-1]
    rs = spy["px"][common].pct_change(63).iloc[-1]
    vol = px["px"].pct_change().std() * np.sqrt(252)
    return float(np.clip((rt - rs) / max(vol * np.sqrt(0.25), 0.05) * 5, -10, 20))

def q_score(fund):
    if not fund: return 0.0
    gm = fund.get("gross_margin") or fund.get("grossMargins") or 0
    om = fund.get("oper_margin") or fund.get("operatingMargins") or 0
    roe = fund.get("returnOnEquity") or 0
    s = 0.0
    s += np.clip((gm - 0.25) / 0.45, 0, 1) * 8
    s += np.clip(om / max(gm, 0.01), 0, 1) * 4
    s += np.clip(roe / 0.30, 0, 1) * 3
    return float(np.clip(s, 0, 15))

def b_score(bottleneck_rubric):
    if not bottleneck_rubric: return 5.0
    total = bottleneck_rubric.get("total", 0)
    return float(np.clip(total / 30 * 10, 0, 10))

def confidence_flag(px, fund):
    bars = len(px)
    try: fresh = (pd.Timestamp.now(tz=px.index.tz) - px.index[-1]).days < 7
    except Exception: fresh = False
    has_fund = bool(fund and (fund.get("gross_margin") or fund.get("grossMargins")))
    if bars >= 250 and fresh and has_fund: return "HIGH"
    if bars >= 150 and fresh: return "MEDIUM"
    return "LOW"

def shadow_alpha_v2(px, spy, fund, rubric=None):
    T = t_score(px); A = a_score(px, spy); R = r_score(px, spy)
    Q = q_score(fund) if fund else 0
    B = b_score(rubric) if rubric else 5.0
    total = T + A + R + Q + B
    return {
        "total": round(total, 1),
        "components": {"trend": round(T, 1), "alpha_indep": round(A, 1),
                       "rel_strength": round(R, 1), "quality": round(Q, 1),
                       "bottleneck_prior": round(B, 1)},
        "verdict": ("CANDIDATE" if total >= 60 else "WATCH" if total >= 45 else "PASS"),
        "confidence": confidence_flag(px, fund),
    }

def score_v2(ticker, upto, prices, spy, fund=None, rubric=None):
    """Public wrapper used by server/screener."""
    px = prices.get(ticker)
    if px is None or px.empty: return None
    return shadow_alpha_v2(px, spy, fund, rubric)