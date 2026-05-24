"""Tests for chart_confluence._climactic_volume_weight."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load():
    try:
        from chart_confluence import _climactic_volume_weight
        return _climactic_volume_weight
    except ImportError as e:
        pytest.skip(f"chart_confluence import failed: {e}")


class TestClimacticVolume:
    def test_no_climactic_when_volume_normal(self):
        w_fn = _load()
        # 1× avg volume — not climactic
        w, _ = w_fn({"ratio_to_avg": 1.0},
                     {"open": 100, "high": 102, "low": 99, "close": 101})
        assert w == 0.0

    def test_climactic_up_bar_with_upper_wick_top(self):
        w_fn = _load()
        # 3× avg volume, green bar with 50% upper wick = rejection at top
        # bar: open=100, close=101 (small green body), high=104 (big upper wick), low=100
        # upper_wick = (104-101)/4 = 75% → rejection
        w, label = w_fn({"ratio_to_avg": 3.0},
                         {"open": 100, "high": 104, "low": 100, "close": 101})
        assert w == -0.2  # bearish — top
        assert "top" in label.lower()

    def test_climactic_down_bar_with_lower_wick_bottom(self):
        w_fn = _load()
        # 3× avg vol, red bar with big lower wick
        # bar: open=100, close=99 (small red body), high=100, low=95
        # lower_wick = (99-95)/5 = 80% → rejection
        w, label = w_fn({"ratio_to_avg": 3.0},
                         {"open": 100, "high": 100, "low": 95, "close": 99})
        assert w == 0.2   # bullish — bottom
        assert "bottom" in label.lower()

    def test_climactic_without_rejection_wick_returns_zero(self):
        w_fn = _load()
        # 3× volume on a clean trend bar (small wick, big body) — not climactic reversal
        # green bar w/ small wick: open=100, close=104, high=104.5, low=99.5
        w, _ = w_fn({"ratio_to_avg": 3.0},
                     {"open": 100, "high": 104.5, "low": 99.5, "close": 104})
        assert w == 0.0  # no rejection

    def test_exhaustion_low_volume_no_directional_weight(self):
        w_fn = _load()
        # 0.3× volume = exhaustion — informational only, no weight
        w, label = w_fn({"ratio_to_avg": 0.3},
                         {"open": 100, "high": 102, "low": 99, "close": 101})
        assert w == 0.0
        assert "exhaustion" in label.lower()

    def test_missing_ratio_returns_zero(self):
        w_fn = _load()
        w, _ = w_fn({}, {"open": 100, "high": 101, "low": 99, "close": 100})
        assert w == 0.0

    def test_missing_candle_returns_zero(self):
        w_fn = _load()
        w, _ = w_fn({"ratio_to_avg": 3.0}, {})
        assert w == 0.0

    def test_zero_bar_range_returns_zero(self):
        # Degenerate doji bar with no range
        w_fn = _load()
        w, _ = w_fn({"ratio_to_avg": 3.0},
                     {"open": 100, "high": 100, "low": 100, "close": 100})
        assert w == 0.0

    def test_non_dict_inputs_return_zero(self):
        w_fn = _load()
        assert w_fn(None, None) == (0.0, "")
        assert w_fn("string", None) == (0.0, "")
