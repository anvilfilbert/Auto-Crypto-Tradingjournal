"""
VMC Cipher A — port of the TradingView overlay indicator (VuManChu Cipher A).

Cipher A is an ON-CHART overlay (drawn on top of price candles), not a
separate oscillator pane like Cipher B. It plots:

  • 8-EMA Ribbon (5/11/15/18/21/24/28/34) coloured by stack direction
  • Signal markers ABOVE the bars:
      - Long EMA (green circle):   crossover(ema2, ema8)   — trend flips bullish
      - Short EMA (red circle):    crossover(ema8, ema2)   — trend flips bearish
      - Red Cross (red ×):         crossunder(ema1, ema2)  — early bearish warning
      - Blue Triangle (▲ blue):    crossover(ema2, ema3)   — early bullish shift
      - Red Diamond (red ◆):       WT cross-down (any zone) — momentum exhaustion
      - Blood Diamond (red ◆ big): red diamond + red cross — strong short
      - Yellow X (yellow ×):       red diamond + deep oversold + RSI extreme
                                   + MFI<-5 → textbook bullish reversal
      - Bull Candle (yellow ◆):    open>ema2, open>ema8, 2 green candles, no reds
                                   → momentum-continuation bar

Defaults match the original Pine source:
  EMA lengths: 5, 11, 15, 18, 21, 24, 28, 34
  RSI:         length=14, oversold=30, overbought=60
  RSI+MFI:     period=60, multiplier=150 (the V6 fix)
  WT:         9/12/3 (matches Cipher B)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import chart_vmc_cipher as _vmc_b


# ── Parameters (match Pine source) ─────────────────────────────────────────────
EMA_LENS = (5, 11, 15, 18, 21, 24, 28, 34)
RSI_LEN = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 60
# RSI-MFI used for the Yellow-X condition (Pine `f_rsimfi` — same formula as
# Cipher B's MFI, NOT a traditional MFI).
RSIMFI_PERIOD = 60
RSIMFI_MULT   = 150.0


def compute_cipher_a(df: pd.DataFrame) -> dict:
    """
    Compute VMC Cipher A markers on an OHLCV DataFrame.

    Returns:
      ema1..ema8     : the 8 EMA series (5..34 period)
      ribbon_bullish : bool series — True when ema8 < ema2 (stack pointing up)
      long_ema       : bool — ema2 crosses ABOVE ema8 (bullish trend start)
      short_ema      : bool — ema8 crosses ABOVE ema2 (bearish trend start)
      red_cross      : bool — ema1 crosses BELOW ema2 (early bearish)
      blue_triangle  : bool — ema2 crosses ABOVE ema3 (early bullish)
      red_diamond    : bool — WT cross-down (any zone)
      blood_diamond  : bool — red_diamond AND red_cross same bar
      yellow_x       : bool — red_diamond + deep OS + RSI extreme + MFI<-5
      bull_candle    : bool — momentum-continuation candle
      params         : dict of params used
    """
    if df is None or df.empty or len(df) < max(EMA_LENS) + 4:
        return _empty(df)

    o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]

    # 8-EMA ribbon
    emas = {}
    for i, n in enumerate(EMA_LENS, start=1):
        emas[f"ema{i}"] = c.ewm(span=n, adjust=False).mean()
    e1, e2, e3, e4, e5, e6, e7, e8 = (emas[f"ema{i}"] for i in range(1, 9))

    ribbon_bullish = (e8 < e2)

    # RSI (14)
    rsi = _rsi(c, RSI_LEN)

    # RSI-MFI (60, multiplier 150) — Pine f_rsimfi
    span_hl = (h - l).replace(0, np.nan)
    rsi_mfi_raw = ((c - o) / span_hl) * RSIMFI_MULT
    rsi_mfi_raw = rsi_mfi_raw.fillna(0.0)
    rsi_mfi = rsi_mfi_raw.rolling(RSIMFI_PERIOD, min_periods=1).mean()

    # WaveTrend (re-using Cipher B's compute for consistency)
    vmc_b = _vmc_b.compute_vmc_cipher(df)
    wt1, wt2 = vmc_b["wt1"], vmc_b["wt2"]

    # EMA cross conditions (Pine crossover/crossunder)
    long_ema      = _crossover(e2, e8)
    short_ema     = _crossover(e8, e2)
    red_cross     = _crossunder(e1, e2)
    blue_triangle = _crossover(e2, e3)

    # WT cross — Pine: cross() means either direction. Combined with
    # wtCrossDown = (wt2 - wt1 >= 0) → the cross was a DOWNWARD cross of wt1
    # through wt2.
    wt_cross = _cross_either(wt1, wt2)
    wt_cross_down = (wt2 - wt1) >= 0
    red_diamond = wt_cross & wt_cross_down

    # Blood Diamond — red_diamond AND red_cross
    blood_diamond = red_diamond & red_cross

    # Yellow X — red_diamond + wt2 in (-80, 45) + RSI in (15, 30) + rsi_mfi < -5
    yellow_x = (
        red_diamond
        & (wt2 < 45)
        & (wt2 > -80)
        & (rsi < 30)
        & (rsi > 15)
        & (rsi_mfi < -5)
    )

    # Bull Candle — open>ema2 AND open>ema8 AND prev bar green AND this bar green
    #                AND NOT red_diamond AND NOT red_cross
    bull_candle = (
        (o > e2) & (o > e8)
        & (c.shift(1) > o.shift(1))
        & (c > o)
        & (~red_diamond)
        & (~red_cross)
    )

    return {
        **emas,
        "ribbon_bullish": ribbon_bullish.fillna(False).astype(bool),
        "rsi":            rsi,
        "rsi_mfi":        rsi_mfi,
        "long_ema":       long_ema.fillna(False).astype(bool),
        "short_ema":      short_ema.fillna(False).astype(bool),
        "red_cross":      red_cross.fillna(False).astype(bool),
        "blue_triangle":  blue_triangle.fillna(False).astype(bool),
        "red_diamond":    red_diamond.fillna(False).astype(bool),
        "blood_diamond":  blood_diamond.fillna(False).astype(bool),
        "yellow_x":       yellow_x.fillna(False).astype(bool),
        "bull_candle":    bull_candle.fillna(False).astype(bool),
        "params": {
            "ema_lens": list(EMA_LENS),
            "rsi_len":  RSI_LEN,
            "rsimfi_period": RSIMFI_PERIOD,
            "rsimfi_mult":   RSIMFI_MULT,
        },
    }


# ── helpers ────────────────────────────────────────────────────────────────────

def _rsi(s: pd.Series, length: int) -> pd.Series:
    """Wilder RSI — matches Pine ta.rsi(close, 14)."""
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    # Wilder smoothing = ema with alpha = 1/n
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def _crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    """Pine ta.crossover(a, b) — True when a crosses ABOVE b."""
    return (a > b) & (a.shift(1) <= b.shift(1))


def _crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    """Pine ta.crossunder(a, b) — True when a crosses BELOW b."""
    return (a < b) & (a.shift(1) >= b.shift(1))


def _cross_either(a: pd.Series, b: pd.Series) -> pd.Series:
    """Pine ta.cross(a, b) — True on any cross direction."""
    return _crossover(a, b) | _crossunder(a, b)


def _empty(df) -> dict:
    idx = df.index if df is not None else []
    n = len(idx) if idx is not None else 0
    empty = pd.Series([np.nan] * n, index=idx, dtype=float)
    falsy = pd.Series([False] * n, index=idx, dtype=bool)
    return {
        **{f"ema{i}": empty for i in range(1, 9)},
        "ribbon_bullish": falsy,
        "rsi":            empty,
        "rsi_mfi":        empty,
        "long_ema":       falsy, "short_ema": falsy,
        "red_cross":      falsy, "blue_triangle": falsy,
        "red_diamond":    falsy, "blood_diamond": falsy,
        "yellow_x":       falsy, "bull_candle": falsy,
        "params": {
            "ema_lens": list(EMA_LENS), "rsi_len": RSI_LEN,
            "rsimfi_period": RSIMFI_PERIOD, "rsimfi_mult": RSIMFI_MULT,
        },
    }


def to_json_payload(cipher_a: dict, df: pd.DataFrame) -> dict:
    """Serialise for the interactive popup."""
    if "timestamp" in df.columns:
        ts = (df["timestamp"].astype("int64") // 1000).tolist()
    else:
        ts = (df.index.astype("int64") // 1_000_000_000).tolist()

    def _ser(s):
        return [
            {"time": int(t), "value": None if (v is None or (isinstance(v, float) and v != v)) else float(v)}
            for t, v in zip(ts, s.tolist())
        ]

    def _dots(mask):
        return [int(t) for t, m in zip(ts, mask.tolist()) if bool(m)]

    return {
        "ema1": _ser(cipher_a["ema1"]), "ema2": _ser(cipher_a["ema2"]),
        "ema3": _ser(cipher_a["ema3"]), "ema4": _ser(cipher_a["ema4"]),
        "ema5": _ser(cipher_a["ema5"]), "ema6": _ser(cipher_a["ema6"]),
        "ema7": _ser(cipher_a["ema7"]), "ema8": _ser(cipher_a["ema8"]),
        "long_ema_ts":       _dots(cipher_a["long_ema"]),
        "short_ema_ts":      _dots(cipher_a["short_ema"]),
        "red_cross_ts":      _dots(cipher_a["red_cross"]),
        "blue_triangle_ts":  _dots(cipher_a["blue_triangle"]),
        "red_diamond_ts":    _dots(cipher_a["red_diamond"]),
        "blood_diamond_ts":  _dots(cipher_a["blood_diamond"]),
        "yellow_x_ts":       _dots(cipher_a["yellow_x"]),
        "bull_candle_ts":    _dots(cipher_a["bull_candle"]),
        "params":            cipher_a["params"],
    }
