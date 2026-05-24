"""
chart_divergence.py — Feature 12 cross-indicator divergence aggregation.

Counts how many of N candidate indicators show price-vs-indicator divergence
in the same direction, returning a single composite weight that's more
robust than any individual divergence signal.

Indicators checked (when computed in compute_all_indicators):
  - MACD histogram
  - RSI
  - Stochastic RSI
  - MFI (Money Flow Index)
  - OBV (On-Balance Volume)
  - CMF (Chaikin Money Flow)

Aggregation:
  N_bullish = count of indicators showing bullish_regular divergence
  N_bearish = count of indicators showing bearish_regular divergence
  weight = sign(N_bullish - N_bearish) × min(0.4, 0.1 × |N_bullish - N_bearish|)

  i.e. 1 indicator agreeing = ±0.1; 2 = ±0.2; 3+ = ±0.3 (capped at ±0.4).

The composite is additive to the 15-signal base — it does NOT replace
existing single-indicator divergence signals (RSI failure swing is still
counted separately in chart_rsi.py). The point is to surface multi-indicator
AGREEMENT.
"""

import logging

logger = logging.getLogger(__name__)

MAX_COMPOSITE_WEIGHT = 0.4
WEIGHT_PER_INDICATOR = 0.1


def aggregate_divergences(inds: dict) -> dict:
    """
    Inspect the indicator dict (output of chart_indicators.compute_all_indicators)
    and tally per-indicator divergences.

    Args:
      inds: dict with sub-dicts for each indicator. We look for these keys:
        - macd, rsi, stoch_rsi, mfi (already standard)
        - obv, cmf (Phase 4 additions)

      Each sub-dict can optionally provide a "divergence" key with one of
      our four labels (bullish_regular, bearish_regular, bullish_hidden,
      bearish_hidden). If absent, we skip that indicator.

    Returns:
      {
        "bullish_count": int,
        "bearish_count": int,
        "indicators_checked": int,
        "indicators_diverging": list[str],
        "composite_weight": float,
        "label": str,
      }
    """
    if not isinstance(inds, dict):
        return _empty()

    checked = 0
    bullish, bearish = 0, 0
    diverging = []

    for key in ("macd", "rsi", "stoch_rsi", "mfi", "obv", "cmf"):
        sub = inds.get(key)
        if not isinstance(sub, dict):
            continue
        checked += 1
        div_label = (sub.get("divergence") or "").lower()
        if "bullish_regular" in div_label:
            bullish += 1
            diverging.append(f"{key}:bull")
        elif "bearish_regular" in div_label:
            bearish += 1
            diverging.append(f"{key}:bear")

    net = bullish - bearish
    if net == 0:
        # Equal bullish/bearish counts cancel out — informational but no weight
        return {
            "bullish_count":         bullish,
            "bearish_count":         bearish,
            "indicators_checked":    checked,
            "indicators_diverging":  diverging,
            "composite_weight":      0.0,
            "label":                 (f"divergence agg: {bullish} bull / {bearish} bear "
                                       f"— cancel out") if (bullish or bearish) else "",
        }

    magnitude = min(MAX_COMPOSITE_WEIGHT, abs(net) * WEIGHT_PER_INDICATOR)
    weight    = magnitude if net > 0 else -magnitude

    return {
        "bullish_count":         bullish,
        "bearish_count":         bearish,
        "indicators_checked":    checked,
        "indicators_diverging":  diverging,
        "composite_weight":      round(weight, 3),
        "label": (f"divergence agg: {bullish} bull / {bearish} bear of "
                   f"{checked} indicators → {weight:+.2f}"),
    }


def _empty(checked: int = 0) -> dict:
    return {
        "bullish_count":         0,
        "bearish_count":         0,
        "indicators_checked":    checked,
        "indicators_diverging":  [],
        "composite_weight":      0.0,
        "label":                 "",
    }


def composite_divergence_weight(inds: dict) -> tuple[float, str]:
    """Convenience: returns (weight, label) — same pattern as other confluence weights."""
    out = aggregate_divergences(inds)
    return out["composite_weight"], out["label"]
