"""
Regression: every consensus_rejected / consensus_approved / consensus_error
event in futures_ai_log must embed the full setup snapshot (entry / SL / TP /
archetype / scores) so a later hindsight pass can replay the trade without
joining against analyzed_calls.

The old payload was sparse — score + a short reason string — and lost the
SL/TP that would let us simulate the rejected setup.
"""
import json
import sqlite3
import sys
import types
from unittest.mock import patch

import pytest

# Stub the heavy ai_call dependency before importing signal_consensus
ai_call_stub = types.ModuleType("ai_call")


def _fake_analyze(*args, **kwargs):
    return {
        "setup_score": 5,
        "direction": "Long",
        "summary": "weak alignment, momentum stalling",
        "_reviewer_warnings": ["Confluence 0.0 — weak multi-signal alignment"],
    }


ai_call_stub.analyze_call = _fake_analyze
sys.modules["ai_call"] = ai_call_stub

from trading import signal_consensus  # noqa: E402


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(
        """
        CREATE TABLE futures_ai_log(
          id INTEGER PRIMARY KEY,
          ts TEXT, event TEXT, symbol TEXT, direction TEXT,
          score INTEGER, payload_json TEXT
        )
        """
    )
    return c


def _scanner_setup():
    return {
        "symbol": "TESTUSDT",
        "direction": "Long",
        "setup_score": 8,
        "entry_zone": {"low": 1.234, "high": 1.240},
        "entry_price": 1.234,
        "sl_price": 1.180,
        "tp1_price": 1.320,
        "tp2_price": 1.400,
        "rr_ratio": "2:1",
        "trade_type": "breakout",
        "confluence": 1.7,
        "_bear_phase": "recovery",
        "_po3_range": "discount",
        "_po3_fvg": "support",
        "_po3_session": "ny_am",
        "regime_label": "bullish_trend",
        "timeframe": "Multi-TF (1D/4H/1H)",
        "_rationale": "EMA reclaim + smart-flow new longs + ADX expansion",
    }


def _last_payload(conn) -> dict:
    row = conn.execute(
        "SELECT event, payload_json FROM futures_ai_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None, "no log row written"
    return {"event": row[0], **json.loads(row[1])}


def test_rejected_low_score_embeds_full_snapshot(conn):
    verdict = signal_consensus.evaluate(_scanner_setup(), conn)
    assert verdict["approved"] is False
    p = _last_payload(conn)
    assert p["event"] == "consensus_rejected"
    assert p["reject_kind"] == "low_score"
    # The whole point of the shadow-log change:
    assert p["entry"] == 1.234
    assert p["sl"] == 1.180
    assert p["tp1"] == 1.320
    assert p["tp2"] == 1.400
    assert p["scanner_score"] == 8
    assert p["ai_score"] == 5
    assert p["archetype"] == "breakout"
    assert p["confluence"] == 1.7
    assert p["bear_phase"] == "recovery"
    assert p["po3_session"] == "ny_am"
    assert "EMA reclaim" in p["rationale"]


def test_rejected_direction_mismatch_embeds_full_snapshot(conn):
    def fake(*a, **kw):
        return {"setup_score": 8, "direction": "Short",
                "summary": "trend up but I see short", "_reviewer_warnings": []}
    with patch.object(ai_call_stub, "analyze_call", fake):
        verdict = signal_consensus.evaluate(_scanner_setup(), conn)
    assert verdict["approved"] is False
    p = _last_payload(conn)
    assert p["reject_kind"] == "direction_mismatch"
    assert p["ai_direction"] == "Short"
    assert p["entry"] == 1.234 and p["sl"] == 1.180 and p["tp1"] == 1.320


def test_rejected_critical_warning_embeds_full_snapshot(conn):
    def fake(*a, **kw):
        return {"setup_score": 8, "direction": "Long",
                "summary": "OK but critical macro risk",
                "_reviewer_warnings": ["Critical: FOMC in 2 hours"]}
    with patch.object(ai_call_stub, "analyze_call", fake):
        verdict = signal_consensus.evaluate(_scanner_setup(), conn)
    assert verdict["approved"] is False
    p = _last_payload(conn)
    assert p["reject_kind"] == "critical_warning"
    assert p["entry"] == 1.234 and p["sl"] == 1.180


def test_approved_also_carries_snapshot(conn):
    def fake(*a, **kw):
        return {"setup_score": 8, "direction": "Long",
                "summary": "agree, strong setup", "_reviewer_warnings": []}
    with patch.object(ai_call_stub, "analyze_call", fake):
        verdict = signal_consensus.evaluate(_scanner_setup(), conn)
    assert verdict["approved"] is True
    p = _last_payload(conn)
    assert p["event"] == "consensus_approved"
    assert p["reject_kind"] is None
    # approved events must also carry the snapshot so we can compare
    # approved vs rejected cohorts apples-to-apples in hindsight
    assert p["entry"] == 1.234
    assert p["sl"] == 1.180
    assert p["tp1"] == 1.320
    assert p["scanner_score"] == 8
    assert p["ai_score"] == 8
