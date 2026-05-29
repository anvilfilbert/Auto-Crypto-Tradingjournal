"""
VuManChu unified signal aggregator (F6).

Combines outputs of Cipher A + Cipher B + MTF EMA into a single signed score
in the range [-1.0, +1.0]. Positive = bullish bias, negative = bearish.

Used by the scanner confluence engine as an optional add-on signal, and by
the chart popup to render an "at-a-glance" verdict per symbol.

Score weights (sum of absolute values ≤ 1.0 on saturation):
   yellow_x         : ±0.40    deep-OS reversal + RSI extreme — RARE, STRONG
   blood_diamond    : ∓0.35    trend exhaustion + EMA cross down — STRONG
   long_ema (cipher A): +0.20  bullish ribbon trend start
   short_ema        : -0.20    bearish ribbon trend start
   gold_buy  (B dot): +0.20    standard oversold cross
   gold_sell        : -0.20    standard overbought cross
   bull_div (B)     : +0.15    bull divergence
   bear_div         : -0.15
   blue_triangle    : +0.10    early bullish shift
   red_cross        : -0.10    early bearish warning
   red_diamond      : -0.05    momentum exhaustion (lone)
   bull_candle      : +0.05    momentum continuation candle
   mtf_ema bias     : ±0.15    macro trend filter (long/short bias)

Only events firing on the MOST RECENT bar contribute. Older signals decay to 0.
"""
from __future__ import annotations

import pandas as pd

import chart_vmc_cipher
import chart_vmc_cipher_a
import chart_mtf_ema


# Weight map — signed contributions to the unified score
_WEIGHTS_BULL = {
    "yellow_x":      0.40,
    "long_ema":      0.20,
    "gold_buy":      0.20,
    "bull_div":      0.15,
    "blue_triangle": 0.10,
    "bull_candle":   0.05,
}
_WEIGHTS_BEAR = {
    "blood_diamond": -0.35,
    "short_ema":     -0.20,
    "gold_sell":     -0.20,
    "bear_div":      -0.15,
    "red_cross":     -0.10,
    "red_diamond":   -0.05,
}
_MTF_WEIGHT = 0.15

# F8 — WT2 zone proximity (added 2026-05-27). EVENT signals (gold_buy, etc.)
# fire only on specific bars; the zone itself is a STATE that persists across
# many bars. A long entry at WT2=+52 is materially different from WT2=-30,
# even when no event-cross fires. This catches that.
# Sign convention: bullish bias positive (mean reversion up from oversold);
# bearish bias negative (overextended on the upside).
_ZONE_DEEP_OB     = 60.0
_ZONE_ALERT_OB    = 53.0
_ZONE_ALERT_OS    = -53.0
_ZONE_DEEP_OS     = -60.0
_ZONE_WEIGHT_DEEP  = 0.10
_ZONE_WEIGHT_ALERT = 0.05

# F9 — WT2 slope at extremes (added 2026-05-27). F8 misses cases where WT2
# is just outside the alert threshold but momentum has already turned. ATOM
# audit showed wt2 = +52.6 at open with prior bars at +58 / +59 — the slope
# was sharply negative ("rolling over from high") but F8's static threshold
# at +53 didn't fire. F9 catches:
#   wt2 > +40 AND falling sharply  → "wt2_rolling_over"  (bearish)
#   wt2 < -40 AND rising sharply   → "wt2_lifting_off"   (bullish)
_SLOPE_HIGH_LEVEL = 40.0     # WT2 above this = "upper range"
_SLOPE_LOW_LEVEL  = -40.0    # WT2 below this = "lower range"
_SLOPE_DELTA      = 3.0      # Δ over lookback > 3 absolute = meaningful slope
_SLOPE_WEIGHT     = 0.10
_SLOPE_LOOKBACK   = 2

# F7 — recency decay (added 2026-05-27). Signals that fired in the LAST
# `len(_RECENCY_DECAY)` bars contribute to the score, decayed by how stale
# they are. Audit of TIA + ATOM showed both setups had a gold_sell + red_diamond
# fire 4 bars before open — the single-bar view missed that context. With
# recency 3 bars, a signal 2 bars ago still contributes ~25% of its weight.
_RECENCY_DECAY = (1.0, 0.5, 0.25)


def _recent_signal_weight(series, last_idx: int, base_weight: float,
                           decay=_RECENCY_DECAY):
    """Return (decayed_weight, bars_ago) if signal fired in the last
    `len(decay)` bars, else (0.0, None). Picks the most recent fire."""
    if series is None:
        return 0.0, None
    for offset, mult in enumerate(decay):
        i = last_idx - offset
        if i < 0:
            break
        if i < len(series) and bool(series.iloc[i]):
            return base_weight * mult, offset
    return 0.0, None


def compute_unified_signal(symbol: str, df: pd.DataFrame,
                            include_mtf: bool = True,
                            recency_bars: int = None) -> dict:
    """
    Aggregate Cipher A + Cipher B + MTF EMA into a single signal payload.

    Returns:
      {
        "symbol":           str,
        "score":            float in [-1.0, +1.0]
        "label":            "strong_long"|"long"|"neutral"|"short"|"strong_short"
        "active_signals":   {signal_name: weight}  — most-recent-bar only
        "mtf_bias":         "long"|"short"|"neutral"  (if include_mtf)
        "mtf_ema_avg":      float
        "details":          full per-component context
      }
    """
    vmc_b = chart_vmc_cipher.compute_vmc_cipher(df)
    cipher_a = chart_vmc_cipher_a.compute_cipher_a(df)

    # Look at the LAST bar only for the live verdict
    last_idx = len(df) - 1 if df is not None else -1
    if last_idx < 0:
        return _empty(symbol)

    active = {}            # decayed weights by signal (most-recent fire wins)
    active_meta = {}       # bar offset per signal (0 = current bar)
    score = 0.0

    # Choose decay window — env override possible, default to the module constant
    decay = _RECENCY_DECAY
    if recency_bars is not None and recency_bars >= 1:
        # Linear decay across the requested window
        decay = tuple(round((recency_bars - i) / recency_bars, 3)
                       for i in range(recency_bars))

    # Bullish signals (F7: decayed across last N bars)
    for name, w in _WEIGHTS_BULL.items():
        src = vmc_b if name in ("gold_buy", "bull_div") else cipher_a
        weight, bars_ago = _recent_signal_weight(src.get(name), last_idx, w, decay)
        if weight != 0:
            active[name] = round(weight, 3)
            active_meta[name] = bars_ago
            score += weight

    # Bearish signals (F7: decayed across last N bars)
    for name, w in _WEIGHTS_BEAR.items():
        src = vmc_b if name in ("gold_sell", "bear_div") else cipher_a
        weight, bars_ago = _recent_signal_weight(src.get(name), last_idx, w, decay)
        if weight != 0:
            active[name] = round(weight, 3)
            active_meta[name] = bars_ago
            score += weight

    # F8 — WT2 zone proximity (state signal, not event)
    # Reads the current WT2 value and applies a contribution based on which
    # band it sits in. Deep OB → bearish; Alert OB → mild bearish; Alert OS
    # → mild bullish; Deep OS → bullish.
    try:
        wt2_now = float(vmc_b["wt2"].iloc[last_idx])
        zone_w = 0.0
        zone_label = None
        if wt2_now >= _ZONE_DEEP_OB:
            zone_w = -_ZONE_WEIGHT_DEEP
            zone_label = "wt2_deep_overbought"
        elif wt2_now >= _ZONE_ALERT_OB:
            zone_w = -_ZONE_WEIGHT_ALERT
            zone_label = "wt2_alert_overbought"
        elif wt2_now <= _ZONE_DEEP_OS:
            zone_w = _ZONE_WEIGHT_DEEP
            zone_label = "wt2_deep_oversold"
        elif wt2_now <= _ZONE_ALERT_OS:
            zone_w = _ZONE_WEIGHT_ALERT
            zone_label = "wt2_alert_oversold"
        if zone_label:
            active[zone_label] = round(zone_w, 3)
            active_meta[zone_label] = 0       # always current-bar
            score += zone_w
    except (KeyError, IndexError, TypeError, ValueError):
        pass

    # F9 — WT2 slope at extremes (catches the ATOM-style "high but rolling
    # over" case that F8's static threshold misses).
    try:
        if last_idx >= _SLOPE_LOOKBACK:
            wt2_now  = float(vmc_b["wt2"].iloc[last_idx])
            wt2_prev = float(vmc_b["wt2"].iloc[last_idx - _SLOPE_LOOKBACK])
            wt2_delta = wt2_now - wt2_prev
            slope_label = None
            slope_w = 0.0
            # High and rolling over = bearish momentum exhaustion
            if wt2_now > _SLOPE_HIGH_LEVEL and wt2_delta < -_SLOPE_DELTA:
                slope_w = -_SLOPE_WEIGHT
                slope_label = "wt2_rolling_over"
            # Low and lifting off = bullish momentum reversal
            elif wt2_now < _SLOPE_LOW_LEVEL and wt2_delta > _SLOPE_DELTA:
                slope_w = _SLOPE_WEIGHT
                slope_label = "wt2_lifting_off"
            if slope_label:
                active[slope_label] = round(slope_w, 3)
                active_meta[slope_label] = 0
                score += slope_w
    except (KeyError, IndexError, TypeError, ValueError):
        pass

    # MTF EMA bias + slope (separate fetch — uses chart_candles)
    # 2026-05-27: slope-aware. Verdict strength modulates the weight:
    #   strong (bias + slope agree)  → full ±0.15
    #   moderate (bias, slope flat)  → ±0.10
    #   warning (bias contradicted)  → ±0.05 OR drop to 0 (treat as neutral)
    mtf = None
    mtf_bias = "neutral"
    mtf_verdict = "no_data"
    if include_mtf:
        try:
            mtf = chart_mtf_ema.compute_mtf_ema_avg(symbol)
            mtf_bias    = mtf.get("bias", "neutral")
            mtf_verdict = mtf.get("verdict", "no_data")
            strength    = mtf.get("verdict_strength", "weak")
            # Direction sign from bias
            sign = +1 if mtf_bias == "long" else (-1 if mtf_bias == "short" else 0)
            if sign != 0:
                # Modulate weight by verdict strength
                w_map = {"strong": _MTF_WEIGHT,        # 0.15
                          "moderate": _MTF_WEIGHT * 0.67,  # ~0.10
                          "warning": 0.0,               # contradicted — skip
                          "weak":    0.0}
                eff_w = w_map.get(strength, 0.0)
                if eff_w > 0:
                    key = f"mtf_{mtf_verdict}"
                    active[key] = sign * eff_w
                    score += sign * eff_w
        except Exception:
            pass

    # Saturate at ±1.0
    score = max(-1.0, min(1.0, score))
    label = _label(score)

    return {
        "symbol":         symbol,
        "score":          round(score, 3),
        "label":          label,
        "active_signals": active,
        "active_bars_ago": active_meta,    # F7: how stale each signal is
        "recency_decay":  list(decay),     # F7: the decay curve used
        "mtf_bias":       mtf_bias,
        "mtf_verdict":    mtf_verdict,
        "mtf_slope_pct":  (mtf or {}).get("slope_pct"),
        "mtf_ema_avg":    (mtf or {}).get("ema_avg"),
        "details": {
            "wt1_last":  _last(vmc_b["wt1"]),
            "wt2_last":  _last(vmc_b["wt2"]),
            "mfi_last":  _last(vmc_b["mfi"]),
            "ribbon_bullish": bool(cipher_a["ribbon_bullish"].iloc[last_idx]
                                    if last_idx < len(cipher_a["ribbon_bullish"]) else False),
            "rsi_last":  _last(cipher_a["rsi"]),
        },
    }


def _label(score: float) -> str:
    if score >=  0.50: return "strong_long"
    if score >=  0.20: return "long"
    if score <= -0.50: return "strong_short"
    if score <= -0.20: return "short"
    return "neutral"


def _last(s: pd.Series):
    if s is None or s.empty:
        return None
    v = s.iloc[-1]
    if v is None or (isinstance(v, float) and v != v):
        return None
    return float(v)


def _empty(symbol: str) -> dict:
    return {
        "symbol":         symbol,
        "score":          0.0,
        "label":          "neutral",
        "active_signals": {},
        "mtf_bias":       "neutral",
        "mtf_verdict":    "no_data",
        "mtf_slope_pct":  None,
        "mtf_ema_avg":    None,
        "details":        {},
    }
