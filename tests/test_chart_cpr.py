"""Tests for chart_cpr — Central Pivot Range detection + alignment weights."""
import sys
import os
import types
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub pandas + ccxt deps before importing chart_cpr (chart_cpr itself has no
# heavy deps but ensures parallel-test compatibility)
if "pandas" not in sys.modules:
    sys.modules["pandas"] = types.ModuleType("pandas")

from chart_cpr import (
    compute_cpr, compute_cpr_from_df,
    two_day_relationship, cpr_alignment_weight,
    cpr_day_type, CPR_NARROW_PCT, CPR_WIDE_PCT,
)


# ── compute_cpr ───────────────────────────────────────────────────────────────

class TestComputeCPR:
    def test_basic_computation(self):
        # H=110, L=90, C=100 → P=100, BC=100, TC=100 (degenerate symmetric case)
        result = compute_cpr(110, 90, 100)
        assert result["pivot"] == 100.0
        assert result["bc"] == 100.0
        assert result["tc"] == 100.0

    def test_close_above_midpoint_yields_higher_pivot(self):
        # H=110, L=90, C=105 → P = (110+90+105)/3 = 101.667
        # BC = (110+90)/2 = 100, TC = 2*P - BC = 103.333
        result = compute_cpr(110, 90, 105)
        assert abs(result["pivot"] - 101.667) < 0.01
        assert result["bc"] == 100.0
        assert abs(result["tc"] - 103.333) < 0.01

    def test_close_below_midpoint_yields_lower_tc(self):
        # H=110, L=90, C=95 → P = (110+90+95)/3 = 98.333
        result = compute_cpr(110, 90, 95)
        assert result["pivot"] < 100
        # TC should be less than BC when close is below midpoint
        assert result["tc"] < result["bc"]

    def test_width_is_always_positive(self):
        # Even when TC < BC, width should be |TC - BC|
        result = compute_cpr(110, 90, 95)
        assert result["width"] > 0
        assert result["width"] == round(abs(result["tc"] - result["bc"]), 6)

    def test_width_pct_normalized(self):
        result = compute_cpr(110, 90, 100)
        # width=0 (symmetric), width_pct=0
        assert result["width_pct"] == 0.0

        # Asymmetric: width should be a percentage of pivot
        result2 = compute_cpr(110, 90, 105)
        assert result2["width_pct"] > 0
        assert result2["width_pct"] < 5  # reasonable for typical asset

    def test_zero_or_negative_returns_empty(self):
        assert compute_cpr(0, 90, 100) == {}
        assert compute_cpr(110, 0, 100) == {}
        assert compute_cpr(110, 90, 0) == {}
        assert compute_cpr(-110, 90, 100) == {}

    def test_high_less_than_low_returns_empty(self):
        assert compute_cpr(80, 100, 90) == {}

    def test_non_numeric_returns_empty(self):
        assert compute_cpr("not-a-number", 90, 100) == {}
        assert compute_cpr(None, None, None) == {}


# ── compute_cpr_from_df ───────────────────────────────────────────────────────

class TestComputeCPRFromDF:
    def test_uses_iloc_minus_2_not_minus_1(self):
        """CPR must use the LAST CLOSED bar (iloc[-2]), not the partial current bar (iloc[-1])."""
        class FakeDF:
            def __init__(self, rows):
                self.rows = rows
            def __len__(self):
                return len(self.rows)
            @property
            def iloc(self):
                return self
            def __getitem__(self, idx):
                return self.rows[idx]

        # rows[-2] is the closed daily bar; rows[-1] is the still-forming bar.
        # If chart_cpr uses iloc[-1] by mistake, we'd see CPR computed from
        # the forming bar (H=999) instead of the closed bar (H=110).
        rows = [
            {"high": 105, "low": 90, "close": 100},   # 2 days ago
            {"high": 110, "low": 95, "close": 105},   # yesterday (closed)
            {"high": 999, "low": 1,   "close": 50},    # today (partial)
        ]
        df = FakeDF(rows)
        result = compute_cpr_from_df(df)
        # Should match compute_cpr(110, 95, 105), NOT compute_cpr(999, 1, 50)
        expected = compute_cpr(110, 95, 105)
        assert result == expected

    def test_short_df_returns_empty(self):
        class FakeDF:
            def __init__(self): pass
            def __len__(self): return 1
        assert compute_cpr_from_df(FakeDF()) == {}

    def test_none_df_returns_empty(self):
        assert compute_cpr_from_df(None) == {}


# ── two_day_relationship ──────────────────────────────────────────────────────

class TestTwoDayRelationship:
    def _make_cpr(self, bc, tc, pivot=None):
        if pivot is None:
            pivot = (bc + tc) / 2
        return {"bc": bc, "tc": tc, "pivot": pivot, "width": abs(tc - bc),
                "width_pct": abs(tc - bc) / pivot * 100 if pivot else 0}

    def test_higher_value_when_today_BC_above_yesterday_TC(self):
        prev = self._make_cpr(bc=100, tc=103)
        curr = self._make_cpr(bc=105, tc=108)  # today's BC > yesterday's TC
        out = two_day_relationship(curr, prev)
        assert out["state"] == "higher_value"
        assert out["bias"] == "strong_bull"

    def test_lower_value_when_today_TC_below_yesterday_BC(self):
        prev = self._make_cpr(bc=100, tc=103)
        curr = self._make_cpr(bc=90, tc=95)  # today's TC < yesterday's BC
        out = two_day_relationship(curr, prev)
        assert out["state"] == "lower_value"
        assert out["bias"] == "strong_bear"

    def test_inside_state(self):
        prev = self._make_cpr(bc=95, tc=110)  # wide range
        curr = self._make_cpr(bc=100, tc=105)  # narrow, entirely inside
        out = two_day_relationship(curr, prev)
        assert out["state"] == "inside"
        assert out["bias"] == "breakout_pending"

    def test_outside_state(self):
        prev = self._make_cpr(bc=100, tc=105)  # narrow range
        curr = self._make_cpr(bc=95, tc=110)  # wide, engulfs yesterday
        out = two_day_relationship(curr, prev)
        assert out["state"] == "outside"
        assert out["bias"] == "neutral"

    def test_unchanged_when_within_tolerance(self):
        prev = self._make_cpr(bc=100, tc=103, pivot=101.5)
        curr = self._make_cpr(bc=100.05, tc=103.02, pivot=101.5)
        out = two_day_relationship(curr, prev)
        assert out["state"] == "unchanged"

    def test_missing_cpr_returns_unknown(self):
        assert two_day_relationship({}, self._make_cpr(100, 103))["state"] == "unknown"
        assert two_day_relationship(self._make_cpr(100, 103), {})["state"] == "unknown"


# ── cpr_alignment_weight ──────────────────────────────────────────────────────

class TestCprAlignmentWeight:
    def _make_cpr(self, bc, tc):
        pivot = (bc + tc) / 2
        return {"bc": bc, "tc": tc, "pivot": pivot, "width": abs(tc - bc),
                "width_pct": abs(tc - bc) / pivot * 100 if pivot else 0}

    def test_long_above_TC_and_strong_bull(self):
        # Best case: price above TC (bullish daily structure) + strong bull 2-day → +0.30 (capped)
        cpr = self._make_cpr(bc=100, tc=103)
        two_day = {"bias": "strong_bull", "state": "higher_value", "label": ""}
        w, reason = cpr_alignment_weight(cpr, current_price=110, two_day=two_day, direction="Long")
        assert w == 0.30  # capped at ±0.30
        assert "Long" in reason

    def test_short_below_BC_and_strong_bear(self):
        # Mirror: best case for Short
        cpr = self._make_cpr(bc=100, tc=103)
        two_day = {"bias": "strong_bear", "state": "lower_value", "label": ""}
        w, reason = cpr_alignment_weight(cpr, current_price=90, two_day=two_day, direction="Short")
        assert w == 0.30
        assert "Short" in reason

    def test_long_above_TC_but_strong_bear_two_day(self):
        # Mixed signals — price says long-OK, but 2-day says bear → net could be negative
        cpr = self._make_cpr(bc=100, tc=103)
        two_day = {"bias": "strong_bear", "state": "lower_value", "label": ""}
        w, reason = cpr_alignment_weight(cpr, current_price=110, two_day=two_day, direction="Long")
        # +0.15 (price>TC+Long) - 0.15 (strong_bear+Long) = 0.00
        assert w == 0.0

    def test_long_below_BC_is_penalized(self):
        cpr = self._make_cpr(bc=100, tc=103)
        w, _ = cpr_alignment_weight(cpr, current_price=90, two_day=None, direction="Long")
        assert w == -0.15

    def test_no_two_day_uses_only_price_position(self):
        cpr = self._make_cpr(bc=100, tc=103)
        w, _ = cpr_alignment_weight(cpr, current_price=110, two_day=None, direction="Long")
        assert w == 0.15  # price>TC+Long alone

    def test_inside_cpr_returns_zero(self):
        # Price between BC and TC = no signal
        cpr = self._make_cpr(bc=100, tc=103)
        w, _ = cpr_alignment_weight(cpr, current_price=101.5, two_day=None, direction="Long")
        assert w == 0.0

    def test_missing_data_returns_zero(self):
        assert cpr_alignment_weight({}, 100, None, "Long") == (0.0, "")
        assert cpr_alignment_weight(self._make_cpr(100, 103), 0, None, "Long") == (0.0, "")
        assert cpr_alignment_weight(self._make_cpr(100, 103), 100, None, "") == (0.0, "")

    def test_unknown_direction_returns_zero(self):
        cpr = self._make_cpr(bc=100, tc=103)
        w, _ = cpr_alignment_weight(cpr, current_price=110, two_day=None, direction="Sideways")
        assert w == 0.0

    def test_total_weight_capped_at_magnitude(self):
        # Even in extreme alignment, total cannot exceed ±0.30
        cpr = self._make_cpr(bc=100, tc=103)
        two_day = {"bias": "strong_bull", "state": "higher_value", "label": ""}
        w, _ = cpr_alignment_weight(cpr, current_price=120, two_day=two_day, direction="Long")
        assert -0.30 <= w <= 0.30


# ── cpr_day_type ──────────────────────────────────────────────────────────────

class TestCprDayType:
    def test_narrow_cpr_predicts_trend_day(self):
        # width_pct 0.3% < 0.5% threshold → trend
        cpr = {"width_pct": 0.3, "pivot": 100, "bc": 99.8, "tc": 100.2, "width": 0.4}
        out = cpr_day_type(cpr)
        assert out["day_type"] == "trend"
        assert out["trail_atr_mult"] == 2.0

    def test_wide_cpr_predicts_range_day(self):
        cpr = {"width_pct": 2.0, "pivot": 100, "bc": 99, "tc": 101, "width": 2.0}
        out = cpr_day_type(cpr)
        assert out["day_type"] == "range"
        assert out["trail_atr_mult"] == 1.0

    def test_normal_cpr_returns_neutral(self):
        cpr = {"width_pct": 1.0, "pivot": 100, "bc": 99.5, "tc": 100.5, "width": 1.0}
        out = cpr_day_type(cpr)
        assert out["day_type"] == "neutral"
        assert out["trail_atr_mult"] == 1.5

    def test_empty_cpr_returns_neutral_default(self):
        out = cpr_day_type({})
        assert out["day_type"] == "neutral"
        assert out["trail_atr_mult"] == 1.5

    def test_boundary_narrow(self):
        # Exactly at threshold → falls into NEUTRAL (>=)
        cpr = {"width_pct": CPR_NARROW_PCT, "pivot": 100, "bc": 99, "tc": 101, "width": 0.5}
        out = cpr_day_type(cpr)
        # < strict, so 0.5 → neutral
        assert out["day_type"] == "neutral"
