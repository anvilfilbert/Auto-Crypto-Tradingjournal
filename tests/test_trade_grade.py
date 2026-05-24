"""Tests for Feature 9 — Trade Grade (Elder A-trade normalization)."""
import sys
import os
import types
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub chart_context BEFORE importing trade_utils
cc = types.ModuleType("chart_context")
cc.get_chart_context = MagicMock(return_value={
    "4H": {"indicators": {"atr": {"value": 100.0, "pct": 2.0}}}
})
cc.get_binance_price = MagicMock(return_value=None)
sys.modules["chart_context"] = cc

from trade_utils import compute_trade_grade


class TestComputeTradeGrade:
    def test_long_winner_positive_grade(self):
        # entry 1000, exit 1500 → move = +500, channel = 4*100 = 400, grade = 500/400 = 1.25
        grade = compute_trade_grade("BTCUSDT", entry=1000, close_price=1500, direction="Long")
        assert grade == 1.25

    def test_long_loser_negative_grade(self):
        # entry 1000, exit 800 → move = -200 (Long), grade = -200/400 = -0.5
        grade = compute_trade_grade("BTCUSDT", entry=1000, close_price=800, direction="Long")
        assert grade == -0.5

    def test_short_winner_positive_grade(self):
        # entry 1000, exit 500 → move = +500 (Short reversed), grade = 500/400 = 1.25
        grade = compute_trade_grade("BTCUSDT", entry=1000, close_price=500, direction="Short")
        assert grade == 1.25

    def test_short_loser_negative_grade(self):
        # entry 1000, exit 1500 → move = -500 (Short reversed), grade = -1.25
        grade = compute_trade_grade("BTCUSDT", entry=1000, close_price=1500, direction="Short")
        assert grade == -1.25

    def test_zero_entry_returns_none(self):
        assert compute_trade_grade("BTC", 0, 1000, "Long") is None

    def test_zero_close_returns_none(self):
        assert compute_trade_grade("BTC", 1000, 0, "Long") is None

    def test_non_numeric_returns_none(self):
        assert compute_trade_grade("BTC", "abc", 1000, "Long") is None

    def test_breakeven_returns_zero(self):
        grade = compute_trade_grade("BTCUSDT", entry=1000, close_price=1000, direction="Long")
        assert grade == 0.0
