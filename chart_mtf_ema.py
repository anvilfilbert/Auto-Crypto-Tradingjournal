"""
Multi-Timeframe EMA Average — port of the Pine source + slope enhancement.

The original Pine just averages 200-EMA across 1H/4H/12H/1D/3D/1W and plots
a single line. Useful as a macro filter, but it tells you only WHERE the
average is — not where it's GOING.

Slope enhancement (2026-05-27): for each TF, also computes how much that
TF's EMA has moved over its own "1 week equivalent" window, then averages.
A 1H EMA's "1 week window" = 168 bars; a 1W EMA's = 1 bar. This makes the
slope comparable across timescales (each TF measures the same calendar
period).

Combined with the price-vs-avg bias, slope gives a stronger directional
read:
  long + rising  → strong bullish stack
  long + flat    → uptrend losing momentum
  long + falling → potential reversal (warning)
  short + falling → strong bearish stack
  short + rising  → potential reversal up
"""
from __future__ import annotations

import pandas as pd

import chart_candles


DEFAULT_LENGTH = 200
DEFAULT_TFS    = ("1H", "4H", "12H", "1D", "3D", "1W")

# Bars-back per TF to give each ~7 calendar days of slope measurement.
# 1H = 168 bars (7×24), 4H = 42 (7×6), 12H = 14 (7×2), 1D = 7, 3D = 2, 1W = 1
_SLOPE_LOOKBACK_BARS = {"1H": 168, "4H": 42, "12H": 14,
                         "1D": 7,  "3D": 2,  "1W": 1}

# Slope thresholds (% change of EMA over the lookback window)
_SLOPE_FLAT_THRESHOLD = 0.30   # ±0.30% over 7 days = flat


def compute_mtf_ema_avg(
    symbol: str,
    length: int = DEFAULT_LENGTH,
    timeframes: tuple = DEFAULT_TFS,
) -> dict:
    """
    Returns:
      {
        symbol, length,
        ema_avg            : float — current cross-TF average
        components         : {tf: ema_value}
        last_close, bias   : "long"|"short"|"neutral" (price-vs-avg)
        slope_pct          : float — average % change of EMA across TFs over ~7d
        slope_label        : "rising"|"flat"|"falling"
        slope_per_tf       : {tf: pct_change}
        verdict            : combined bias × slope label
        verdict_strength   : "strong"|"moderate"|"weak"|"warning"
        missing_tfs        : list
      }
    """
    components: dict = {}
    slopes: dict = {}
    missing: list = []
    last_close = None

    for tf in timeframes:
        lb_bars = _SLOPE_LOOKBACK_BARS.get(tf, 7)
        # Pull enough bars to fully warm up the EMA(200) AND have lookback room
        need = length + lb_bars + 20
        try:
            df = chart_candles.get_candles(symbol, tf, limit=need)
            if df is None or df.empty or len(df) < length:
                missing.append(tf)
                continue
            ema = df["close"].ewm(span=length, adjust=False).mean()
            ema_now = float(ema.iloc[-1])
            components[tf] = ema_now
            # Slope = % change over lookback bars (clamped to series length)
            lb = min(lb_bars, len(ema) - 1)
            if lb >= 1:
                ema_then = float(ema.iloc[-1 - lb])
                if ema_then > 0:
                    slopes[tf] = (ema_now - ema_then) / ema_then * 100.0
            if tf == "1H":
                last_close = float(df["close"].iloc[-1])
        except Exception:
            missing.append(tf)
            continue

    if not components:
        return _empty(symbol, length, list(timeframes))

    ema_avg = sum(components.values()) / len(components)
    slope_pct = (sum(slopes.values()) / len(slopes)) if slopes else 0.0

    # Fallback for last_close if 1H missing
    if last_close is None:
        try:
            df0 = chart_candles.get_candles(symbol, "1D", limit=5)
            if df0 is not None and not df0.empty:
                last_close = float(df0["close"].iloc[-1])
        except Exception:
            pass

    # Price-vs-avg bias (±0.5% buffer to avoid whipsaws)
    if last_close is None:
        bias = "neutral"
    elif last_close > ema_avg * 1.005:
        bias = "long"
    elif last_close < ema_avg * 0.995:
        bias = "short"
    else:
        bias = "neutral"

    # Slope classification
    if slope_pct >  _SLOPE_FLAT_THRESHOLD:
        slope_label = "rising"
    elif slope_pct < -_SLOPE_FLAT_THRESHOLD:
        slope_label = "falling"
    else:
        slope_label = "flat"

    # Combined verdict — bias × slope cross
    verdict, strength = _combined_verdict(bias, slope_label)

    return {
        "symbol":           symbol,
        "length":           length,
        "ema_avg":          ema_avg,
        "components":       components,
        "last_close":       last_close,
        "bias":             bias,
        "slope_pct":        round(slope_pct, 3),
        "slope_label":      slope_label,
        "slope_per_tf":     {tf: round(v, 3) for tf, v in slopes.items()},
        "verdict":          verdict,
        "verdict_strength": strength,
        "missing_tfs":      missing,
    }


def _combined_verdict(bias: str, slope: str) -> tuple[str, str]:
    """
    Cross-tabulate bias × slope into a single verdict label + strength.

      bias \\ slope:  rising      flat        falling
      long           strong_long aging_long  warning_long_reversal
      neutral        rising_chop chop        falling_chop
      short          warning_short_reversal aging_short strong_short
    """
    if bias == "long":
        if slope == "rising":  return "trend_up_confirmed",   "strong"
        if slope == "flat":    return "trend_up_aging",       "moderate"
        return                       "long_reversal_warning",  "warning"
    if bias == "short":
        if slope == "falling": return "trend_down_confirmed", "strong"
        if slope == "flat":    return "trend_down_aging",     "moderate"
        return                       "short_reversal_warning", "warning"
    # neutral bias
    if slope == "rising":     return "chop_with_uplift",      "weak"
    if slope == "falling":    return "chop_with_downdrift",   "weak"
    return                            "chop",                  "weak"


def _empty(symbol: str, length: int, missing: list) -> dict:
    return {
        "symbol":           symbol,
        "length":           length,
        "ema_avg":          None,
        "components":       {},
        "last_close":       None,
        "bias":             "neutral",
        "slope_pct":        0.0,
        "slope_label":      "flat",
        "slope_per_tf":     {},
        "verdict":          "no_data",
        "verdict_strength": "weak",
        "missing_tfs":      missing,
    }
