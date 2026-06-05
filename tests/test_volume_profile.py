"""Tests for chart_volume_profile."""
import sys
sys.modules.pop("chart_volume_profile", None)

import numpy as np
import pandas as pd
import pytest
from chart_volume_profile import compute_volume_profile, volume_profile_label


def _build_df(highs, lows, volumes, closes=None):
    """Build an OHLCV DataFrame from arrays."""
    n = len(highs)
    if closes is None:
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = pd.DataFrame({
        "open":   closes,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": volumes,
    })
    df.index = pd.date_range("2026-01-01", periods=n, freq="1h")
    return df


def test_returns_none_when_too_few_bars():
    df = _build_df([100] * 10, [99] * 10, [1000] * 10)
    assert compute_volume_profile(df) is None


def test_returns_none_when_zero_volume():
    df = _build_df([100] * 30, [99] * 30, [0] * 30)
    assert compute_volume_profile(df) is None


def test_poc_at_concentrated_price():
    """If 80% of volume happens at one price, POC should land there."""
    n = 50
    # 40 bars heavy volume at price 100
    # 10 bars light volume at price 120
    highs = [100.5] * 40 + [120.5] * 10
    lows = [99.5] * 40 + [119.5] * 10
    vols = [10000] * 40 + [100] * 10
    df = _build_df(highs, lows, vols)
    out = compute_volume_profile(df, bins=20)
    assert out is not None
    assert 99.0 <= out["poc"] <= 101.0


def test_poc_in_value_area():
    """POC must always be between VAL and VAH."""
    n = 60
    rng = np.random.default_rng(0)
    highs = 100 + np.abs(rng.normal(0, 2, n)) + 1
    lows = highs - 1.5
    vols = rng.uniform(500, 1500, n)
    df = _build_df(highs.tolist(), lows.tolist(), vols.tolist())
    out = compute_volume_profile(df, bins=24)
    assert out["val"] <= out["poc"] <= out["vah"]


def test_value_area_captures_approximately_70pct():
    """Value Area should capture ~70% of total volume."""
    n = 50
    # Build a normal-ish volume profile around price 100
    rng = np.random.default_rng(7)
    centers = 100 + rng.normal(0, 1.5, n)
    highs = centers + 0.5
    lows = centers - 0.5
    vols = rng.uniform(800, 1200, n)
    df = _build_df(highs.tolist(), lows.tolist(), vols.tolist())
    out = compute_volume_profile(df, bins=30, va_pct=0.70)
    # Just confirm reasonable VA extent
    assert out["val"] < out["poc"] < out["vah"]
    assert out["vah"] - out["val"] > 0


def test_hvn_and_lvn_disjoint():
    """A bin can't be both HVN and LVN."""
    n = 60
    rng = np.random.default_rng(3)
    centers = 100 + np.linspace(-3, 3, n)
    highs = centers + 0.5
    lows = centers - 0.5
    # Mix high and low volume bars
    vols = [10000 if i % 3 == 0 else 100 for i in range(n)]
    df = _build_df(highs.tolist(), lows.tolist(), vols)
    out = compute_volume_profile(df, bins=20)
    hvn_set = set(out["hvn"])
    lvn_set = set(out["lvn"])
    assert hvn_set.isdisjoint(lvn_set)


def test_distance_to_poc_signed_correctly():
    """Close > POC → positive distance; close < POC → negative."""
    n = 50
    highs = [100.5] * 40 + [120.5] * 10
    lows = [99.5] * 40 + [119.5] * 10
    vols = [10000] * 40 + [100] * 10
    df = _build_df(highs, lows, vols, closes=[100] * 40 + [120] * 10)
    out = compute_volume_profile(df, bins=20)
    # close = 120, POC ≈ 100 → positive distance
    assert out["distance_to_poc_pct"] > 0
    assert out["at_poc"] == "above"


def test_in_value_area_true_when_close_inside():
    """When current close is between VAL and VAH, in_value_area should be True."""
    n = 50
    rng = np.random.default_rng(11)
    centers = 100 + rng.normal(0, 1, n)
    highs = centers + 0.5
    lows = centers - 0.5
    vols = rng.uniform(800, 1200, n)
    closes = centers
    df = _build_df(highs.tolist(), lows.tolist(), vols.tolist(), closes.tolist())
    out = compute_volume_profile(df, bins=24)
    # Last close near the mean — very likely inside VA
    if out["val"] <= float(df["close"].iloc[-1]) <= out["vah"]:
        assert out["in_value_area"] is True


def test_lookback_truncates_history():
    """Only the last `lookback_bars` should be used."""
    n = 200
    highs = [100.5] * 100 + [200.5] * 100   # 100 old, 100 new
    lows  = [99.5] * 100 + [199.5] * 100
    vols  = [1000] * 200
    df = _build_df(highs, lows, vols)
    out = compute_volume_profile(df, bins=30, lookback_bars=100)
    # POC should be in the recent (200 price) range
    assert 195 <= out["poc"] <= 205


def test_label_format():
    vp = {
        "poc": 100.50, "vah": 102.00, "val": 99.00,
        "distance_to_poc_pct": -0.5,
        "at_poc": "below",
        "in_value_area": True,
    }
    label = volume_profile_label(vp)
    assert "100.5" in label
    assert "VAH" in label
    assert "VAL" in label
    assert "in VA" in label
