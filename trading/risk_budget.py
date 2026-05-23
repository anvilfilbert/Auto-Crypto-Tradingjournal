"""
trading.risk_budget — per-trade sizing.

Given a setup (score, entry, SL) + the trader's current equity, compute
the notional size + leverage that bounds loss to the configured risk %.

Formula:
  risk_dollars = equity × RISK_PER_TRADE_PCT × score_mult × streak_mult
  sl_distance_pct = |entry - SL| / entry
  notional = risk_dollars / sl_distance_pct
  notional = min(notional, max(MAX_NOTIONAL_USDT, equity × MAX_NOTIONAL_PCT))
  leverage = min(MAX_LEVERAGE, ceil(notional / margin_available))

Profit Compounding Strategy (2026-05-23) additions:
  - score_mult — unchanged: 1.0×/1.5×/2.0× by setup score
  - streak_mult — NEW: 1× base, multiplied by N consecutive wins since
    last loss / breaker reset (capped at MAX_STREAK_MULTIPLIER)
  - dynamic notional cap = max(fixed_floor, equity × MAX_NOTIONAL_PCT)
    so position size grows naturally as the book compounds

Returns None when the setup can't be sized (SL too tight, score too low,
math degenerate, etc.) — caller treats None as "skip this signal".
"""
from __future__ import annotations

import math
from typing import Optional

from . import config


def _consecutive_wins(conn=None) -> int:
    """
    Count of consecutive auto-trader WINNERS up to the most recent close.
    Mirrors kill_switch._consecutive_losses but inverted. Honors the
    operator-initiated breaker_reset_at stamp — only counts trades closed
    AFTER the reset, so the streak builds fresh from each operator
    override.

    Used by the Profit Compounding Strategy progression: each consecutive
    win since the last loss/reset multiplies the per-trade risk budget.
    Returns 0 if no closes, no DB access, or the most recent close was a
    loss (which is what resets the progression to base risk).
    """
    if conn is None:
        from database import db_conn
        with db_conn() as c:
            return _consecutive_wins(c)
    reset_at = config.breaker_reset_at(conn)
    try:
        if config.is_real_mode():
            if reset_at:
                rows = conn.execute(
                    "SELECT realized_pnl FROM positions "
                    "WHERE chain='auto_ai' "
                    "AND close_time IS NOT NULL AND close_time != '' "
                    "AND close_time > ? "
                    "ORDER BY close_time DESC LIMIT 10",
                    (reset_at,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT realized_pnl FROM positions "
                    "WHERE chain='auto_ai' AND close_time IS NOT NULL AND close_time != '' "
                    "ORDER BY close_time DESC LIMIT 10"
                ).fetchall()
        else:
            if reset_at:
                rows = conn.execute(
                    "SELECT realized_pnl FROM paper_positions "
                    "WHERE status='closed' AND closed_at > ? "
                    "ORDER BY closed_at DESC LIMIT 10",
                    (reset_at,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT realized_pnl FROM paper_positions "
                    "WHERE status='closed' "
                    "ORDER BY closed_at DESC LIMIT 10"
                ).fetchall()
        n = 0
        for (pnl,) in rows:
            if (pnl or 0) > 0:    # strict — breakeven (0) does NOT extend streak
                n += 1
            else:
                break
        return n
    except Exception:
        return 0


def _streak_multiplier(wins: int) -> float:
    """Profit Compounding Strategy progression — risk grows with the
    winning streak, capped at MAX_STREAK_MULTIPLIER. Matches the guide's
    Trade 1 + Trade 2 at base risk (Lock & Load), then progressive:

      streak 0  → 1.0×  (Trade 1 — foundation)
      streak 1  → 1.0×  (Trade 2 — lock first win, risk base again)
      streak 2  → 2.0×  (Trade 3 — begin compounding)
      streak 3  → 3.0×  (Trade 4 — progressive growth)
      streak N  → min(N, MAX_STREAK_MULTIPLIER)×

    A loss resets the streak to 0 → multiplier back to 1.0×.
    """
    if not config.COMPOUND_STREAK_ENABLED or wins < 2:
        return 1.0
    return float(min(int(wins), config.MAX_STREAK_MULTIPLIER))


def _effective_notional_cap(equity_usdt: float) -> float:
    """Dynamic notional cap — grows with equity per Profit Compounding.
    Floor of MAX_NOTIONAL_USDT so small accounts still get a tradeable
    minimum size."""
    return max(config.MAX_NOTIONAL_USDT,
                equity_usdt * config.MAX_NOTIONAL_PCT)


def size_trade(score: int, entry: float, sl: float,
               equity_usdt: Optional[float] = None,
               conn=None) -> Optional[dict]:
    """
    Returns sizing dict or None if not sizeable.
      {
        notional_usdt: float, leverage: int, risk_usdt: float,
        sl_distance_pct: float, score_multiplier: float,
        streak_multiplier: float, win_streak: int,
        effective_cap_usdt: float, capped: bool
      }
    """
    if score < min(config.RISK_SCORE_MULTIPLIERS):
        return None
    try:
        entry = float(entry); sl = float(sl)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or sl <= 0 or entry == sl:
        return None

    eq = equity_usdt if equity_usdt is not None else config.starting_equity()
    if eq <= 0:
        return None

    score = max(min(int(score), 10), 0)
    score_mult = config.RISK_SCORE_MULTIPLIERS.get(score,
            max(config.RISK_SCORE_MULTIPLIERS.values()) if score > 10 else 0)
    if score_mult <= 0:
        return None

    # Profit Compounding Strategy — streak progression
    wins = _consecutive_wins(conn)
    streak_mult = _streak_multiplier(wins)

    risk_dollars = eq * config.RISK_PER_TRADE_PCT * score_mult * streak_mult
    sl_dist_pct = abs(entry - sl) / entry
    if sl_dist_pct < 0.002:   # SL closer than 0.2% — would be unreasonable lev
        return None

    notional_raw = risk_dollars / sl_dist_pct
    cap = _effective_notional_cap(eq)
    notional = min(notional_raw, cap)

    # Leverage = notional / margin_per_position. With our notional small
    # relative to equity, even 1× covers it. Force lev to whatever brings
    # required margin to ~10% of equity so we don't over-collateralise.
    target_margin = max(eq * 0.10, 1.0)
    lev = max(1, math.ceil(notional / target_margin))
    lev = min(lev, config.MAX_LEVERAGE)

    return {
        "notional_usdt":      round(notional, 2),
        "leverage":           lev,
        "risk_usdt":          round(min(risk_dollars, notional * sl_dist_pct), 2),
        "sl_distance_pct":    round(sl_dist_pct * 100, 2),
        "score_multiplier":   score_mult,
        "streak_multiplier":  streak_mult,
        "win_streak":         wins,
        "effective_cap_usdt": round(cap, 2),
        "capped":             notional_raw > cap,
    }
