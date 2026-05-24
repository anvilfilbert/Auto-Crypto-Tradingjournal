"""
Entry-drift guard — refuses trades where the actual Bitget fill drifted
> MAX_ENTRY_DRIFT_PCT from the scanner's intended entry.

Caught from two consecutive incidents on 2026-05-24:
  QNTUSDT — Opus entry $74.20 → fill $79.62  (+7.3% drift)
  ARKMUSDT — Opus entry $0.1204 → fill $0.1457 (+21% drift)
Both had TP1/TP2 below the actual fill price because the ladder was
anchored to the stale scanner entry. The guard closes such positions
immediately and logs `real_entry_drift_aborted`.
"""
import sys
import sqlite3
import types
from unittest.mock import patch, MagicMock

import pytest

# Stub heavy deps so executor can be imported in isolation
for _m in (
    "bitget_client", "ai_client", "ai_call", "agent_orchestrator",
    "prompt_builder", "gemini_client", "consensus", "agent_chart_draw",
    "ai_scanner",
):
    sys.modules.setdefault(_m, types.ModuleType(_m))

# Stub trading.bitget_trader before executor imports it
bt = types.ModuleType("trading.bitget_trader")
bt.place_market_order = MagicMock()
bt.close_position = MagicMock()
bt.modify_position_sl = MagicMock()
bt.get_open_positions = MagicMock(return_value=[])
sys.modules["trading.bitget_trader"] = bt

# chart_context for the lifecycle path (not exercised here but executor imports it lazily)
sys.modules.setdefault("chart_context", types.ModuleType("chart_context"))


from trading import config as fa_config  # noqa: E402
from trading import executor  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

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
            symbol TEXT, base_asset TEXT, direction TEXT,
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
        );
    """)
    return c


def _signal(entry=100.0, sl=95.0, tp1=110.0, tp2=120.0):
    return {
        "symbol": "TESTUSDT", "direction": "Long",
        "consensus_score": 7, "entry_price": entry,
        "sl_price": sl, "tp1_price": tp1, "tp2_price": tp2,
        "tp_levels": [
            {"idx": 1, "price": tp1, "pct": 60, "hit": False, "hit_at": None},
            {"idx": 2, "price": tp2, "pct": 40, "hit": False, "hit_at": None},
        ],
        "scanner": {"score": 7, "direction": "Long", "archetype": "continuation"},
        "ai": {"score": 7, "direction": "Long"},
        "consensus_model_used": "opus",
        "bear_phase_at_open": "decline",
        "archetype_at_open": "continuation",
        "po3_total": 0.0,
        "opus_had_overrides": 0,
        "tp_levels_count": 2,
    }


# ── The guard fires ─────────────────────────────────────────────────────────

def test_aborts_when_fill_drifts_above_long_tolerance(conn, monkeypatch):
    """ARKMUSDT scenario: signal entry 100, Bitget fills at 121 (+21%)."""
    monkeypatch.setattr(fa_config, "MAX_ENTRY_DRIFT_PCT", 0.02)
    monkeypatch.setattr(fa_config, "is_real_mode", lambda: True)
    bt.place_market_order.return_value = {
        "mark_at_entry": 121.0,
        "size_usdt": 75.0, "size_contracts": 0.5,
        "leverage": 3, "order_id": "abc", "attached_sl": True,
        "attached_tp1": True,
    }
    bt.close_position.return_value = {"ok": True}

    pid = executor.open_real_trade(conn, _signal(), {"notional_usdt": 75, "leverage": 3})

    assert pid is None, "expected position refused"
    # close_position called
    bt.close_position.assert_called_once()
    # aborted event present
    abort_rows = conn.execute(
        "SELECT payload_json FROM futures_ai_log WHERE event='real_entry_drift_aborted'"
    ).fetchall()
    assert len(abort_rows) == 1
    import json as _json
    p = _json.loads(abort_rows[0]["payload_json"])
    assert p["intended_entry"] == 100.0
    assert p["fill_price"] == 121.0
    assert p["drift_pct"] == pytest.approx(21.0, rel=1e-3)


def test_aborts_when_fill_drifts_below_short_tolerance(conn, monkeypatch):
    """Mirror for Shorts: signal entry 100, fill at 78 (-22% drift)."""
    monkeypatch.setattr(fa_config, "MAX_ENTRY_DRIFT_PCT", 0.02)
    monkeypatch.setattr(fa_config, "is_real_mode", lambda: True)
    bt.close_position.reset_mock()
    sig = _signal()
    sig["direction"] = "Short"
    bt.place_market_order.return_value = {"mark_at_entry": 78.0, "size_usdt": 75,
                                            "size_contracts": 0.5, "leverage": 3,
                                            "order_id": "abc"}
    bt.close_position.return_value = {"ok": True}

    pid = executor.open_real_trade(conn, sig, {"notional_usdt": 75, "leverage": 3})

    assert pid is None
    assert bt.close_position.call_count == 1


# ── The guard does NOT fire ─────────────────────────────────────────────────

def test_allows_fill_within_tolerance(conn, monkeypatch):
    """Small drift well under 2% tolerance — trade goes through."""
    monkeypatch.setattr(fa_config, "MAX_ENTRY_DRIFT_PCT", 0.02)
    monkeypatch.setattr(fa_config, "is_real_mode", lambda: True)
    bt.close_position.reset_mock()
    bt.place_market_order.return_value = {
        "mark_at_entry": 100.5,   # +0.5% drift, well inside 2%
        "size_usdt": 75.0, "size_contracts": 0.5,
        "leverage": 3, "order_id": "ok123",
    }

    pid = executor.open_real_trade(conn, _signal(), {"notional_usdt": 75, "leverage": 3})

    assert pid is not None, "expected position inserted"
    bt.close_position.assert_not_called()


def test_allows_when_intended_entry_missing(conn, monkeypatch):
    """If the signal doesn't carry an entry_price, we can't compute drift —
    fall back to placing the trade (don't refuse on missing data)."""
    monkeypatch.setattr(fa_config, "MAX_ENTRY_DRIFT_PCT", 0.02)
    monkeypatch.setattr(fa_config, "is_real_mode", lambda: True)
    bt.close_position.reset_mock()
    sig = _signal(); sig["entry_price"] = None
    bt.place_market_order.return_value = {"mark_at_entry": 100.0, "size_usdt": 75,
                                            "size_contracts": 0.5, "leverage": 3,
                                            "order_id": "ok"}
    pid = executor.open_real_trade(conn, sig, {"notional_usdt": 75, "leverage": 3})

    assert pid is not None
    bt.close_position.assert_not_called()


def test_guard_disabled_when_tolerance_zero(conn, monkeypatch):
    """Setting MAX_ENTRY_DRIFT_PCT=0 disables the guard (escape hatch)."""
    monkeypatch.setattr(fa_config, "MAX_ENTRY_DRIFT_PCT", 0.0)
    monkeypatch.setattr(fa_config, "is_real_mode", lambda: True)
    bt.close_position.reset_mock()
    bt.place_market_order.return_value = {"mark_at_entry": 200.0, "size_usdt": 75,
                                            "size_contracts": 0.5, "leverage": 3,
                                            "order_id": "ok"}
    # +100% drift but guard is off
    pid = executor.open_real_trade(conn, _signal(), {"notional_usdt": 75, "leverage": 3})

    assert pid is not None
    bt.close_position.assert_not_called()


# ── Live-incident shapes ────────────────────────────────────────────────────

def test_qnt_scenario_aborts(conn, monkeypatch):
    """QNTUSDT 2026-05-24 00:02: scanner entry 74.20, Bitget fill 79.62
    (+7.3%). Must abort under the default 2% tolerance."""
    monkeypatch.setattr(fa_config, "MAX_ENTRY_DRIFT_PCT", 0.02)
    monkeypatch.setattr(fa_config, "is_real_mode", lambda: True)
    bt.close_position.reset_mock()
    bt.close_position.return_value = {"ok": True}
    bt.place_market_order.return_value = {"mark_at_entry": 79.62, "size_usdt": 75,
                                            "size_contracts": 0.5, "leverage": 3,
                                            "order_id": "qnt_id"}
    sig = _signal(entry=74.20, sl=73.14, tp1=76.32, tp2=78.42)
    pid = executor.open_real_trade(conn, sig, {"notional_usdt": 75, "leverage": 3})
    assert pid is None
    bt.close_position.assert_called_once()


def test_arkm_scenario_aborts(conn, monkeypatch):
    """ARKMUSDT 2026-05-24 08:09: scanner entry 0.1204, fill 0.1457 (+21%)."""
    monkeypatch.setattr(fa_config, "MAX_ENTRY_DRIFT_PCT", 0.02)
    monkeypatch.setattr(fa_config, "is_real_mode", lambda: True)
    bt.close_position.reset_mock()
    bt.close_position.return_value = {"ok": True}
    bt.place_market_order.return_value = {"mark_at_entry": 0.1457, "size_usdt": 75,
                                            "size_contracts": 500, "leverage": 3,
                                            "order_id": "arkm_id"}
    sig = _signal(entry=0.1204, sl=0.1163, tp1=0.1285, tp2=0.134)
    pid = executor.open_real_trade(conn, sig, {"notional_usdt": 75, "leverage": 3})
    assert pid is None
    bt.close_position.assert_called_once()
