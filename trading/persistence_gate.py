"""
N-1 (Master plan Noise §2.1): signal persistence gate.

Signals must hold for ≥N consecutive bars before they count toward
confluence. Default N=2 (current + prior bar) — filters out single-bar
flickers that an indicator briefly produces and then reverses.

Reversal signals (failure swing, divergence) use N=1 since they're
inherently momentary.

Public:
  persists(condition_series, min_bars=2) -> bool
  recent_holds(series, condition_fn, min_bars=2) -> bool
  weight_with_persistence(raw_weight, condition_series, min_bars=2) -> float
"""
from __future__ import annotations

from typing import Callable, Optional


def persists(condition_series: list[bool], min_bars: int = 2) -> bool:
    """True iff the condition has been True for the last `min_bars` items.

    Empty / shorter-than-min returns False.
    """
    if not condition_series or len(condition_series) < min_bars:
        return False
    return all(condition_series[-min_bars:])


def recent_holds(values: list, condition_fn: Callable[[any], bool],
                  min_bars: int = 2) -> bool:
    """Apply condition_fn to the last min_bars values; True iff ALL pass.

    Useful for "RSI > 70 for ≥2 bars" or "EMA12 > EMA26 for ≥2 bars" checks.
    """
    if not values or len(values) < min_bars:
        return False
    return all(condition_fn(v) for v in values[-min_bars:])


def weight_with_persistence(raw_weight: float,
                              condition_series: list[bool],
                              min_bars: int = 2) -> float:
    """Apply persistence as a binary gate on a confluence weight.

    Returns 0 if the condition hasn't held for min_bars; else raw_weight.
    """
    if persists(condition_series, min_bars):
        return raw_weight
    return 0.0


def get_default_persistence_bars(signal_kind: Optional[str] = None) -> int:
    """Configurable default per signal kind.

    Reversal signals use 1 (momentary by design).
    Everything else uses 2.
    Can be overridden via FUTURES_AI_PERSISTENCE_BARS env var (global).
    """
    import os
    try:
        env = int(os.environ.get("FUTURES_AI_PERSISTENCE_BARS", "0"))
        if env > 0:
            return env
    except Exception:
        pass
    if signal_kind in ("failure_swing", "divergence", "smt_divergence"):
        return 1
    return 2
