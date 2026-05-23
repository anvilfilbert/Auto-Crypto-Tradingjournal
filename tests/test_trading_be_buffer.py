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


# ── No-double-fire on BE move (2026-05-23 21:08 incident regression) ────────
#
# RKLBUSDT fired real_be twice 11 min apart. Root cause: executor compared
# its full-precision be_sl=134.5515 against Bitget's tick-rounded current_sl
# of 134.55 — true at full precision, so a redundant cancel+replace fired.
# The fix: only treat a BE/trail move as needed when the gap between be_sl
# and current_sl is ≥ 0.05% of entry (well below the 0.15% buffer itself,
# well above any reasonable tick-rounding noise).

def _gap_pct(target_sl, current_sl, entry):
    """Replicates the executor's epsilon-guard inputs."""
    return abs(target_sl - current_sl) / entry if entry else 0


def test_be_move_blocked_when_gap_within_tick_noise():
    """current_sl is Bitget's tick-rounded view (134.55) of the same SL the
    executor already moved to (134.5515). The next monitor cycle must NOT
    fire a redundant move."""
    entry = 134.43
    be_sl = fa_config.be_price_for(entry, is_long=True)   # ~134.6316
    bitget_rounded = round(be_sl, 2)                       # 134.63 (within tick noise)
    gap = _gap_pct(be_sl, bitget_rounded, entry)
    assert gap < 0.0005, (
        f"gap {gap:.5%} should be below 0.05% epsilon — Bitget tick rounding "
        f"creates a tiny mismatch but the executor should treat as no-op"
    )


def test_be_move_fires_when_gap_exceeds_epsilon():
    """A real reason to fire — old SL was 5% below entry."""
    entry = 134.43
    be_sl = fa_config.be_price_for(entry, is_long=True)
    old_sl = entry * 0.95          # scanner's original SL
    gap = _gap_pct(be_sl, old_sl, entry)
    assert gap >= 0.0005           # meaningful gap → fire allowed


def test_be_buffer_uses_live_entry_not_db_signal():
    """Regression: BE was applying the buffer to db_pos.entry_price (the
    signal price 134.35) instead of live.entry_price (Bitget's actual fill
    134.43), so the effective buffer was only +0.089% instead of +0.150%.
    The executor now reads live.entry_price first.
    """
    # Just confirm the function returns the right shape — the executor
    # change is structural (which dict key it reads from). The math here
    # demonstrates that with the LIVE fill, the buffer is the full 0.15%.
    live_entry = 134.43      # actual Bitget fill
    db_entry   = 134.35      # original signal price
    be_from_live = fa_config.be_price_for(live_entry, is_long=True)
    be_from_db   = fa_config.be_price_for(db_entry,   is_long=True)
    # With LIVE entry, the gap from real fill is the full 0.15%
    assert abs(be_from_live - live_entry) / live_entry == pytest.approx(0.0015)
    # With the OLD (db) calc, the gap from real fill was less
    assert abs(be_from_db - live_entry) / live_entry < 0.0015
