"""
VMC Cipher B — port of the popular TradingView indicator (Falcon / VuManChu).

Two layers stacked in one oscillator pane:
  • WaveTrend dual-line + area fill (the dominant blue wave)
  • Money Flow money-pressure histogram (the yellow/red ribbon)

Signals plotted on top:
  • Green dot  — WT bullish cross while in oversold (gold_buy)
  • Red dot    — WT bearish cross while in overbought (gold_sell)

Default params match the TradingView original (9 / 12 / 3 / 60 / 2.5).
Kept separate from `chart_indicators.compute_wavetrend` so we don't disturb
the confluence engine that uses 10/21/4 + RSI-based MFI.

This module is the SINGLE SOURCE OF TRUTH for VMC compute. Both the static
PNG renderer (agent_chart_draw.py) and the interactive popup (chart.html
via /api/vmc-cipher/<symbol>) consume the output of `compute_vmc_cipher()`.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Optional


# ── Default parameters (match VuManChu B Divergences V6 Pine source) ──────────
WT_CHANNEL_LEN = 9      # n1: hlc3 EMA period
WT_AVERAGE_LEN = 12     # n2: WT1 EMA period
WT_MA_LEN      = 3      # WT2 SMA of WT1
MFI_LENGTH     = 60     # Money Flow SMA period
MFI_MULT       = 150.0  # Money Flow scaling multiplier (Pine source: 150!)
MFI_OFFSET     = 2.5    # Money Flow Y-offset (subtracted after smoothing)
OVERBOUGHT     = 60.0
OVERSOLD       = -60.0
OB_ALERT       = 53.0   # standard cross dot zone (Pine uses ±53)
OS_ALERT       = -53.0
PIVOT_LEFT     = 2      # divergence pivot lookback (Pine: 2, 2)
PIVOT_RIGHT    = 2


def compute_vmc_cipher(
    df: pd.DataFrame,
    wt_n1: int = WT_CHANNEL_LEN,
    wt_n2: int = WT_AVERAGE_LEN,
    wt_ma: int = WT_MA_LEN,
    mfi_length: int = MFI_LENGTH,
    mfi_mult: float = MFI_MULT,
    mfi_offset: float = MFI_OFFSET,
) -> dict:
    """
    Compute VMC Cipher B on an OHLCV DataFrame — straight port of the Pine V6
    source (`VuManChu B Divergences - Optimized v6`).

    Returns:
      wt1, wt2, mfi, vwap (wt1 - wt2), gold_buy, gold_sell, bull_div, bear_div,
      cross_bull, cross_bear, params
    """
    if df is None or df.empty or len(df) < max(wt_n1, wt_n2, mfi_length) + PIVOT_LEFT + PIVOT_RIGHT + 4:
        return _empty(df)

    o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]
    hlc3 = (h + l + c) / 3.0

    # ── WaveTrend (the blue area) — Pine f_wavetrend ───────────────────────────
    esa = hlc3.ewm(span=wt_n1, adjust=False).mean()
    de  = (hlc3 - esa).abs().ewm(span=wt_n1, adjust=False).mean()
    ci  = (hlc3 - esa) / (0.015 * de.replace(0, np.nan))
    ci  = ci.fillna(0.0)
    wt1 = ci.ewm(span=wt_n2, adjust=False).mean()
    wt2 = wt1.rolling(wt_ma, min_periods=1).mean()

    # ── VWAP series (wt1 - wt2) — Pine: `wtVwap` yellow area ───────────────────
    vwap = wt1 - wt2

    # ── Money Flow — corrected to match Pine source ────────────────────────────
    # Pine: rawMfi = ((close-open)/(high-low)) * 150
    #       rsiMFI = sma(rawMfi, 60) - 2.5
    span_hl = (h - l).replace(0, np.nan)
    raw_mfi = ((c - o) / span_hl) * mfi_mult
    raw_mfi = raw_mfi.fillna(0.0)
    mfi = raw_mfi.rolling(mfi_length, min_periods=1).mean() - mfi_offset

    # ── Crosses + gold-dot signals ─────────────────────────────────────────────
    cross_bull = (wt1 > wt2) & (wt1.shift(1) <= wt2.shift(1))
    cross_bear = (wt1 < wt2) & (wt1.shift(1) >= wt2.shift(1))
    # Pine source uses ±53 (OB_ALERT) for the buy/sell dots, not ±60
    gold_buy   = cross_bull & (wt2 < OS_ALERT)
    gold_sell  = cross_bear & (wt2 > OB_ALERT)

    # ── Divergences (pivot-based) — direct port of Pine V6 lines 46-55 ─────────
    # ta.pivothigh(wt2, 2, 2) returns the pivot value at the pivot bar; only
    # confirmed after 2 right-side bars. We compute pivots aligned to the
    # pivot bar's index, then test divergence against the previous pivot.
    pivot_high = _pivot_high(wt2, PIVOT_LEFT, PIVOT_RIGHT)
    pivot_low  = _pivot_low(wt2,  PIVOT_LEFT, PIVOT_RIGHT)
    bull_div, bear_div = _detect_divergences(wt2, h, l, pivot_high, pivot_low)

    return {
        "wt1":        wt1,
        "wt2":        wt2,
        "vwap":       vwap,
        "mfi":        mfi,
        "gold_buy":   gold_buy.fillna(False).astype(bool),
        "gold_sell":  gold_sell.fillna(False).astype(bool),
        "bull_div":   bull_div.fillna(False).astype(bool),
        "bear_div":   bear_div.fillna(False).astype(bool),
        "cross_bull": cross_bull.fillna(False).astype(bool),
        "cross_bear": cross_bear.fillna(False).astype(bool),
        "params": {
            "wt_n1": wt_n1, "wt_n2": wt_n2, "wt_ma": wt_ma,
            "mfi_length": mfi_length, "mfi_mult": mfi_mult,
            "mfi_offset": mfi_offset,
            "ob": OVERBOUGHT, "os": OVERSOLD,
            "ob_alert": OB_ALERT, "os_alert": OS_ALERT,
        },
    }


# ── Divergence helpers ─────────────────────────────────────────────────────────

def _pivot_high(s: pd.Series, left: int, right: int) -> pd.Series:
    """Pine ta.pivothigh(s, left, right) — returns value at pivot bar position."""
    n = len(s)
    out = pd.Series(np.nan, index=s.index, dtype=float)
    arr = s.to_numpy()
    for i in range(left, n - right):
        v = arr[i]
        if np.isnan(v): continue
        if all(v > arr[j] for j in range(i - left, i)) \
           and all(v > arr[j] for j in range(i + 1, i + right + 1)):
            out.iloc[i] = v
    return out


def _pivot_low(s: pd.Series, left: int, right: int) -> pd.Series:
    """Pine ta.pivotlow(s, left, right)."""
    n = len(s)
    out = pd.Series(np.nan, index=s.index, dtype=float)
    arr = s.to_numpy()
    for i in range(left, n - right):
        v = arr[i]
        if np.isnan(v): continue
        if all(v < arr[j] for j in range(i - left, i)) \
           and all(v < arr[j] for j in range(i + 1, i + right + 1)):
            out.iloc[i] = v
    return out


def _detect_divergences(wt2: pd.Series, high: pd.Series, low: pd.Series,
                         pivot_high: pd.Series, pivot_low: pd.Series
                         ) -> tuple[pd.Series, pd.Series]:
    """
    Port of Pine V6 lines 46-55:
      wtBearDiv: not na(fTop) AND high[2] > hPrice AND wt2[2] < hPrev
      wtBullDiv: not na(fBot) AND low[2]  < lPrice AND wt2[2] > lPrev

    Pine signals the divergence on bar `i` even though the pivot was at `i-2`
    (because that's when right-side confirmation completes). We match that
    by shifting pivot detection forward 2 bars.

    Returns (bull_div, bear_div) — boolean Series aligned to wt2.index.
    """
    right = PIVOT_RIGHT  # Pine right-side lookback used during pivot detection
    # `valuewhen(not na(fTop), wt2[2], 0)` in Pine = the last non-NA pivot's
    # wt2-2bars-ago value. We approximate by treating the pivot itself
    # (already at pivot bar's position) as the value to compare against.
    # Shift by `right` to mirror Pine's "confirmed N bars later" behaviour.
    confirmed_high = pivot_high.shift(right)   # last confirmed pivot, value-at-pivot
    confirmed_low  = pivot_low.shift(right)

    # Previous confirmed pivot (the one BEFORE the current confirmed one)
    prev_high_value = confirmed_high.ffill().shift(1)
    prev_low_value  = confirmed_low.ffill().shift(1)
    # Price (high/low) at the previous confirmed pivot bar — we approximate by
    # forward-filling the high/low aligned with the pivot detection.
    high_at_pivot = high.where(pivot_high.notna()).shift(right)
    low_at_pivot  = low.where(pivot_low.notna()).shift(right)
    prev_high_price = high_at_pivot.ffill().shift(1)
    prev_low_price  = low_at_pivot.ffill().shift(1)

    # Bear div: current pivot has higher HIGH but LOWER wt2 than previous pivot
    bear_div = (
        confirmed_high.notna()
        & (high.shift(right) > prev_high_price)
        & (confirmed_high < prev_high_value)
    )
    # Bull div: current pivot has lower LOW but HIGHER wt2 than previous
    bull_div = (
        confirmed_low.notna()
        & (low.shift(right) < prev_low_price)
        & (confirmed_low > prev_low_value)
    )
    return bull_div, bear_div


def to_json_payload(vmc: dict, df: pd.DataFrame) -> dict:
    """Serialise compute_vmc_cipher output for the interactive popup.
    Uses unix-second timestamps so LightweightCharts can consume it directly."""
    # chart_candles returns df with a RangeIndex and a 'timestamp' column
    # in milliseconds. Fall back to DatetimeIndex if the column isn't there.
    if "timestamp" in df.columns:
        ts = (df["timestamp"].astype("int64") // 1000).tolist()
    else:
        ts = (df.index.astype("int64") // 1_000_000_000).tolist()

    def _series(s):
        # Replace NaN with None so JSON serialises cleanly
        out = []
        for t, v in zip(ts, s.tolist()):
            if v is None or (isinstance(v, float) and (v != v)):  # NaN check
                out.append({"time": int(t), "value": None})
            else:
                out.append({"time": int(t), "value": float(v)})
        return out

    def _dots(mask):
        out = []
        for t, m in zip(ts, mask.tolist()):
            if bool(m):
                out.append(int(t))
        return out

    return {
        "wt1":        _series(vmc["wt1"]),
        "wt2":        _series(vmc["wt2"]),
        "vwap":       _series(vmc["vwap"]),
        "mfi":        _series(vmc["mfi"]),
        "gold_buy_ts":  _dots(vmc["gold_buy"]),
        "gold_sell_ts": _dots(vmc["gold_sell"]),
        "bull_div_ts":  _dots(vmc["bull_div"]),
        "bear_div_ts":  _dots(vmc["bear_div"]),
        "params":     vmc["params"],
    }


def _empty(df) -> dict:
    """Return an empty-but-shaped result when input data is too short."""
    idx = df.index if df is not None else []
    n = len(idx) if idx is not None else 0
    empty = pd.Series([np.nan] * n, index=idx, dtype=float)
    falsy = pd.Series([False] * n, index=idx, dtype=bool)
    return {
        "wt1": empty, "wt2": empty, "vwap": empty, "mfi": empty,
        "gold_buy": falsy, "gold_sell": falsy,
        "bull_div": falsy, "bear_div": falsy,
        "cross_bull": falsy, "cross_bear": falsy,
        "params": {
            "wt_n1": WT_CHANNEL_LEN, "wt_n2": WT_AVERAGE_LEN,
            "wt_ma": WT_MA_LEN, "mfi_length": MFI_LENGTH,
            "mfi_mult": MFI_MULT, "mfi_offset": MFI_OFFSET,
            "ob": OVERBOUGHT, "os": OVERSOLD,
            "ob_alert": OB_ALERT, "os_alert": OS_ALERT,
        },
    }
