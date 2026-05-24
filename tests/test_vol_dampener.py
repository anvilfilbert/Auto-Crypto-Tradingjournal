"""Tests for risk_budget._vol_dampener (per-asset volatility-aware sizing)."""
import sys
import os
import types
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub heavy deps before importing risk_budget
for mod in ("chart_context", "ccxt", "pandas"):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)


def _load_dampener():
    try:
        from trading import risk_budget
        return risk_budget
    except ImportError as e:
        pytest.skip(f"risk_budget import failed: {e}")


class TestVolDampener:
    def setup_method(self):
        # Clear cache between tests for determinism
        rb = _load_dampener()
        rb._VOL_CACHE.clear()

    def test_returns_one_when_disabled(self, monkeypatch):
        rb = _load_dampener()
        monkeypatch.setattr(rb, "VOL_DAMPENER_ENABLED", False)
        mult, reason = rb._vol_dampener("BTCUSDT")
        assert mult == 1.0
        assert reason == ""

    def test_returns_one_when_atr_unavailable(self, monkeypatch):
        rb = _load_dampener()
        monkeypatch.setattr(rb, "VOL_DAMPENER_ENABLED", True)
        monkeypatch.setattr(rb, "_get_asset_atr_pct", lambda s: None)
        mult, reason = rb._vol_dampener("BTCUSDT")
        assert mult == 1.0
        assert "unavailable" in reason.lower() or reason == ""

    def test_no_dampening_when_atr_at_reference(self, monkeypatch):
        rb = _load_dampener()
        monkeypatch.setattr(rb, "VOL_DAMPENER_ENABLED", True)
        monkeypatch.setattr(rb, "VOL_REFERENCE_ATR_PCT", 3.0)
        monkeypatch.setattr(rb, "VOL_OUTLIER_RATIO", 1.5)
        monkeypatch.setattr(rb, "_get_asset_atr_pct", lambda s: 3.0)
        mult, _ = rb._vol_dampener("BTCUSDT")
        assert mult == 1.0

    def test_no_dampening_when_below_outlier_threshold(self, monkeypatch):
        rb = _load_dampener()
        monkeypatch.setattr(rb, "VOL_DAMPENER_ENABLED", True)
        monkeypatch.setattr(rb, "VOL_REFERENCE_ATR_PCT", 3.0)
        monkeypatch.setattr(rb, "VOL_OUTLIER_RATIO", 1.5)
        # ratio = 4.0 / 3.0 = 1.33 < 1.5 → no dampening
        monkeypatch.setattr(rb, "_get_asset_atr_pct", lambda s: 4.0)
        mult, _ = rb._vol_dampener("ETHUSDT")
        assert mult == 1.0

    def test_dampens_when_above_outlier_threshold(self, monkeypatch):
        rb = _load_dampener()
        monkeypatch.setattr(rb, "VOL_DAMPENER_ENABLED", True)
        monkeypatch.setattr(rb, "VOL_REFERENCE_ATR_PCT", 3.0)
        monkeypatch.setattr(rb, "VOL_OUTLIER_RATIO", 1.5)
        monkeypatch.setattr(rb, "VOL_DAMPENER_FLOOR", 0.5)
        # ratio = 6.0 / 3.0 = 2.0 > 1.5 → mult = 1.5 / 2.0 = 0.75
        monkeypatch.setattr(rb, "_get_asset_atr_pct", lambda s: 6.0)
        mult, reason = rb._vol_dampener("WILDUSDT")
        assert mult == 0.75
        assert "ratio" in reason
        assert "0.75" in reason

    def test_floor_caps_extreme_dampening(self, monkeypatch):
        rb = _load_dampener()
        monkeypatch.setattr(rb, "VOL_DAMPENER_ENABLED", True)
        monkeypatch.setattr(rb, "VOL_REFERENCE_ATR_PCT", 3.0)
        monkeypatch.setattr(rb, "VOL_OUTLIER_RATIO", 1.5)
        monkeypatch.setattr(rb, "VOL_DAMPENER_FLOOR", 0.5)
        # ratio = 12 / 3 = 4 → uncapped = 1.5/4 = 0.375; floored to 0.5
        monkeypatch.setattr(rb, "_get_asset_atr_pct", lambda s: 12.0)
        mult, _ = rb._vol_dampener("CRAZYUSDT")
        assert mult == 0.5

    def test_empty_symbol_returns_one(self, monkeypatch):
        rb = _load_dampener()
        monkeypatch.setattr(rb, "VOL_DAMPENER_ENABLED", True)
        # _get_asset_atr_pct("") returns None
        mult, _ = rb._vol_dampener("")
        assert mult == 1.0
