"""Tests for chart_session.compute_initial_balance + ib_alignment_weight."""
import sys
import os
import datetime
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_session():
    try:
        from chart_session import compute_initial_balance, ib_alignment_weight
        return compute_initial_balance, ib_alignment_weight
    except ImportError as e:
        pytest.skip(f"chart_session import failed: {e}")


def _make_df(bars):
    """bars: list of (ts_ms, high, low) — open_time + extremes."""
    pd = pytest.importorskip("pandas")
    return pd.DataFrame([
        {"open_time": b[0], "high": b[1], "low": b[2], "close": (b[1]+b[2])/2,
         "open": (b[1]+b[2])/2, "volume": 1000}
        for b in bars
    ])


def _session_open_today_utc():
    """Return today's NYSE-aligned session open at 14:30 UTC."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.replace(hour=14, minute=30, second=0, microsecond=0)


class TestComputeInitialBalance:
    def test_empty_df_returns_empty(self):
        compute_ib, _ = _load_session()
        pd = pytest.importorskip("pandas")
        assert compute_ib(pd.DataFrame()) == {}

    def test_extracts_high_low_within_first_60min(self):
        compute_ib, _ = _load_session()
        session_open = datetime.datetime(2026, 5, 24, 14, 30, tzinfo=datetime.timezone.utc)
        so_ms = int(session_open.timestamp() * 1000)
        # Build 8 × 15m bars: first 4 are in IB window (14:30-15:30), next 4 outside
        bars = [
            (so_ms,              100, 98),     # 14:30
            (so_ms + 15*60*1000, 103, 99),     # 14:45 — highest high
            (so_ms + 30*60*1000, 102, 97),     # 15:00 — lowest low
            (so_ms + 45*60*1000, 101, 99),     # 15:15
            (so_ms + 60*60*1000, 105, 101),    # 15:30 — outside IB (>= ib_end)
            (so_ms + 75*60*1000, 110, 103),    # 15:45 — outside
            (so_ms + 90*60*1000, 108, 100),    # 16:00 — outside
            (so_ms + 105*60*1000,112, 105),    # 16:15 — outside
        ]
        df = _make_df(bars)
        # Simulate "now" at 16:30 (after IB complete)
        now = session_open + datetime.timedelta(minutes=120)
        ib = compute_ib(df, now_utc=now)
        assert ib["high"] == 103.0  # max of first 4 bars
        assert ib["low"]  == 97.0   # min of first 4 bars
        assert ib["range"] == 6.0
        assert ib["is_complete"] is True

    def test_ib_incomplete_when_session_still_forming(self):
        compute_ib, _ = _load_session()
        session_open = datetime.datetime(2026, 5, 24, 14, 30, tzinfo=datetime.timezone.utc)
        so_ms = int(session_open.timestamp() * 1000)
        bars = [
            (so_ms,              100, 98),
            (so_ms + 15*60*1000, 103, 99),
        ]
        df = _make_df(bars)
        # "now" at 15:00 (30 min into IB) — not yet complete
        now = session_open + datetime.timedelta(minutes=30)
        ib = compute_ib(df, now_utc=now)
        assert ib["is_complete"] is False

    def test_no_bars_in_window_returns_empty(self):
        compute_ib, _ = _load_session()
        session_open = datetime.datetime(2026, 5, 24, 14, 30, tzinfo=datetime.timezone.utc)
        so_ms = int(session_open.timestamp() * 1000)
        # All bars BEFORE session open
        bars = [
            (so_ms - 3600*1000, 100, 98),
            (so_ms - 1800*1000, 102, 99),
        ]
        df = _make_df(bars)
        now = session_open + datetime.timedelta(minutes=120)
        ib = compute_ib(df, now_utc=now)
        assert ib == {}


class TestIbAlignmentWeight:
    def _ib(self, high=110, low=100, is_complete=True):
        return {"high": high, "low": low, "range": high-low,
                "session_open": "2026-05-24T14:30:00+00:00",
                "is_complete": is_complete, "label": ""}

    def test_long_above_IB_high_positive(self):
        _, w_fn = _load_session()
        w, reason = w_fn(self._ib(), current_price=115, direction="Long")
        assert w == 0.2
        assert "Long" in reason
        assert "+0.2" in reason

    def test_short_above_IB_high_negative(self):
        _, w_fn = _load_session()
        w, _ = w_fn(self._ib(), current_price=115, direction="Short")
        assert w == -0.2

    def test_long_below_IB_low_negative(self):
        _, w_fn = _load_session()
        w, _ = w_fn(self._ib(), current_price=95, direction="Long")
        assert w == -0.2

    def test_short_below_IB_low_positive(self):
        _, w_fn = _load_session()
        w, _ = w_fn(self._ib(), current_price=95, direction="Short")
        assert w == 0.2

    def test_inside_IB_returns_zero(self):
        _, w_fn = _load_session()
        w, reason = w_fn(self._ib(), current_price=105, direction="Long")
        assert w == 0.0
        assert "inside" in reason.lower() or reason == ""

    def test_incomplete_IB_returns_zero(self):
        _, w_fn = _load_session()
        w, _ = w_fn(self._ib(is_complete=False), current_price=115, direction="Long")
        assert w == 0.0

    def test_missing_ib_returns_zero(self):
        _, w_fn = _load_session()
        assert w_fn({}, 115, "Long") == (0.0, "")
        assert w_fn(None, 115, "Long") == (0.0, "")

    def test_unknown_direction_returns_zero(self):
        _, w_fn = _load_session()
        w, _ = w_fn(self._ib(), current_price=115, direction="Sideways")
        assert w == 0.0
