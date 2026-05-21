"""
scanner_invalidator.py — Mark stale scanner-emitted setups so they stop
cluttering the Saved Calls UI.

Why this exists: the scanner saves every flagged setup into analyzed_calls
with status='saved'. Most setups never get entered, but they linger in the
list forever. After a few days the list contains hundreds of entries that
are no longer actionable (price has long moved past the entry). Hindsight
analytics still need these rows for accuracy tracking, so we don't delete —
we widen the state machine:

    saved       → AI-emitted setup, no action yet (kept in UI for max 24h)
    matched     → user entered a position aligned with this call (existing)
    closed      → position closed (existing)
    dismissed   → user explicitly dropped it (existing)
    expired     → saved + older than 24h with no match            ← NEW
    invalidated → saved + price moved >5% past entry, wrong way   ← NEW

UI: Saved Calls hides 'expired' + 'invalidated' by default.
Analytics: hindsight, blindspots, self-review read ALL states.

This module is pure DB + (optional) live price; no AI calls.
"""
import logging
from typing import Optional

from database import db_conn

_log = logging.getLogger(__name__)

# Default knobs (tunable via env in future if needed)
EXPIRY_HOURS         = 24    # un-entered setups older than this → expired
INVALIDATION_PCT     = 5.0   # price moved >X% past entry in wrong way → invalidated
INVALIDATION_GRACE_M = 10    # ignore setups newer than this many minutes


def _get_live_price(symbol: str) -> Optional[float]:
    """Try a cheap live-price source. Returns None on any failure."""
    try:
        import bitget_client
        prices = bitget_client.get_mark_prices([symbol])
        p = prices.get(symbol)
        return float(p) if p else None
    except Exception:
        return None


def mark_expired(conn) -> int:
    """Mark setups older than EXPIRY_HOURS with status='saved' as 'expired'."""
    cur = conn.execute(
        f"""
        UPDATE analyzed_calls
        SET status = 'expired'
        WHERE status = 'saved'
          AND analyst = 'scanner'
          AND created_at < datetime('now', '-{EXPIRY_HOURS} hours')
        """
    )
    conn.commit()
    return cur.rowcount or 0


def mark_invalidated(conn) -> dict:
    """For 'saved' setups still within the freshness window: if the current
    live price has moved >INVALIDATION_PCT past the entry in the wrong
    direction, mark as 'invalidated'."""
    rows = conn.execute(
        f"""
        SELECT id, symbol, direction, entry_price
        FROM analyzed_calls
        WHERE status = 'saved'
          AND analyst = 'scanner'
          AND entry_price IS NOT NULL AND entry_price > 0
          AND created_at < datetime('now', '-{INVALIDATION_GRACE_M} minutes')
          AND created_at >= datetime('now', '-{EXPIRY_HOURS} hours')
        """
    ).fetchall()

    checked   = 0
    flagged   = 0
    skipped   = 0
    by_symbol_price: dict[str, Optional[float]] = {}
    threshold = INVALIDATION_PCT / 100.0

    for r in rows:
        checked += 1
        sym = r["symbol"]
        # Cache live-price lookups across rows of the same symbol
        if sym not in by_symbol_price:
            by_symbol_price[sym] = _get_live_price(sym)
        live = by_symbol_price[sym]
        if live is None:
            skipped += 1
            continue
        entry  = float(r["entry_price"])
        is_long = (r["direction"] or "long").lower().startswith("l")
        # Wrong-way move: for a long, price has FALLEN >X% below entry
        #                 for a short, price has RISEN >X% above entry
        delta_pct = (live - entry) / entry
        if is_long and delta_pct < -threshold:
            wrong_way = True
        elif (not is_long) and delta_pct > threshold:
            wrong_way = True
        else:
            wrong_way = False

        if wrong_way:
            conn.execute(
                "UPDATE analyzed_calls SET status='invalidated' WHERE id=?",
                (r["id"],),
            )
            flagged += 1

    conn.commit()
    return {"checked": checked, "flagged": flagged, "skipped_no_price": skipped}


def run_full_pass() -> dict:
    """Apply both passes in sequence. Designed to be called by a scheduler
    or via API. Returns a summary dict suitable for JSON response."""
    with db_conn() as conn:
        n_expired = mark_expired(conn)
        inv_result = mark_invalidated(conn)
    return {
        "expired":     n_expired,
        "invalidated": inv_result["flagged"],
        "checked":     inv_result["checked"],
        "skipped_no_price": inv_result["skipped_no_price"],
    }
