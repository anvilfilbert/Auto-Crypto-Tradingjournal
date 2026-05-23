"""
Regression: orchestrator._parse_po3_modifier must extract the signed
decimal from scanner PO3/bear_phase descriptive strings and never crash
on edge inputs (None, numeric, neutral-no-arrow, etc.).

Caught from a live incident on 2026-05-23 22:36 UTC: INJUSDT was approved
by Opus consensus (ai_score=7) but the orchestrator crashed with
`could not convert string to float: 'PO3 range: premium (76%) → -0.3'`
because skill_provenance was doing naive float() on the verbose string.
The trade was lost — would have been the first production multi-TP entry.
"""
import sys
import types

import pytest

# Stub heavy deps so orchestrator can import in isolation
for _m in (
    "bitget_client", "trading.bitget_trader", "trading.executor",
    "trading.signal_consensus", "trading.kill_switch", "trading.risk_budget",
    "trading.paper", "ai_client", "ai_call", "agent_orchestrator",
    "prompt_builder", "gemini_client", "consensus", "agent_chart_draw",
    "ai_scanner",
):
    sys.modules.setdefault(_m, types.ModuleType(_m))


from trading.orchestrator import _parse_po3_modifier  # noqa: E402


# ── The live-incident shapes ─────────────────────────────────────────────────

def test_po3_range_premium_negative():
    """The exact string that crashed the orchestrator on 2026-05-23 22:36."""
    assert _parse_po3_modifier("PO3 range: premium (76%) → -0.3") == pytest.approx(-0.3)


def test_po3_range_discount_positive():
    assert _parse_po3_modifier("PO3 range: discount (15%) → +0.3") == pytest.approx(0.3)


def test_po3_fvg_bullish_support():
    assert _parse_po3_modifier(
        "FVG: bullish FVG below @ 0.8% (support, age 0) → +0.30"
    ) == pytest.approx(0.3)


def test_po3_session_silver_bullet():
    assert _parse_po3_modifier(
        "Session: Silver Bullet (13:30-14:30 UTC) → +0.30"
    ) == pytest.approx(0.3)


def test_bear_phase_string_also_parses():
    """Same arrow convention is used for the bear-phase modifier."""
    assert _parse_po3_modifier(
        "bear-phase: decline (F&G 28 fear, BTC drifting) → -0.3"
    ) == pytest.approx(-0.3)


# ── Neutral / absent-modifier shapes ─────────────────────────────────────────

def test_neutral_range_no_arrow():
    """Equilibrium has no arrow modifier — must return 0.0, not crash."""
    assert _parse_po3_modifier("PO3 range: equilibrium (60%)") == 0.0


def test_empty_string():
    assert _parse_po3_modifier("") == 0.0


def test_none_input():
    assert _parse_po3_modifier(None) == 0.0


def test_no_arrow_no_modifier():
    assert _parse_po3_modifier("some descriptive text without modifier") == 0.0


# ── Backward-compatible numeric inputs ───────────────────────────────────────

def test_numeric_float_passthrough():
    """A future scanner might emit the modifier as a bare float — accept it."""
    assert _parse_po3_modifier(0.3) == 0.3


def test_numeric_int_passthrough():
    assert _parse_po3_modifier(1) == 1.0


def test_numeric_negative():
    assert _parse_po3_modifier(-0.3) == -0.3


# ── Robustness against malformed inputs ─────────────────────────────────────

def test_arrow_without_decimal():
    """Arrow present but no number — defensive zero, no exception."""
    assert _parse_po3_modifier("range: discount →") == 0.0


def test_multiple_arrows_picks_first():
    assert _parse_po3_modifier("a → 0.1 then → 0.2") == pytest.approx(0.1)


def test_decimal_without_sign():
    """Plus sign is optional."""
    assert _parse_po3_modifier("level → 0.5") == 0.5


def test_object_input_safe():
    """Some non-stringable input — return 0.0, don't crash the orchestrator."""
    class _Weird: pass
    assert _parse_po3_modifier(_Weird()) == 0.0


# ── End-to-end sum (the actual orchestrator code path) ──────────────────────

def test_sum_of_three_modifiers_matches_live_incident():
    """The INJUSDT setup that crashed the orchestrator had:
      _po3_range:   "PO3 range: premium (76%) → -0.3"
      _po3_fvg:     "FVG: bullish FVG below @ 0.8% (support, age 0) → +0.30"
      _po3_session: ""  (empty — no Silver Bullet, no dead hour)
    Expected po3_total = -0.3 + 0.3 + 0 = 0.0 (net neutral).
    """
    total = (
        _parse_po3_modifier("PO3 range: premium (76%) → -0.3")
        + _parse_po3_modifier("FVG: bullish FVG below @ 0.8% (support, age 0) → +0.30")
        + _parse_po3_modifier("")
    )
    assert total == pytest.approx(0.0)
