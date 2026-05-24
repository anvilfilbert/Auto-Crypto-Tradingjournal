"""Tests for ai_calibration.compute_calibration — Opus threshold analysis."""
import sys
import os
import sqlite3
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_calibration import compute_calibration, MIN_BUCKET_N, RELIABLE_N


@pytest.fixture
def db():
    """In-memory SQLite with minimal positions schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            chain TEXT,
            open_time TEXT,
            close_time TEXT,
            realized_pnl REAL,
            close_reason TEXT,
            ai_score_at_open REAL
        )
    """)
    return conn


def _insert(conn, **kw):
    defaults = {
        "symbol": "BTCUSDT", "chain": "auto_ai",
        "open_time": "2026-01-01 00:00:00", "close_time": "2026-01-01 04:00:00",
        "realized_pnl": 0.0, "close_reason": "TP", "ai_score_at_open": 7.0,
    }
    defaults.update(kw)
    conn.execute(
        "INSERT INTO positions(symbol, chain, open_time, close_time, "
        "realized_pnl, close_reason, ai_score_at_open) VALUES (?,?,?,?,?,?,?)",
        (defaults["symbol"], defaults["chain"], defaults["open_time"],
         defaults["close_time"], defaults["realized_pnl"],
         defaults["close_reason"], defaults["ai_score_at_open"]),
    )
    conn.commit()


class TestEmptyData:
    def test_no_positions_returns_zeros(self, db):
        result = compute_calibration(db)
        assert result["n_total"] == 0
        assert result["n_with_score"] == 0
        assert result["buckets"] == []
        assert "insufficient" in result["verdict"].lower()

    def test_positions_without_score_excluded(self, db):
        _insert(db, ai_score_at_open=None)
        _insert(db, ai_score_at_open=None)
        result = compute_calibration(db)
        assert result["n_total"] == 2
        assert result["n_with_score"] == 0
        assert result["buckets"] == []


class TestBucketingByScore:
    def test_groups_by_ai_score(self, db):
        _insert(db, ai_score_at_open=7.0, realized_pnl=1.0)
        _insert(db, ai_score_at_open=7.0, realized_pnl=-0.5)
        _insert(db, ai_score_at_open=8.0, realized_pnl=2.0)
        result = compute_calibration(db)
        assert len(result["buckets"]) == 2
        scores = sorted(b["score"] for b in result["buckets"])
        assert scores == [7.0, 8.0]
        bucket_7 = next(b for b in result["buckets"] if b["score"] == 7.0)
        assert bucket_7["n"] == 2

    def test_excludes_manual_chain(self, db):
        _insert(db, chain="manual", ai_score_at_open=7.0)
        _insert(db, chain="auto_ai", ai_score_at_open=7.0)
        result = compute_calibration(db)
        assert result["n_with_score"] == 1

    def test_excludes_open_positions(self, db):
        _insert(db, close_time=None, ai_score_at_open=7.0)
        _insert(db, close_time="2026-01-01 04:00:00", ai_score_at_open=7.0)
        result = compute_calibration(db)
        assert result["n_with_score"] == 1


class TestMetrics:
    def test_win_rate_calculation(self, db):
        # 2 wins + 1 loss + 1 breakeven = 50% WR (only PnL>0.01 counts)
        _insert(db, realized_pnl=1.0, ai_score_at_open=7)
        _insert(db, realized_pnl=2.0, ai_score_at_open=7)
        _insert(db, realized_pnl=-1.0, ai_score_at_open=7)
        _insert(db, realized_pnl=0.001, ai_score_at_open=7)
        result = compute_calibration(db)
        bucket = result["buckets"][0]
        assert bucket["n"] == 4
        assert bucket["win_rate"] == 50.0

    def test_tp_and_sl_hit_rates(self, db):
        _insert(db, close_reason="TP", ai_score_at_open=7)
        _insert(db, close_reason="TP", ai_score_at_open=7)
        _insert(db, close_reason="SL", ai_score_at_open=7)
        _insert(db, close_reason="BE_stop", ai_score_at_open=7)
        result = compute_calibration(db)
        b = result["buckets"][0]
        assert b["tp1_hit_rate"] == 50.0  # 2/4
        assert b["sl_hit_rate"] == 25.0   # 1/4

    def test_expectancy_per_trade(self, db):
        _insert(db, realized_pnl=10.0, ai_score_at_open=7)
        _insert(db, realized_pnl=-4.0, ai_score_at_open=7)
        result = compute_calibration(db)
        b = result["buckets"][0]
        assert b["total_pnl"] == 6.0
        assert b["expectancy"] == 3.0


class TestReliability:
    def test_below_reliable_n_flagged(self, db):
        for _ in range(RELIABLE_N - 1):
            _insert(db, ai_score_at_open=7.0)
        result = compute_calibration(db)
        assert result["buckets"][0]["reliable"] is False

    def test_at_reliable_n_marked_reliable(self, db):
        for _ in range(RELIABLE_N):
            _insert(db, ai_score_at_open=7.0)
        result = compute_calibration(db)
        assert result["buckets"][0]["reliable"] is True


class TestVerdict:
    def test_insufficient_data_message(self, db):
        for _ in range(3):
            _insert(db, ai_score_at_open=7.0)
        result = compute_calibration(db)
        # 3 < MIN_BUCKET_N (5) → "insufficient data" message
        assert "insufficient" in result["verdict"].lower()

    def test_observation_only_when_below_reliable(self, db):
        # 5 positions — above MIN_BUCKET_N but below RELIABLE_N
        for _ in range(MIN_BUCKET_N):
            _insert(db, ai_score_at_open=7.0)
        result = compute_calibration(db)
        assert "observation only" in result["verdict"].lower()

    def test_reliable_buckets_summary(self, db):
        """Two reliable buckets — verdict should mention best/worst expectancy."""
        for _ in range(RELIABLE_N):
            _insert(db, ai_score_at_open=7.0, realized_pnl=1.0)
        for _ in range(RELIABLE_N):
            _insert(db, ai_score_at_open=5.0, realized_pnl=-1.0)
        result = compute_calibration(db)
        verdict = result["verdict"].lower()
        # Independent of threshold availability, the verdict must summarise
        # best/worst expectancy from reliable buckets
        assert "best" in verdict
        assert "score 7" in verdict
        # Both buckets are reliable (n=15 each)
        assert all(b["reliable"] for b in result["buckets"])
