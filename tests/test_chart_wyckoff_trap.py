"""Tests for chart_wyckoff.detect_single_bar_trap (spring/upthrust)."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_df(rows):
    """Build a minimal OHLCV DataFrame from a list of (o,h,l,c) tuples."""
    pd = pytest.importorskip("pandas")
    return pd.DataFrame([
        {"open": r[0], "high": r[1], "low": r[2], "close": r[3], "volume": 1000}
        for r in rows
    ])


def _load_detector():
    try:
        from chart_wyckoff import detect_single_bar_trap, single_bar_trap_weight
        return detect_single_bar_trap, single_bar_trap_weight
    except ImportError as e:
        pytest.skip(f"chart_wyckoff import failed: {e}")


# Build a clean prior range (20 bars chopping around 100-105) then add
# a candidate bar + next bar.
def _prior_range_around_100():
    """20-bar chop with low=98, high=107."""
    return [
        (100, 105, 98,  103),   # bar 0
        (103, 104, 100, 102),
        (102, 106, 100, 104),
        (104, 107, 102, 105),
        (105, 106, 101, 103),
        (103, 105, 99,  102),
        (102, 104, 99,  101),
        (101, 103, 98,  100),
        (100, 104, 99,  102),
        (102, 105, 100, 104),
        (104, 106, 101, 103),
        (103, 105, 100, 102),
        (102, 104, 99,  101),
        (101, 103, 98,  100),
        (100, 103, 98,  102),
        (102, 105, 100, 104),
        (104, 106, 101, 103),
        (103, 104, 99,  101),
        (101, 103, 99,  102),
        (102, 105, 100, 104),
    ]


class TestBullishSpring:
    def test_classic_spring_detected(self):
        detect, _ = _load_detector()
        prior = _prior_range_around_100()
        # Candidate: opens 100, wicks down to 95 (below prior low 98), closes back at 101 (above prior low)
        # Big lower wick: 100-95 = 5; body: |101-100| = 1; ratio = 5
        # Next bar: stays above candidate's low — no follow-through
        candidate = (100, 102, 95, 101)
        next_bar  = (101, 103, 100, 102)
        df = _make_df(prior + [candidate, next_bar])
        result = detect(df, lookback=20)
        assert result["detected"] is True
        assert result["type"] == "spring"
        assert result["direction"] == "Long"
        assert result["weight"] == 0.3
        assert result["wick_price"] == 95.0

    def test_spring_rejected_when_no_close_back(self):
        # Candidate wicks below AND closes below the prior low — not a spring (continuation breakdown)
        detect, _ = _load_detector()
        prior = _prior_range_around_100()
        candidate = (100, 101, 95, 96)  # close 96 < prior low 98
        next_bar  = (96, 97, 95, 96)
        df = _make_df(prior + [candidate, next_bar])
        result = detect(df, lookback=20)
        assert result["detected"] is False

    def test_spring_rejected_when_next_bar_follows_through(self):
        # Candidate looks like a spring but next bar breaks the candidate's low
        detect, _ = _load_detector()
        prior = _prior_range_around_100()
        candidate = (100, 102, 95, 101)
        next_bar  = (101, 102, 94, 95)  # n_low 94 < c_low 95 → follow-through breakdown
        df = _make_df(prior + [candidate, next_bar])
        result = detect(df, lookback=20)
        assert result["detected"] is False

    def test_micro_wick_rejected(self):
        # Wick goes JUST barely below prior low (< MIN_WICK_PENETRATION_PCT)
        detect, _ = _load_detector()
        prior = _prior_range_around_100()
        # bar height = 102-97.8 = 4.2; wick below = 98-97.8 = 0.2; pct = 0.2/4.2 = 4.7% < 10%
        candidate = (101, 102, 97.8, 100)
        next_bar  = (100, 101, 99,  100)
        df = _make_df(prior + [candidate, next_bar])
        result = detect(df, lookback=20)
        assert result["detected"] is False  # micro-wick below threshold


class TestBearishUpthrust:
    def test_classic_upthrust_detected(self):
        detect, _ = _load_detector()
        prior = _prior_range_around_100()
        # Candidate: opens 105, wicks up to 112 (above prior high 107), closes back at 104 (below prior high)
        # Upper wick: 112-105 = 7; body: |104-105| = 1; ratio = 7
        # Next bar: stays below candidate's high
        candidate = (105, 112, 103, 104)
        next_bar  = (104, 106, 102, 103)
        df = _make_df(prior + [candidate, next_bar])
        result = detect(df, lookback=20)
        assert result["detected"] is True
        assert result["type"] == "upthrust"
        assert result["direction"] == "Short"
        assert result["weight"] == -0.3
        assert result["wick_price"] == 112.0

    def test_upthrust_rejected_when_follow_through(self):
        detect, _ = _load_detector()
        prior = _prior_range_around_100()
        candidate = (105, 112, 103, 104)
        next_bar  = (104, 115, 103, 113)  # next bar exceeds candidate's high → follow-through
        df = _make_df(prior + [candidate, next_bar])
        result = detect(df, lookback=20)
        assert result["detected"] is False


class TestEdgeCases:
    def test_insufficient_data_returns_not_detected(self):
        detect, _ = _load_detector()
        df = _make_df([(100, 101, 99, 100)] * 5)
        result = detect(df, lookback=20)
        assert result["detected"] is False

    def test_no_pattern_returns_not_detected(self):
        # Boring chop with no wick — no spring or upthrust
        detect, _ = _load_detector()
        prior = _prior_range_around_100()
        candidate = (101, 103, 100, 102)
        next_bar  = (102, 104, 101, 103)
        df = _make_df(prior + [candidate, next_bar])
        result = detect(df, lookback=20)
        assert result["detected"] is False

    def test_none_df_returns_not_detected(self):
        detect, _ = _load_detector()
        result = detect(None, lookback=20)
        assert result["detected"] is False


class TestWeightFunction:
    def test_weight_passthrough_when_detected(self):
        _, w = _load_detector()
        assert w({"detected": True,  "weight": 0.3,  "direction": "Long"})  == 0.3
        assert w({"detected": True,  "weight": -0.3, "direction": "Short"}) == -0.3

    def test_weight_zero_when_not_detected(self):
        _, w = _load_detector()
        assert w({"detected": False, "weight": 0.0}) == 0.0
        assert w({}) == 0.0
        assert w(None) == 0.0
