"""
Phase 2 of multi-TP execution:
- bitget_trader.place_market_order accepts a tp_levels ladder and attaches
  ONE Bitget plan order per tier sized at size_contracts × pct/100
- executor._detect_tp_fills compares originally-placed tiers (db) vs
  currently-pending tiers (live) and flags any that disappeared as filled
- Lifecycle hook: when TP1 fires, an immediate BE move kicks in regardless
  of ATR position (operator default 2026-05-24: first profit = capital
  protection)
"""
import json
import sqlite3
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ── Stub deps so the modules-under-test import in isolation ────────────────

for _m in (
    "bitget_client", "ai_client", "ai_call", "agent_orchestrator",
    "prompt_builder", "gemini_client", "consensus", "agent_chart_draw",
    "ai_scanner", "chart_context",
):
    sys.modules.setdefault(_m, types.ModuleType(_m))
sys.modules.setdefault("trading.bitget_trader", types.ModuleType("trading.bitget_trader"))

from trading import config as fa_config  # noqa: E402
from trading import executor  # noqa: E402


# Module-level mock holder — reassigned per test by autouse fixture below
bt = None


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _patch_trader(monkeypatch):
    """Patch the executor's bitget_trader reference directly via monkeypatch.
    Avoids sys.modules pollution between test files (real 2026-05-24 bug)."""
    fresh = types.ModuleType("trading.bitget_trader")
    fresh.place_market_order  = MagicMock()
    fresh.close_position      = MagicMock()
    fresh.modify_position_sl  = MagicMock()
    fresh.get_open_positions  = MagicMock(return_value=[])
    fresh.get_mark_price      = MagicMock(return_value=0.0)
    monkeypatch.setattr(executor, "bitget_trader", fresh)
    global bt
    bt = fresh


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE futures_ai_log(
            id INTEGER PRIMARY KEY,
            ts TEXT DEFAULT (datetime('now')),
            event TEXT, symbol TEXT, direction TEXT,
            score INTEGER, payload_json TEXT
        );
        CREATE TABLE positions(
            id INTEGER PRIMARY KEY,
            symbol TEXT, direction TEXT, entry_price REAL,
            tp_levels TEXT
        );
    """)
    return c


def _three_tier_ladder():
    return [
        {"idx": 1, "price": 100.0, "pct": 40.0, "hit": False, "hit_at": None},
        {"idx": 2, "price": 110.0, "pct": 40.0, "hit": False, "hit_at": None},
        {"idx": 3, "price": 120.0, "pct": 20.0, "hit": False, "hit_at": None},
    ]


# ── _detect_tp_fills ───────────────────────────────────────────────────────

def test_no_tp_levels_returns_empty(conn):
    """Single-TP / no-ladder positions skip Phase 2 path entirely."""
    conn.execute("INSERT INTO positions(id, symbol, direction, entry_price, tp_levels) "
                 "VALUES (1, 'X', 'Long', 100, NULL)")
    pos = conn.execute("SELECT * FROM positions WHERE id=1").fetchone()
    pos = dict(pos)
    assert executor._detect_tp_fills(conn, pos, {"tp_levels": []}) == []


def test_single_tier_returns_empty(conn):
    """Tier count = 1 is not "multi-TP" — skip the Phase 2 check."""
    single = [{"idx": 1, "price": 100.0, "pct": 100.0, "hit": False, "hit_at": None}]
    conn.execute("INSERT INTO positions VALUES (1, 'X', 'Long', 100, ?)",
                 (json.dumps(single),))
    pos = dict(conn.execute("SELECT * FROM positions WHERE id=1").fetchone())
    assert executor._detect_tp_fills(conn, pos, {"tp_levels": []}) == []


def test_all_tiers_still_pending_no_fills(conn):
    """All 3 tiers still pending on Bitget → no fills."""
    ladder = _three_tier_ladder()
    conn.execute("INSERT INTO positions VALUES (1, 'X', 'Long', 100, ?)",
                 (json.dumps(ladder),))
    pos = dict(conn.execute("SELECT * FROM positions WHERE id=1").fetchone())
    live = {"tp_levels": [
        {"price": 100.0}, {"price": 110.0}, {"price": 120.0}
    ]}
    filled = executor._detect_tp_fills(conn, pos, live)
    assert filled == []


def test_tp1_filled_returned(conn):
    """TP1 (100) no longer pending → flagged as filled."""
    ladder = _three_tier_ladder()
    conn.execute("INSERT INTO positions VALUES (1, 'X', 'Long', 100, ?)",
                 (json.dumps(ladder),))
    pos = dict(conn.execute("SELECT * FROM positions WHERE id=1").fetchone())
    # Bitget only shows TP2 + TP3 pending → TP1 fired
    live = {"tp_levels": [{"price": 110.0}, {"price": 120.0}]}
    filled = executor._detect_tp_fills(conn, pos, live)
    assert len(filled) == 1
    assert filled[0]["idx"] == 1
    assert filled[0]["hit"] is True
    assert filled[0]["hit_at"]  # timestamp set


def test_tp1_fill_persisted_to_db(conn):
    """After detection, the JSON column is updated so a re-run sees .hit=True."""
    ladder = _three_tier_ladder()
    conn.execute("INSERT INTO positions VALUES (1, 'X', 'Long', 100, ?)",
                 (json.dumps(ladder),))
    pos = dict(conn.execute("SELECT * FROM positions WHERE id=1").fetchone())
    live = {"tp_levels": [{"price": 110.0}, {"price": 120.0}]}
    executor._detect_tp_fills(conn, pos, live)

    # Re-fetch + re-run: should report no NEW fills (already known)
    pos2 = dict(conn.execute("SELECT * FROM positions WHERE id=1").fetchone())
    fresh = executor._detect_tp_fills(conn, pos2, live)
    assert fresh == []
    # JSON has TP1.hit=True
    row = json.loads(pos2["tp_levels"])
    assert row[0]["hit"] is True


def test_multiple_tiers_filled_at_once(conn):
    """Stale state — both TP1 and TP2 fired between cycles."""
    ladder = _three_tier_ladder()
    conn.execute("INSERT INTO positions VALUES (1, 'X', 'Long', 100, ?)",
                 (json.dumps(ladder),))
    pos = dict(conn.execute("SELECT * FROM positions WHERE id=1").fetchone())
    live = {"tp_levels": [{"price": 120.0}]}   # only TP3 left
    filled = executor._detect_tp_fills(conn, pos, live)
    idxs = sorted(t["idx"] for t in filled)
    assert idxs == [1, 2]


def test_tick_rounding_doesnt_false_positive(conn):
    """Bitget may report 110.00 for our 110.0 — must NOT count as fill."""
    ladder = _three_tier_ladder()
    conn.execute("INSERT INTO positions VALUES (1, 'X', 'Long', 100, ?)",
                 (json.dumps(ladder),))
    pos = dict(conn.execute("SELECT * FROM positions WHERE id=1").fetchone())
    live = {"tp_levels": [
        {"price": 100.0}, {"price": 110.0000001}, {"price": 120.0}
    ]}
    filled = executor._detect_tp_fills(conn, pos, live)
    assert filled == []   # within 1e-6 rounding


# ── _categorize_close_reason — recheck BE_stop with new 0.25% buffer ────────

def test_be_stop_still_detected_with_wider_buffer(conn):
    """The wider 0.25% buffer doesn't break BE_stop detection — the
    ±0.6% window in the categoriser still captures the typical fill."""
    # Simulate a real_be event on position 99
    conn.execute("""
        INSERT INTO futures_ai_log(ts, event, symbol, direction, score, payload_json)
        VALUES ('2026-05-24 10:00:00', 'real_be', 'X', 'Long', 7,
                '{"position_id": 99, "old_sl": 0.95, "new_sl": 1.0025}')
    """)
    # Entry 1.00, SL fires at 1.001 (within 0.6% of entry) → BE_stop
    r = executor._categorize_close_reason(
        pnl=0.05, entry_px=1.0, close_px=1.001, direction="Long",
        raw_reason="auto_close", conn=conn, position_id=99,
    )
    assert r == "BE_stop"


# ── place_market_order multi-TP path (stubbed) ──────────────────────────────
# The real trader is stubbed; we just validate that when tp_levels is passed,
# the orchestrator's signal flows it through and our orchestrator-side build
# of the ladder is correct (already covered in test_trading_multi_tp.py).
# Here we sanity-check the executor passes tp_levels in the kwargs.

def test_executor_passes_tp_levels_to_trader(conn, monkeypatch):
    """Regression: executor must forward signal['tp_levels'] to trader.place_market_order
    so Phase 2 multi-TP plan orders actually get attached."""
    monkeypatch.setattr(fa_config, "is_real_mode", lambda: True)
    monkeypatch.setattr(fa_config, "MAX_ENTRY_DRIFT_PCT", 0)   # disable drift guard
    bt.place_market_order.reset_mock()
    bt.place_market_order.return_value = {
        "mark_at_entry": 100.0, "size_usdt": 75, "size_contracts": 0.5,
        "leverage": 3, "order_id": "ok", "attached_sl": True,
        "attached_tp1": True, "tp_attach_results": [
            {"idx": 1, "ok": True}, {"idx": 2, "ok": True}, {"idx": 3, "ok": True},
        ],
    }
    # Minimal positions schema for _insert_open_position
    conn.executescript("DROP TABLE positions; " + """
        CREATE TABLE positions(
            id INTEGER PRIMARY KEY, symbol TEXT, base_asset TEXT, direction TEXT,
            margin_mode TEXT, open_time TEXT, close_time TEXT,
            entry_price REAL, close_price REAL,
            size_usdt REAL, size_contracts TEXT,
            realized_pnl REAL, position_pnl REAL,
            opening_fee REAL, closing_fee REAL, total_fees REAL,
            is_manual INTEGER, exchange TEXT, leverage INTEGER,
            chain TEXT, setup_type TEXT, setup_score INTEGER,
            signal_price REAL, tp_levels TEXT,
            consensus_model_used TEXT, bear_phase_at_open TEXT,
            archetype_at_open TEXT, po3_total REAL,
            opus_had_overrides INTEGER, tp_levels_count INTEGER
        )
    """)

    signal = {
        "symbol": "X", "direction": "Long",
        "consensus_score": 7,
        "entry_price": 100.0, "sl_price": 95.0,
        "tp1_price": 105.0, "tp2_price": 110.0,
        "tp_levels": _three_tier_ladder(),
        "scanner": {"score": 7, "archetype": "continuation"},
        "ai": {},
        "consensus_model_used": "opus",
        "bear_phase_at_open": "decline", "archetype_at_open": "continuation",
        "po3_total": 0.0, "opus_had_overrides": 0, "tp_levels_count": 3,
    }
    pid = executor.open_real_trade(conn, signal, {"notional_usdt": 75, "leverage": 3})
    assert pid is not None

    # Verify the trader was called WITH tp_levels
    bt.place_market_order.assert_called_once()
    kwargs = bt.place_market_order.call_args.kwargs
    assert kwargs.get("tp_levels") == _three_tier_ladder()
