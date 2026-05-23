"""
Multi-TP wiring tests:
- TP_SPLITS table + pick_max_tp_count clamping
- signal_consensus._build_overrides (entry/SL/TP-ladder merging)
- tp_levels structure built by orchestrator
"""
import json
import sqlite3
import sys
import types
from unittest.mock import patch

import pytest

# Stub ai_call so signal_consensus can be imported in isolation
ai_call_stub = types.ModuleType("ai_call")
ai_call_stub.analyze_call = lambda **kw: {}
sys.modules["ai_call"] = ai_call_stub

from trading.config import TP_SPLITS, MIN_TP_SLICE_USDT, pick_max_tp_count  # noqa: E402
from trading import signal_consensus  # noqa: E402


# ── TP_SPLITS table ──────────────────────────────────────────────────────────

def test_tp_splits_sum_to_100():
    for n, pcts in TP_SPLITS.items():
        assert sum(pcts) == 100, f"TP_SPLITS[{n}] = {pcts} sums to {sum(pcts)} ≠ 100"
        assert len(pcts) == n


def test_tp_splits_match_operator_table():
    """User-provided splits from 2026-05-23."""
    assert TP_SPLITS[3] == [40, 40, 20]
    assert TP_SPLITS[4] == [40, 30, 20, 10]
    assert TP_SPLITS[5] == [30, 25, 20, 15, 10]
    assert TP_SPLITS[6] == [30, 25, 15, 15, 10, 5]
    assert TP_SPLITS[7] == [25, 20, 15, 15, 10, 10, 5]


def test_tp_splits_decreasing_runner_share():
    """Final-runner percentage must NEVER exceed earlier-tier percentages."""
    for n, pcts in TP_SPLITS.items():
        if n >= 2:
            assert pcts[-1] <= max(pcts[:-1])


# ── pick_max_tp_count: notional-aware clamping ───────────────────────────────

def test_pick_max_tp_count_small_account_25usdt():
    # Smallest 7-TP slice is 5% = $1.25 → can't fill $5 min → cap to 3 (smallest 20% = $5)
    assert pick_max_tp_count(25.0, ideal=7) == 3


def test_pick_max_tp_count_100usdt_supports_6():
    # 5% × 100 = $5 ≥ floor → 6 OK; 7-TP smallest is also 5% so 7 also fits
    assert pick_max_tp_count(100.0, ideal=7) == 7
    assert pick_max_tp_count(100.0, ideal=6) == 6


def test_pick_max_tp_count_50usdt_supports_5():
    # 5-TP smallest is 10% × 50 = $5 ≥ floor
    assert pick_max_tp_count(50.0, ideal=7) == 5


def test_pick_max_tp_count_micro_account_falls_to_1():
    # $10 → 3-TP smallest 20% = $2 < $5; 1 is the floor
    assert pick_max_tp_count(10.0, ideal=7) == 1


def test_pick_max_tp_count_respects_ideal_ceiling():
    """Even if notional supports 7, ideal=3 caps at 3."""
    assert pick_max_tp_count(500.0, ideal=3) == 3


def test_pick_max_tp_count_invalid_inputs():
    assert pick_max_tp_count(0, ideal=7) == 1
    assert pick_max_tp_count(100, ideal=0) == 1
    assert pick_max_tp_count(100, ideal=99) == 7


# ── signal_consensus._build_overrides ────────────────────────────────────────

def _scanner_long():
    return {
        "symbol": "TESTUSDT", "direction": "Long",
        "entry_zone": {"low": 100.0, "high": 100.5},
        "entry_price": 100.0, "sl_price": 95.0,
        "tp1_price": 105.0, "tp2_price": 110.0,
    }


def test_override_sl_only_tightens_long():
    """Tighter SL on Long = higher SL → accepted."""
    ai = {"entry_price": 100.0, "sl_price": 97.0, "tp_prices": []}
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert out.get("sl") == 97.0


def test_override_sl_loosen_rejected_long():
    """Looser SL on Long = lower SL → ignored (would increase risk)."""
    ai = {"entry_price": 100.0, "sl_price": 92.0, "tp_prices": []}
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert "sl" not in out


def test_override_sl_above_entry_rejected_long():
    """SL above entry on Long is nonsense — ignore."""
    ai = {"entry_price": 100.0, "sl_price": 101.0, "tp_prices": []}
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert "sl" not in out


def test_override_sl_tightens_short():
    """Tighter SL on Short = lower SL."""
    s = _scanner_long(); s["direction"] = "Short"; s["sl_price"] = 105.0; s["tp1_price"] = 95.0
    ai = {"entry_price": 100.0, "sl_price": 102.0, "tp_prices": []}
    out = signal_consensus._build_overrides(s, ai)
    assert out.get("sl") == 102.0


def test_override_entry_drift_under_2pct_accepted():
    ai = {"entry_price": 100.5, "sl_price": 95.0, "tp_prices": []}  # 0.5% drift
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert out.get("entry") == 100.5


def test_override_entry_drift_over_2pct_rejected():
    ai = {"entry_price": 103.0, "sl_price": 95.0, "tp_prices": []}  # 3% drift
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert "entry" not in out


def test_override_tp_ladder_3_levels_accepted_long():
    ai = {"entry_price": 100.0, "sl_price": 95.0,
          "tp_prices": [104.0, 108.0, 115.0]}
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert out["tp_prices"] == [104.0, 108.0, 115.0]


def test_override_tp_ladder_non_monotonic_rejected_long():
    """TPs must be strictly increasing for a Long."""
    ai = {"entry_price": 100.0, "sl_price": 95.0,
          "tp_prices": [108.0, 104.0, 115.0]}  # out of order
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert "tp_prices" not in out


def test_override_tp_ladder_first_below_entry_rejected_long():
    ai = {"entry_price": 100.0, "sl_price": 95.0,
          "tp_prices": [99.0, 104.0, 110.0]}  # first TP below entry
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert "tp_prices" not in out


def test_override_tp_ladder_2_levels_not_a_ladder():
    """Need ≥3 prices to qualify as a 'ladder' override."""
    ai = {"entry_price": 100.0, "sl_price": 95.0, "tp_prices": [104.0, 110.0]}
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert "tp_prices" not in out


def test_override_tp_ladder_truncates_at_7():
    ai = {"entry_price": 100.0, "sl_price": 95.0,
          "tp_prices": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0]}
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert len(out["tp_prices"]) == 7


def test_override_short_descending_ladder_accepted():
    s = _scanner_long(); s["direction"] = "Short"; s["sl_price"] = 105.0; s["tp1_price"] = 95.0
    ai = {"entry_price": 100.0, "sl_price": 102.0,
          "tp_prices": [96.0, 92.0, 85.0]}
    out = signal_consensus._build_overrides(s, ai)
    assert out["tp_prices"] == [96.0, 92.0, 85.0]


def test_override_empty_when_scanner_targets_unchanged():
    """No overrides emitted when Opus's prices match the scanner's."""
    ai = {"entry_price": 100.0, "sl_price": 95.0,
          "tp_prices": [], "tp1_price": 105.0, "tp2_price": 110.0}
    out = signal_consensus._build_overrides(_scanner_long(), ai)
    assert out == {}  # 2 TPs backfilled but only 2 = not a ladder, entry/SL identical
