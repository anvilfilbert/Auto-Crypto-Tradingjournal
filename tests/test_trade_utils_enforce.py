"""Tests for enforce_tp_floor, enforce_sl_floor, validate_direction_vs_levels."""
import sys
import os
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "chart_context" not in sys.modules:
    _cc = types.ModuleType("chart_context")
    _cc.get_chart_context = MagicMock(return_value={})
    _cc.get_binance_price = MagicMock(return_value=None)
    sys.modules["chart_context"] = _cc

from trade_utils import (
    enforce_tp_floor, enforce_sl_floor, validate_direction_vs_levels,
)


# ── enforce_tp_floor ─────────────────────────────────────────────────────────

class TestEnforceTpFloorLong:
    def test_correct_side_far_enough_passes_through(self):
        # Long, entry 100, ATR 2 → min TP1 = 102, min TP2 = 104
        # TP1 105 and TP2 110 are both far enough → unchanged
        tp1, tp2, notes = enforce_tp_floor(100, "Long", 105, 110, 2.0)
        assert tp1 == 105
        assert tp2 == 110
        assert notes == []

    def test_correct_side_too_tight_gets_bumped(self):
        # Long, entry 100, ATR 2 → TP1 must be >= 102
        # TP1 101 is too tight → bumped to 102
        tp1, tp2, notes = enforce_tp_floor(100, "Long", 101, 110, 2.0)
        assert tp1 == 102
        assert tp2 == 110
        assert any("TP1 bumped" in n for n in notes)

    def test_wrong_side_tp_repaired(self):
        # Long with TP1 *below* entry — clearly inverted
        tp1, tp2, notes = enforce_tp_floor(100, "Long", 95, 90, 2.0)
        assert tp1 == 102  # entry + 1× ATR
        assert tp2 > tp1
        assert notes  # produced at least one note

    def test_ladder_preserved_when_tp1_bumped(self):
        # TP1 bumped above original TP2 → TP2 must be pushed further
        tp1, tp2, notes = enforce_tp_floor(100, "Long", 99, 99.5, 2.0)
        assert tp1 == 102
        assert tp2 > tp1


class TestEnforceTpFloorShort:
    def test_correct_side_passes_through(self):
        # Short, entry 100, ATR 2 → min TP1 = 98, min TP2 = 96
        tp1, tp2, notes = enforce_tp_floor(100, "Short", 95, 90, 2.0)
        assert tp1 == 95
        assert tp2 == 90
        assert notes == []

    def test_wrong_side_tp_repaired(self):
        # Short with TP1 *above* entry — inverted (the QNT-style bug)
        tp1, tp2, notes = enforce_tp_floor(100, "Short", 105, 110, 2.0)
        assert tp1 == 98   # entry - 1× ATR
        assert tp2 < tp1   # TP2 must be below TP1 for Short
        assert notes


class TestEnforceTpFloorEdgeCases:
    def test_zero_entry_skips(self):
        # When entry is 0 the function early-returns unchanged
        tp1, tp2, notes = enforce_tp_floor(0, "Long", 105, 110, 2.0)
        assert tp1 == 105
        assert tp2 == 110
        assert notes == []

    def test_zero_atr_skips(self):
        # When ATR is 0 the function early-returns unchanged
        tp1, tp2, notes = enforce_tp_floor(100, "Long", 105, 110, 0)
        assert tp1 == 105
        assert tp2 == 110

    def test_non_numeric_returns_input(self):
        tp1, tp2, notes = enforce_tp_floor(100, "Long", "not-a-number", 110, 2.0)
        # Bad numeric coerces to 0; function still runs (TP1 stays 0)
        # The contract: don't crash
        assert isinstance(notes, list)


# ── enforce_sl_floor ─────────────────────────────────────────────────────────

class TestEnforceSlFloorLong:
    def test_correct_side_sane_distance_passes_through(self):
        # Long entry 100 ATR 2 → SL 98 is 1× ATR below — sane
        sl, notes = enforce_sl_floor(100, "Long", 98, 2.0)
        assert sl == 98
        assert notes == []

    def test_wrong_side_sl_repaired_to_default(self):
        # Long with SL *above* entry — inverted, SL must repair to entry - 1× ATR
        sl, notes = enforce_sl_floor(100, "Long", 102, 2.0)
        assert sl == 98
        assert any("Long" in n for n in notes)

    def test_too_tight_sl_bumped_out(self):
        # SL 99.5 is 0.25× ATR — below 0.5× floor → bumped to entry - 0.5× ATR = 99
        sl, notes = enforce_sl_floor(100, "Long", 99.5, 2.0)
        assert sl == 99
        assert notes

    def test_too_wide_sl_pulled_in(self):
        # SL 80 is 10× ATR — above 8× cap → pulled to entry - 8× ATR = 84
        sl, notes = enforce_sl_floor(100, "Long", 80, 2.0)
        assert sl == 84
        assert notes


class TestEnforceSlFloorShort:
    def test_correct_side_passes_through(self):
        # Short entry 100, SL 102 — correct side and sane distance
        sl, notes = enforce_sl_floor(100, "Short", 102, 2.0)
        assert sl == 102
        assert notes == []

    def test_wrong_side_sl_repaired(self):
        # Short with SL *below* entry (the QNT-style bug)
        sl, notes = enforce_sl_floor(100, "Short", 95, 2.0)
        assert sl == 102  # entry + 1× ATR
        assert any("Short" in n for n in notes)

    def test_missing_sl_defaults(self):
        # SL = 0 → fall back to entry ∓ 1× ATR on the correct side
        sl, notes = enforce_sl_floor(100, "Short", 0, 2.0)
        assert sl == 102
        assert any("missing" in n for n in notes)


class TestEnforceSlFloorEdgeCases:
    def test_zero_entry_skips(self):
        sl, notes = enforce_sl_floor(0, "Long", 95, 2.0)
        assert sl == 95
        assert notes == []

    def test_zero_atr_skips(self):
        sl, notes = enforce_sl_floor(100, "Long", 95, 0)
        assert sl == 95
        assert notes == []


# ── validate_direction_vs_levels ─────────────────────────────────────────────

class TestValidateDirection:
    def test_long_correct_geometry_ok(self):
        ok, why = validate_direction_vs_levels("Long", 100, 95, 105, 110)
        assert ok is True
        assert why == ""

    def test_short_correct_geometry_ok(self):
        ok, why = validate_direction_vs_levels("Short", 100, 105, 95, 90)
        assert ok is True

    def test_long_with_short_shaped_levels_dropped(self):
        # Long direction but SL above entry — clearly inverted
        ok, why = validate_direction_vs_levels("Long", 100, 105, 95, 90)
        assert ok is False
        assert "Long" in why

    def test_short_with_long_shaped_levels_dropped(self):
        # The QNT bug: Short direction, SL below entry, TPs above
        ok, why = validate_direction_vs_levels("Short", 74.2, 72.8, 76.8, 79.5)
        assert ok is False
        assert "Short" in why
        assert "sl" in why.lower()

    def test_long_tp1_at_entry_dropped(self):
        ok, why = validate_direction_vs_levels("Long", 100, 95, 100, 110)
        assert ok is False

    def test_short_tp2_above_entry_dropped(self):
        ok, why = validate_direction_vs_levels("Short", 100, 105, 95, 101)
        assert ok is False

    def test_zero_entry_dropped(self):
        ok, why = validate_direction_vs_levels("Long", 0, 95, 105)
        assert ok is False
        assert "entry" in why

    def test_missing_tp2_ignored(self):
        # tp2=0 is fine — only validate tp2 when present
        ok, why = validate_direction_vs_levels("Long", 100, 95, 105, 0)
        assert ok is True

    def test_non_numeric_dropped(self):
        ok, why = validate_direction_vs_levels("Long", "x", 95, 105)
        assert ok is False
