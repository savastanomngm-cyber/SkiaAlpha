"""Computed lead-lag scanner with stationarity/cointegration gates (PART 12)."""
import numpy as np
import pandas as pd
import yfinance as yf
from statsmodels.tsa.stattools import coint, adfuller


def _flatten_columns(df):
    """Handle yfinance MultiIndex columns robustly."""
    if df is None or df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # Normalize column names to title case (Close, High, Low, Volume)
    rename = {}
    for c in df.columns:
        if str(c).lower() == "close":
            rename[c] = "Close"
        elif str(c).lower() == "high":
            rename[c] = "High"
        elif str(c).lower() == "low":
            rename[c] = "Low"
        elif str(c).lower() == "open":
            rename[c] = "Open"
        elif str(c).lower() == "volume":
            rename[c] = "Volume"
    if rename:
        df = df.rename(columns=rename)
    return df


def _align_rth(eq, px):
    if eq.index.tz is None:
        return eq, px
    return eq.between_time('09:30', '16:00'), px.between_time('09:30', '16:00')


def _lag_corr(re, rp, lag):
    if lag == 0:
        a, b = re, rp
    elif lag > 0:
        a, b = re.iloc[:-lag], rp.iloc[lag:]
    else:
        a, b = re.iloc[-lag:], rp.iloc[:lag]
    common = a.index.intersection(b.index)
    if len(common) < 30:
        return None
    c = a[common].corr(b[common])
    return None if c is None or np.isnan(c) else float(c)


def lead_lag_scan(equity, proxy, interval="5m", period="60d"):
    try:
        eq_raw = yf.download(equity, interval=interval, period=period,
                             progress=False, prepost=False, threads=False)
        px_raw = yf.download(proxy, interval=interval, period=period,
                             progress=False, prepost=False, threads=False)
        eq = _flatten_columns(eq_raw)
        px = _flatten_columns(px_raw)
        if eq is None or px is None or eq.empty or px.empty:
            return {"error": "No intraday data available (market may be closed)"}
        if "Close" not in eq.columns or "Close" not in px.columns:
            return {"error": f"Missing Close column. eq cols: {list(eq.columns)}, px cols: {list(px.columns)}"}
        if len(eq) < 100 or len(px) < 100:
            return {"error": f"Insufficient intraday bars: eq={len(eq)}, px={len(px)}"}

        eq, px = _align_rth(eq, px)
        re = eq["Close"].pct_change().dropna()
        rp = px["Close"].pct_change().dropna()
        if len(re) < 30 or len(rp) < 30:
            return {"error": f"Insufficient RTH overlap: re={len(re)}, rp={len(rp)}"}

        results = {}
        for lag in range(-5, 6):
            c = _lag_corr(re, rp, lag)
            if c is not None:
                results[lag] = c
        if not results:
            return {"error": "could not compute correlations"}
        best_lag = max(results, key=lambda k: abs(results[k]))

        eq_c, px_c = eq["Close"].dropna(), px["Close"].dropna()
        common = eq_c.index.intersection(px_c.index)
        eq_c, px_c = eq_c[common], px_c[common]
        spread_stat = coint_p = 1.0
        cointegrated = False
        if len(eq_c) > 50:
            try:
                log_eq, log_px = np.log(eq_c), np.log(px_c)
                beta = np.polyfit(log_px, log_eq, 1)[0]
                spread = log_eq - beta * log_px
                spread_stat = adfuller(spread.dropna())[1]
            except Exception:
                pass
            try:
                coint_p = coint(eq_c, px_c)[1]
                cointegrated = coint_p < 0.05
            except Exception:
                pass
        corr_best = results.get(best_lag, 0.0)
        tradeable = all([abs(corr_best) >= 0.15, best_lag > 0, spread_stat < 0.05])
        return {
            "pair": f"{equity}->{proxy}",
            "best_lag_bars": best_lag,
            "lag_note": ("equity LEADS proxy" if best_lag > 0 else
                         "proxy LEADS equity (thesis violated)" if best_lag < 0 else "no lead"),
            "corr_at_best": round(corr_best, 4),
            "min_overlap_ok": len(re) >= 30 * 78 // 5,
            "spread_stationary": spread_stat < 0.05,
            "spread_pval": round(spread_stat, 4),
            "cointegrated": cointegrated,
            "coint_pval": round(coint_p, 4),
            "tradeable": tradeable,
            "bars": len(re),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:150]}"}	