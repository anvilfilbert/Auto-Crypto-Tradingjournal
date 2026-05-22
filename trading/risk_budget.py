"""
trading.risk_budget — per-trade sizing.

Given a setup (score, entry, SL) + the trader's current equity, compute
the notional size + leverage that bounds loss to the configured risk %.

Formula:
  risk_dollars = equity × RISK_PER_TRADE_PCT × RISK_SCORE_MULTIPLIERS[score]
  sl_distance_pct = |entry - SL| / entry
  notional = risk_dollars / sl_distance_pct
  notional = min(notional, MAX_NOTIONAL_USDT)        # hard cap
  leverage = min(MAX_LEVERAGE, ceil(notional / margin_available))

Returns None when the setup can't be sized (SL too tight, score too low,
math degenerate, etc.) — caller treats None as "skip this signal".
"""
from __future__ import annotations

import math
from typing import Optional

from . import config


def size_trade(score: int, entry: float, sl: float,
               equity_usdt: Optional[float] = None) -> Optional[dict]:
    """
    Returns sizing dict or None if not sizeable.
      {
        notional_usdt: float, leverage: int, risk_usdt: float,
        sl_distance_pct: float, score_multiplier: float, reason?: str
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
    mult = config.RISK_SCORE_MULTIPLIERS.get(score,
            max(config.RISK_SCORE_MULTIPLIERS.values()) if score > 10 else 0)
    if mult <= 0:
        return None

    risk_dollars = eq * config.RISK_PER_TRADE_PCT * mult
    sl_dist_pct = abs(entry - sl) / entry
    if sl_dist_pct < 0.002:   # SL closer than 0.2% — would be unreasonable lev
        return None

    notional_raw = risk_dollars / sl_dist_pct
    notional = min(notional_raw, config.MAX_NOTIONAL_USDT)

    # Leverage = notional / margin_per_position. With our notional <= $25
    # and equity $100, even 1x lev covers it. Force lev to whatever brings
    # required margin to ~10% of equity so we don't over-collateralise.
    # margin_per_position = notional / leverage. Target margin = 10% × eq.
    target_margin = max(eq * 0.10, 1.0)
    lev = max(1, math.ceil(notional / target_margin))
    lev = min(lev, config.MAX_LEVERAGE)

    return {
        "notional_usdt":     round(notional, 2),
        "leverage":          lev,
        "risk_usdt":         round(min(risk_dollars, notional * sl_dist_pct), 2),
        "sl_distance_pct":   round(sl_dist_pct * 100, 2),
        "score_multiplier":  mult,
        "capped":            notional_raw > config.MAX_NOTIONAL_USDT,
    }
