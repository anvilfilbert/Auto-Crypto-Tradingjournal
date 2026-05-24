"""Tests for Feature 12 — cross-indicator divergence aggregation."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load():
    try:
        from chart_divergence import (aggregate_divergences,
                                       composite_divergence_weight)
        return aggregate_divergences, composite_divergence_weight
    except ImportError as e:
        pytest.skip(f"chart_divergence import failed: {e}")


class TestAggregateDivergences:
    def test_no_indicators_returns_zero(self):
        agg, _ = _load()
        out = agg({})
        assert out["composite_weight"] == 0.0
        assert out["bullish_count"] == 0
        assert out["bearish_count"] == 0

    def test_single_bullish_indicator(self):
        agg, _ = _load()
        out = agg({
            "macd":      {"divergence": "bullish_regular"},
            "rsi":       {"divergence": ""},
            "stoch_rsi": {},
        })
        assert out["bullish_count"] == 1
        assert out["bearish_count"] == 0
        assert out["composite_weight"] == 0.1

    def test_three_bullish_indicators(self):
        agg, _ = _load()
        out = agg({
            "macd": {"divergence": "bullish_regular"},
            "rsi":  {"divergence": "bullish_regular"},
            "obv":  {"divergence": "bullish_regular"},
        })
        assert out["bullish_count"] == 3
        assert out["composite_weight"] == 0.3

    def test_four_bullish_capped_at_max(self):
        agg, _ = _load()
        out = agg({
            "macd":      {"divergence": "bullish_regular"},
            "rsi":       {"divergence": "bullish_regular"},
            "obv":       {"divergence": "bullish_regular"},
            "cmf":       {"divergence": "bullish_regular"},
            "stoch_rsi": {"divergence": "bullish_regular"},
        })
        # 5 bullish would be 0.5, but capped at MAX_COMPOSITE_WEIGHT=0.4
        assert out["composite_weight"] == 0.4

    def test_mixed_bullish_bearish_net(self):
        agg, _ = _load()
        out = agg({
            "macd": {"divergence": "bullish_regular"},
            "rsi":  {"divergence": "bullish_regular"},
            "obv":  {"divergence": "bearish_regular"},
        })
        # net = 2 - 1 = 1 → +0.1
        assert out["composite_weight"] == 0.1

    def test_equal_bullish_bearish_returns_zero(self):
        agg, _ = _load()
        out = agg({
            "macd": {"divergence": "bullish_regular"},
            "rsi":  {"divergence": "bearish_regular"},
        })
        assert out["composite_weight"] == 0.0

    def test_three_bearish_returns_negative(self):
        agg, _ = _load()
        out = agg({
            "macd": {"divergence": "bearish_regular"},
            "rsi":  {"divergence": "bearish_regular"},
            "mfi":  {"divergence": "bearish_regular"},
        })
        assert out["bearish_count"] == 3
        assert out["composite_weight"] == -0.3

    def test_hidden_divergences_not_counted(self):
        # Only bullish_regular / bearish_regular tracked — hidden divergences
        # are continuation signals (different intent)
        agg, _ = _load()
        out = agg({
            "macd": {"divergence": "bullish_hidden"},
            "rsi":  {"divergence": "bearish_hidden"},
        })
        assert out["composite_weight"] == 0.0

    def test_indicators_diverging_list_includes_keys(self):
        agg, _ = _load()
        out = agg({
            "macd": {"divergence": "bullish_regular"},
            "obv":  {"divergence": "bearish_regular"},
        })
        assert "macd:bull" in out["indicators_diverging"]
        assert "obv:bear" in out["indicators_diverging"]

    def test_non_dict_input_returns_zero(self):
        agg, _ = _load()
        assert agg(None)["composite_weight"] == 0.0
        assert agg("string")["composite_weight"] == 0.0


class TestCompositeWeight:
    def test_returns_tuple(self):
        _, fn = _load()
        w, label = fn({"macd": {"divergence": "bullish_regular"}})
        assert w == 0.1
        assert "bull" in label
