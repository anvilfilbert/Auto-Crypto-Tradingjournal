"""
position_risk_monitor.py — Live-trade SL discipline alerts.

Hindsight (90d, n=111) showed two leaks the Sonnet trade monitor wasn't
catching reliably:

  1. Break-even discipline: 12 of 31 losers (39%) reached +2% MFE before
     reversing into the SL. Moving SL to entry once the trade is +1× ATR_4H
     in favor would have converted those 12 trades from -$27 avg loss into
     scratches — saves ~$324 / 90d.

  2. Grade-D MAE bleed: 16 grade-D trades produced -$356 with avg MAE
     -4.11%. The pattern is "hold hoping for reversal" past the structural
     stop. A hard threshold at -1.5× ATR_4H gives the trader a final
     deterministic warning before damage accumulates.

This module is alert-only — no exchange writes. The trader confirms the
SL move themselves. Idempotent: each alert fires at most once per
position lifecycle, tracked in-memory by (symbol, open_time_iso).
"""
from __future__ import annotations

import threading
from typing import Optional

import chart_context

# Triggers — tightened after the 90d R:R audit. Avg win $10 vs avg loss
# $26 (1:2.64 against), and 39% of losers reached +2% MFE before reversing.
# Two new alerts added on top of BE_TRIGGER + MAE_BREACH:
#   TRAIL_TRIGGER fires when price reaches +2× ATR favorable, prompting a
#     trail-stop to lock in part of the move (avoids the give-back pattern
#     where MFE 5.23% on winners becomes only ~2% captured).
#   MAE_BREACH tightened from 1.5× → 1.0× ATR — the 0.5× ATR window we
#     were leaving below structural SL was the main grade-D leak driver.
BE_ATR_MULTIPLE      = 1.0
MAE_ATR_MULTIPLE     = 1.0    # was 1.5 — tighter cut on Grade-D bleed
TRAIL_ATR_MULTIPLE   = 2.0
BE_MIN_PCT           = 1.0    # never under +1%
MAE_MIN_PCT          = -1.5   # was -2.0 — pair with the tighter ATR mult
TRAIL_MIN_PCT        = 2.5

# Idempotency state — keyed by (symbol, open_time_iso). Lives for the
# process lifetime; restarting the service may re-fire one alert, which
# is acceptable (better than missing the trigger entirely).
_alerted_be:        set[tuple[str, str]] = set()
_alerted_mae:       set[tuple[str, str]] = set()
_alerted_trail:     set[tuple[str, str]] = set()
_lock = threading.Lock()


def _atr_4h_for(symbol: str) -> Optional[float]:
    """Best-effort ATR_4H lookup. Cached upstream by chart_context."""
    try:
        ctx = chart_context.get_chart_context(symbol, ["4H"]) or {}
        atr = (ctx.get("4H", {}).get("indicators", {}).get("atr") or {})
        return float(atr.get("value") or 0) or None
    except Exception:
        return None


def check(position: dict) -> list[dict]:
    """
    Return zero or more alert dicts describing risk events on this
    position. Each alert dict shape:
      {kind: "BE_TRIGGER"|"MAE_BREACH",
       symbol, direction, entry, mark, atr_pct, current_pct,
       threshold_pct, title, body}

    Caller fires Telegram / UI / DB updates from the returned list.
    Idempotency keyed by (symbol, open_time) — closing the position and
    re-opening produces a fresh open_time and so re-arms the alerts.
    """
    sym       = (position.get("symbol") or "").upper()
    open_iso  = (position.get("open_time") or "").strip()
    direction = (position.get("direction") or "").strip().lower()
    if not sym or not open_iso or direction not in ("long", "short"):
        return []

    try:
        entry  = float(position.get("entry_price") or 0)
        mark   = float(position.get("mark_price") or 0)
        unrl   = float(position.get("unrealized_pct") or 0)
    except (TypeError, ValueError):
        return []
    if entry <= 0 or mark <= 0:
        return []

    is_long = (direction == "long")
    sign    = 1 if is_long else -1
    # current % move in the favorable direction (negative if adverse)
    current_pct = ((mark - entry) / entry * 100.0) * sign

    atr_4h = _atr_4h_for(sym)
    if atr_4h is None or atr_4h <= 0:
        return []
    atr_pct = (atr_4h / entry) * 100.0   # ATR_4H as a % of entry

    be_threshold    = max(atr_pct * BE_ATR_MULTIPLE,    BE_MIN_PCT)
    mae_threshold   = min(-atr_pct * MAE_ATR_MULTIPLE,   MAE_MIN_PCT)
    trail_threshold = max(atr_pct * TRAIL_ATR_MULTIPLE,  TRAIL_MIN_PCT)

    key = (sym, open_iso)
    alerts: list[dict] = []

    # --- BE trigger ---
    with _lock:
        if current_pct >= be_threshold and key not in _alerted_be:
            _alerted_be.add(key)
            alerts.append({
                "kind":            "BE_TRIGGER",
                "symbol":          sym,
                "direction":       direction.title(),
                "entry":           entry,
                "mark":            mark,
                "atr_pct":         round(atr_pct, 2),
                "current_pct":     round(current_pct, 2),
                "threshold_pct":   round(be_threshold, 2),
                "title":           f"Move SL → BE on {sym}",
                "body":            (
                    f"{sym} {direction.title()} is +{current_pct:.2f}% in favor "
                    f"(threshold +{be_threshold:.2f}% = {BE_ATR_MULTIPLE}× ATR_4H {atr_pct:.2f}%). "
                    f"Move SL to entry {entry:.6g} now — 39% of past losers reached this point "
                    "and reversed into loss."
                ),
            })

        # --- Trail trigger (lock partial after +2× ATR) ---
        # Without this, winners average +5.23% MFE but realized P&L
        # is much smaller — the give-back gap.
        if current_pct >= trail_threshold and key not in _alerted_trail:
            _alerted_trail.add(key)
            # Suggest a trail SL halfway between BE and current — keeps
            # ~50% of the current favorable move while still leaving room.
            sign = 1 if is_long else -1
            trail_sl = entry + sign * (atr_pct * 0.5 / 100.0 * entry)
            alerts.append({
                "kind":            "TRAIL_TRIGGER",
                "symbol":          sym,
                "direction":       direction.title(),
                "entry":           entry,
                "mark":            mark,
                "atr_pct":         round(atr_pct, 2),
                "current_pct":     round(current_pct, 2),
                "threshold_pct":   round(trail_threshold, 2),
                "title":           f"Trail SL on {sym} — lock partial winner",
                "body":            (
                    f"{sym} {direction.title()} is +{current_pct:.2f}% in favor "
                    f"(threshold +{trail_threshold:.2f}% = {TRAIL_ATR_MULTIPLE}× ATR_4H). "
                    f"Move SL to {trail_sl:.6g} (BE + 0.5× ATR) to lock partial profit. "
                    "Historical winners reached avg 5.23% MFE but most got given back."
                ),
            })

        # --- MAE breach ---
        if current_pct <= mae_threshold and key not in _alerted_mae:
            _alerted_mae.add(key)
            alerts.append({
                "kind":            "MAE_BREACH",
                "symbol":          sym,
                "direction":       direction.title(),
                "entry":           entry,
                "mark":            mark,
                "atr_pct":         round(atr_pct, 2),
                "current_pct":     round(current_pct, 2),
                "threshold_pct":   round(mae_threshold, 2),
                "title":           f"MAE breach on {sym} — cut now",
                "body":            (
                    f"{sym} {direction.title()} is {current_pct:.2f}% against "
                    f"(threshold {mae_threshold:.2f}% = {MAE_ATR_MULTIPLE}× ATR_4H). "
                    "Tightened from 1.5× to 1.0× ATR — the 0.5× ATR window we "
                    "were leaving below structural SL was the main grade-D leak. "
                    f"Entry {entry:.6g}, mark {mark:.6g}."
                ),
            })

    return alerts


def reset_for_test() -> None:
    """Clear idempotency state — only for unit tests."""
    with _lock:
        _alerted_be.clear()
        _alerted_mae.clear()
        _alerted_trail.clear()
