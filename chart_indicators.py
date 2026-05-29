"""
chart_indicators.py — Pure indicator computation functions.

Accepts a DataFrame (columns: open, high, low, close, volume) and returns
structured dicts. No API calls, no caching, no side effects.

All functions degrade gracefully when < 30 bars are available.

Public API (stable — tested by tests/test_chart_indicators.py):
  compute_rsi, compute_ema_alignment, compute_macd, compute_adx,
  compute_prompt_text

Extended API (full suite used by chart_context.compute_indicators):
  compute_wavetrend, compute_stochrsi, compute_bollinger, compute_atr,
  compute_volume, compute_recent_candles, compute_cvd, compute_all_indicators
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta


# ── Stable public functions (format unchanged — tests depend on these) ─────────

def compute_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    """RSI(period). Returns {"value": float, "level": str}."""
    if len(df) < 30:
        return {"value": 50.0, "level": "neutral"}
    rsi_s = ta.rsi(df["close"], length=period)
    if rsi_s is None or rsi_s.empty or pd.isna(rsi_s.iloc[-1]):
        return {"value": 50.0, "level": "neutral"}
    val = round(float(rsi_s.iloc[-1]), 1)
    level = "overbought" if val > 70 else "oversold" if val < 30 else "neutral"
    return {"value": val, "level": level}


def compute_ema_alignment(df: pd.DataFrame) -> dict:
    """EMA 20/50/200 alignment. Returns alignment + stack keys."""
    default = {"ema20": 0.0, "ema50": 0.0, "ema200": 0.0,
               "current_price": 0.0, "alignment": "neutral", "stack": "mixed"}
    if df.empty or len(df) < 30:
        return default

    close = df["close"]
    emas: dict[str, float] = {}
    for length in [20, 50, 200]:
        if len(df) >= length:
            s = ta.ema(close, length=length)
            if s is not None and not s.empty and not pd.isna(s.iloc[-1]):
                emas[f"ema{length}"] = round(float(s.iloc[-1]), 4)

    if not emas:
        return default

    cur = round(float(close.iloc[-1]), 4)
    above = [k for k, v in emas.items() if cur > v > 0]
    below = [k for k, v in emas.items() if cur < v > 0]
    total = len(emas)

    if len(above) == total:        alignment = "bullish"
    elif len(below) == total:      alignment = "bearish"
    elif len(above) > len(below):  alignment = "mixed-bullish"
    elif len(below) > len(above):  alignment = "mixed-bearish"
    else:                          alignment = "neutral"

    e20, e50, e200 = emas.get("ema20", 0.0), emas.get("ema50", 0.0), emas.get("ema200", 0.0)
    if e20 and e50 and e200:
        stack = "bullish" if e20 > e50 > e200 else "bearish" if e20 < e50 < e200 else "mixed"
    else:
        stack = "mixed"

    return {**default, **emas, "current_price": cur, "alignment": alignment, "stack": stack}


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD with crossover detection. Returns {"macd","signal","histogram","bias",...}."""
    default = {"macd": 0.0, "signal": 0.0, "histogram": 0.0,
               "bias": "bearish", "histogram_growing": False,
               "crossover": False, "crossunder": False}
    if len(df) < 30:
        return default
    m = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
    if m is None or m.empty:
        return default

    mc = [c for c in m.columns if c.startswith("MACD_")]
    sc = [c for c in m.columns if c.startswith("MACDs_")]
    hc = [c for c in m.columns if c.startswith("MACDh_")]
    if not (mc and sc and hc):
        return default

    mv, sv, hv = m[mc[0]].iloc[-1], m[sc[0]].iloc[-1], m[hc[0]].iloc[-1]
    if pd.isna(mv) or pd.isna(sv) or pd.isna(hv):
        return default

    mv, sv, hv = round(float(mv), 4), round(float(sv), 4), round(float(hv), 4)
    hp = float(m[hc[0]].iloc[-2]) if len(m) > 1 else hv
    mp = float(m[mc[0]].iloc[-2]) if len(m) > 1 else mv
    sp = float(m[sc[0]].iloc[-2]) if len(m) > 1 else sv

    return {
        "macd": mv, "signal": sv, "histogram": hv,
        "bias":              "bullish" if mv > sv else "bearish",
        "histogram_growing": hv > hp,
        "crossover":         (mv > sv) and (mp <= sp),
        "crossunder":        (mv < sv) and (mp >= sp),
    }


def compute_adx(df: pd.DataFrame, period: int = 14) -> dict:
    """ADX trend strength and direction."""
    default = {"value": 0.0, "trend_strength": "weak", "direction": "undetermined"}
    if df.empty or len(df) < 30:
        return default
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
    if adx_df is None or adx_df.empty:
        return default

    ac  = [c for c in adx_df.columns if c.startswith("ADX_")]
    dmp = [c for c in adx_df.columns if c.startswith("DMP_")]
    dmn = [c for c in adx_df.columns if c.startswith("DMN_")]
    if not ac:
        return default

    av = adx_df[ac[0]].iloc[-1]
    if pd.isna(av):
        return default
    av = round(float(av), 1)

    strength = "strong" if av > 25 else "trending" if av > 20 else "weak"
    direction = "undetermined"
    if dmp and dmn:
        dp, dn = adx_df[dmp[0]].iloc[-1], adx_df[dmn[0]].iloc[-1]
        if not pd.isna(dp) and not pd.isna(dn):
            direction = "bullish" if float(dp) > float(dn) else "bearish"

    return {"value": av, "trend_strength": strength, "direction": direction}


def compute_prompt_text(df: pd.DataFrame, sr_levels: list[float]) -> str:
    """
    Compute all indicators and return a compact single-line summary < 250 chars.
    Returns empty string if < 30 bars.
    """
    if df.empty or len(df) < 30:
        return ""

    parts: list[str] = []

    rsi = compute_rsi(df)
    sig = "OB" if rsi["value"] > 70 else ("OS" if rsi["value"] < 30 else "neu")
    parts.append(f"RSI {rsi['value']}({sig})")

    macd = compute_macd(df)
    cross = "↑XO" if macd["crossover"] else ("↓XO" if macd["crossunder"] else "")
    parts.append(f"MACD {macd['bias'][:4]}{cross}")

    ema = compute_ema_alignment(df)
    sk = ema.get("stack", "mixed")
    al = ema["alignment"]
    if al == "bullish" and sk == "bullish":   parts.append("EMA ↑all")
    elif al == "bearish" and sk == "bearish": parts.append("EMA ↓all")
    elif "bullish" in sk: parts.append("EMA ↑stk")
    elif "bearish" in sk: parts.append("EMA ↓stk")
    else:                  parts.append("EMA mix")

    adx = compute_adx(df)
    da = "↑" if adx["direction"] == "bullish" else "↓" if adx["direction"] == "bearish" else ""
    st = {"strong": "str", "trending": "trn", "weak": "wk"}.get(adx["trend_strength"], "wk")
    parts.append(f"ADX {adx['value']}{da}({st})")

    try:
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=14)
        if atr_s is not None and not atr_s.empty and not pd.isna(atr_s.iloc[-1]):
            cur = float(df["close"].iloc[-1])
            atr_pct = round(float(atr_s.iloc[-1]) / cur * 100, 2) if cur else 0
            parts.append(f"ATR {atr_pct}%")
    except Exception:
        pass

    if sr_levels and len(df) > 0:
        cur_p = float(df["close"].iloc[-1])
        sups  = sorted([p for p in sr_levels if p < cur_p], reverse=True)
        ress  = sorted([p for p in sr_levels if p >= cur_p])
        if sups:  parts.append(f"S:{round(sups[0], 4)}")
        if ress:  parts.append(f"R:{round(ress[0], 4)}")

    text = " | ".join(parts)
    return text[:249] if len(text) > 249 else text


# ── Extended API — full suite consumed by chart_context.compute_indicators ─────

def compute_wavetrend(df: pd.DataFrame,
                      n1: int = 10, n2: int = 21,
                      ob: float = 53, os_: float = -53,
                      mfi_period: int = 60) -> pd.DataFrame:
    """
    Compute WaveTrend (VMC Cipher A/B).

    Returns a DataFrame aligned to df with columns:
      wt1, wt2, histogram, mfi, cross_bull, cross_bear, signal
    """
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
    esa  = hlc3.ewm(span=n1, adjust=False).mean()
    d    = (hlc3 - esa).abs().ewm(span=n1, adjust=False).mean()
    ci   = (hlc3 - esa) / (0.015 * d.replace(0, float("nan"))).fillna(1e-9)
    wt1  = ci.ewm(span=n2, adjust=False).mean()
    wt2  = wt1.rolling(4, min_periods=1).mean()
    hist = wt1 - wt2

    mfi_src = hlc3 * df["volume"]
    mfi_rsi = ta.rsi(mfi_src, length=mfi_period)
    if mfi_rsi is not None and not mfi_rsi.empty:
        mfi = (mfi_rsi - 50.0) * 2.0
    else:
        mfi = pd.Series(0.0, index=df.index)

    cross_bull = (wt1 > wt2) & (wt1.shift(1) <= wt2.shift(1))
    cross_bear = (wt1 < wt2) & (wt1.shift(1) >= wt2.shift(1))

    signal = pd.Series(None, index=df.index, dtype=object)
    # Gold signals fire at the deepest extremes (±80). 2026-05-26: added the
    # gold_sell mirror — previously only gold_buy existed, leaving WaveTrend
    # structurally Long-biased (extreme oversold gave +1.0 but extreme
    # overbought gave only -0.85).
    gold_buy_mask  = cross_bull & (wt2 < -80)
    gold_sell_mask = cross_bear & (wt2 >  80)
    buy_mask       = cross_bull & (wt2 < os_) & ~gold_buy_mask
    sell_mask      = cross_bear & (wt2 >  ob) & ~gold_sell_mask
    signal[gold_buy_mask]  = "gold_buy"
    signal[gold_sell_mask] = "gold_sell"
    signal[buy_mask]       = "buy"
    signal[sell_mask]      = "sell"

    return pd.DataFrame({
        "wt1":       wt1.round(2),
        "wt2":       wt2.round(2),
        "histogram": hist.round(2),
        "mfi":       mfi.round(2),
        "cross_bull": cross_bull,
        "cross_bear": cross_bear,
        "signal":    signal,
    }, index=df.index)


def compute_stochrsi(df: pd.DataFrame) -> dict | None:
    """Stochastic RSI(14). Enriched 2026-05-30 — adds:
      - k_prev / d_prev: previous-bar values for slope detection
      - crossover: 'bullish' / 'bearish' / 'none' (K vs D within last bar)
      - regime: 'above_50' / 'below_50' — directional bias
      - failure_swing: 'bullish' / 'bearish' / None — same rejected-at-extreme
        pattern as chart_rsi.detect_failure_swing but with 20/80 thresholds
      - signal: enriched human label combining all of the above

    Returns dict or None. Existing fields (k, d, signal) preserved for
    backward compatibility; new fields are additive.
    """
    if len(df) < 30:
        return None
    stochrsi = ta.stochrsi(df["close"], length=14, rsi_length=14, k=3, d=3)
    if stochrsi is None or stochrsi.empty:
        return None
    k_col = next((c for c in stochrsi.columns if "STOCHRSIk" in c), None)
    d_col = next((c for c in stochrsi.columns if "STOCHRSId" in c), None)
    if not (k_col and d_col):
        return None
    k_series = stochrsi[k_col].dropna()
    d_series = stochrsi[d_col].dropna()
    if len(k_series) < 5 or len(d_series) < 5:
        return None

    k_v = float(k_series.iloc[-1])
    d_v = float(d_series.iloc[-1])
    k_prev = float(k_series.iloc[-2])
    d_prev = float(d_series.iloc[-2])
    if any(pd.isna(x) for x in (k_v, d_v, k_prev, d_prev)):
        return None

    k, d = round(k_v, 1), round(d_v, 1)

    # Crossover within last bar
    if k_prev <= d_prev and k_v > d_v:
        crossover = "bullish"
    elif k_prev >= d_prev and k_v < d_v:
        crossover = "bearish"
    else:
        crossover = "none"

    regime = "above_50" if k_v > 50 else "below_50"

    failure_swing = _detect_stochrsi_failure_swing(k_series.tail(30))

    zone = (
        "overbought (K>80)" if k > 80 else
        "oversold (K<20)"   if k < 20 else
        "neutral"
    )
    parts = [zone]
    if crossover != "none":
        parts.append(f"{crossover} cross")
    if failure_swing:
        parts.append(f"{failure_swing} failure swing")

    return {
        "k": k, "d": d,
        "k_prev": round(k_prev, 1),
        "d_prev": round(d_prev, 1),
        "signal": ", ".join(parts),
        "crossover": crossover,
        "regime": regime,
        "failure_swing": failure_swing,
    }


def _detect_stochrsi_failure_swing(k_series) -> str | None:
    """Stoch RSI failure swing — mirrors chart_rsi.detect_failure_swing
    pattern but with 20/80 thresholds (Stoch RSI's "extreme" boundaries).

    Bullish: K dips below 20 → recovers above 20 → next local low stays
             above the first low (rejected at oversold). Age ≤ 5 bars.
    Bearish: mirror with 80.

    Returns 'bullish' / 'bearish' / None.
    """
    try:
        vals = k_series.dropna().values
        if len(vals) < 8:
            return None

        # Bullish: two oversold attempts, second higher
        below20 = [i for i, v in enumerate(vals) if v < 20]
        if below20:
            first_low_idx = below20[0]
            first_low_val = float(vals[first_low_idx])
            cross_back = next((i for i in range(first_low_idx + 1, len(vals))
                               if vals[i] > 20), None)
            if cross_back is not None:
                after = vals[cross_back:]
                if len(after) >= 3:
                    second_low_val = float(after.min())
                    second_low_off = int(after.argmin()) + cross_back
                    if second_low_val > first_low_val and second_low_val < 50:
                        age = len(vals) - 1 - second_low_off
                        if age <= 5:
                            return "bullish"

        # Bearish: two overbought attempts, second lower
        above80 = [i for i, v in enumerate(vals) if v > 80]
        if above80:
            first_high_idx = above80[0]
            first_high_val = float(vals[first_high_idx])
            cross_back = next((i for i in range(first_high_idx + 1, len(vals))
                               if vals[i] < 80), None)
            if cross_back is not None:
                after = vals[cross_back:]
                if len(after) >= 3:
                    second_high_val = float(after.max())
                    second_high_off = int(after.argmax()) + cross_back
                    if second_high_val < first_high_val and second_high_val > 50:
                        age = len(vals) - 1 - second_high_off
                        if age <= 5:
                            return "bearish"
        return None
    except Exception:
        return None


def compute_stochastic(df: pd.DataFrame, k_period: int = 14,
                       d_period: int = 3) -> dict | None:
    """
    Classic Stochastic Oscillator (%K, %D) — the AI flagged this as a missing
    signal across multiple self-review cycles (2026-05-21 wishlist). Distinct
    from compute_stochrsi: this one is on raw price highs/lows, faster-reacting
    and more useful as an entry-timing overlay on 1H/4H.

    Returns {"k","d","signal"} or None when the data window is too small.
    """
    if len(df) < (k_period + d_period + 5):
        return None
    try:
        stoch = ta.stoch(df["high"], df["low"], df["close"],
                         k=k_period, d=d_period, smooth_k=3)
    except Exception:
        return None
    if stoch is None or stoch.empty:
        return None
    k_cols = [c for c in stoch.columns if c.startswith("STOCHk")]
    d_cols = [c for c in stoch.columns if c.startswith("STOCHd")]
    if not (k_cols and d_cols):
        return None
    k_v = stoch[k_cols[0]].iloc[-1]
    d_v = stoch[d_cols[0]].iloc[-1]
    if pd.isna(k_v) or pd.isna(d_v):
        return None
    k, d = round(float(k_v), 1), round(float(d_v), 1)
    return {
        "k": k, "d": d,
        "signal": (
            "overbought (K>80)" if k > 80 else
            "oversold (K<20)"   if k < 20 else
            "neutral"
        ),
    }


def compute_bollinger(df: pd.DataFrame) -> dict | None:
    """Bollinger Bands(20,2). Returns {"upper","mid","lower","position_pct","band_width","signal"} or None."""
    if len(df) < 30:
        return None
    bbands = ta.bbands(df["close"], length=20, std=2)
    if bbands is None or bbands.empty:
        return None
    upper_col  = [c for c in bbands.columns if "BBU" in c]
    lower_col  = [c for c in bbands.columns if "BBL" in c]
    mid_col    = [c for c in bbands.columns if "BBM" in c]
    bwidth_col = [c for c in bbands.columns if "BBB" in c]
    if not (upper_col and lower_col and mid_col):
        return None
    upper = float(bbands[upper_col[0]].iloc[-1])
    lower = float(bbands[lower_col[0]].iloc[-1])
    mid   = float(bbands[mid_col[0]].iloc[-1])
    if any(pd.isna(v) for v in (upper, lower, mid)):
        return None
    price = float(df["close"].iloc[-1])
    band_range   = upper - lower
    position_pct = round((price - lower) / band_range * 100, 1) if band_range > 0 else 50.0
    bw = round(float(bbands[bwidth_col[0]].iloc[-1]), 4) if bwidth_col else None
    return {
        "upper":        round(upper, 4),
        "mid":          round(mid, 4),
        "lower":        round(lower, 4),
        "position_pct": position_pct,
        "band_width":   bw,
        "signal": (
            "near upper band (overbought zone)" if position_pct > 80 else
            "near lower band (oversold zone)"   if position_pct < 20 else
            "mid-band area"
        ),
    }


def compute_obv(df: pd.DataFrame) -> dict | None:
    """
    On-Balance Volume (Granville, 1963).

    Returns {"value", "trend", "slope_pct"} or None.
    Trend = direction of OBV slope over last 20 bars.
    """
    if len(df) < 20:
        return None
    try:
        obv = ta.obv(df["close"], df["volume"])
        if obv is None or obv.empty:
            return None
        recent  = obv.iloc[-20:]
        current = float(obv.iloc[-1])
        start   = float(recent.iloc[0])
        if start == 0:
            slope_pct = 0.0
        else:
            slope_pct = (current - start) / abs(start) * 100
        trend = "rising" if slope_pct > 2 else ("falling" if slope_pct < -2 else "flat")
        return {
            "value":     round(current, 2),
            "trend":     trend,
            "slope_pct": round(slope_pct, 2),
        }
    except Exception:
        return None


def compute_cmf(df: pd.DataFrame, period: int = 20) -> dict | None:
    """
    Chaikin Money Flow (Marc Chaikin).

    CMF > 0.10 → strong buying pressure
    CMF < -0.10 → strong selling pressure
    Returns {"value", "signal"} or None.
    """
    if len(df) < period + 5:
        return None
    try:
        cmf = ta.cmf(df["high"], df["low"], df["close"], df["volume"], length=period)
        if cmf is None or cmf.empty:
            return None
        current = float(cmf.iloc[-1])
        if pd.isna(current):
            return None
        signal = ("strong_buying" if current > 0.10
                  else ("buying" if current > 0.05
                  else ("strong_selling" if current < -0.10
                  else ("selling" if current < -0.05 else "neutral"))))
        return {
            "value":  round(current, 4),
            "signal": signal,
        }
    except Exception:
        return None


def _detect_simple_divergence(price_series, indicator_series, lookback: int = 15) -> str:
    """
    Quick divergence check: compare slope of price vs indicator over last `lookback` bars.

    Returns:
      "bullish_regular"  — price LL, indicator HL → reversal up
      "bearish_regular"  — price HH, indicator LH → reversal down
      "bullish_hidden"   — price HL, indicator LL → continuation up
      "bearish_hidden"   — price LH, indicator HH → continuation down
      ""                  — no clear divergence

    Compact: uses first-vs-last comparison (not pivot-detection).
    """
    if price_series is None or indicator_series is None:
        return ""
    try:
        p = price_series.iloc[-lookback:].dropna()
        i = indicator_series.iloc[-lookback:].dropna()
        if len(p) < 5 or len(i) < 5:
            return ""
        p_start, p_end = float(p.iloc[0]), float(p.iloc[-1])
        i_start, i_end = float(i.iloc[0]), float(i.iloc[-1])
        # Threshold: must move at least 0.5% to count as direction
        if abs(p_end - p_start) / max(abs(p_start), 1) < 0.005:
            return ""
        p_up = p_end > p_start
        i_up = i_end > i_start
        if p_up and not i_up:
            return "bearish_regular"   # price up, indicator down
        if not p_up and i_up:
            return "bullish_regular"   # price down, indicator up
        return ""   # both same direction = no regular divergence
    except Exception:
        return ""


def compute_bollinger_squeeze(df: pd.DataFrame, lookback: int = 50,
                                squeeze_pct: float = 0.25,
                                expansion_threshold: float = 1.5) -> dict | None:
    """
    Bollinger Squeeze detector — measures volatility contraction → expansion.

    A squeeze fires when current band-width is in the bottom `squeeze_pct`
    (default 25%) of the last `lookback` bars. A release fires when the
    band-width within the last 1-3 bars was squeezed AND current width has
    expanded to ≥ `expansion_threshold` × the recent squeeze minimum.

    Direction of release: bullish if close > mid (Bollinger middle),
    bearish if close < mid.

    Returns:
      {
        "state":        "squeezing" | "releasing" | "expanded" | "neutral",
        "direction":    "Long" | "Short" | None (only when state == releasing),
        "bw_current":   float,
        "bw_min_recent": float,
        "bw_percentile": float (0-100, current bw's percentile in lookback window),
        "bars_since_squeeze_low": int | None,
      }

    Returns None if insufficient data.
    """
    if df is None or len(df) < lookback + 5:
        return None
    bbands = ta.bbands(df["close"], length=20, std=2)
    if bbands is None or bbands.empty:
        return None
    bwidth_col = [c for c in bbands.columns if "BBB" in c]
    mid_col    = [c for c in bbands.columns if "BBM" in c]
    if not bwidth_col or not mid_col:
        return None
    bw_series = bbands[bwidth_col[0]].dropna()
    if len(bw_series) < lookback:
        return None

    recent = bw_series.iloc[-lookback:]
    current_bw   = float(bw_series.iloc[-1])
    bw_min       = float(recent.min())
    threshold    = float(recent.quantile(squeeze_pct))

    if pd.isna(current_bw) or pd.isna(bw_min):
        return None

    # Percentile of current bw within recent window
    pct = float((recent < current_bw).sum()) / len(recent) * 100.0

    # Find bars-since-squeeze-low (the most recent bar where bw was at/below threshold)
    bars_since = None
    for i in range(1, min(lookback, len(recent))):
        if recent.iloc[-i] <= threshold:
            bars_since = i - 1  # 0 = current bar still squeezed
            break

    # State classification
    state = "neutral"
    direction = None
    if current_bw <= threshold:
        state = "squeezing"
    elif bars_since is not None and bars_since <= 3 and current_bw >= bw_min * expansion_threshold:
        state = "releasing"
        # Direction from close vs mid
        mid = float(bbands[mid_col[0]].iloc[-1])
        close = float(df["close"].iloc[-1])
        if not pd.isna(mid):
            direction = "Long" if close > mid else "Short"
    elif current_bw >= bw_min * expansion_threshold:
        state = "expanded"

    return {
        "state":                  state,
        "direction":              direction,
        "bw_current":             round(current_bw, 4),
        "bw_min_recent":          round(bw_min, 4),
        "bw_percentile":          round(pct, 1),
        "bars_since_squeeze_low": bars_since,
    }


def compute_atr(df: pd.DataFrame, period: int = 14) -> dict | None:
    """ATR(period). Returns {"value","pct","comment"} or None."""
    if len(df) < 30:
        return None
    atr_s = ta.atr(df["high"], df["low"], df["close"], length=period)
    if atr_s is None or atr_s.empty or pd.isna(atr_s.iloc[-1]):
        return None
    atr_val   = round(float(atr_s.iloc[-1]), 4)
    cur_price = float(df["close"].iloc[-1])
    atr_pct   = round(atr_val / cur_price * 100, 2) if cur_price else 0
    return {
        "value":   atr_val,
        "pct":     atr_pct,
        "comment": f"typical candle range {atr_pct}% of price — useful for SL sizing",
    }


def compute_supertrend(df: pd.DataFrame, period: int = 10,
                        multiplier: float = 3.0) -> dict | None:
    """Supertrend trend-flip indicator (ATR-based).

    Added 2026-05-30 — fills the "fast trend flip" gap surfaced by external
    research (Phantom Flow scalping survey). Distinct from MACD/EMA cross:
    Supertrend is a *binary* state (+1 trend up / -1 trend down) computed
    from price vs the ATR-banded mid-line, so it gives clean discrete flips
    you can use as confluence votes or trailing stops.

    Args:
      df: OHLCV DataFrame.
      period: ATR period (default 10).
      multiplier: ATR multiplier for band width (default 3.0).

    Returns:
      {
        "direction":         +1 (up) | -1 (down),
        "supertrend_value":  float — current line value,
        "flip_bars_ago":     int — bars since last direction change (0 if just flipped),
        "signal":            "uptrend" | "downtrend" | "flip_bullish" | "flip_bearish",
      }
      or None on insufficient data.
    """
    if len(df) < period + 5:
        return None
    try:
        st = ta.supertrend(df["high"], df["low"], df["close"],
                            length=period, multiplier=multiplier)
    except Exception:
        return None
    if st is None or st.empty:
        return None

    # pandas-ta columns: SUPERT_{period}_{multiplier}, SUPERTd_{period}_{multiplier}
    value_col = next((c for c in st.columns if c.startswith("SUPERT_")), None)
    dir_col = next((c for c in st.columns if c.startswith("SUPERTd_")), None)
    if not (value_col and dir_col):
        return None

    dir_series = st[dir_col].dropna()
    val_series = st[value_col].dropna()
    if dir_series.empty or val_series.empty:
        return None

    cur_dir = int(dir_series.iloc[-1])
    cur_val = float(val_series.iloc[-1])
    if pd.isna(cur_val):
        return None

    # Count bars since last flip
    flip_bars_ago = 0
    for i in range(2, min(len(dir_series), 200)):
        if int(dir_series.iloc[-i]) != cur_dir:
            flip_bars_ago = i - 1
            break
    else:
        flip_bars_ago = len(dir_series) - 1

    if flip_bars_ago == 0:
        signal = "flip_bullish" if cur_dir > 0 else "flip_bearish"
    else:
        signal = "uptrend" if cur_dir > 0 else "downtrend"

    return {
        "direction":        cur_dir,
        "supertrend_value": round(cur_val, 6),
        "flip_bars_ago":    int(flip_bars_ago),
        "signal":           signal,
    }


def compute_volume(df: pd.DataFrame) -> dict | None:
    """Volume vs 20-bar average. Returns {"current","avg_20","ratio","signal"} or None."""
    if len(df["volume"]) < 20:
        return None
    vol_now = float(df["volume"].iloc[-1])
    vol_avg = float(df["volume"].iloc[-20:].mean())
    ratio   = round(vol_now / vol_avg, 2) if vol_avg else 1.0
    return {
        "current": round(vol_now, 2),
        "avg_20":  round(vol_avg, 2),
        "ratio":   ratio,
        "signal": (
            f"high volume ({ratio}x avg)" if ratio > 1.5 else
            f"low volume ({ratio}x avg)"  if ratio < 0.7 else
            f"average volume ({ratio}x avg)"
        ),
    }


def compute_recent_candles(df: pd.DataFrame) -> list[str] | None:
    """Last 3 candle body descriptions. Returns list of 3 strings or None."""
    if len(df) < 3:
        return None
    candles = []
    for i in range(-3, 0):
        row = df.iloc[i]
        o, c_p, h, lo = float(row["open"]), float(row["close"]), float(row["high"]), float(row["low"])
        body       = abs(c_p - o)
        full_range = h - lo
        body_pct   = round(body / full_range * 100, 0) if full_range else 0
        candle_type = "doji" if body_pct < 20 else ("bullish" if c_p > o else "bearish")
        candles.append(f"{candle_type} (body {body_pct:.0f}% of range)")
    return candles


def compute_cvd(df: pd.DataFrame) -> dict | None:
    """
    Cumulative Volume Delta (Money Flow Multiplier approximation).
    Returns {"value","trend","signal"} or None.
    """
    if len(df) < 4:
        return None
    try:
        h_arr = df["high"].values.astype(float)
        l_arr = df["low"].values.astype(float)
        c_arr = df["close"].values.astype(float)
        v_arr = df["volume"].values.astype(float)
        running = 0.0
        cvd_series = []
        for i in range(len(h_arr)):
            denom = h_arr[i] - l_arr[i]
            delta = v_arr[i] * (2 * c_arr[i] - l_arr[i] - h_arr[i]) / denom if denom > 0 else 0.0
            running += delta
            cvd_series.append(running)
        cvd_now  = cvd_series[-1]
        cvd_prev = cvd_series[-4] if len(cvd_series) >= 4 else cvd_series[0]
        trend = "rising" if cvd_now > cvd_prev * 1.001 else (
                "falling" if cvd_now < cvd_prev * 0.999 else "flat")
        return {
            "value":  round(cvd_now, 2),
            "trend":  trend,
            "signal": (
                "bullish (net buy pressure)"  if trend == "rising" else
                "bearish (net sell pressure)" if trend == "falling" else
                "neutral"
            ),
        }
    except Exception:
        return None


def compute_all_indicators(df: pd.DataFrame) -> dict:
    """
    Full indicator suite in chart_context format.

    Returns {"ok": bool, ...} with all indicator sub-dicts.
    Does NOT include support_resistance or trendlines — chart_context adds those.
    """
    if df is None or df.empty or len(df) < 30:
        return {"ok": False, "error": "Insufficient candle data"}

    result: dict = {"ok": True, "candles_used": len(df)}

    # RSI — adapt "level" → "signal" with verbose labels
    rsi = compute_rsi(df)
    result["rsi"] = {
        "value":  rsi["value"],
        "signal": (
            "overbought (>70)" if rsi["level"] == "overbought" else
            "oversold (<30)"   if rsi["level"] == "oversold"   else
            "neutral"
        ),
    }

    # Stochastic RSI
    stochrsi = compute_stochrsi(df)
    if stochrsi:
        result["stoch_rsi"] = stochrsi

    # Classic Stochastic Oscillator — AI self-review wishlist signal (2026-05-21)
    stoch = compute_stochastic(df)
    if stoch:
        result["stochastic"] = stoch

    # MACD — rename "bias" → "trend", "histogram_growing" → "histogram_trend"
    macd = compute_macd(df)
    result["macd"] = {
        "macd":            macd["macd"],
        "signal":          macd["signal"],
        "histogram":       macd["histogram"],
        "trend":           macd["bias"],
        "histogram_trend": "growing" if macd["histogram_growing"] else "shrinking",
        "crossover":       macd["crossover"],
        "crossunder":      macd["crossunder"],
    }

    # EMA — expand short alignment codes to verbose strings chart_context expects
    ema       = compute_ema_alignment(df)
    cur_price = ema.get("current_price", 0.0)
    ema_vals  = {k: v for k, v in ema.items() if k.startswith("ema") and v}
    if ema_vals:
        above = [f"EMA{k[3:]}" for k, v in ema_vals.items() if cur_price > v > 0]
        below = [f"EMA{k[3:]}" for k, v in ema_vals.items() if cur_price < v > 0]
        total = len(ema_vals)

        if len(above) == total:
            alignment = "fully bullish — price above all EMAs"
        elif len(below) == total:
            alignment = "fully bearish — price below all EMAs"
        else:
            alignment = (
                f"mixed — above {', '.join(above)}; below {', '.join(below)}"
                if above else f"below {', '.join(below)}"
            )

        stack_s = ema.get("stack", "mixed")
        stack = (
            "bullish (20 > 50 > 200)" if stack_s == "bullish" else
            "bearish (20 < 50 < 200)" if stack_s == "bearish" else
            "mixed"
        )
        result["ema"] = {**ema_vals, "current_price": cur_price,
                         "alignment": alignment, "stack": stack}

    # Bollinger Bands
    bb = compute_bollinger(df)
    if bb:
        result["bollinger"] = bb

    # Bollinger Squeeze (added 2026-05-24) — volatility-regime signal,
    # not a direct band reading. Separate dict so consumers can read
    # either the bands OR the squeeze state.
    bb_sq = compute_bollinger_squeeze(df)
    if bb_sq:
        result["bollinger_squeeze"] = bb_sq

    # Wyckoff single-bar trap (added 2026-05-24) — spring (bullish reversal)
    # or upthrust (bearish reversal). Uses last 2 closed bars for the
    # candidate + next-bar confirmation. Reference Schlotmann TA Masterclass.
    try:
        from chart_wyckoff import detect_single_bar_trap
        trap = detect_single_bar_trap(df)
        if trap and trap.get("detected"):
            result["wyckoff_trap"] = trap
    except Exception:
        pass

    # Wyckoff multi-bar signals (added 2026-05-24, Phase 2) — Spring,
    # Upthrust, Absorption. Reference: David Weis "Trades About to Happen".
    try:
        from chart_wyckoff import detect_spring, detect_upthrust, detect_absorption
        sp = detect_spring(df)
        if sp and sp.get("detected"):
            result["wyckoff_spring"] = sp
        ut = detect_upthrust(df)
        if ut and ut.get("detected"):
            result["wyckoff_upthrust"] = ut
        # Only check absorption if no spring/upthrust (mutually exclusive)
        if not (sp and sp.get("detected")) and not (ut and ut.get("detected")):
            ab = detect_absorption(df)
            if ab and ab.get("detected"):
                result["wyckoff_absorption"] = ab
    except Exception:
        pass

    # SOT (Shortening of the Thrust) + Wave leg ratio — Phase 2, share
    # swing-pivot extraction. Reference Weis + Schlotmann.
    try:
        from chart_wyckoff import detect_sot, detect_wave_ratio
        sot = detect_sot(df)
        if sot and sot.get("detected"):
            result["sot"] = sot
        wave = detect_wave_ratio(df)
        if wave and wave.get("detected"):
            result["wave_ratio"] = wave
    except Exception:
        pass

    # OBV + CMF — Phase 4 prerequisite for divergence aggregation (F12).
    obv = compute_obv(df)
    if obv:
        result["obv"] = obv
    cmf = compute_cmf(df)
    if cmf:
        result["cmf"] = cmf

    # Last bar OHLC — used by climactic volume detection (Feature 18) and
    # other downstream wick-aware signals. Cheap snapshot of the most
    # recent closed candle.
    try:
        last = df.iloc[-1]
        result["last_bar"] = {
            "open":  float(last["open"]),
            "high":  float(last["high"]),
            "low":   float(last["low"]),
            "close": float(last["close"]),
        }
    except Exception:
        pass

    # ATR
    atr = compute_atr(df)
    if atr:
        result["atr"] = atr

    # ADX — expand short strings to verbose labels
    adx = compute_adx(df)
    if adx["value"] > 0:
        strength_map  = {
            "strong":   "strong trend (>25)",
            "trending": "trending (20–25)",
            "weak":     "weak/no trend (<20)",
        }
        direction_map = {
            "bullish": "bullish (+DI > -DI)",
            "bearish": "bearish (-DI > +DI)",
        }
        adx_result: dict = {
            "value":    adx["value"],
            "strength": strength_map.get(adx["trend_strength"], "weak/no trend (<20)"),
        }
        if adx["direction"] in direction_map:
            adx_result["direction"] = direction_map[adx["direction"]]
        result["adx"] = adx_result

    # Volume
    vol = compute_volume(df)
    if vol:
        result["volume"] = vol

    # Recent candles
    recent = compute_recent_candles(df)
    if recent:
        result["recent_candles"] = recent

    # WaveTrend
    try:
        wt_df    = compute_wavetrend(df)
        wt1_last = float(wt_df["wt1"].iloc[-1])
        wt2_last = float(wt_df["wt2"].iloc[-1])
        mfi_last = float(wt_df["mfi"].iloc[-1])
        sig_last = wt_df["signal"].iloc[-1]
        cb_last  = bool(wt_df["cross_bull"].iloc[-1])
        cs_last  = bool(wt_df["cross_bear"].iloc[-1])
        result["wavetrend"] = {
            "wt1":       round(wt1_last, 2),
            "wt2":       round(wt2_last, 2),
            "histogram": round(wt1_last - wt2_last, 2),
            "mfi":       round(mfi_last, 2),
            "cross":     "bullish" if cb_last else ("bearish" if cs_last else None),
            "zone":      (
                "overbought" if wt1_last >  53 else
                "oversold"   if wt1_last < -53 else
                "neutral"
            ),
            "signal":    sig_last,
        }
    except Exception:
        pass

    # CVD
    cvd = compute_cvd(df)
    if cvd:
        result["cvd"] = cvd

    result["order_flow"] = compute_order_flow_delta(df)

    return result


def compute_order_flow_delta(df: pd.DataFrame) -> dict | None:
    """
    Tick-rule proxy for per-candle aggressor delta.
    Positive delta = net buying pressure; negative = net selling pressure.
    Returns: {delta, cumulative_delta, signal, divergence}
    """
    if df is None or len(df) < 3:
        return None
    try:
        body      = df["close"] - df["open"]
        body_abs  = body.abs()
        ratio     = (body_abs / (body_abs + 1e-9)).clip(0.10, 0.90)
        buy_vol   = df["volume"] * ratio.where(body >= 0, 1 - ratio)
        sell_vol  = df["volume"] - buy_vol
        delta_bar = buy_vol - sell_vol

        delta     = float(delta_bar.iloc[-1])
        cum_delta = float(delta_bar.sum())

        price_high    = df["close"].iloc[-1] > df["close"].iloc[-5:-1].max()
        prior_avg     = float(delta_bar.iloc[-5:-1].mean()) if len(delta_bar) >= 5 else 0.0
        divergence    = bool(price_high and delta < prior_avg)

        signal = ("buying_pressure"  if delta > 0 else
                  "selling_pressure" if delta < 0 else "neutral")

        return {"delta": delta, "cumulative_delta": cum_delta,
                "signal": signal, "divergence": divergence}
    except Exception:
        return None
