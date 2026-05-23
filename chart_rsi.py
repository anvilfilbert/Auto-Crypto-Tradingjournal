"""
chart_rsi.py — RSI structural analysis (trader research RSI Mastery framework).

Goes beyond raw 0-100 reading to extract:
- Regime classification (bullish/bearish/range) — same RSI value means
  different things in different regimes.
- Failure swings — the most reliable reversal signal per the guide.
  RSI fails to confirm a new price extreme = shift in control.
- Divergences (regular + hidden):
  - Regular bullish: price LL + RSI HL → reversal up
  - Regular bearish: price HH + RSI LH → reversal down
  - Hidden bullish:  price HL + RSI LL → continuation up
  - Hidden bearish:  price LH + RSI HH → continuation down

Public:
- compute_rsi_series(df, period=14) → pd.Series of RSI values
- classify_regime(rsi_series, lookback=20) → "bullish"|"bearish"|"range"
- detect_failure_swing(rsi_series, lookback=30) → {type, strength, age}
- detect_divergences(df, rsi_series, lookback=30) → list of divergence dicts
- summarize_rsi(df, rsi_period=14) → dict with all of the above + weight
"""
from __future__ import annotations
from typing import Optional

try:
    import pandas_ta as _ta
except ImportError:
    _ta = None


def compute_rsi_series(df, period: int = 14):
    """Return the RSI series for the given df['close'], or None on failure."""
    if df is None or len(df) < period + 5 or _ta is None:
        return None
    try:
        return _ta.rsi(df["close"], length=period)
    except Exception:
        return None


def classify_regime(rsi_series, lookback: int = 20) -> str:
    """
    Bullish regime: RSI mostly 40-80; recent average above 50.
    Bearish regime: RSI mostly 20-60; recent average below 50.
    Range:          neither dominant.
    """
    if rsi_series is None or len(rsi_series) < lookback:
        return "range"
    try:
        recent = rsi_series.iloc[-lookback:].dropna()
        if len(recent) < 5:
            return "range"
        avg = float(recent.mean())
        # Count time spent in each "healthy zone"
        bullish_bars = int(((recent >= 40) & (recent <= 80)).sum())
        bearish_bars = int(((recent >= 20) & (recent <= 60)).sum())
        n = len(recent)
        bullish_pct = bullish_bars / n
        bearish_pct = bearish_bars / n
        if avg > 55 and bullish_pct >= 0.7:
            return "bullish"
        if avg < 45 and bearish_pct >= 0.7:
            return "bearish"
        return "range"
    except Exception:
        return "range"


def detect_failure_swing(rsi_series, lookback: int = 30) -> dict:
    """
    Bullish failure swing:
      1. RSI dips below 30 (oversold).
      2. RSI rallies back above 30.
      3. RSI pulls back BUT stays above the previous oversold low.
      4. Optional confirmation: RSI then turns up again.
    Bearish failure swing: mirror with 70.

    Returns {"type": str, "age": int} or {} if none detected.
    type is "bullish"|"bearish"; age is bars since the failure-swing low.
    """
    if rsi_series is None or len(rsi_series) < 10:
        return {}
    try:
        s = rsi_series.iloc[-lookback:].dropna()
        if len(s) < 8:
            return {}
        vals = s.values

        # Bullish failure swing — scan for: low<30 → up>30 → next-low>30 (no new low)
        below30_indices = [i for i, v in enumerate(vals) if v < 30]
        if len(below30_indices) >= 1:
            first_low_idx = below30_indices[0]
            first_low_val = vals[first_low_idx]
            # Find where RSI first crossed back above 30
            cross_back = None
            for i in range(first_low_idx + 1, len(vals)):
                if vals[i] > 30:
                    cross_back = i
                    break
            if cross_back is not None:
                # Find subsequent dip (local low) that stays above first_low_val
                # We accept the most recent local low after cross_back.
                after = vals[cross_back:]
                if len(after) >= 3:
                    # Local min in the tail of the series
                    second_low_val = float(min(after))
                    second_low_offset = int(after.argmin()) + cross_back
                    # Failure swing valid if second low stayed above first low
                    if second_low_val > first_low_val and second_low_val < 50:
                        age = len(vals) - 1 - second_low_offset
                        if age <= 5:    # only count recent failure swings
                            return {"type": "bullish", "age": age}

        # Bearish failure swing — mirror
        above70_indices = [i for i, v in enumerate(vals) if v > 70]
        if len(above70_indices) >= 1:
            first_high_idx = above70_indices[0]
            first_high_val = vals[first_high_idx]
            cross_back = None
            for i in range(first_high_idx + 1, len(vals)):
                if vals[i] < 70:
                    cross_back = i
                    break
            if cross_back is not None:
                after = vals[cross_back:]
                if len(after) >= 3:
                    second_high_val = float(max(after))
                    second_high_offset = int(after.argmax()) + cross_back
                    if second_high_val < first_high_val and second_high_val > 50:
                        age = len(vals) - 1 - second_high_offset
                        if age <= 5:
                            return {"type": "bearish", "age": age}
    except Exception:
        pass
    return {}


def _find_swings(values, window: int = 3):
    """Helper — return (swing_high_indices, swing_low_indices) by local extrema.
    A bar is a swing high if it's the max in [i-window, i+window]; same for low."""
    n = len(values)
    highs, lows = [], []
    for i in range(window, n - window):
        win = values[i-window:i+window+1]
        v = values[i]
        if v == max(win) and (highs and i - highs[-1] >= window or not highs):
            highs.append(i)
        if v == min(win) and (lows and i - lows[-1] >= window or not lows):
            lows.append(i)
    return highs, lows


def detect_divergences(df, rsi_series, lookback: int = 30,
                        swing_window: int = 3) -> list[dict]:
    """
    Compare last two swing highs and lows in PRICE vs RSI. Returns a list of
    divergences found, newest first. Each: {"type": str, "kind": str, "age": int}
    where kind is "regular"|"hidden" and type encodes direction.

    Categories:
      - regular_bullish: price LL + RSI HL  → reversal up
      - regular_bearish: price HH + RSI LH  → reversal down
      - hidden_bullish:  price HL + RSI LL  → continuation up
      - hidden_bearish:  price LH + RSI HH  → continuation down
    """
    if df is None or rsi_series is None or len(df) < lookback or len(rsi_series) < lookback:
        return []
    try:
        # Align — take last `lookback` of both
        n = min(len(df), len(rsi_series), lookback)
        highs_px = df["high"].iloc[-n:].values
        lows_px  = df["low"].iloc[-n:].values
        rsi_v    = rsi_series.iloc[-n:].dropna().values
        if len(rsi_v) < n:
            return []   # NaN at the start screws alignment

        sh_idx, sl_idx = _find_swings(rsi_v, window=swing_window)

        out = []

        # Compare last two swing highs (bearish divergences live here)
        if len(sh_idx) >= 2:
            i1, i2 = sh_idx[-2], sh_idx[-1]
            px1, px2 = float(highs_px[i1]), float(highs_px[i2])
            r1, r2   = float(rsi_v[i1]),    float(rsi_v[i2])
            # Regular bearish: price HH + RSI LH
            if px2 > px1 and r2 < r1:
                out.append({"type": "bearish", "kind": "regular",
                            "age": int(n - 1 - i2)})
            # Hidden bearish: price LH + RSI HH
            elif px2 < px1 and r2 > r1:
                out.append({"type": "bearish", "kind": "hidden",
                            "age": int(n - 1 - i2)})

        # Compare last two swing lows (bullish divergences live here)
        if len(sl_idx) >= 2:
            i1, i2 = sl_idx[-2], sl_idx[-1]
            px1, px2 = float(lows_px[i1]), float(lows_px[i2])
            r1, r2   = float(rsi_v[i1]),   float(rsi_v[i2])
            # Regular bullish: price LL + RSI HL
            if px2 < px1 and r2 > r1:
                out.append({"type": "bullish", "kind": "regular",
                            "age": int(n - 1 - i2)})
            # Hidden bullish: price HL + RSI LL
            elif px2 > px1 and r2 < r1:
                out.append({"type": "bullish", "kind": "hidden",
                            "age": int(n - 1 - i2)})

        # Filter stale divergences (age > 8 bars probably already played out)
        out = [d for d in out if d["age"] <= 8]
        out.sort(key=lambda d: d["age"])
        return out
    except Exception:
        return []


def regime_aware_rsi_weight(rsi_val: float, regime: str) -> float:
    """
    Regime-adjusted RSI score contribution. Same value, different meaning:
      - Bullish regime: RSI > 70 is NOT bearish (trend is hot). RSI < 40 IS
        the warning (trend losing momentum).
      - Bearish regime: RSI < 30 is NOT bullish (trend is cold). RSI > 60 IS
        the warning (trend losing momentum).
      - Range regime: classic 30/70 logic applies symmetrically.
    """
    if regime == "bullish":
        if rsi_val >= 70:    return +0.5    # trending hot, NOT a sell
        if rsi_val >= 60:    return +1.0    # strong bull pressure
        if rsi_val >= 50:    return +0.3    # holding bias
        if rsi_val >= 40:    return -0.2    # caution — testing dynamic support
        return -1.0                          # broke 40 = trend in trouble
    if regime == "bearish":
        if rsi_val <= 30:    return -0.5    # trending cold, NOT a buy
        if rsi_val <= 40:    return -1.0    # strong bear pressure
        if rsi_val <= 50:    return -0.3    # holding bias
        if rsi_val <= 60:    return +0.2    # caution — testing dynamic resistance
        return +1.0                          # broke 60 = trend in trouble
    # range regime — classic interpretation
    if rsi_val > 70:    return -1.0
    if rsi_val > 55:    return min((rsi_val - 50) / 30.0,  1.0)
    if rsi_val < 30:    return +1.0
    if rsi_val < 45:    return max((rsi_val - 50) / 30.0, -1.0)
    return 0.0


def summarize_rsi(df, rsi_period: int = 14) -> dict:
    """Single entry point — compute everything in one call.

    Returns:
        {
            "value":          float,             # current RSI
            "regime":         str,               # bullish|bearish|range
            "weight":         float,             # regime-aware score contribution
            "failure_swing":  dict,              # {} or {type, age}
            "divergences":    list[dict],        # regular + hidden
            "fs_weight":      float,             # failure-swing score contribution
            "div_weight":     float,             # divergence score contribution
            "label":          str,               # short human-readable summary
        }
    """
    rsi_s = compute_rsi_series(df, period=rsi_period)
    if rsi_s is None or rsi_s.empty:
        return {"value": 50.0, "regime": "range", "weight": 0.0,
                "failure_swing": {}, "divergences": [],
                "fs_weight": 0.0, "div_weight": 0.0, "label": ""}

    val = float(rsi_s.iloc[-1])
    regime = classify_regime(rsi_s)
    weight = regime_aware_rsi_weight(val, regime)
    fs = detect_failure_swing(rsi_s)
    divs = detect_divergences(df, rsi_s)

    # Failure swing scoring — strongest reversal signal per the guide
    fs_weight = 0.0
    if fs.get("type") == "bullish":
        fs_weight = +0.4
    elif fs.get("type") == "bearish":
        fs_weight = -0.4

    # Divergence scoring
    div_weight = 0.0
    div_labels = []
    for d in divs:
        # Regular = reversal warning (stronger); hidden = continuation (lighter)
        mag = 0.3 if d["kind"] == "regular" else 0.2
        sign = +1 if d["type"] == "bullish" else -1
        div_weight += sign * mag
        div_labels.append(f"{d['kind']} {d['type']} div (age {d['age']})")
    # Cap to ±0.4 so divergences don't dominate (max 2 simultaneous, otherwise noisy)
    div_weight = max(-0.4, min(0.4, div_weight))

    label_bits = [f"RSI {val:.0f}", f"regime: {regime}"]
    if fs:
        label_bits.append(f"failure swing {fs['type']} (age {fs['age']})")
    if div_labels:
        label_bits.append(" + ".join(div_labels[:2]))

    return {
        "value":         round(val, 1),
        "regime":        regime,
        "weight":        round(weight, 2),
        "failure_swing": fs,
        "divergences":   divs,
        "fs_weight":     round(fs_weight, 2),
        "div_weight":    round(div_weight, 2),
        "label":         " · ".join(label_bits),
    }
