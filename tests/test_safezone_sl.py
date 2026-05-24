"""Tests for trade_utils.safezone_sl + _round_number_for."""
import sys
import os
import types
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "chart_context" not in sys.modules:
    cc = types.ModuleType("chart_context")
    cc.get_chart_context = MagicMock(return_value={})
    sys.modules["chart_context"] = cc

from trade_utils import safezone_sl, _round_number_for


class TestRoundNumberFor:
    def test_btc_magnitude(self):
        # $65,000 → step = 1000 → nearest 1000 = 65000
        assert _round_number_for(65000) == 65000

    def test_btc_off_round(self):
        # $65,234 → nearest 1000 = 65000
        assert _round_number_for(65234) == 65000

    def test_eth_magnitude(self):
        # $3,500 → step = 100 → nearest 100 = 3500
        assert _round_number_for(3500) == 3500

    def test_alt_magnitude(self):
        # $0.045 → magnitude=-2, step=10^-3 = 0.001
        # 0.045/0.001 = 45 → 45 * 0.001 = 0.045
        r = _round_number_for(0.045)
        assert r == 0.045

    def test_negative_price_returns_none(self):
        assert _round_number_for(-10) is None

    def test_zero_returns_none(self):
        assert _round_number_for(0) is None


class TestSafezoneSL:
    def test_long_sl_near_round_pushed_lower(self):
        # entry=65500, SL=65000 (exactly a round number), ATR=200
        # proximity = (65500-65000)/65500*100 = 0.76% > 0.5% threshold? Let's check
        # |65000 - 65000| / 65500 * 100 = 0.00% — way under 0.5% threshold
        sl, notes = safezone_sl(entry=65500, direction="Long",
                                  sl=65000, atr_4h=200)
        # Should be pushed: 65000 - 200*0.5 = 64900
        assert sl == 64900
        assert any("SafeZone" in n for n in notes)
        assert any("65000" in n or "round" in n.lower() for n in notes)

    def test_short_sl_near_round_pushed_higher(self):
        # entry=64500, SL=65000 (Short = SL above entry), ATR=200
        sl, notes = safezone_sl(entry=64500, direction="Short",
                                  sl=65000, atr_4h=200)
        # 65000 + 200*0.5 = 65100
        assert sl == 65100
        assert notes  # had adjustment

    def test_sl_not_near_round_no_change(self):
        # SL=63250, far from any round (63000 or 64000) at this magnitude
        # nearest round (magnitude=4, step=1000) of 63250 = 63000
        # |63250 - 63000| / 65500 * 100 = 0.38% < 0.5% → STILL within threshold
        # Use a more clearly off-round number:
        sl, notes = safezone_sl(entry=65500, direction="Long",
                                  sl=63800, atr_4h=200)
        # |63800 - 64000| / 65500 = 0.31% — under threshold, no change
        # Wait actually 63800 nearest 1000 = 64000, dist=200, 200/65500=0.30% < 0.5%
        # So this still triggers... let me pick a price farther from any round
        sl, notes = safezone_sl(entry=65500, direction="Long",
                                  sl=63500, atr_4h=200)
        # 63500 → nearest 1000 = 63000 OR 64000? round(63500/1000)*1000 = 64000
        # |63500 - 64000| / 65500 = 0.76% > 0.5% → no SafeZone adjustment
        assert sl == 63500
        assert notes == []

    def test_missing_data_returns_unchanged(self):
        assert safezone_sl(0, "Long", 65000, 200) == (65000, [])
        assert safezone_sl(65500, "Long", 0, 200) == (0, [])
        assert safezone_sl(65500, "Long", 65000, 0) == (65000, [])

    def test_non_numeric_returns_unchanged(self):
        sl, notes = safezone_sl("abc", "Long", 65000, 200)
        assert sl == 65000
        assert notes == []
