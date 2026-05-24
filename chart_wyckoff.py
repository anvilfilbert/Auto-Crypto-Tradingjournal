"""
chart_wyckoff.py — Wyckoff-style pattern detectors.

Phase 1 (this file, initial scaffold):
  - detect_single_bar_trap: spring/upthrust single-bar pattern

Phase 2 (added later):
  - detect_spring: multi-bar Wyckoff Spring (failed breakdown + secondary test)
  - detect_upthrust: multi-bar Upthrust (failed breakout)
  - detect_absorption: range-top absorption (rising supports near resistance)
  - detect_sot: Shortening of the Thrust (momentum exhaustion)
  - detect_wave_ratio: impulse-vs-correction leg ratio

The single-bar trap pattern (Schlotmann TA Masterclass, Ch. 4) is the
cheapest Wyckoff-adjacent signal: wick beyond prior range → close back
inside → next bar fails to follow through. Strong reversal trigger at S/R.

Reference: Schlotmann & Czubatinski (2019), "Trading: Technical Analysis
Masterclass", Ch. 4 "Wave Analysis" + Ch. "Bull/Bear Traps".
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)


# Default lookback for "prior range" reference (Schlotmann uses ~10-30 bars)
DEFAULT_TRAP_LOOKBACK = 20

# Minimum wick-to-body ratio to qualify as a meaningful trap (filters micro-wicks)
MIN_WICK_BODY_RATIO = 1.0

# Minimum wick penetration beyond prior extreme, as fraction of bar's range
# (e.g., 0.10 = wick must extend ≥10% of bar height beyond prior H/L)
MIN_WICK_PENETRATION_PCT = 0.10


def detect_single_bar_trap(df, lookback: int = DEFAULT_TRAP_LOOKBACK) -> dict:
    """
    Detect a single-bar spring (bullish reversal) or upthrust (bearish reversal)
    using the LAST CLOSED bar as the "next bar" (no-follow-through) and the
    PRIOR closed bar as the candidate trap bar.

    Pattern (bullish spring):
      candidate bar:
        - Low wicks BELOW the prior lookback-window low (failed breakdown)
        - Closes back INSIDE the prior range (back above the lookback low)
        - Wick-to-body ratio ≥ MIN_WICK_BODY_RATIO (real rejection, not noise)
      next bar:
        - Did NOT close below the candidate bar's low (no follow-through)
      → Direction: Long, weight: +0.3

    Pattern (bearish upthrust): mirror.

    Args:
      df: pandas DataFrame with 'high', 'low', 'close', 'open' (chronological)
      lookback: bars to define the prior range (excluding the candidate bar)

    Returns:
      {
        "detected":  bool,
        "type":      "spring" | "upthrust" | None,
        "direction": "Long" | "Short" | None,
        "weight":    float (±0.3 when detected, 0 otherwise),
        "wick_price": float | None (the extreme wick that defined the trap),
        "label":     str (human-readable description)
      }
    """
    out = {"detected": False, "type": None, "direction": None,
           "weight": 0.0, "wick_price": None, "label": ""}

    if df is None or len(df) < lookback + 2:
        return out

    try:
        # Use the SECOND-TO-LAST closed bar as candidate, LAST closed bar as confirmation
        candidate = df.iloc[-2]
        next_bar  = df.iloc[-1]
        # Prior range = bars before the candidate (lookback bars excluding candidate)
        prior     = df.iloc[-(lookback + 2):-2]

        if len(prior) < lookback:
            return out

        prior_high = float(prior["high"].max())
        prior_low  = float(prior["low"].min())
        c_high  = float(candidate["high"])
        c_low   = float(candidate["low"])
        c_open  = float(candidate["open"])
        c_close = float(candidate["close"])
        n_close = float(next_bar["close"])
        n_low   = float(next_bar["low"])
        n_high  = float(next_bar["high"])

        bar_range = c_high - c_low
        if bar_range <= 0:
            return out
        body = abs(c_close - c_open)
    except (KeyError, IndexError, TypeError, ValueError):
        return out

    # ── Bullish spring detection ─────────────────────────────────────────
    if c_low < prior_low:
        wick_below   = prior_low - c_low           # how far the wick extended below
        wick_pen_pct = wick_below / bar_range if bar_range > 0 else 0
        lower_wick   = (min(c_open, c_close) - c_low) if body > 0 else (c_high - c_low) / 2
        wb_ratio     = (lower_wick / body) if body > 0 else 999.0
        # Confirmation: candidate closed back ABOVE the prior low
        closed_back  = c_close > prior_low
        # Next bar: no follow-through below (didn't break candidate's low decisively)
        no_follow    = n_low >= c_low and n_close > c_low

        if (wick_pen_pct >= MIN_WICK_PENETRATION_PCT
                and wb_ratio >= MIN_WICK_BODY_RATIO
                and closed_back and no_follow):
            return {
                "detected":   True,
                "type":       "spring",
                "direction":  "Long",
                "weight":     0.3,
                "wick_price": round(c_low, 6),
                "label":      f"spring: failed breakdown @ {c_low:.6g} → close-back, no follow",
            }

    # ── Bearish upthrust detection ───────────────────────────────────────
    if c_high > prior_high:
        wick_above   = c_high - prior_high
        wick_pen_pct = wick_above / bar_range if bar_range > 0 else 0
        upper_wick   = (c_high - max(c_open, c_close)) if body > 0 else (c_high - c_low) / 2
        wb_ratio     = (upper_wick / body) if body > 0 else 999.0
        closed_back  = c_close < prior_high
        no_follow    = n_high <= c_high and n_close < c_high

        if (wick_pen_pct >= MIN_WICK_PENETRATION_PCT
                and wb_ratio >= MIN_WICK_BODY_RATIO
                and closed_back and no_follow):
            return {
                "detected":   True,
                "type":       "upthrust",
                "direction":  "Short",
                "weight":     -0.3,
                "wick_price": round(c_high, 6),
                "label":      f"upthrust: failed breakout @ {c_high:.6g} → close-back, no follow",
            }

    return out


def single_bar_trap_weight(trap: dict) -> float:
    """
    Confluence weight from a detected single-bar trap.

    Returns 0.0 when no trap, +0.3 for spring (bullish), -0.3 for upthrust.
    Pure passthrough — separated as a function so it matches the
    `_<X>_weight` pattern used in chart_confluence.py.
    """
    if not isinstance(trap, dict) or not trap.get("detected"):
        return 0.0
    return float(trap.get("weight", 0.0))


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Multi-bar Wyckoff signals (added 2026-05-24, Features 3, 4)
# ──────────────────────────────────────────────────────────────────────────────

# Settings shared by spring/upthrust/absorption detectors
SPRING_LOOKBACK            = 30  # bars to define the trading range
SPRING_RECOVERY_BARS       = 3   # candidate must recover within this many bars
SPRING_MIN_VOL_RATIO       = 1.3 # recovery bar volume must exceed avg by this much
ABSORPTION_MIN_TOUCHES     = 3   # bars near resistance/support
ABSORPTION_NEAR_PCT        = 0.5 # within 0.5% of level counts as a touch
ABSORPTION_DECLINING_VOL_PCT = 0.85  # later touches volume < this × first touch


def detect_spring(df, lookback: int = SPRING_LOOKBACK) -> dict:
    """
    Multi-bar Wyckoff Spring (Phase 2 — distinct from single-bar trap above).

    A Spring is a probe BELOW the range support that fails to follow through:
      1. Identify recent range (low of last N bars, excluding most recent 3)
      2. Find a bar that broke below the range low in the last 5 bars
      3. Confirm: price recovered back above the range low within SPRING_RECOVERY_BARS
      4. Confirm: the recovery bar(s) showed above-average volume (≥1.3× avg)

    Returns:
      {detected, type="spring", direction="Long", weight=+0.3, range_low, label}
    """
    out = {"detected": False, "type": None, "direction": None,
           "weight": 0.0, "label": ""}
    if df is None or len(df) < lookback + 5:
        return out

    try:
        # Reference range: bars from -lookback-5 to -5 (exclude last 5 for breakout window)
        ref_range = df.iloc[-(lookback + 5):-5]
        range_low = float(ref_range["low"].min())
        recent    = df.iloc[-5:]  # last 5 bars = breakout/recovery window

        # Find a bar in 'recent' that broke below range_low
        break_idx = None
        for i, low in enumerate(recent["low"].values):
            if float(low) < range_low:
                break_idx = i
                break
        if break_idx is None:
            return out

        # Confirm recovery within SPRING_RECOVERY_BARS after the break
        recovery_window = recent.iloc[break_idx:break_idx + 1 + SPRING_RECOVERY_BARS]
        if len(recovery_window) < 2:
            return out
        # At least one bar after break closed above range_low
        recovered = (recovery_window["close"].iloc[-1] > range_low)
        if not recovered:
            return out

        # Volume confirmation: at least one bar in the recovery window
        # must have above-average volume (signals real buying interest).
        avg_vol = float(ref_range["volume"].mean())
        recovery_vol_max = float(recovery_window["volume"].max())
        if avg_vol > 0 and recovery_vol_max < avg_vol * SPRING_MIN_VOL_RATIO:
            # Recovery without volume — not strong enough
            return out

        break_low = float(recovery_window["low"].min())
        return {
            "detected":   True,
            "type":       "spring",
            "direction":  "Long",
            "weight":     0.3,
            "range_low":  round(range_low, 6),
            "break_low":  round(break_low, 6),
            "label":      f"Wyckoff Spring: broke {range_low:.6g} → recovered (vol "
                          f"{recovery_vol_max/avg_vol:.1f}×)",
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return out


def detect_upthrust(df, lookback: int = SPRING_LOOKBACK) -> dict:
    """
    Multi-bar Wyckoff Upthrust — mirror of Spring at range resistance.

    A probe ABOVE range resistance that fails to follow through:
      1. Reference range = lookback bars excluding last 5
      2. Last 5 bars: find one that broke above range_high
      3. Confirm: closed back below range_high within SPRING_RECOVERY_BARS
      4. Confirm: rejection bar volume ≥ SPRING_MIN_VOL_RATIO × avg
    """
    out = {"detected": False, "type": None, "direction": None,
           "weight": 0.0, "label": ""}
    if df is None or len(df) < lookback + 5:
        return out
    try:
        ref_range = df.iloc[-(lookback + 5):-5]
        range_high = float(ref_range["high"].max())
        recent    = df.iloc[-5:]
        break_idx = None
        for i, high in enumerate(recent["high"].values):
            if float(high) > range_high:
                break_idx = i
                break
        if break_idx is None:
            return out
        recovery_window = recent.iloc[break_idx:break_idx + 1 + SPRING_RECOVERY_BARS]
        if len(recovery_window) < 2:
            return out
        rejected = (recovery_window["close"].iloc[-1] < range_high)
        if not rejected:
            return out
        avg_vol = float(ref_range["volume"].mean())
        rejection_vol_max = float(recovery_window["volume"].max())
        if avg_vol > 0 and rejection_vol_max < avg_vol * SPRING_MIN_VOL_RATIO:
            return out
        break_high = float(recovery_window["high"].max())
        return {
            "detected":   True,
            "type":       "upthrust",
            "direction":  "Short",
            "weight":     -0.3,
            "range_high": round(range_high, 6),
            "break_high": round(break_high, 6),
            "label":      f"Wyckoff Upthrust: broke {range_high:.6g} → rejected (vol "
                          f"{rejection_vol_max/avg_vol:.1f}×)",
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return out


def detect_absorption(df, lookback: int = SPRING_LOOKBACK) -> dict:
    """
    Absorption near range resistance — distinct from Upthrust because no
    breakout occurs. Rather: 3+ bars touch the resistance level without
    breaking it, with declining volume per touch (supply being absorbed).

    A bullish-bias signal: longs are buying every dip; resistance will
    eventually break upward. Direction = Long, weight +0.25 (slightly
    less than spring/upthrust which are concrete reversal triggers).

    Returns absorption_top resistance level for context.
    """
    out = {"detected": False, "type": None, "direction": None,
           "weight": 0.0, "label": ""}
    if df is None or len(df) < lookback + 3:
        return out
    try:
        ref_range = df.iloc[-(lookback + 3):]
        range_high = float(ref_range["high"].max())
        # Bars within ABSORPTION_NEAR_PCT% of range_high
        threshold = range_high * (1 - ABSORPTION_NEAR_PCT / 100)
        near_idx = [i for i, h in enumerate(ref_range["high"].values)
                    if float(h) >= threshold]
        if len(near_idx) < ABSORPTION_MIN_TOUCHES:
            return out
        # The most recent 3+ touches should have declining volume
        touch_vols = [float(ref_range["volume"].iloc[i]) for i in near_idx[-ABSORPTION_MIN_TOUCHES:]]
        if touch_vols[0] <= 0:
            return out
        # Each later touch < earlier × ABSORPTION_DECLINING_VOL_PCT
        for i in range(1, len(touch_vols)):
            if touch_vols[i] > touch_vols[i-1] * ABSORPTION_DECLINING_VOL_PCT:
                return out  # not declining enough
        return {
            "detected":      True,
            "type":          "absorption",
            "direction":     "Long",
            "weight":        0.25,
            "absorption_top": round(range_high, 6),
            "touch_count":   len(near_idx),
            "label":         f"Wyckoff Absorption: {len(near_idx)} touches @ {range_high:.6g} "
                              f"with declining volume → bullish bias",
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return out


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Swing-pivot extraction + SOT + Wave leg ratio (Features 5, 14)
# ──────────────────────────────────────────────────────────────────────────────

SWING_PIVOT_RADIUS = 3  # bar must be higher/lower than N bars on each side


def _extract_swing_pivots(df, radius: int = SWING_PIVOT_RADIUS) -> list:
    """
    Extract local-high / local-low swing pivots from a candle series.

    A bar at index i is a SWING HIGH if its high is strictly greater than
    every bar in [i-radius, i+radius] (excluding itself). Same definition
    for SWING LOW with low.

    Returns list of (index, price, type) tuples in chronological order,
    where type is "H" or "L".
    """
    if df is None or len(df) < 2 * radius + 1:
        return []
    out = []
    highs = df["high"].values
    lows  = df["low"].values
    n     = len(df)
    for i in range(radius, n - radius):
        window_h = highs[i - radius:i + radius + 1]
        window_l = lows[i - radius:i + radius + 1]
        if highs[i] == max(window_h) and (window_h == highs[i]).sum() == 1:
            out.append((i, float(highs[i]), "H"))
        elif lows[i] == min(window_l) and (window_l == lows[i]).sum() == 1:
            out.append((i, float(lows[i]), "L"))
    return out


def detect_sot(df, radius: int = SWING_PIVOT_RADIUS) -> dict:
    """
    Shortening of the Thrust (SOT) — momentum exhaustion via swing-leg decline.

    Looks at the last 3 same-direction impulse swings. If each new push
    extends less than the prior (measured as the high-of-swing-high or
    low-of-swing-low gain), momentum is exhausting → REVERSAL warning.

    For uptrends: last 3 swing highs (H1<H2<H3 with H2-H1 > H3-H2) → bearish
    For downtrends: last 3 swing lows (L1>L2>L3 with L1-L2 > L2-L3) → bullish

    Weight: ±0.2 (warning signal, not as strong as spring/upthrust).

    Returns:
      {detected, type="sot", direction, weight, label}
    """
    out = {"detected": False, "type": None, "direction": None,
           "weight": 0.0, "label": ""}
    if df is None or len(df) < 30:
        return out

    pivots = _extract_swing_pivots(df, radius)
    if len(pivots) < 6:
        return out

    # Last 3 highs in chronological order
    highs = [p for p in pivots if p[2] == "H"][-3:]
    if len(highs) == 3:
        h1, h2, h3 = highs[0][1], highs[1][1], highs[2][1]
        if h1 < h2 < h3:  # uptrend in swing highs
            push1 = h2 - h1
            push2 = h3 - h2
            if push2 > 0 and push1 > 0 and push2 < push1 * 0.7:
                return {
                    "detected": True, "type": "sot", "direction": "Short",
                    "weight": -0.2,
                    "label": f"SOT: 3 swing highs, each push smaller ({push1:.2f} → {push2:.2f}) → uptrend exhaustion",
                }

    # Last 3 lows in chronological order
    lows = [p for p in pivots if p[2] == "L"][-3:]
    if len(lows) == 3:
        l1, l2, l3 = lows[0][1], lows[1][1], lows[2][1]
        if l1 > l2 > l3:  # downtrend in swing lows
            push1 = l1 - l2
            push2 = l2 - l3
            if push2 > 0 and push1 > 0 and push2 < push1 * 0.7:
                return {
                    "detected": True, "type": "sot", "direction": "Long",
                    "weight": 0.2,
                    "label": f"SOT: 3 swing lows, each push smaller ({push1:.2f} → {push2:.2f}) → downtrend exhaustion",
                }
    return out


def detect_wave_ratio(df, radius: int = SWING_PIVOT_RADIUS) -> dict:
    """
    Schlotmann wave-leg ratio — impulse vs correction leg comparison.

    For a healthy trend, impulse legs (in trend direction) should be
    longer than correction legs (counter-trend). Ratio > 1.618 (golden
    ratio extension) = strong trend; ratio < 1.0 = trend exhausted.

    Returns a context tag (not a directional weight), surfaced in
    parts[] for Sonnet visibility. Magnitude ±0.15 — small, additive.

    Direction is inferred from the most recent swing direction.
    """
    out = {"detected": False, "type": None, "direction": None,
           "weight": 0.0, "ratio": None, "label": ""}
    if df is None or len(df) < 30:
        return out

    pivots = _extract_swing_pivots(df, radius)
    # Need at least 4 pivots (2 impulses + 1 correction or similar)
    if len(pivots) < 4:
        return out

    last4 = pivots[-4:]
    # Compute leg lengths between consecutive pivots
    legs = []
    for i in range(1, len(last4)):
        prev_idx, prev_price, prev_type = last4[i-1]
        curr_idx, curr_price, curr_type = last4[i]
        if prev_type == curr_type:  # H-H or L-L → not a clean alternation
            continue
        leg_size = abs(curr_price - prev_price)
        # Type: H→L = downward leg; L→H = upward leg
        leg_dir = "down" if (prev_type == "H" and curr_type == "L") else "up"
        legs.append((leg_size, leg_dir))

    if len(legs) < 3:
        return out

    # Classify dominant direction from majority of legs
    up_total   = sum(s for s, d in legs if d == "up")
    down_total = sum(s for s, d in legs if d == "down")
    if up_total == 0 or down_total == 0:
        return out

    # Impulse / correction depends on direction. If up_total > down_total
    # we're in an uptrend; up legs = impulse, down = correction.
    if up_total > down_total:
        ratio = up_total / down_total
        direction = "Long"
    else:
        ratio = down_total / up_total
        direction = "Short"

    # Weight: trend health
    if ratio >= 1.618:
        w = 0.15 if direction == "Long" else -0.15
        label = f"wave ratio {ratio:.2f}× — healthy {direction.lower()} trend (impulse > correction)"
    elif ratio < 1.0:
        # Inverted — reverse the sign (trend is failing)
        w = -0.15 if direction == "Long" else 0.15
        label = f"wave ratio {ratio:.2f}× — {direction.lower()} trend exhausted (correction > impulse)"
    else:
        return out   # neutral zone (1.0-1.618), no signal

    return {
        "detected": True, "type": "wave_ratio", "direction": direction,
        "weight": w, "ratio": round(ratio, 3), "label": label,
    }


def sot_weight(sot: dict) -> float:
    """Weight passthrough for SOT signal."""
    if not isinstance(sot, dict) or not sot.get("detected"):
        return 0.0
    return float(sot.get("weight", 0.0))


def wave_ratio_weight(wave: dict) -> float:
    """Weight passthrough for wave-ratio signal."""
    if not isinstance(wave, dict) or not wave.get("detected"):
        return 0.0
    return float(wave.get("weight", 0.0))


def wyckoff_multibar_weight(spring: dict, upthrust: dict, absorption: dict) -> tuple[float, str]:
    """
    Combine multi-bar Wyckoff signals into a single weight.

    Only one signal can fire at a time on the same chart (mutually exclusive
    structurally). If multiple fire (shouldn't happen but defensive), prefer
    spring > upthrust > absorption.

    Returns (weight, reason_label).
    """
    if spring and spring.get("detected"):
        return spring["weight"], spring["label"]
    if upthrust and upthrust.get("detected"):
        return upthrust["weight"], upthrust["label"]
    if absorption and absorption.get("detected"):
        return absorption["weight"], absorption["label"]
    return 0.0, ""
