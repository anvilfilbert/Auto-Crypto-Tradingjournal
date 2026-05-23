"""
Regression: analytics.get_deep_stats must surface the 6 skill-provenance
slices for auto_ai positions, regardless of the caller's chain filter.

The bug we're guarding against (caught + fixed in commit a52b9b2):
_build_where() defaults to chain='manual', which silently excluded every
auto_ai-tagged row from the new aggregations. Caller passes no filter,
expects skill data, gets empty arrays.
"""
import sqlite3

import pytest

import analytics


@pytest.fixture()
def conn_with_auto_ai_positions(tmp_path, monkeypatch):
    """Real-schema DB seeded with 4 auto_ai + 2 manual trades."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    # Force database.py to use the temp file via env var (matches its
    # existing fallback path)
    import database
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row

    def insert(pid, sym, direction, entry, close, pnl, ot, ct, chain,
               setup_type, grade, dur, cm, bp, arch, po3, ovr, tpc):
        c.execute("""
            INSERT INTO positions(id, symbol, base_asset, direction,
                                  entry_price, close_price,
                                  realized_pnl, open_time, close_time, chain,
                                  setup_type, execution_grade, duration_minutes,
                                  consensus_model_used, bear_phase_at_open,
                                  archetype_at_open, po3_total,
                                  opus_had_overrides, tp_levels_count,
                                  total_fees)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 0)
        """, (pid, sym, sym.replace("USDT", ""), direction, entry, close, pnl,
              ot, ct, chain, setup_type, grade, dur, cm, bp, arch, po3,
              ovr, tpc))

    # auto_ai cohort
    insert(1, "BTCUSDT",  "Long",  100, 105,  5.0,  "2026-05-23 09:00", "2026-05-23 10:00",
           "auto_ai", "breakout", "A", 60, "opus",   "recovery",   "breakout", 0.6, 1, 3)
    insert(2, "ETHUSDT",  "Short", 100,  95,  5.0,  "2026-05-23 11:00", "2026-05-23 12:00",
           "auto_ai", "reversal", "B", 60, "opus",   "decline",    "reversal", 0.3, 0, 2)
    insert(3, "SOLUSDT",  "Long",  100,  98, -2.0,  "2026-05-23 13:00", "2026-05-23 14:00",
           "auto_ai", "low_conv", "C", 60, "sonnet", "distribution","low_conv", 0.0, 0, 0)
    insert(4, "BNBUSDT",  "Long",  100,  96, -4.0,  "2026-05-23 15:00", "2026-05-23 16:00",
           "auto_ai", "low_conv", "C", 60, "sonnet", "distribution","low_conv", -0.3, 0, 0)
    # manual cohort — no skill tagging (the journal's main book)
    insert(5, "XRPUSDT",  "Long",  100, 110, 10.0,  "2026-05-22 10:00", "2026-05-22 12:00",
           "manual",  "scalp",    "B", 120, None, None, None, None, 0, 0)
    insert(6, "DOGEUSDT", "Short", 100, 102, -2.0,  "2026-05-22 14:00", "2026-05-22 15:00",
           "manual",  "fade",     "C", 60, None, None, None, None, 0, 0)
    c.commit()
    yield c
    c.close()


def test_by_consensus_model_returns_only_auto_ai(conn_with_auto_ai_positions):
    """Skill slices ignore caller's chain filter — always query auto_ai."""
    deep = analytics.get_deep_stats(filters={}, conn=conn_with_auto_ai_positions)
    bcm = deep["by_consensus_model"]
    assert len(bcm) == 2  # opus + sonnet
    by_model = {r["consensus_model"]: r for r in bcm}
    assert by_model["opus"]["trade_count"] == 2     # rows 1 + 2
    assert by_model["sonnet"]["trade_count"] == 2   # rows 3 + 4
    # Manual rows (5, 6) MUST NOT be counted even though they're profitable
    total = sum(r["trade_count"] for r in bcm)
    assert total == 4


def test_by_consensus_model_pnl_correct(conn_with_auto_ai_positions):
    deep = analytics.get_deep_stats(filters={}, conn=conn_with_auto_ai_positions)
    by_model = {r["consensus_model"]: r for r in deep["by_consensus_model"]}
    # opus cohort: row 1 (+5) + row 2 (+5) = +10
    assert by_model["opus"]["total_pnl"] == 10.0
    assert by_model["opus"]["win_rate"] == 100.0
    # sonnet cohort: row 3 (-2) + row 4 (-4) = -6
    assert by_model["sonnet"]["total_pnl"] == -6.0
    assert by_model["sonnet"]["win_rate"] == 0.0


def test_by_bear_phase_groups_cleanly(conn_with_auto_ai_positions):
    """Bear phase strings must be the keyword only, not verbose descriptions."""
    deep = analytics.get_deep_stats(filters={}, conn=conn_with_auto_ai_positions)
    bp = {r["bear_phase"]: r for r in deep["by_bear_phase"]}
    assert "recovery" in bp
    assert "decline" in bp
    assert "distribution" in bp
    assert bp["distribution"]["trade_count"] == 2  # rows 3 + 4


def test_by_archetype_separates_winners_from_losers(conn_with_auto_ai_positions):
    deep = analytics.get_deep_stats(filters={}, conn=conn_with_auto_ai_positions)
    arch = {r["archetype"]: r for r in deep["by_archetype"]}
    # low_conv archetype = 0% WR (the lesson AI Advisor should surface)
    assert arch["low_conv"]["win_rate"] == 0.0
    assert arch["low_conv"]["total_pnl"] == -6.0
    # breakout = 100% WR
    assert arch["breakout"]["win_rate"] == 100.0


def test_by_po3_bucket_includes_negative_and_stacked(conn_with_auto_ai_positions):
    deep = analytics.get_deep_stats(filters={}, conn=conn_with_auto_ai_positions)
    bucket_names = {r["po3_bucket"] for r in deep["by_po3_bucket"]}
    # row 1: po3_total=0.6  → "two modifiers"
    # row 2: po3_total=0.3  → "one modifier (+small)"
    # row 3: po3_total=0.0  → "neutral (no PO3)"
    # row 4: po3_total=-0.3 → "negative (fighting)"
    assert "negative (fighting)" in bucket_names
    assert "neutral (no PO3)" in bucket_names
    assert "one modifier (+small)" in bucket_names
    assert "two modifiers" in bucket_names


def test_by_opus_overrides_split(conn_with_auto_ai_positions):
    deep = analytics.get_deep_stats(filters={}, conn=conn_with_auto_ai_positions)
    splits = {r["opus_overrides"]: r for r in deep["by_opus_overrides"]}
    # row 1 = with_overrides (1), rows 2/3/4 = no_overrides (3)
    assert splits["with_overrides"]["trade_count"] == 1
    assert splits["no_overrides"]["trade_count"] == 3


def test_by_tp_count_distribution(conn_with_auto_ai_positions):
    deep = analytics.get_deep_stats(filters={}, conn=conn_with_auto_ai_positions)
    counts = {r["tp_levels_count"]: r["trade_count"] for r in deep["by_tp_count"]}
    assert counts.get(3) == 1   # row 1
    assert counts.get(2) == 1   # row 2
    assert counts.get(0) == 2   # rows 3 + 4


def test_skill_slices_independent_of_chain_filter(conn_with_auto_ai_positions):
    """Even when caller passes a filter that would normally restrict to
    manual chain, the skill slices stay on auto_ai (since that's where
    the skills live)."""
    deep = analytics.get_deep_stats(filters={"chain": "manual"},
                                    conn=conn_with_auto_ai_positions)
    bcm = {r["consensus_model"]: r for r in deep["by_consensus_model"]}
    assert bcm.get("opus", {}).get("trade_count") == 2
    assert bcm.get("sonnet", {}).get("trade_count") == 2
