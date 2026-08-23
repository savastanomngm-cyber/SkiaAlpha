"""Position lifecycle monitor — stops, time stops, trailing ratchets, thesis decay."""
import pandas as pd
from .. import store, config
from ..quant import score as S


def _norm(pos):
    """Normalize a position row across legacy/current schemas."""
    entry = pos.get("entry_price") or pos.get("entry") or 0
    opened = pos.get("entry_date") or pos.get("opened_at") or ""
    return {
        "id": pos.get("id"),
        "ticker": pos.get("ticker"),
        "entry_price": float(entry) if entry else 0,
        "opened_at": opened,
        "direction": pos.get("direction", "LONG"),
        "shares": pos.get("shares"),
        "stop": pos.get("stop"),
        "trail_dist": pos.get("trail_dist") or pos.get("trail"),
        "time_stop_days": pos.get("time_stop_days") or 63,
        "state": pos.get("state", "OPEN"),
    }


def positions_table():
    """Open positions enriched with last price / unrealized P/L.
    Defensive against both legacy and current schemas."""
    positions = []
    for raw in store.open_positions():
        pos = _norm(raw)
        px = store.load_prices(pos["ticker"])
        last_price = None
        unrealized_pct = None
        days_held = None
        if not px.empty:
            last_price = float(px["px"].iloc[-1])
            sign = 1 if pos["direction"] == "LONG" else -1
            if pos["entry_price"]:
                unrealized_pct = round(
                    (last_price / pos["entry_price"] - 1) * 100 * sign, 2)
            try:
                days_held = (pd.Timestamp.now() -
                             pd.Timestamp(pos["opened_at"])).days
            except Exception:
                days_held = None
        positions.append({
            "id": pos["id"],
            "ticker": pos["ticker"],
            "direction": pos["direction"],
            "entry_price": pos["entry_price"] or None,
            "entry": pos["entry_price"] or None,   # alias for legacy frontend
            "opened_at": pos["opened_at"],
            "shares": pos["shares"],
            "last_price": last_price,
            "unrealized_pct": unrealized_pct,
            "days_held": days_held,
            "stop": pos["stop"],
            "trail_stop": pos["trail_dist"],
            "state": pos["state"],
        })
    return positions


def daily_monitor():
    """Run after each data refresh. Hard stops / time stops / thesis decay /
    trailing ratchet. Direction-aware for shorts."""
    actions = []
    cfg = config.load()
    spy = store.load_prices(cfg["settings"]["benchmark"])
    for raw in store.open_positions():
        pos = _norm(raw)
        px = store.load_prices(pos["ticker"])
        if px.empty or not pos["entry_price"]:
            continue
        last = float(px["px"].iloc[-1])
        pid = pos["id"]

        # 1. Hard stop
        stop = pos["stop"]
        if stop is not None:
            hit = (last <= stop) if pos["direction"] == "LONG" else (last >= stop)
            if hit:
                pct = store.close_position(pid, last, "STOP_HIT")
                actions.append({"ticker": pos["ticker"], "action": "CLOSED",
                                "reason": "STOP_HIT", "pct": pct})
                continue

        # 2. Time stop
        try:
            days = (pd.Timestamp.now() - pd.Timestamp(pos["opened_at"])).days
        except Exception:
            days = 0
        if days > pos["time_stop_days"]:
            sign = 1 if pos["direction"] == "LONG" else -1
            ret = (last / pos["entry_price"] - 1) * 100 * sign
            if ret < 0:
                pct = store.close_position(pid, last, "TIME_STOP")
                actions.append({"ticker": pos["ticker"], "action": "CLOSED",
                                "reason": "TIME_STOP", "pct": pct})
                continue

        # 3. Thesis decay (direction-aware)
        if not spy.empty and len(px) >= 250:
            fund = store.get_fundamentals(pos["ticker"])
            s = S.score_v2(pos["ticker"], px.index[-1],
                           {pos["ticker"]: px}, spy, fund=fund)
            if s:
                if pos["direction"] == "LONG" and s["total"] < 40:
                    actions.append({"ticker": pos["ticker"], "action": "TRIM",
                                    "reason": f"SCORE_DECAY ({s['total']:.0f})"})
                elif pos["direction"] == "SHORT" and s["total"] >= 45:
                    actions.append({"ticker": pos["ticker"], "action": "TRIM",
                                    "reason": f"SCORE_RECOVERY ({s['total']:.0f})"})

        # 4. Trailing stop ratchet
        trail = pos["trail_dist"]
        if trail is not None and stop is not None:
            new_stop = (last - trail) if pos["direction"] == "LONG" else (last + trail)
            improved = (new_stop > stop) if pos["direction"] == "LONG" else (new_stop < stop)
            if improved and pid is not None:
                store.update_position_stop(pid, new_stop)
    store.audit_log("lifecycle_monitor", {"actions": len(actions)})
    return actions