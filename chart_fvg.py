"""
chart_fvg.py — Fair Value Gap detection (ICT / Smart Money Concepts).

An FVG is a 3-candle pattern that leaves a price imbalance:
- Bullish FVG: candle1.high < candle3.low → gap between them acts as
  future support; price often returns to "rebalance" before continuing up.
- Bearish FVG: candle1.low > candle3.high → gap acts as future
  resistance; price often returns to rebalance before continuing down.

Unfilled FVGs are high-probability reaction zones. We score them as:
- Same-direction FVG below current price (Long entry near bullish FVG
  support) → bullish confluence (+0.3)
- Opposing FVG above current price (bearish FVG resistance overhead on
  a Long) → bearish confluence (-0.3)
- Mirror for Shorts.

Public functions:
- detect_unfilled_fvgs(df, lookback=30, min_gap_pct=0.1)
- nearest_fvg_signal(df, current_price, direction)
"""
from __future__ import annotations
from typing import Optional


def detect_unfilled_fvgs(df, lookback: int = 30,
                          min_gap_pct: float = 0.1) -> list[dict]:
    """
    Find Fair Value Gaps in the last `lookback` candles.

    Args:
        df: pandas DataFrame with 'high', 'low', 'close' columns (chronological).
        lookback: how many recent candles to scan (default 30).
        min_gap_pct: minimum gap size as % of price; filters micro-noise.

    Returns: list of dicts ordered most-recent-first:
        {
            "type": "bullish" | "bearish",
            "top": float,       # upper bound of gap
            "bottom": float,    # lower bound of gap
            "candle_age": int,  # how many bars ago the gap was formed
            "gap_pct": float,   # gap size as % of price at formation
        }
    Only UNFILLED FVGs (price has not returned to fill them) are returned.
    """
    if df is None or len(df) < 3:
        return []

    n = len(df)
    start = max(0, n - lookback)
    fvgs: list[dict] = []
    highs = df["high"].values
    lows  = df["low"].values

    # Scan windows of 3 consecutive bars (i-2, i-1, i). The middle bar's
    # range is irrelevant — what matters is candle1's high vs candle3's low.
    for i in range(start + 2, n):
        c1_h = float(highs[i-2])
        c1_l = float(lows[i-2])
        c3_h = float(highs[i])
        c3_l = float(lows[i])

        # Bullish FVG: candle1.high < candle3.low (gap between them)
        if c1_h < c3_l:
            gap_size = c3_l - c1_h
            ref_price = (c1_h + c3_l) / 2
            gap_pct = (gap_size / ref_price) * 100 if ref_price > 0 else 0
            if gap_pct < min_gap_pct:
                continue
            # Check if filled: any subsequent low since formation went below c3_l
            filled = False
            if i + 1 < n:
                subsequent_lows = lows[i+1:]
                if len(subsequent_lows) > 0 and float(min(subsequent_lows)) <= c1_h:
                    filled = True
            if not filled:
                fvgs.append({
                    "type":       "bullish",
                    "top":        round(c3_l, 8),
                    "bottom":     round(c1_h, 8),
                    "candle_age": n - 1 - i,
                    "gap_pct":    round(gap_pct, 3),
                })

        # Bearish FVG: candle1.low > candle3.high
        if c1_l > c3_h:
            gap_size = c1_l - c3_h
            ref_price = (c1_l + c3_h) / 2
            gap_pct = (gap_size / ref_price) * 100 if ref_price > 0 else 0
            if gap_pct < min_gap_pct:
                continue
            filled = False
            if i + 1 < n:
                subsequent_highs = highs[i+1:]
                if len(subsequent_highs) > 0 and float(max(subsequent_highs)) >= c1_l:
                    filled = True
            if not filled:
                fvgs.append({
                    "type":       "bearish",
                    "top":        round(c1_l, 8),
                    "bottom":     round(c3_h, 8),
                    "candle_age": n - 1 - i,
                    "gap_pct":    round(gap_pct, 3),
                })

    # Most recent first
    fvgs.sort(key=lambda f: f["candle_age"])
    return fvgs


def nearest_fvg_signal(df, current_price: float, direction: str,
                        lookback: int = 30) -> dict:
    """
    Find the most relevant unfilled FVG for a trade in `direction` from
    `current_price` and return its signal interpretation.

    For Long entries:
      - Same-direction support: nearest unfilled bullish FVG BELOW price
        → +0.3 confluence (price has a high-probability bounce zone)
      - Opposing resistance: nearest unfilled bearish FVG ABOVE price
        → -0.3 (overhead resistance to fight through)

    For Short entries: mirror.

    Returns:
        {
            "weight":          float (-0.3, 0, or +0.3),
            "label":           str (human-readable),
            "support":         dict | None (nearest same-direction FVG),
            "resistance":      dict | None (nearest opposing FVG),
        }
    """
    out = {"weight": 0.0, "label": "", "support": None, "resistance": None}
    fvgs = detect_unfilled_fvgs(df, lookback=lookback)
    if not fvgs or not current_price:
        return out

    is_long = (direction or "").strip().lower() == "long"

    # For Longs: bullish FVG below = support, bearish FVG above = resistance
    # For Shorts: bearish FVG above = support (target), bullish FVG below = resistance
    if is_long:
        below_bullish = [f for f in fvgs
                          if f["type"] == "bullish" and f["top"] < current_price]
        above_bearish = [f for f in fvgs
                          if f["type"] == "bearish" and f["bottom"] > current_price]
        # nearest by price distance
        if below_bullish:
            below_bullish.sort(key=lambda f: current_price - f["top"])
            out["support"] = below_bullish[0]
        if above_bearish:
            above_bearish.sort(key=lambda f: f["bottom"] - current_price)
            out["resistance"] = above_bearish[0]
    else:
        # Short: same-direction support = bearish FVG above (resistance from
        # buyer side that price already moved past, now serves as resistance
        # holding for a continuation lower)
        above_bearish = [f for f in fvgs
                          if f["type"] == "bearish" and f["bottom"] > current_price]
        below_bullish = [f for f in fvgs
                          if f["type"] == "bullish" and f["top"] < current_price]
        if above_bearish:
            above_bearish.sort(key=lambda f: f["bottom"] - current_price)
            out["support"] = above_bearish[0]
        if below_bullish:
            below_bullish.sort(key=lambda f: current_price - f["top"])
            out["resistance"] = below_bullish[0]

    # Score: same-direction FVG present = +0.3, opposing resistance close = -0.3.
    # Both can fire simultaneously (net 0) when price is sandwiched.
    weight = 0.0
    label_parts = []
    if out["support"]:
        weight += 0.3
        dist_pct = abs(current_price - (out["support"]["top"]
                       if is_long else out["support"]["bottom"])) / current_price * 100
        label_parts.append(
            f"{'bullish' if is_long else 'bearish'} FVG "
            f"{'below' if is_long else 'above'} @ {dist_pct:.1f}% "
            f"(support, age {out['support']['candle_age']})"
        )
    if out["resistance"]:
        # Only penalise if resistance is close (<2x ATR-ish — use 3% as proxy)
        dist_pct = abs((out["resistance"]["bottom"] if is_long
                        else out["resistance"]["top"]) - current_price) / current_price * 100
        if dist_pct < 3.0:
            weight -= 0.3
            label_parts.append(
                f"{'bearish' if is_long else 'bullish'} FVG "
                f"{'above' if is_long else 'below'} @ {dist_pct:.1f}% "
                f"(resistance, age {out['resistance']['candle_age']})"
            )

    out["weight"] = round(weight, 2)
    out["label"]  = " · ".join(label_parts)
    return out
