"""
chart_volume_profile.py — Volume Profile (POC, Value Area, HVN/LVN).

Different from chart_sr.py's touch-counted S/R. Volume Profile shows
WHERE price spent the most volume, not just where it visited. The two
often disagree — a level can be touched 5 times in quick wicks (high
S/R "touches", low Volume Profile activity) while a different level
acted as accumulation zone (modest touches, massive volume).

Computed by:
  1. Bucketing the price range into `bins` equal-width zones.
  2. Distributing each candle's volume across the bins its high-low
     range covers (proportional to overlap).
  3. POC = bin with the highest cumulative volume.
  4. Value Area = contiguous bins around POC capturing `va_pct` of total
     volume (default 70% — Steidlmayer's original definition).
  5. HVN = bins where volume ≥ 150% of average bin volume.
  6. LVN = bins where volume ≤ 50% of average bin volume.

HVN levels act as magnets where price tends to consolidate.
LVN levels act as low-friction zones where price moves through quickly.
VAH (Value Area High) and VAL (Value Area Low) are the boundaries the
auction process accepted as "fair" — outside them, price is in discovery.

Public function:
  - compute_volume_profile(df, bins=24, va_pct=0.70, lookback_bars=120)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_volume_profile(df: pd.DataFrame,
                            bins: int = 24,
                            va_pct: float = 0.70,
                            lookback_bars: int = 120) -> dict | None:
    """Compute Volume Profile over the last `lookback_bars` candles.

    Args:
      df: OHLCV DataFrame with high, low, close, volume.
      bins: number of price bins for the profile (default 24).
      va_pct: fraction of total volume that defines Value Area (default 0.70).
      lookback_bars: how many recent bars to include (default 120).

    Returns:
      {
        "poc":   float,     # Point of Control — bin midpoint with max volume
        "vah":   float,     # Value Area High
        "val":   float,     # Value Area Low
        "hvn":   [float],   # midpoints of high-volume nodes (>= 1.5× avg)
        "lvn":   [float],   # midpoints of low-volume nodes  (<= 0.5× avg)
        "at_poc":           "above" | "below" | "at",
        "distance_to_poc_pct": float,    # signed (close - poc)/poc × 100
        "in_value_area":     bool,
        "bins_used":         int,
        "lookback_bars":     int,
      }
      or None on insufficient data.
    """
    required = {"high", "low", "close", "volume"}
    if df is None or df.empty or not required.issubset(df.columns):
        return None
    if len(df) < 20:
        return None

    # Trim to lookback
    df_view = df.iloc[-lookback_bars:] if len(df) > lookback_bars else df

    low_min = float(df_view["low"].min())
    high_max = float(df_view["high"].max())
    if high_max <= low_min:
        return None

    # Bin edges (uniform)
    edges = np.linspace(low_min, high_max, bins + 1)
    bin_volumes = np.zeros(bins, dtype=float)

    highs = df_view["high"].astype(float).values
    lows = df_view["low"].astype(float).values
    vols = df_view["volume"].astype(float).values

    # Distribute each candle's volume across bins it overlaps, proportional
    # to overlap with each bin's range.
    for h, l, v in zip(highs, lows, vols):
        if v <= 0 or h <= l:
            continue
        candle_range = h - l
        # Indices of bins this candle touches
        first_bin = np.searchsorted(edges, l, side="right") - 1
        last_bin = np.searchsorted(edges, h, side="right") - 1
        first_bin = max(0, min(first_bin, bins - 1))
        last_bin = max(0, min(last_bin, bins - 1))
        if first_bin == last_bin:
            bin_volumes[first_bin] += v
            continue
        for i in range(first_bin, last_bin + 1):
            bin_lo = edges[i]
            bin_hi = edges[i + 1]
            overlap = max(0.0, min(h, bin_hi) - max(l, bin_lo))
            if overlap > 0:
                bin_volumes[i] += v * (overlap / candle_range)

    total_vol = float(bin_volumes.sum())
    if total_vol <= 0:
        return None

    # POC bin
    poc_idx = int(np.argmax(bin_volumes))
    poc = float((edges[poc_idx] + edges[poc_idx + 1]) / 2)

    # Value Area — expand around POC until cumulative volume ≥ va_pct
    target = va_pct * total_vol
    cum = float(bin_volumes[poc_idx])
    lo_idx = hi_idx = poc_idx
    while cum < target and (lo_idx > 0 or hi_idx < bins - 1):
        # Compare neighbour volumes (use 2-bin lookahead when possible)
        left_vol = bin_volumes[lo_idx - 1] if lo_idx > 0 else -1.0
        right_vol = bin_volumes[hi_idx + 1] if hi_idx < bins - 1 else -1.0
        if right_vol >= left_vol and hi_idx < bins - 1:
            hi_idx += 1
            cum += float(bin_volumes[hi_idx])
        elif lo_idx > 0:
            lo_idx -= 1
            cum += float(bin_volumes[lo_idx])
        else:
            break

    vah = float(edges[hi_idx + 1])
    val = float(edges[lo_idx])

    # HVN / LVN classification (bin midpoints)
    avg_vol = float(bin_volumes.mean())
    hvn = []
    lvn = []
    for i in range(bins):
        midpoint = float((edges[i] + edges[i + 1]) / 2)
        if bin_volumes[i] >= 1.5 * avg_vol:
            hvn.append(round(midpoint, 6))
        elif bin_volumes[i] <= 0.5 * avg_vol:
            lvn.append(round(midpoint, 6))

    last_close = float(df_view["close"].iloc[-1])
    distance_pct = ((last_close - poc) / poc * 100.0) if poc > 0 else 0.0
    if abs(distance_pct) < 0.05:
        at_poc = "at"
    elif distance_pct > 0:
        at_poc = "above"
    else:
        at_poc = "below"

    return {
        "poc":   round(poc, 6),
        "vah":   round(vah, 6),
        "val":   round(val, 6),
        "hvn":   hvn,
        "lvn":   lvn,
        "at_poc": at_poc,
        "distance_to_poc_pct": round(distance_pct, 3),
        "in_value_area": val <= last_close <= vah,
        "bins_used":     bins,
        "lookback_bars": int(len(df_view)),
    }


def volume_profile_label(vp: dict) -> str:
    """Compact human-readable tag for prompt insertion."""
    if not vp:
        return ""
    poc = vp.get("poc", 0)
    dist = vp.get("distance_to_poc_pct", 0)
    at = vp.get("at_poc", "")
    in_va = "in VA" if vp.get("in_value_area") else "out of VA"
    sign = "+" if dist >= 0 else ""
    return (f"POC {poc:.4f} ({sign}{dist:.2f}% {at}, "
            f"{in_va}; VAH {vp.get('vah', 0):.4f} VAL {vp.get('val', 0):.4f})")
