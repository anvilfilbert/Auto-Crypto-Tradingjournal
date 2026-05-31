"""
R-3 (Master plan Week 1 Day 3): backfill funding_paid_usd + liq_distance_atr.

Two functions runnable from the monitor scheduler or one-shot script:

  update_liq_distance_for_open(conn)
    For every OPEN auto_ai position missing liq_distance_atr, query
    Bitget for the current liquidation price + use cached 4H ATR, compute
    distance in ATR units, write back.

  update_funding_for_closed(conn)
    For every CLOSED auto_ai position missing funding_paid_usd, query
    Bitget position history for the matching symbol+close_time, write
    totalFunding (which Bitget tracks per position).

Both are idempotent — skip any position whose field is already non-NULL.
Forward-only by design; historical positions opened before the columns
existed stay NULL unless we explicitly backfill (out of scope for v1).
"""
from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger(__name__)


def _atr_pct_4h(symbol: str) -> Optional[float]:
    """Cached 4H ATR percentage for the symbol. Reuses risk_budget helper."""
    try:
        from trading.risk_budget import _get_asset_atr_pct
        return _get_asset_atr_pct(symbol)
    except Exception:
        return None


def update_liq_distance_for_open(conn) -> dict:
    """For every open auto_ai position with NULL liq_distance_atr, compute and write.

    Skips entirely when FUTURES_AI_MARGIN_MODE=cross (default), because the
    per-position liquidation price is computed against whole-account equity
    in cross mode and the "distance in ATR" number becomes meaningless
    (always huge — measures basket-level risk, not per-trade risk).
    A proper cross-margin equivalent — "max single-position adverse move
    before basket liquidation" — is queued for L-5 (Week 9 risk learners).

    Returns {checked, updated, skipped_no_data, skipped_cross_mode}.
    """
    summary = {"checked": 0, "updated": 0, "skipped_no_data": 0, "skipped_cross_mode": 0}

    # Margin mode gate — env-driven, default 'cross' (current auto-trader setup)
    import os as _os
    margin_mode = _os.environ.get("FUTURES_AI_MARGIN_MODE", "cross").strip().lower()
    if margin_mode == "cross":
        # Count what we WOULD have updated for transparency, then skip
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM positions "
                "WHERE chain='auto_ai' AND (close_time IS NULL OR close_time='') "
                "AND liq_distance_atr IS NULL"
            ).fetchone()[0]
            summary["skipped_cross_mode"] = int(n or 0)
        except Exception:
            pass
        return summary

    try:
        from trading import bitget_trader
    except Exception as e:
        _log.warning("R-3: cannot import bitget_trader: %s", e)
        return summary

    try:
        rows = conn.execute(
            "SELECT id, symbol, entry_price FROM positions "
            "WHERE chain='auto_ai' AND (close_time IS NULL OR close_time='') "
            "AND liq_distance_atr IS NULL"
        ).fetchall()
    except Exception as e:
        _log.warning("R-3: SELECT failed: %s", e)
        return summary

    if not rows:
        return summary
    summary["checked"] = len(rows)

    # Pull all open positions from Bitget once
    try:
        live = bitget_trader.get_open_positions() or []
    except Exception as e:
        _log.warning("R-3: get_open_positions failed: %s", e)
        return summary

    live_by_sym = {p.get("symbol"): p for p in live if p.get("symbol")}

    for r in rows:
        sym = r["symbol"]
        entry = float(r["entry_price"] or 0)
        if entry <= 0:
            summary["skipped_no_data"] += 1
            continue
        match = live_by_sym.get(sym)
        if not match:
            summary["skipped_no_data"] += 1
            continue
        liq_price = match.get("liquidation")
        atr_pct = _atr_pct_4h(sym)
        if not liq_price or atr_pct is None or atr_pct <= 0:
            summary["skipped_no_data"] += 1
            continue
        try:
            liq_dist_pct = abs(entry - float(liq_price)) / entry
            liq_dist_atr = liq_dist_pct / (atr_pct / 100.0)
            conn.execute(
                "UPDATE positions SET liq_distance_atr=? WHERE id=?",
                (round(liq_dist_atr, 3), r["id"]),
            )
            summary["updated"] += 1
        except Exception as e:
            _log.warning("R-3: liq compute failed for pos %s: %s", r["id"], e)
            summary["skipped_no_data"] += 1

    conn.commit()
    return summary


def update_funding_for_closed(conn, lookback_hours: int = 168) -> dict:
    """For every closed auto_ai position missing funding_paid_usd, look up
    totalFunding from Bitget position history and write it back.

    Default lookback 168h = 1 week. Bitget caps history; for older trades
    we'd need a backfill against archived data (out of scope).

    Returns {checked, updated, skipped_no_match}.
    """
    summary = {"checked": 0, "updated": 0, "skipped_no_match": 0}
    try:
        from trading import bitget_trader
    except Exception as e:
        _log.warning("R-3: cannot import bitget_trader: %s", e)
        return summary

    try:
        rows = conn.execute(
            "SELECT id, symbol, open_time, close_time FROM positions "
            "WHERE chain='auto_ai' AND close_time IS NOT NULL AND close_time != '' "
            "AND funding_paid_usd IS NULL "
            "AND close_time >= datetime('now', ?) "
            "ORDER BY close_time DESC",
            (f"-{int(lookback_hours)} hours",),
        ).fetchall()
    except Exception as e:
        _log.warning("R-3: SELECT closed failed: %s", e)
        return summary

    if not rows:
        return summary
    summary["checked"] = len(rows)

    # Pull recent Bitget position history once. The API takes start/end ms.
    try:
        import time as _t
        end_ms = int(_t.time() * 1000)
        start_ms = end_ms - int(lookback_hours * 3600 * 1000)
        history = bitget_trader.get_position_history(start_ms, end_ms, limit=100) or []
    except Exception as e:
        _log.warning("R-3: get_position_history failed: %s", e)
        return summary

    # Index by (symbol, close_time approx) — close times can drift a few seconds
    # so we match by symbol + closest-within-15-minutes timestamp
    from datetime import datetime as _dt
    def _parse_dt(s):
        try: return _dt.fromisoformat((s or "")[:19])
        except Exception: return None

    history_idx: list[tuple] = []
    for h in history:
        sym = h.get("symbol")
        # bitget_trader.get_position_history emits "close_ms" (unix ms)
        ct_ms = h.get("close_ms") or h.get("utime")
        ct_dt = None
        if ct_ms:
            try: ct_dt = _dt.utcfromtimestamp(int(ct_ms) / 1000.0)
            except Exception: pass
        if sym and ct_dt:
            history_idx.append((sym, ct_dt, h.get("total_funding") or 0))

    for r in rows:
        sym = r["symbol"]
        target = _parse_dt(r["close_time"])
        if not target:
            summary["skipped_no_match"] += 1
            continue
        # Find the closest history row for this symbol
        candidates = [(abs((h_dt - target).total_seconds()), funding)
                      for (h_sym, h_dt, funding) in history_idx if h_sym == sym]
        if not candidates:
            summary["skipped_no_match"] += 1
            continue
        # Pick the closest within 15 minutes
        candidates.sort()
        delta_s, funding = candidates[0]
        if delta_s > 900:  # 15 min — likely wrong match
            summary["skipped_no_match"] += 1
            continue
        try:
            conn.execute(
                "UPDATE positions SET funding_paid_usd=? WHERE id=?",
                (round(float(funding), 4), r["id"]),
            )
            summary["updated"] += 1
        except Exception as e:
            _log.warning("R-3: write funding failed for pos %s: %s", r["id"], e)
            summary["skipped_no_match"] += 1

    conn.commit()
    return summary


def run_all(conn) -> dict:
    """Convenience wrapper for the monitor scheduler — runs both backfills."""
    return {
        "liq": update_liq_distance_for_open(conn),
        "funding": update_funding_for_closed(conn),
    }
