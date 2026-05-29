"""Tests for the 2026-05-30 Supertrend addition to chart_indicators."""
import sys
sys.modules.pop("chart_indicators", None)
sys.modules.pop("pandas_ta", None)

import numpy as np
import pandas as pd
import pytest

pandas_ta = pytest.importorskip("pandas_ta")

from chart_indicators import compute_supertrend


def _build_df(closes, base=100.0):
    closes = np.asarray(closes, dtype=float)
    df = pd.DataFrame({
        "open":   closes,
        "high":   closes + 0.5,
        "low":    closes - 0.5,
        "close":  closes,
        "volume": np.ones_like(closes) * 1000.0,
    })
    df.index = pd.date_range("2026-01-01", periods=len(closes), freq="1h")
    return df


def test_returns_none_on_short_data():
    df = _build_df([100.0] * 5)
    assert compute_supertrend(df) is None


def test_returns_dict_with_keys_on_realistic_input():
    rng = np.random.default_rng(0)
    closes = 100 + np.cumsum(rng.normal(0, 1, 100))
    df = _build_df(closes)
    out = compute_supertrend(df)
    assert out is not None
    for k in ("direction", "supertrend_value", "flip_bars_ago", "signal"):
        assert k in out


def test_direction_is_plus_or_minus_one():
    rng = np.random.default_rng(1)
    closes = 100 + np.cumsum(rng.normal(0, 1, 100))
    df = _build_df(closes)
    out = compute_supertrend(df)
    assert out["direction"] in (1, -1)


def test_flip_bars_ago_is_non_negative():
    rng = np.random.default_rng(2)
    closes = 100 + np.cumsum(rng.normal(0, 1, 80))
    df = _build_df(closes)
    out = compute_supertrend(df)
    assert out["flip_bars_ago"] >= 0


def test_signal_label_matches_direction():
    rng = np.random.default_rng(3)
    closes = 100 + np.cumsum(rng.normal(0, 1, 80))
    df = _build_df(closes)
    out = compute_supertrend(df)
    if out["flip_bars_ago"] == 0:
        assert out["signal"] in ("flip_bullish", "flip_bearish")
    else:
        if out["direction"] > 0:
            assert out["signal"] == "uptrend"
        else:
            assert out["signal"] == "downtrend"


def test_strong_uptrend_yields_positive_direction():
    """Monotonic up close → direction should converge to +1 within ~30 bars."""
    closes = list(np.linspace(100, 130, 60))
    df = _build_df(closes)
    out = compute_supertrend(df)
    assert out["direction"] == 1


def test_strong_downtrend_yields_negative_direction():
    closes = list(np.linspace(100, 70, 60))
    df = _build_df(closes)
    out = compute_supertrend(df)
    assert out["direction"] == -1
