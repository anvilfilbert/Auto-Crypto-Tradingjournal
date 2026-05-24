"""
chart_cpr.py — Central Pivot Range (CPR) detection + two-day relationship state.

CPR is derived from the prior CLOSED daily bar's High, Low, Close:
  Pivot (P)       = (H + L + C) / 3
  Bottom Central (BC) = (H + L) / 2
  Top Central (TC)    = 2P - BC      (equivalently P + (P - BC))

The three numbers define a directional bias zone for the next session:
  - Price ABOVE TC = bullish bias (longs favored)
  - Price BELOW BC = bearish bias (shorts favored)
  - Price INSIDE [BC, TC] = neutral / range

CPR width = (TC - BC) — narrow CPR (small width vs prior ATR) tends to
precede trend days; wide CPR tends to precede range days. This is used
by Feature 2 (CPR width forecasting) for trail-stop multiplier adjustment.

Two-day relationship state (Franklin Ochoa, "Secrets of a Pivot Boss",
Ch.6): comparing TODAY's CPR to YESTERDAY's CPR produces a 7-state
classifier with directional bias:
  - higher_value      : both BC and TC higher than yesterday → strong bull
  - lower_value       : both BC and TC lower than yesterday → strong bear
  - overlapping_higher: TC higher, BC inside yesterday's range → mild bull
  - overlapping_lower : BC lower, TC inside yesterday's range → mild bear
  - unchanged         : ranges roughly equal → continuation/no bias
  - inside            : today's range entirely inside yesterday's → breakout pending
  - outside           : today's range entirely outside yesterday's → exhaustion

Reference: Ochoa, F. (2010). Secrets of a Pivot Boss, Ch.5-6.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def compute_cpr(prev_day_high: float, prev_day_low: float, prev_day_close: float
                ) -> dict:
    """
    Compute CPR from prior day's H/L/C.

    Returns:
      {
        "pivot":    float,
        "bc":       float (Bottom Central),
        "tc":       float (Top Central),
        "width":    float (TC - BC, always positive),
        "width_pct": float (width as % of pivot, useful cross-asset),
      }

    Returns empty dict on invalid input (zero/negative prices, missing data).
    """
    try:
        h = float(prev_day_high or 0)
        l = float(prev_day_low or 0)
        c = float(prev_day_close or 0)
    except (TypeError, ValueError):
        return {}

    if h <= 0 or l <= 0 or c <= 0 or h < l:
        return {}

    pivot = (h + l + c) / 3.0
    bc    = (h + l) / 2.0
    tc    = (2.0 * pivot) - bc
    width = abs(tc - bc)
    width_pct = (width / pivot * 100.0) if pivot > 0 else 0.0

    return {
        "pivot":     round(pivot, 6),
        "bc":        round(bc, 6),
        "tc":        round(tc, 6),
        "width":     round(width, 6),
        "width_pct": round(width_pct, 4),
    }


def compute_cpr_from_df(df_1d) -> dict:
    """
    Convenience: compute CPR from a 1D pandas DataFrame.

    df must have 'high', 'low', 'close' columns and ≥2 rows. Uses
    iloc[-2] (the LAST CLOSED daily bar) — never iloc[-1] which may be a
    partial/forming bar.
    """
    if df_1d is None or len(df_1d) < 2:
        return {}
    try:
        prev = df_1d.iloc[-2]
        return compute_cpr(prev["high"], prev["low"], prev["close"])
    except (KeyError, IndexError, TypeError):
        return {}


def two_day_relationship(curr_cpr: dict, prev_cpr: dict,
                          equality_tolerance_pct: float = 0.10) -> dict:
    """
    Classify today's CPR vs yesterday's into one of 7 states.

    Args:
      curr_cpr: today's CPR dict (from compute_cpr_from_df with df ending today)
      prev_cpr: yesterday's CPR dict (compute_cpr called on day before yesterday)
      equality_tolerance_pct: percent tolerance for "unchanged" classification

    Returns:
      {
        "state":  one of {higher_value, lower_value, overlapping_higher,
                          overlapping_lower, unchanged, inside, outside, unknown},
        "bias":   one of {strong_bull, mild_bull, neutral, mild_bear, strong_bear, breakout_pending},
        "label":  human-readable summary,
      }
    """
    if not curr_cpr or not prev_cpr:
        return {"state": "unknown", "bias": "neutral", "label": ""}

    curr_bc, curr_tc = curr_cpr["bc"], curr_cpr["tc"]
    prev_bc, prev_tc = prev_cpr["bc"], prev_cpr["tc"]

    # Equality test: both BC and TC within tolerance
    tol = (prev_cpr["pivot"] or 1) * equality_tolerance_pct / 100.0
    if abs(curr_bc - prev_bc) < tol and abs(curr_tc - prev_tc) < tol:
        return {"state": "unchanged", "bias": "neutral",
                "label": f"CPR unchanged ({curr_cpr['width_pct']:.2f}% width)"}

    # Strong directional moves
    if curr_bc > prev_tc:
        return {"state": "higher_value", "bias": "strong_bull",
                "label": "CPR higher_value (today's BC > yesterday's TC) → strong bull"}
    if curr_tc < prev_bc:
        return {"state": "lower_value", "bias": "strong_bear",
                "label": "CPR lower_value (today's TC < yesterday's BC) → strong bear"}

    # Overlapping shifts
    if curr_tc > prev_tc and curr_bc >= prev_bc:
        return {"state": "overlapping_higher", "bias": "mild_bull",
                "label": "CPR overlapping_higher (range shifted up) → mild bull"}
    if curr_bc < prev_bc and curr_tc <= prev_tc:
        return {"state": "overlapping_lower", "bias": "mild_bear",
                "label": "CPR overlapping_lower (range shifted down) → mild bear"}

    # Containment
    if curr_bc >= prev_bc and curr_tc <= prev_tc:
        return {"state": "inside", "bias": "breakout_pending",
                "label": "CPR inside (today's range inside yesterday's) → breakout pending"}
    if curr_bc <= prev_bc and curr_tc >= prev_tc:
        return {"state": "outside", "bias": "neutral",
                "label": "CPR outside (today's range engulfs yesterday's) → exhaustion"}

    return {"state": "unknown", "bias": "neutral", "label": ""}


# CPR width forecasting thresholds (Feature 2, added 2026-05-24).
# Narrow CPR (width % < threshold) tends to precede trend days; wide CPR
# tends to precede range/sideways days. Tuned roughly against equity-index
# norms (Ochoa ch.6); the absolute % is asset-relative so we use width_pct
# (width as % of pivot) not raw price units.
CPR_NARROW_PCT = 0.5   # width <0.5% of pivot = NARROW → expect trend
CPR_WIDE_PCT   = 1.5   # width >1.5% of pivot = WIDE → expect range


def cpr_day_type(cpr: dict) -> dict:
    """
    Classify expected day-type from CPR width.

    Returns:
      {
        "day_type":  "trend" | "range" | "neutral",
        "trail_atr_mult": float (suggested trail-stop ATR multiplier),
        "label":     str
      }

    Day-type → trail multiplier mapping (Feature 2):
      trend  → 2.0× ATR  (wider stops, let trend run)
      range  → 1.0× ATR  (tighter stops, take profit fast)
      neutral → 1.5× ATR (system default)
    """
    if not cpr:
        return {"day_type": "neutral", "trail_atr_mult": 1.5, "label": ""}
    width_pct = cpr.get("width_pct", 0)
    if width_pct < CPR_NARROW_PCT:
        return {"day_type": "trend", "trail_atr_mult": 2.0,
                "label": f"CPR narrow ({width_pct:.2f}%) → trend day → trail 2.0×ATR"}
    if width_pct > CPR_WIDE_PCT:
        return {"day_type": "range", "trail_atr_mult": 1.0,
                "label": f"CPR wide ({width_pct:.2f}%) → range day → trail 1.0×ATR"}
    return {"day_type": "neutral", "trail_atr_mult": 1.5,
            "label": f"CPR normal ({width_pct:.2f}%) → trail 1.5×ATR"}


# CPR directional weight: distinct from generic confluence — applies after
# the 15-signal base. Magnitude matches PO3 range (±0.3) since CPR is
# similarly a structural-context signal.
_CPR_WEIGHT_MAGNITUDE = 0.3


def cpr_alignment_weight(curr_cpr: dict, current_price: float, two_day: dict,
                          direction: str) -> tuple[float, str]:
    """
    Score modifier based on CPR position + two-day state + setup direction.

    Combines two effects:
      1. Price position relative to today's CPR:
         - ABOVE TC + Long  = +0.15 (bullish setup confirmed by daily structure)
         - BELOW BC + Long  = -0.15 (longing into bearish daily structure)
         - mirror for Short
      2. Two-day relationship bias:
         - strong_bull  + Long  = +0.15
         - strong_bear  + Short = +0.15
         - mirror inversions (e.g., strong_bear + Long) = -0.15
         - mild_bull/mild_bear = ±0.10
         - breakout_pending or neutral = 0

    Total capped at ±0.3.

    Returns (weight, reason). On missing data returns (0.0, "").
    """
    if not curr_cpr or not direction or not current_price or current_price <= 0:
        return 0.0, ""

    dir_lc = direction.strip().lower()
    if dir_lc not in ("long", "short"):
        return 0.0, ""
    is_long = (dir_lc == "long")

    weight = 0.0
    parts = []

    # Effect 1: price vs CPR
    if current_price > curr_cpr["tc"]:
        if is_long:
            weight += 0.15
            parts.append("price>TC+Long")
        else:
            weight -= 0.15
            parts.append("price>TC+Short")
    elif current_price < curr_cpr["bc"]:
        if is_long:
            weight -= 0.15
            parts.append("price<BC+Long")
        else:
            weight += 0.15
            parts.append("price<BC+Short")

    # Effect 2: two-day relationship
    if two_day:
        bias = two_day.get("bias")
        if bias == "strong_bull":
            weight += 0.15 if is_long else -0.15
            parts.append(f"strong_bull+{'Long' if is_long else 'Short'}")
        elif bias == "strong_bear":
            weight += -0.15 if is_long else 0.15
            parts.append(f"strong_bear+{'Long' if is_long else 'Short'}")
        elif bias == "mild_bull":
            weight += 0.10 if is_long else -0.10
            parts.append(f"mild_bull+{'Long' if is_long else 'Short'}")
        elif bias == "mild_bear":
            weight += -0.10 if is_long else 0.10
            parts.append(f"mild_bear+{'Long' if is_long else 'Short'}")

    # Cap to ±_CPR_WEIGHT_MAGNITUDE
    weight = max(-_CPR_WEIGHT_MAGNITUDE,
                 min(_CPR_WEIGHT_MAGNITUDE, weight))

    if not parts:
        return 0.0, ""

    reason = f"CPR: {' + '.join(parts)} → {weight:+.2f}"
    return round(weight, 3), reason
