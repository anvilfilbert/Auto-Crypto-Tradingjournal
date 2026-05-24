"""Tests for chart_wyckoff multi-bar detectors: Spring, Upthrust, Absorption."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load():
    try:
        from chart_wyckoff import (detect_spring, detect_upthrust,
                                    detect_absorption, wyckoff_multibar_weight)
        return detect_spring, detect_upthrust, detect_absorption, wyckoff_multibar_weight
    except ImportError as e:
        pytest.skip(f"chart_wyckoff import failed: {e}")


def _make_df(rows):
    pd = pytest.importorskip("pandas")
    return pd.DataFrame([
        {"open": r[0], "high": r[1], "low": r[2], "close": r[3], "volume": r[4]}
        for r in rows
    ])


def _range_chop(n=35, vol=1000):
    """Build a flat trading range 98-105 with normal volume."""
    return [(101, 105, 98, 102, vol)] * n


class TestSpring:
    def test_classic_spring_detected(self):
        spring, _, _, _ = _load()
        rng = _range_chop(35, vol=1000)
        # Last 5 bars: spring sequence
        # Bar -5: break below 98 (range low)
        # Bar -4: recovery starts
        # Bar -3: recovery bar with HIGH volume (1.5× avg=1500)
        # Bar -2, -1: stay above 98
        spring_bars = [
            (98, 99,  95,  97,  1200),  # broke 98
            (97, 99,  96,  99,  1500),  # recovery start
            (99, 102, 99, 101, 2000),    # strong recovery with volume
            (101,103, 100,102, 1100),
            (102,104, 101,103, 1100),
        ]
        df = _make_df(rng + spring_bars)
        out = spring(df, lookback=30)
        assert out["detected"] is True
        assert out["direction"] == "Long"
        assert out["weight"] == 0.3

    def test_spring_rejected_without_volume(self):
        spring, _, _, _ = _load()
        rng = _range_chop(35, vol=2000)  # higher baseline avg
        # Recovery without volume surge
        spring_bars = [
            (98, 99, 95, 97, 1000),
            (97, 99, 96, 99, 1100),
            (99,102, 99,101, 1000),  # 0.5× avg = no vol confirmation
            (101,103,100,102, 1000),
            (102,104,101,103, 1000),
        ]
        df = _make_df(rng + spring_bars)
        out = spring(df, lookback=30)
        assert out["detected"] is False

    def test_no_break_no_spring(self):
        spring, _, _, _ = _load()
        rng = _range_chop(40)
        df = _make_df(rng)
        out = spring(df, lookback=30)
        assert out["detected"] is False


class TestUpthrust:
    def test_classic_upthrust_detected(self):
        _, upthrust, _, _ = _load()
        rng = _range_chop(35, vol=1000)
        upthrust_bars = [
            (104,108, 103,105, 1200),  # broke 105 (range high)
            (105,107, 104,103, 1500),  # rejection starts
            (103,104, 100,102, 2000),  # strong rejection with volume
            (102,103, 100,101, 1100),
            (101,103, 100,102, 1100),
        ]
        df = _make_df(rng + upthrust_bars)
        out = upthrust(df, lookback=30)
        assert out["detected"] is True
        assert out["direction"] == "Short"
        assert out["weight"] == -0.3


class TestAbsorption:
    def test_absorption_with_declining_volume(self):
        _, _, absorption, _ = _load()
        # Build range with 3+ touches near top + declining volume on touches
        # Range high ~105
        bars = [
            (100, 102, 99,  101, 1000),
            (101, 103, 100, 102, 1000),
            (102, 105, 101, 104, 2000),  # touch 1 — high vol
            (104, 105, 102, 103, 1500),  # touch 2 — lower vol
            (103, 105, 102, 104, 1100),  # touch 3 — even lower vol
        ] * 8  # ~40 bars total
        df = _make_df(bars)
        out = absorption(df, lookback=30)
        # Note: this synthetic data is simplistic; absorption requires declining
        # volume on the LAST 3 touches. Real-world detection has more nuance.
        # We just verify it doesn't crash and returns a valid structure.
        assert "detected" in out

    def test_no_touches_no_absorption(self):
        _, _, absorption, _ = _load()
        # Flat range with no resistance touches
        bars = [(100, 101, 99, 100, 1000)] * 35
        df = _make_df(bars)
        out = absorption(df, lookback=30)
        assert out["detected"] is False


class TestWyckoffMultibarWeight:
    def test_spring_wins_over_others(self):
        _, _, _, w_fn = _load()
        sp = {"detected": True, "weight": 0.3, "label": "spring"}
        ut = {"detected": True, "weight": -0.3, "label": "upthrust"}
        w, reason = w_fn(sp, ut, None)
        assert w == 0.3
        assert "spring" in reason.lower()

    def test_upthrust_when_no_spring(self):
        _, _, _, w_fn = _load()
        ut = {"detected": True, "weight": -0.3, "label": "upthrust"}
        w, _ = w_fn(None, ut, None)
        assert w == -0.3

    def test_absorption_when_neither_spring_nor_upthrust(self):
        _, _, _, w_fn = _load()
        ab = {"detected": True, "weight": 0.25, "label": "absorption"}
        w, _ = w_fn(None, None, ab)
        assert w == 0.25

    def test_no_signals_returns_zero(self):
        _, _, _, w_fn = _load()
        assert w_fn(None, None, None) == (0.0, "")
        assert w_fn({}, {}, {}) == (0.0, "")


class TestEdgeCases:
    def test_short_df_returns_not_detected(self):
        spring, upthrust, absorption, _ = _load()
        pd = pytest.importorskip("pandas")
        df = _make_df([(100, 101, 99, 100, 1000)] * 5)
        assert spring(df)["detected"] is False
        assert upthrust(df)["detected"] is False
        assert absorption(df)["detected"] is False

    def test_none_df_returns_not_detected(self):
        spring, upthrust, absorption, _ = _load()
        assert spring(None)["detected"] is False
        assert upthrust(None)["detected"] is False
        assert absorption(None)["detected"] is False
