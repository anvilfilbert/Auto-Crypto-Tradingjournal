"""Tests for ai_score_comparison.compute_comparison — three-system score audit."""
import sys
import os
import sqlite3
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_score_comparison import (
    compute_comparison, _find_disagreements, _aggregate_for,
    MIN_SAMPLE_N, MIN_OVERLAP_N, ENTER_THRESHOLD,
)


@pytest.fixture
def db():
    """In-memory SQLite with positions + trade_hindsight + settings tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            symbol TEXT, direction TEXT,
            open_time TEXT, close_time TEXT,
            realized_pnl REAL, close_reason TEXT,
            chain TEXT, setup_score REAL, ai_score_at_open REAL
        )
    """)
    conn.execute("""
        CREATE TABLE trade_hindsight (
            id INTEGER PRIMARY KEY,
            position_id INTEGER UNIQUE,
            setup_score INTEGER, would_enter INTEGER, verdict TEXT
        )
    """)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def _ins_pos(conn, pid, **kw):
    defaults = {
        "symbol": "BTCUSDT", "direction": "Long",
        "open_time": "2026-01-01 00:00:00", "close_time": "2026-01-01 04:00:00",
        "realized_pnl": 1.0, "close_reason": "TP", "chain": "auto_ai",
        "setup_score": 7, "ai_score_at_open": 7,
    }
    defaults.update(kw)
    conn.execute(
        "INSERT INTO positions(id, symbol, direction, open_time, close_time, "
        "realized_pnl, close_reason, chain, setup_score, ai_score_at_open) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pid, defaults["symbol"], defaults["direction"], defaults["open_time"],
         defaults["close_time"], defaults["realized_pnl"], defaults["close_reason"],
         defaults["chain"], defaults["setup_score"], defaults["ai_score_at_open"]),
    )
    conn.commit()


def _ins_hindsight(conn, pid, score=7, would_enter=1, verdict="TP"):
    conn.execute(
        "INSERT INTO trade_hindsight(position_id, setup_score, would_enter, verdict) "
        "VALUES (?,?,?,?)",
        (pid, score, would_enter, verdict),
    )
    conn.commit()


class TestEmptyDb:
    def test_no_positions_returns_zeros(self, db):
        result = compute_comparison(db)
        assert result["per_trade"] == []
        assert result["disagreements"] == []
        assert result["meta"]["n_total"] == 0

    def test_position_without_close_time_excluded(self, db):
        _ins_pos(db, 1, close_time=None)
        _ins_pos(db, 2, close_time="")
        _ins_pos(db, 3, close_time="2026-01-01 04:00:00")
        result = compute_comparison(db)
        assert result["meta"]["n_total"] == 1


class TestPerTradeStructure:
    def test_three_scores_present(self, db):
        _ins_pos(db, 1, setup_score=7, ai_score_at_open=8, realized_pnl=2.5)
        _ins_hindsight(db, 1, score=6)
        result = compute_comparison(db)
        row = result["per_trade"][0]
        assert row["scanner_score"]   == 7.0
        assert row["opus_score"]      == 8.0
        assert row["hindsight_score"] == 6.0
        assert row["realized_pnl"]    == 2.5

    def test_missing_scores_are_none(self, db):
        _ins_pos(db, 1, setup_score=None, ai_score_at_open=None)
        result = compute_comparison(db)
        row = result["per_trade"][0]
        assert row["scanner_score"]   is None
        assert row["opus_score"]      is None
        assert row["hindsight_score"] is None


class TestCoverageCounts:
    def test_meta_counts_track_each_system(self, db):
        # 5 positions: all 5 have scanner, 3 have opus, 2 have hindsight, 1 has all 3
        _ins_pos(db, 1, setup_score=7, ai_score_at_open=7)
        _ins_hindsight(db, 1, score=6)        # all three
        _ins_pos(db, 2, setup_score=6, ai_score_at_open=7)  # scanner + opus
        _ins_pos(db, 3, setup_score=7, ai_score_at_open=None)
        _ins_hindsight(db, 3, score=8)        # scanner + hindsight
        _ins_pos(db, 4, setup_score=5, ai_score_at_open=8)  # scanner + opus
        _ins_pos(db, 5, setup_score=4, ai_score_at_open=None)  # scanner only

        result = compute_comparison(db)
        m = result["meta"]
        assert m["n_total"]          == 5
        assert m["n_with_scanner"]   == 5
        assert m["n_with_opus"]      == 3
        assert m["n_with_hindsight"] == 2
        assert m["n_all_three"]      == 1


class TestAggregates:
    def test_insufficient_data_flag(self, db):
        # 3 positions with scanner score — below MIN_SAMPLE_N=5
        for i in range(3):
            _ins_pos(db, i + 1, setup_score=7)
        result = compute_comparison(db)
        assert result["aggregates"]["scanner"]["insufficient_data"] is True
        assert result["aggregates"]["scanner"]["n"] == 3

    def test_bucketing(self, db):
        # 5 positions across buckets
        _ins_pos(db, 1, setup_score=3,  realized_pnl=-1.0)
        _ins_pos(db, 2, setup_score=5,  realized_pnl=0.5)
        _ins_pos(db, 3, setup_score=7,  realized_pnl=1.5)
        _ins_pos(db, 4, setup_score=8,  realized_pnl=2.0)
        _ins_pos(db, 5, setup_score=9,  realized_pnl=3.0)
        result = compute_comparison(db)
        sc = result["aggregates"]["scanner"]
        buckets = {b["bucket"]: b for b in sc["by_bucket"]}
        assert buckets["<5"]["n"]   == 1
        assert buckets["5-6"]["n"]  == 1
        assert buckets["7-8"]["n"]  == 2
        assert buckets["9-10"]["n"] == 1

    def test_signal_accuracy_math(self, db):
        # 5 trades, all scanner-scored. ENTER_THRESHOLD = 7
        # Score 7 + win = TP; Score 7 + loss = FP
        # Score 5 + loss = TN; Score 5 + win = FN
        _ins_pos(db, 1, setup_score=7, realized_pnl=1.0)   # TP
        _ins_pos(db, 2, setup_score=7, realized_pnl=-1.0)  # FP
        _ins_pos(db, 3, setup_score=5, realized_pnl=-1.0)  # TN
        _ins_pos(db, 4, setup_score=5, realized_pnl=1.0)   # FN
        _ins_pos(db, 5, setup_score=8, realized_pnl=2.0)   # TP
        result = compute_comparison(db)
        overall = result["aggregates"]["scanner"]["overall"]
        assert overall["tp"] == 2
        assert overall["fp"] == 1
        assert overall["tn"] == 1
        assert overall["fn"] == 1


class TestDisagreements:
    def test_no_disagreement_when_scores_close(self, db):
        _ins_pos(db, 1, setup_score=7, ai_score_at_open=8)
        _ins_hindsight(db, 1, score=7)  # all within 1 of each other
        result = compute_comparison(db)
        assert len(result["disagreements"]) == 0

    def test_disagreement_when_delta_ge_2(self, db):
        _ins_pos(db, 1, setup_score=8, ai_score_at_open=3, realized_pnl=2.0)
        result = compute_comparison(db)
        assert len(result["disagreements"]) == 1
        d = result["disagreements"][0]
        assert d["delta"] == 5
        assert "scanner" in d["pair"]
        assert "opus" in d["pair"]

    def test_disagreements_sorted_by_magnitude(self, db):
        # Two disagreements; the one with bigger |delta × pnl| sorts first
        _ins_pos(db, 1, setup_score=7, ai_score_at_open=3, realized_pnl=10.0)  # delta=4, pnl=10, mag=40
        _ins_pos(db, 2, setup_score=8, ai_score_at_open=2, realized_pnl=1.0)   # delta=6, pnl=1, mag=6
        result = compute_comparison(db)
        # mag(pos1)=40 > mag(pos2)=6
        assert result["disagreements"][0]["position_id"] == 1


class TestCache:
    def test_get_cached_empty_returns_none(self, db):
        from ai_score_comparison import get_cached
        assert get_cached(db) is None

    def test_save_and_load_roundtrip(self, db):
        from ai_score_comparison import save_cache, get_cached
        data = {"per_trade": [], "meta": {"n_total": 0}}
        save_cache(db, data)
        loaded = get_cached(db)
        assert loaded == data

    def test_recompute_and_save_populates_cache(self, db):
        from ai_score_comparison import recompute_and_save, get_cached
        _ins_pos(db, 1, setup_score=7)
        data = recompute_and_save(db)
        cached = get_cached(db)
        assert cached is not None
        assert cached["meta"]["n_total"] == 1
        assert cached == data
