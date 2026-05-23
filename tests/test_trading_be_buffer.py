"""
BE-buffer logic — three properties:

1. be_price_for() returns entry × (1 + buf) for Long, × (1 - buf) for Short.
2. The categoriser tags a close as 'BE_stop' (NOT 'early_close'/'manual_close')
   when (a) a prior real_be event exists for the position AND (b) the close
   landed near the entry.
3. The BE-buffer default (0.15%) is at least the round-trip Bitget taker fee
   (0.12%) — we never want the buffer SMALLER than the fees we're trying
   to cover.
"""
import sqlite3
import sys
import types

import pytest


# ── Stub heavy deps so we can import executor + config in isolation ─────────

def _stub_module(name):
    sys.modules.setdefault(name, types.ModuleType(name))

for _m in ("bitget_client", "trading.bitget_trader", "trading.bitget_trader",
           "trading.signal_consensus", "ai_client", "ai_call",
           "agent_orchestrator", "prompt_builder", "gemini_client", "consensus",
           "agent_chart_draw"):
    _stub_module(_m)


from trading import config as fa_config  # noqa: E402
from trading.executor import _categorize_close_reason  # noqa: E402


# ── be_price_for ────────────────────────────────────────────────────────────

def test_be_price_long_above_entry():
    assert fa_config.be_price_for(100.0, is_long=True, buffer_pct=0.0015) == pytest.approx(100.15)


def test_be_price_short_below_entry():
    assert fa_config.be_price_for(100.0, is_long=False, buffer_pct=0.0015) == pytest.approx(99.85)


def test_be_price_zero_entry_returns_zero():
    """Safety: a zero entry returns zero, not NaN."""
    assert fa_config.be_price_for(0.0, is_long=True) == 0.0


def test_be_buffer_covers_round_trip_taker_fee():
    """Default buffer must NEVER be smaller than 0.12% (Bitget round-trip
    taker fee). If it were, hitting the BE SL would lock a guaranteed loss.

    A future operator changing FUTURES_AI_BE_BUFFER_PCT below 0.0012 would
    break the invariant — fix the env var, not the test."""
    assert fa_config.BE_BUFFER_PCT >= 0.0012


# ── _categorize_close_reason — BE_stop detection ────────────────────────────

@pytest.fixture()
def conn_with_be_event():
    c = sqlite3.connect(":memory:")
    c.execute("""
        CREATE TABLE futures_ai_log(
            id INTEGER PRIMARY KEY,
            ts TEXT, event TEXT, symbol TEXT, direction TEXT,
            score INTEGER, payload_json TEXT
        )
    """)
    # One historical real_be event for position 42
    c.execute("""
        INSERT INTO futures_ai_log(ts, event, symbol, direction, score, payload_json)
        VALUES ('2026-05-23 18:00:00', 'real_be', 'BEATUSDT', 'Long', 7,
                '{"position_id": 42, "old_sl": 1.18, "new_sl": 1.2}')
    """)
    c.commit()
    return c


def test_close_near_entry_after_be_move_tagged_BE_stop(conn_with_be_event):
    """Long position 42: entry 1.20, close 1.201 (within 0.6% buffer),
    realised pnl ~$0. WITHOUT the BE detection this falls into early_close.
    With it, → 'BE_stop'."""
    r = _categorize_close_reason(
        pnl=0.05, entry_px=1.20, close_px=1.201, direction="Long",
        raw_reason="Bitget close · open 1.20 → close 1.201",
        conn=conn_with_be_event, position_id=42,
    )
    assert r == "BE_stop"


def test_close_below_entry_after_be_move_long_still_BE_stop(conn_with_be_event):
    """Even if Bitget reports a tiny dip below entry on the fill, as long as
    move_pct is within the 0.6% threshold AND the BE event happened, this
    is a BE_stop."""
    r = _categorize_close_reason(
        pnl=-0.02, entry_px=1.20, close_px=1.1995, direction="Long",
        raw_reason="auto_close", conn=conn_with_be_event, position_id=42,
    )
    assert r == "BE_stop"


def test_no_be_event_falls_back_to_early_close(conn_with_be_event):
    """Without a real_be event, the same small-move close is still
    'early_close' / 'manual_close' — no false BE attribution."""
    r = _categorize_close_reason(
        pnl=0.05, entry_px=1.20, close_px=1.201, direction="Long",
        raw_reason="", conn=conn_with_be_event, position_id=999,  # no event
    )
    assert r in ("early_close", "manual_close")


def test_be_event_but_large_move_is_TP_not_BE(conn_with_be_event):
    """A position that had a BE move BUT eventually ran to TP (+4%) is a TP,
    not a BE_stop. BE detection only kicks in for small moves near entry."""
    r = _categorize_close_reason(
        pnl=5.0, entry_px=1.20, close_px=1.25, direction="Long",
        raw_reason="", conn=conn_with_be_event, position_id=42,
    )
    assert r == "TP"


def test_be_event_with_large_drawdown_close_is_SL(conn_with_be_event):
    """If somehow price moved -5% and we exited at SL, it's still SL — the
    BE detection only fires when the close is near entry."""
    r = _categorize_close_reason(
        pnl=-5.0, entry_px=1.20, close_px=1.14, direction="Long",
        raw_reason="", conn=conn_with_be_event, position_id=42,
    )
    assert r == "SL"


def test_no_conn_no_be_detection():
    """If conn isn't passed (e.g. older callers), BE detection is skipped
    and we fall back to old behaviour."""
    r = _categorize_close_reason(
        pnl=0.05, entry_px=1.20, close_px=1.201, direction="Long",
        raw_reason="",
    )
    assert r in ("early_close", "manual_close")
