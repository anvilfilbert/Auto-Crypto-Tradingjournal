"""
N-3 (Master plan Week 7): three structural noise gates that complement
N-1 (consensus variance) and N-2 (FDR multiple-testing). All three return
a (weight, reason) tuple in the same shape as the bear_phase/HMM/PO3
modifiers so they slot directly into the Stage-3 modifier stack.

Wick rejection filter:
  A long upper wick on the most recent 4H candle relative to the body is
  evidence of supply at the top — bad for Longs. Mirror for Shorts on
  long lower wicks. Off when the candle is doji-ish (small body).

ADX low-trend gate:
  ADX < 20 means there is no trend on this timeframe — modifier-only,
  not a veto, because some setups (mean-reversion / range) genuinely
  prefer a flat ADX. Env knob `FUTURES_AI_ADX_HARD_GATE=1` upgrades
  this to a hard veto.

BB squeeze breakout-prep:
  Band width compressed into bottom decile of recent history → volatility
  expansion likely. Mild bonus (+0.2) when the setup is a breakout in the
  direction the bands have been pinching against. Inverse-mean-reversion
  setups get no bonus from this.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any

_log = logging.getLogger(__name__)

# Tunables (env-overridable)
_WICK_BODY_MIN_PCT      = float(os.environ.get("FUTURES_AI_WICK_BODY_MIN", "0.30"))   # body must be ≥30% of range
_WICK_REJECT_RATIO      = float(os.environ.get("FUTURES_AI_WICK_REJECT_RATIO", "1.5"))  # wick ≥1.5× body
_WICK_PENALTY           = float(os.environ.get("FUTURES_AI_WICK_PENALTY", "0.4"))     # score - 0.4 on hit

_ADX_TREND_THRESHOLD    = float(os.environ.get("FUTURES_AI_ADX_THRESHOLD", "20"))
_ADX_PENALTY            = float(os.environ.get("FUTURES_AI_ADX_PENALTY", "0.3"))      # score - 0.3 if below
_ADX_HARD_GATE          = int(os.environ.get("FUTURES_AI_ADX_HARD_GATE", "0"))        # 1 = veto

_BB_SQUEEZE_DECILE      = float(os.environ.get("FUTURES_AI_BB_SQUEEZE_DECILE", "0.15"))  # bottom 15%
_BB_SQUEEZE_BOOST       = float(os.environ.get("FUTURES_AI_BB_SQUEEZE_BOOST", "0.2"))


# ─── Wick rejection ─────────────────────────────────────────────────────

def wick_rejection_weight(candle: dict | None, direction: str) -> tuple[float, str]:
    """One 4H candle (open/high/low/close), direction = 'Long' or 'Short'.

    Returns (score_delta, reason). Negative delta = penalty.

    A Long entered on a candle with a long UPPER wick is buying into clear
    rejection. A Short into a long LOWER wick is selling into rejection.
    """
    if not candle:
        return (0.0, "")
    try:
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        c = float(candle["close"])
    except (KeyError, TypeError, ValueError):
        return (0.0, "")

    rng = h - l
    if rng <= 0:
        return (0.0, "")
    body  = abs(c - o)
    body_pct = body / rng
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # Skip doji / inside bars where wick math is meaningless
    if body_pct < _WICK_BODY_MIN_PCT:
        return (0.0, "")
    if body <= 0:
        return (0.0, "")

    if direction == "Long" and upper_wick >= body * _WICK_REJECT_RATIO:
        return (-_WICK_PENALTY,
                f"upper wick {upper_wick/body:.1f}× body — rejection at top")
    if direction == "Short" and lower_wick >= body * _WICK_REJECT_RATIO:
        return (-_WICK_PENALTY,
                f"lower wick {lower_wick/body:.1f}× body — rejection at bottom")
    return (0.0, "")


# ─── ADX low-trend ─────────────────────────────────────────────────────

def adx_low_trend_weight(adx_value: float | None) -> tuple[float, str, bool]:
    """Returns (score_delta, reason, veto). veto=True only when hard gate enabled."""
    if adx_value is None:
        return (0.0, "", False)
    try:
        v = float(adx_value)
    except (TypeError, ValueError):
        return (0.0, "", False)
    if v < _ADX_TREND_THRESHOLD:
        veto = bool(_ADX_HARD_GATE)
        return (-_ADX_PENALTY,
                f"ADX {v:.1f} < {_ADX_TREND_THRESHOLD:.0f} — no trend",
                veto)
    return (0.0, "", False)


# ─── BB squeeze ─────────────────────────────────────────────────────────

def bb_squeeze_weight(bb_widths_history: list[float] | None,
                      current_bw: float | None,
                      direction: str,
                      archetype: str | None = None) -> tuple[float, str]:
    """Bottom-decile band-width = squeeze. Boost ONLY for breakout-style
    archetypes; mean-reversion setups in a squeeze are not improved.

    bb_widths_history: list of historical band-width values (most recent last)
    current_bw:        the latest band width
    archetype:         setup archetype name (e.g., 'breakout', 'reversal')
    """
    if not bb_widths_history or current_bw is None:
        return (0.0, "")
    n = len(bb_widths_history)
    if n < 20:
        return (0.0, "")
    try:
        # Percentile rank of current_bw
        sorted_hist = sorted(float(x) for x in bb_widths_history if x is not None)
        rank = sum(1 for v in sorted_hist if v <= current_bw) / len(sorted_hist)
    except (TypeError, ValueError):
        return (0.0, "")
    if rank > _BB_SQUEEZE_DECILE:
        return (0.0, "")
    # Only reward breakout-style archetypes
    arch = (archetype or "").lower()
    if "breakout" in arch or "trend_continuation" in arch or "po3_expansion" in arch:
        return (_BB_SQUEEZE_BOOST,
                f"BB squeeze (bw rank {rank*100:.0f}%) + breakout archetype")
    return (0.0, "")


def evaluate_all(*, last_candle: dict | None,
                  adx_4h: float | None,
                  bb_widths_history: list[float] | None,
                  bb_current_width: float | None,
                  direction: str,
                  archetype: str | None = None) -> dict[str, Any]:
    """Run all three noise gates, return merged result.

    Returns {
      'total_delta': float,
      'veto': bool,
      'reasons': [str, ...],
      'parts': {
         'wick':    (delta, reason),
         'adx':     (delta, reason, veto_flag),
         'bb':      (delta, reason),
      },
    }
    """
    wick_delta, wick_reason = wick_rejection_weight(last_candle, direction)
    adx_delta, adx_reason, adx_veto = adx_low_trend_weight(adx_4h)
    bb_delta, bb_reason = bb_squeeze_weight(bb_widths_history, bb_current_width,
                                              direction, archetype)
    total = wick_delta + adx_delta + bb_delta
    reasons = [r for r in (wick_reason, adx_reason, bb_reason) if r]
    return {
        "total_delta": round(total, 3),
        "veto":        adx_veto,
        "reasons":     reasons,
        "parts": {
            "wick": (wick_delta, wick_reason),
            "adx":  (adx_delta, adx_reason, adx_veto),
            "bb":   (bb_delta, bb_reason),
        },
    }
