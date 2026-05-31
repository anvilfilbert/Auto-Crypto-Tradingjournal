"""
chart_vwap.py — Volume Weighted Average Price (true price-volume VWAP).

Distinct from chart_vmc_cipher's "VWAP" line, which is `WT1 - WT2` (a
momentum oscillator named VWAP for historical reasons). This module
implements the real institutional VWAP that:

  vwap_t = Σ(typical_price_i × volume_i) / Σ(volume_i), i over session

  where typical_price = (high + low + close) / 3

Session-anchored: VWAP resets at the start of each UTC day. For crypto
this is the convention since markets are 24/7.

Standard-deviation bands (1σ, 2σ) bracket the VWAP and act as natural
take-profit / mean-reversion zones — they widen as intraday variance
grows. Institutional desks use the 2σ band as a "stretched" mark, the
1σ band as a fade target.

Public functions:
  - compute_vwap(df, anchored='session', bands=(1, 2))
  - vwap_label(distance_pct, position) -> short human-readable tag
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_vwap_series(df: pd.DataFrame,
                         anchored: str = "session",
                         bands: tuple = (1, 2)) -> list[dict] | None:
    """Per-bar VWAP + bands series for chart overlays.

    Returns a list of dicts ordered chronologically:
      [
        {"time": int, "vwap": float,
         "upper_1": float, "lower_1": float,
         "upper_2": float, "lower_2": float},
        ...
      ]

    `time` is unix seconds (LightweightCharts native format).
    Session-anchored — VWAP and bands restart at UTC midnight.
    Returns None on insufficient data or missing columns.
    """
    required = {"high", "low", "close", "volume"}
    if df is None or df.empty or not required.issubset(df.columns):
        return None
    if len(df) < 3:
        return None

    # Accept either the chart_candles convention (RangeIndex + 'timestamp' col
    # in ms) or a DatetimeIndex. Both convert to unix SECONDS + UTC-day for
    # session anchoring.
    if "timestamp" in df.columns:
        ts_ms = df["timestamp"].astype("int64").values
        ts_s  = (ts_ms // 1000).astype("int64")
        idx   = pd.to_datetime(ts_ms, unit="ms", utc=True)
    elif isinstance(df.index, pd.DatetimeIndex):
        idx = df.index
        if idx.tz is not None:
            idx = idx.tz_convert("UTC")
        else:
            idx = idx.tz_localize("UTC")
        ts_s = (idx.asi8 // 1_000_000_000).astype("int64")
    else:
        return None

    typ = ((df["high"] + df["low"] + df["close"]) / 3.0).values
    vol = df["volume"].astype(float).values
    session_id = idx.normalize()

    series = []
    cum_pv = 0.0
    cum_v = 0.0
    cum_pv2 = 0.0   # for running variance
    prev_session = None

    for i, sid in enumerate(session_id):
        if sid != prev_session:
            cum_pv = cum_pv2 = cum_v = 0.0
            prev_session = sid

        v = float(vol[i])
        p = float(typ[i])
        cum_pv += p * v
        cum_v += v
        cum_pv2 += v * p * p

        if cum_v <= 0:
            continue

        vwap = cum_pv / cum_v
        variance = max(0.0, (cum_pv2 / cum_v) - vwap * vwap)
        stdev = variance ** 0.5

        row = {
            "time": int(ts_s[i]),
            "vwap": round(vwap, 6),
        }
        for mult in bands:
            row[f"upper_{mult}"] = round(vwap + mult * stdev, 6)
            row[f"lower_{mult}"] = round(vwap - mult * stdev, 6)
        series.append(row)

    return series if series else None


def compute_vwap(df: pd.DataFrame,
                  anchored: str = "session",
                  bands: tuple = (1, 2)) -> dict | None:
    """Compute session-anchored VWAP + std-dev bands.

    Args:
      df: pandas DataFrame with 'high', 'low', 'close', 'volume' columns,
          chronological. Index must be DatetimeIndex for session anchoring.
      anchored: "session" → reset at UTC midnight (default).
                "rolling" → no reset, treats df as one window.
      bands: tuple of std-dev multiples for the bands (default (1, 2)).
             Each multiple X yields upper_X and lower_X keys.

    Returns dict or None:
      {
        "vwap": float,
        "upper_1": float, "lower_1": float,
        "upper_2": float, "lower_2": float,   # only present if 2 in bands
        "distance_pct": float,    # (close - vwap) / vwap × 100, signed
        "position": "above_2sigma" | "above_1sigma" | "above_vwap" |
                    "at_vwap" | "below_vwap" | "below_1sigma" | "below_2sigma",
        "session_bars": int,      # bars in current session contributing
      }

    Returns None on insufficient data, missing columns, or zero volume.
    """
    required = {"high", "low", "close", "volume"}
    if df is None or df.empty or not required.issubset(df.columns):
        return None
    if len(df) < 3:
        return None

    typ = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float)

    if anchored == "session":
        if not isinstance(df.index, pd.DatetimeIndex):
            # Without a datetime index we can't anchor — fall back to rolling
            anchored = "rolling"

    if anchored == "session":
        # Convert to UTC if tz-aware, else assume already UTC
        idx = df.index
        if idx.tz is not None:
            idx = idx.tz_convert("UTC")
        session_id = idx.normalize()
        df_view = pd.DataFrame({
            "typ": typ.values,
            "vol": vol.values,
            "session": session_id,
        }, index=idx)
        # Limit to the most recent session
        current_session = session_id[-1]
        mask = session_id == current_session
        if not mask.any():
            return None
        typ_s = df_view.loc[mask, "typ"].values
        vol_s = df_view.loc[mask, "vol"].values
    else:
        typ_s = typ.values
        vol_s = vol.values

    total_vol = float(vol_s.sum())
    if total_vol <= 0:
        return None

    vwap = float(np.sum(typ_s * vol_s) / total_vol)

    # Volume-weighted variance for the band calculation
    variance = float(np.sum(vol_s * (typ_s - vwap) ** 2) / total_vol)
    stdev = float(np.sqrt(variance))

    last_close = float(df["close"].iloc[-1])
    distance_pct = ((last_close - vwap) / vwap * 100.0) if vwap > 0 else 0.0

    result = {
        "vwap":         round(vwap, 6),
        "distance_pct": round(distance_pct, 3),
        "session_bars": int(len(typ_s)),
    }

    upper_1 = lower_1 = upper_2 = lower_2 = None
    for mult in bands:
        upper = vwap + mult * stdev
        lower = vwap - mult * stdev
        result[f"upper_{mult}"] = round(upper, 6)
        result[f"lower_{mult}"] = round(lower, 6)
        if mult == 1:
            upper_1, lower_1 = upper, lower
        if mult == 2:
            upper_2, lower_2 = upper, lower

    # Position classification (relative to bands)
    if upper_2 is not None and last_close >= upper_2:
        position = "above_2sigma"
    elif upper_1 is not None and last_close >= upper_1:
        position = "above_1sigma"
    elif last_close > vwap:
        position = "above_vwap"
    elif lower_2 is not None and last_close <= lower_2:
        position = "below_2sigma"
    elif lower_1 is not None and last_close <= lower_1:
        position = "below_1sigma"
    elif last_close < vwap:
        position = "below_vwap"
    else:
        position = "at_vwap"

    result["position"] = position
    return result


def vwap_label(distance_pct: float, position: str) -> str:
    """Short human-readable tag for prompt insertion.

    Examples:
      "VWAP -0.4% (below 1σ)" — fade signal
      "VWAP +1.2% (above 2σ)" — stretched / reversion target
      "VWAP +0.1% (at VWAP)" — neutral magnet
    """
    pretty = {
        "above_2sigma":  "above 2σ",
        "above_1sigma":  "above 1σ",
        "above_vwap":    "above VWAP",
        "at_vwap":       "at VWAP",
        "below_vwap":    "below VWAP",
        "below_1sigma":  "below 1σ",
        "below_2sigma":  "below 2σ",
    }.get(position, position)
    sign = "+" if distance_pct >= 0 else ""
    return f"VWAP {sign}{distance_pct:.2f}% ({pretty})"
