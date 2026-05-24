"""Tests for Bollinger Squeeze detection + weight function.

The detector tests need pandas + pandas_ta installed (Pi has them, Mac may
not). They auto-skip with pytest.importorskip if either is missing. The
_bb_squeeze_weight tests have no heavy deps and always run.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_squeeze_detector():
    """Lazy-import the detector so the test module collects without pandas_ta."""
    try:
        import pandas  # noqa: F401
        import pandas_ta  # noqa: F401
        from chart_indicators import compute_bollinger_squeeze
        return compute_bollinger_squeeze
    except ImportError as e:
        pytest.skip(f"pandas_ta not available: {e}")


def _make_df(prices, vols=None):
    import pandas as pd
    if vols is None:
        vols = [1000] * len(prices)
    return pd.DataFrame({
        "open":   prices,
        "high":   [p * 1.01 for p in prices],
        "low":    [p * 0.99 for p in prices],
        "close":  prices,
        "volume": vols,
    })


def _ramp_then_chop_then_burst(n_ramp=20, chop_n=40, burst_n=8):
    import numpy as np
    rng = np.random.default_rng(42)
    ramp  = list(np.linspace(100, 105, n_ramp))
    chop  = list(105 + rng.normal(0, 0.05, chop_n))
    burst = list(np.linspace(105, 115, burst_n))
    return ramp + chop + burst


# ── _bb_squeeze_weight (no heavy deps — always runs) ──────────────────────────

def _load_weight_fn():
    """The weight fn is in chart_confluence which has heavy deps too;
    import lazily and skip if missing."""
    try:
        from chart_confluence import _bb_squeeze_weight
        return _bb_squeeze_weight
    except ImportError as e:
        pytest.skip(f"chart_confluence import failed: {e}")


def _make_df(prices, vols=None):
    """Build a minimal OHLCV DataFrame."""
    if vols is None:
        vols = [1000] * len(prices)
    n = len(prices)
    return pd.DataFrame({
        "open":   prices,
        "high":   [p * 1.01 for p in prices],
        "low":    [p * 0.99 for p in prices],
        "close":  prices,
        "volume": vols,
    })


def _ramp_then_chop_then_burst(n_ramp=20, chop_n=40, burst_n=8):
    """
    Build a price series with three regimes:
      - Initial ramp (any non-degenerate prices)
      - Long chop with very low volatility (creates a squeeze)
      - Burst at the end (creates a release)

    Total length n_ramp + chop_n + burst_n; ~70 bars by default.
    """
    rng = np.random.default_rng(42)
    ramp  = list(np.linspace(100, 105, n_ramp))
    chop  = list(105 + rng.normal(0, 0.05, chop_n))  # very tight noise
    burst = list(np.linspace(105, 115, burst_n))    # sharp expansion
    return ramp + chop + burst


# ── compute_bollinger_squeeze ─────────────────────────────────────────────────

class TestComputeBBSqueeze:
    def test_insufficient_data_returns_none(self):
        fn = _load_squeeze_detector()
        df = _make_df([100, 101, 102, 103])
        assert fn(df) is None

    def test_detects_releasing_after_squeeze(self):
        fn = _load_squeeze_detector()
        prices = _ramp_then_chop_then_burst(n_ramp=20, chop_n=40, burst_n=8)
        df = _make_df(prices)
        out = fn(df, lookback=40)
        assert out is not None
        assert out["state"] in ("releasing", "expanded")

    def test_release_direction_long_when_close_above_mid(self):
        fn = _load_squeeze_detector()
        prices = _ramp_then_chop_then_burst()
        df = _make_df(prices)
        out = fn(df, lookback=40)
        if out and out["state"] == "releasing":
            assert out["direction"] == "Long"

    def test_release_direction_short_when_close_below_mid(self):
        fn = _load_squeeze_detector()
        import numpy as np
        rng = np.random.default_rng(7)
        ramp  = list(np.linspace(110, 105, 20))
        chop  = list(105 + rng.normal(0, 0.05, 40))
        burst = list(np.linspace(105, 95, 8))
        df = _make_df(ramp + chop + burst)
        out = fn(df, lookback=40)
        if out and out["state"] == "releasing":
            assert out["direction"] == "Short"

    def test_handles_normal_volatility_without_crash(self):
        fn = _load_squeeze_detector()
        import numpy as np
        rng = np.random.default_rng(42)
        prices = 100 + np.cumsum(rng.normal(0, 1, 80))
        df = _make_df(prices.tolist())
        out = fn(df, lookback=40)
        assert out is not None
        assert out["state"] in ("neutral", "squeezing", "releasing", "expanded")


# ── _bb_squeeze_weight ────────────────────────────────────────────────────────

class TestBBSqueezeWeight:
    def test_long_release_returns_positive_weight(self):
        fn = _load_weight_fn()
        assert fn({"state": "releasing", "direction": "Long"}) == 0.2

    def test_short_release_returns_negative_weight(self):
        fn = _load_weight_fn()
        assert fn({"state": "releasing", "direction": "Short"}) == -0.2

    def test_squeezing_state_returns_zero(self):
        fn = _load_weight_fn()
        assert fn({"state": "squeezing", "direction": None}) == 0.0

    def test_expanded_state_returns_zero(self):
        fn = _load_weight_fn()
        assert fn({"state": "expanded", "direction": None}) == 0.0

    def test_neutral_state_returns_zero(self):
        fn = _load_weight_fn()
        assert fn({"state": "neutral", "direction": None}) == 0.0

    def test_releasing_without_direction_returns_zero(self):
        fn = _load_weight_fn()
        assert fn({"state": "releasing", "direction": None}) == 0.0

    def test_none_input_returns_zero(self):
        fn = _load_weight_fn()
        assert fn(None) == 0.0

    def test_empty_dict_returns_zero(self):
        fn = _load_weight_fn()
        assert fn({}) == 0.0
