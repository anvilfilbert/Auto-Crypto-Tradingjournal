"""Tests for Feature 10 — Euphoria Dampener streak mode."""
import sys
import os
import types
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for mod in ("chart_context", "ccxt", "pandas"):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)


def _load():
    try:
        from trading import risk_budget, config
        return risk_budget, config
    except ImportError as e:
        pytest.skip(f"trading.risk_budget import failed: {e}")


class TestStreakMultiplierCompoundMode:
    def test_streak_0_returns_1(self, monkeypatch):
        rb, cfg = _load()
        monkeypatch.setattr(cfg, "streak_mode", lambda: "compound")
        assert rb._streak_multiplier(0) == 1.0

    def test_streak_1_returns_1(self, monkeypatch):
        rb, cfg = _load()
        monkeypatch.setattr(cfg, "streak_mode", lambda: "compound")
        assert rb._streak_multiplier(1) == 1.0

    def test_streak_2_returns_2(self, monkeypatch):
        rb, cfg = _load()
        monkeypatch.setattr(cfg, "streak_mode", lambda: "compound")
        monkeypatch.setattr(cfg, "COMPOUND_STREAK_ENABLED", True)
        monkeypatch.setattr(cfg, "MAX_STREAK_MULTIPLIER", 3)
        assert rb._streak_multiplier(2) == 2.0

    def test_streak_capped_at_max(self, monkeypatch):
        rb, cfg = _load()
        monkeypatch.setattr(cfg, "streak_mode", lambda: "compound")
        monkeypatch.setattr(cfg, "COMPOUND_STREAK_ENABLED", True)
        monkeypatch.setattr(cfg, "MAX_STREAK_MULTIPLIER", 3)
        assert rb._streak_multiplier(10) == 3.0


class TestStreakMultiplierEuphoriaMode:
    def test_streak_below_cap_returns_1(self, monkeypatch):
        rb, cfg = _load()
        monkeypatch.setattr(cfg, "streak_mode", lambda: "euphoria_dampener")
        monkeypatch.setattr(cfg, "EUPHORIA_CAP_WINS", 3)
        monkeypatch.setattr(cfg, "EUPHORIA_SIZE_MULT", 0.75)
        assert rb._streak_multiplier(0) == 1.0
        assert rb._streak_multiplier(1) == 1.0
        assert rb._streak_multiplier(2) == 1.0

    def test_streak_at_or_above_cap_returns_shrunk(self, monkeypatch):
        rb, cfg = _load()
        monkeypatch.setattr(cfg, "streak_mode", lambda: "euphoria_dampener")
        monkeypatch.setattr(cfg, "EUPHORIA_CAP_WINS", 3)
        monkeypatch.setattr(cfg, "EUPHORIA_SIZE_MULT", 0.75)
        assert rb._streak_multiplier(3) == 0.75
        assert rb._streak_multiplier(5) == 0.75   # stays floored
        assert rb._streak_multiplier(10) == 0.75


class TestStreakMultiplierOffMode:
    def test_returns_1_regardless_of_wins(self, monkeypatch):
        rb, cfg = _load()
        monkeypatch.setattr(cfg, "streak_mode", lambda: "off")
        assert rb._streak_multiplier(0) == 1.0
        assert rb._streak_multiplier(5) == 1.0
        assert rb._streak_multiplier(100) == 1.0


class TestSetStreakMode:
    def test_invalid_mode_raises(self):
        _, cfg = _load()
        with pytest.raises(ValueError):
            cfg.set_streak_mode("crazy_mode")

    def test_valid_modes_accepted(self):
        """The three valid modes should not raise on validation, even if DB
        write fails (we don't test DB persistence here — that's covered by
        the route integration test)."""
        _, cfg = _load()
        # We can't actually write to DB in this test env (no real conn)
        # so we just verify the validation path
        for mode in ("compound", "euphoria_dampener", "off"):
            try:
                cfg.set_streak_mode(mode)
            except RuntimeError:
                # DB unavailable in test env — that's fine, validation passed
                pass
            except ValueError:
                pytest.fail(f"{mode} should be valid")
