"""Tests for chart_vwap — session-anchored VWAP + bands."""
import sys
sys.modules.pop("chart_vwap", None)

import numpy as np
import pandas as pd
import pytest
from chart_vwap import compute_vwap, vwap_label


def _build_df(prices, volumes=None, start="2026-01-01 00:00", freq="1h"):
    """Build a minimal OHLCV df with a DatetimeIndex (UTC)."""
    closes = np.asarray(prices, dtype=float)
    vols = np.asarray(volumes if volumes is not None
                       else np.ones_like(closes) * 1000.0, dtype=float)
    df = pd.DataFrame({
        "open":   closes,
        "high":   closes + 0.5,
        "low":    closes - 0.5,
        "close":  closes,
        "volume": vols,
    })
    df.index = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    return df


def test_returns_none_when_empty():
    assert compute_vwap(pd.DataFrame()) is None


def test_returns_none_when_missing_columns():
    df = pd.DataFrame({"close": [100.0] * 5})
    df.index = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    assert compute_vwap(df) is None


def test_returns_none_when_zero_volume():
    df = _build_df([100.0] * 24, volumes=[0.0] * 24)
    assert compute_vwap(df) is None


def test_vwap_equals_constant_price():
    """When price is constant, VWAP equals that price; bands collapse to zero spread."""
    df = _build_df([100.0] * 24)
    out = compute_vwap(df)
    assert out is not None
    assert out["vwap"] == pytest.approx(100.0, abs=0.01)
    assert out["upper_1"] == pytest.approx(100.0, abs=0.01)
    assert out["lower_1"] == pytest.approx(100.0, abs=0.01)


def test_band_ordering_2sigma_outside_1sigma():
    """Bands must be ordered: lower_2 < lower_1 < vwap < upper_1 < upper_2."""
    closes = np.linspace(100, 110, 24)
    df = _build_df(closes)
    out = compute_vwap(df)
    assert out["lower_2"] < out["lower_1"] < out["vwap"] < out["upper_1"] < out["upper_2"]


def test_position_classification_above_vwap():
    """A late-session price spike should land above VWAP."""
    closes = list(np.linspace(100, 105, 23)) + [200.0]
    df = _build_df(closes)
    out = compute_vwap(df)
    assert out["position"] in ("above_vwap", "above_1sigma", "above_2sigma")
    assert out["distance_pct"] > 0


def test_position_classification_below_vwap():
    closes = list(np.linspace(100, 105, 23)) + [50.0]
    df = _build_df(closes)
    out = compute_vwap(df)
    assert out["position"] in ("below_vwap", "below_1sigma", "below_2sigma")
    assert out["distance_pct"] < 0


def test_session_anchored_resets_at_utc_midnight():
    """VWAP should only include current-session bars, not yesterday's."""
    # Build 48h: yesterday flat at 100, today flat at 200.
    yesterday = pd.date_range("2026-01-01 00:00", periods=24, freq="1h", tz="UTC")
    today     = pd.date_range("2026-01-02 00:00", periods=24, freq="1h", tz="UTC")
    idx = yesterday.append(today)
    prices = np.array([100.0] * 24 + [200.0] * 24)
    df = pd.DataFrame({
        "open": prices, "high": prices + 0.5, "low": prices - 0.5,
        "close": prices, "volume": np.ones(48) * 1000,
    }, index=idx)
    out = compute_vwap(df, anchored="session")
    # Current session is today (200), so VWAP should be ~200, not weighted by 100s
    assert out["vwap"] == pytest.approx(200.0, abs=0.5)
    assert out["session_bars"] == 24


def test_rolling_mode_no_reset():
    """In rolling mode, all bars contribute regardless of session boundary."""
    yesterday = pd.date_range("2026-01-01 00:00", periods=24, freq="1h", tz="UTC")
    today     = pd.date_range("2026-01-02 00:00", periods=24, freq="1h", tz="UTC")
    idx = yesterday.append(today)
    prices = np.array([100.0] * 24 + [200.0] * 24)
    df = pd.DataFrame({
        "open": prices, "high": prices + 0.5, "low": prices - 0.5,
        "close": prices, "volume": np.ones(48) * 1000,
    }, index=idx)
    out = compute_vwap(df, anchored="rolling")
    # Equal volume on each, so VWAP = average of 100s and 200s ≈ 150
    assert out["vwap"] == pytest.approx(150.0, abs=1.0)


def test_distance_pct_signed_correctly():
    df = _build_df(list(np.linspace(100, 105, 23)) + [110.0])
    out = compute_vwap(df)
    assert out["distance_pct"] > 0


def test_label_format():
    label = vwap_label(0.42, "above_1sigma")
    assert "+0.42%" in label
    assert "above 1σ" in label

    label = vwap_label(-1.30, "below_2sigma")
    assert "-1.30%" in label
    assert "below 2σ" in label


def test_volume_weighting_pulls_vwap():
    """Heavier-volume bars should pull VWAP toward their price."""
    closes = [100.0, 100.0, 100.0, 200.0]  # last bar at 200
    light_vol = compute_vwap(_build_df(closes, volumes=[1, 1, 1, 1]))
    heavy_vol = compute_vwap(_build_df(closes, volumes=[1, 1, 1, 100]))
    # Heavy volume on the 200 bar should push VWAP higher
    assert heavy_vol["vwap"] > light_vol["vwap"]
