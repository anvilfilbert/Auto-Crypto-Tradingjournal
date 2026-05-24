"""Tests for Feature 21 — Wyckoff phase classification."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load():
    try:
        from chart_confluence import classify_wyckoff_phase
        return classify_wyckoff_phase
    except ImportError as e:
        pytest.skip(f"chart_confluence import failed: {e}")


class TestClassifyWyckoffPhase:
    def test_markup_when_trending_up(self):
        fn = _load()
        out = fn({"label": "equilibrium"}, {"value": 30}, {"trend": "bullish"})
        assert out["phase"] == "markup"
        assert "long" in out["hint"].lower()

    def test_markdown_when_trending_down(self):
        fn = _load()
        out = fn({"label": "equilibrium"}, {"value": 30}, {"trend": "bearish"})
        assert out["phase"] == "markdown"
        assert "short" in out["hint"].lower()

    def test_accumulation_when_chop_at_discount(self):
        fn = _load()
        out = fn({"label": "discount"}, {"value": 15}, {"trend": "neutral"})
        assert out["phase"] == "accumulation"
        assert "long" in out["hint"].lower()

    def test_distribution_when_chop_at_premium(self):
        fn = _load()
        out = fn({"label": "premium"}, {"value": 15}, {"trend": "neutral"})
        assert out["phase"] == "distribution"
        assert "short" in out["hint"].lower()

    def test_trading_range_when_chop_at_equilibrium(self):
        fn = _load()
        out = fn({"label": "equilibrium"}, {"value": 15}, {"trend": "neutral"})
        assert out["phase"] == "trading_range"
        assert "no clear bias" in out["hint"].lower()

    def test_transitional_for_mid_adx(self):
        fn = _load()
        out = fn({"label": "equilibrium"}, {"value": 22}, {"trend": "neutral"})
        assert out["phase"] == "transitional"

    def test_missing_range_info_returns_unknown(self):
        fn = _load()
        out = fn({}, {"value": 30}, {"trend": "bullish"})
        assert out["phase"] == "unknown"

    def test_none_inputs_dont_crash(self):
        fn = _load()
        out = fn(None, None, None)
        assert out["phase"] == "unknown"

    def test_missing_adx_uses_zero(self):
        fn = _load()
        # adx=0 → should classify as accumulation (discount + low ADX)
        out = fn({"label": "discount"}, {}, {"trend": "neutral"})
        assert out["phase"] == "accumulation"
