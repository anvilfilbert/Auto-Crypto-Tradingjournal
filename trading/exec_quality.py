"""
A-D (Master plan Week 11): Execution Quality Monitor. Tracks slippage
on every auto_ai fill — expected entry vs actual fill — and surfaces
deteriorating execution to the daily report.

Why this matters:
  A 10bps slippage per round-trip eats ~40% of a typical 4-tick winner.
  When slippage drifts up (e.g., we're entering during thin liquidity or
  using market orders on illiquid alts), we need to know BEFORE it
  silently erodes the edge.

Per-trade calculation:
  signed_slippage_bps = ((fill_price - expected) / expected) × 10_000
                        × sign(direction)

  For Longs, paying ABOVE expected = positive slippage (cost to us).
  For Shorts, selling BELOW expected = positive slippage (cost to us).
  Negative values = positive surprise (we filled better than expected).

Reads:
  positions.entry_price      — what Bitget actually filled at
  positions.intended_entry   — what the scanner/consensus wanted
                                (added idempotently via _ensure_column())

Daily aggregate written to settings.exec_quality_summary (JSON).
"""
from __future__ import annotations

import json
import logging
import statistics
from typing import Any

_log = logging.getLogger(__name__)

WARN_AVG_BPS = 8.0     # 8 bps avg slippage = warn
ALERT_AVG_BPS = 15.0   # 15 bps avg slippage = alert
LOOKBACK_DAYS = 7


def _ensure_column(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "intended_entry" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN intended_entry REAL")
    if "slippage_bps" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN slippage_bps REAL")
    conn.commit()


def record_slippage(conn, position_id: int,
                     intended_entry: float, actual_entry: float,
                     direction: str) -> dict[str, Any]:
    """Write per-trade slippage. Called from executor immediately after fill."""
    _ensure_column(conn)
    if not intended_entry or not actual_entry:
        return {"ok": False, "reason": "missing entry"}
    sign = 1.0 if (direction or "").lower().startswith("l") else -1.0
    raw = (actual_entry - intended_entry) / intended_entry
    bps = raw * 10_000.0 * sign
    try:
        conn.execute(
            "UPDATE positions SET intended_entry=?, slippage_bps=? WHERE id=?",
            (intended_entry, round(bps, 2), position_id)
        )
        conn.commit()
    except Exception as e:
        _log.warning("record_slippage failed for id=%s: %s", position_id, e)
        return {"ok": False, "reason": str(e)}
    return {"ok": True, "slippage_bps": round(bps, 2)}


def aggregate(conn, lookback_days: int = LOOKBACK_DAYS) -> dict[str, Any]:
    """Aggregate slippage stats over the lookback window."""
    _ensure_column(conn)
    rows = conn.execute(
        "SELECT slippage_bps FROM positions "
        "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
        "AND slippage_bps IS NOT NULL "
        f"AND open_time >= datetime('now', '-{int(lookback_days)} days')"
    ).fetchall()
    values = [float(r[0]) for r in rows if r[0] is not None]
    n = len(values)
    if n == 0:
        return {"n": 0, "avg_bps": None, "median_bps": None,
                "max_bps": None, "alert": False, "warn": False}
    avg = statistics.fmean(values)
    med = statistics.median(values)
    mx  = max(values)
    return {
        "n":         n,
        "avg_bps":   round(avg, 2),
        "median_bps": round(med, 2),
        "max_bps":   round(mx, 2),
        "warn":      avg >= WARN_AVG_BPS and avg < ALERT_AVG_BPS,
        "alert":     avg >= ALERT_AVG_BPS,
    }


def snapshot_to_settings(conn) -> dict[str, Any]:
    """Write the current aggregate into settings for the UI to consume."""
    agg = aggregate(conn)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("exec_quality_summary", json.dumps(agg))
        )
        conn.commit()
    except Exception as e:
        _log.warning("exec_quality snapshot persist failed: %s", e)
    return agg


def daily_report_line(conn) -> str:
    """One-line text for the daily Telegram report."""
    agg = aggregate(conn)
    if agg["n"] == 0:
        return "  (no auto_ai fills in 7d)"
    avg = agg["avg_bps"]
    flag = " 🚨" if agg["alert"] else " ⚠" if agg["warn"] else " ✓"
    return (f"  avg slip {avg:+.1f}bps / med {agg['median_bps']:+.1f}bps "
            f"/ worst {agg['max_bps']:+.1f}bps (n={agg['n']}){flag}")
