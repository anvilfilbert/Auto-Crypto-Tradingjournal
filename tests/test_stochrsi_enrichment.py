"""Tests for the 2026-05-30 Stoch RSI enrichment in chart_indicators.

Validates:
  - Backward-compat keys (k, d, signal) still present
  - New keys (k_prev, d_prev, crossover, regime, failure_swing) added
  - Crossover detection logic
  - Failure-swing detection mirrors chart_rsi pattern but with 20/80 boundaries
"""
# Evict conftest's MagicMock stub of chart_indicators — we need the real module.
import sys
sys.modules.pop("chart_indicators", None)
sys.modules.pop("pandas_ta", None)

import numpy as np
import pandas as pd
import pytest

# Skip whole module if pandas-ta isn't installed (e.g., Python 3.14 + numba
# build failure on Mac dev). Pi has pandas-ta and runs everything end-to-end.
pandas_ta = pytest.importorskip("pandas_ta")

from chart_indicators import compute_stochrsi, _detect_stochrsi_failure_swing


def _synthetic_ohlcv(close_pattern, base=100.0):
    """Build a minimal OHLCV df from a list of close prices."""
    closes = np.asarray(close_pattern, dtype=float)
    df = pd.DataFrame({
        "open":   closes,
        "high":   closes + 0.5,
        "low":    closes - 0.5,
        "close":  closes,
        "volume": np.ones_like(closes) * 1000.0,
    })
    df.index = pd.date_range("2026-01-01", periods=len(closes), freq="1h")
    return df


def test_returns_none_when_too_short():
    df = _synthetic_ohlcv([100.0] * 10)
    assert compute_stochrsi(df) is None


def test_all_keys_present_on_realistic_input():
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0, 1, 100))
    df = _synthetic_ohlcv(closes)
    out = compute_stochrsi(df)
    assert out is not None
    # Backward-compat
    assert "k" in out
    assert "d" in out
    assert "signal" in out
    # New enrichment
    assert "k_prev" in out
    assert "d_prev" in out
    assert "crossover" in out
    assert "regime" in out
    assert "failure_swing" in out


def test_regime_is_above_or_below_50():
    rng = np.random.default_rng(0)
    closes = 100 + np.cumsum(rng.normal(0, 1, 100))
    df = _synthetic_ohlcv(closes)
    out = compute_stochrsi(df)
    assert out["regime"] in ("above_50", "below_50")


def test_crossover_values_are_legal():
    rng = np.random.default_rng(1)
    closes = 100 + np.cumsum(rng.normal(0, 1, 100))
    df = _synthetic_ohlcv(closes)
    out = compute_stochrsi(df)
    assert out["crossover"] in ("bullish", "bearish", "none")


def test_failure_swing_bullish_pattern():
    """Synthetic K-series that should trigger bullish failure swing."""
    # K dips to 10 (below 20), recovers to 40, dips to 25 (above first low) within 5 bars
    pattern = [60, 50, 40, 30, 10, 30, 45, 35, 25]
    s = pd.Series(pattern, dtype=float)
    result = _detect_stochrsi_failure_swing(s)
    assert result == "bullish"


def test_failure_swing_bearish_pattern():
    """K rises to 90, falls to 50, rises to 75 (below first high)."""
    pattern = [30, 40, 60, 80, 90, 70, 55, 65, 75]
    s = pd.Series(pattern, dtype=float)
    result = _detect_stochrsi_failure_swing(s)
    assert result == "bearish"


def test_failure_swing_returns_none_when_no_pattern():
    """Steady trending K with no extreme rejections."""
    s = pd.Series(list(range(20, 60)), dtype=float)
    assert _detect_stochrsi_failure_swing(s) is None


def test_failure_swing_returns_none_when_second_low_below_first():
    """If second dip goes lower than first, NOT a failure swing — it's a continuation."""
    pattern = [60, 50, 40, 30, 15, 30, 45, 35, 10]  # second dip lower
    s = pd.Series(pattern, dtype=float)
    assert _detect_stochrsi_failure_swing(s) is None


def test_failure_swing_handles_short_series():
    s = pd.Series([50.0, 60.0], dtype=float)
    assert _detect_stochrsi_failure_swing(s) is None


def test_signal_string_includes_zone_and_extras():
    """When crossover or failure_swing fires, the signal string mentions them."""
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0, 2, 200))
    df = _synthetic_ohlcv(closes)
    out = compute_stochrsi(df)
    assert out is not None
    # Signal always contains a zone descriptor
    assert any(z in out["signal"] for z in ("overbought", "oversold", "neutral"))
    # If crossover fires, signal should mention it
    if out["crossover"] != "none":
        assert "cross" in out["signal"]


def test_k_prev_d_prev_are_numeric():
    rng = np.random.default_rng(11)
    closes = 100 + np.cumsum(rng.normal(0, 1, 80))
    df = _synthetic_ohlcv(closes)
    out = compute_stochrsi(df)
    assert isinstance(out["k_prev"], float)
    assert isinstance(out["d_prev"], float)
    assert 0 <= out["k_prev"] <= 100
    assert 0 <= out["d_prev"] <= 100
