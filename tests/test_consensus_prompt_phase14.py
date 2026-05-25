"""Verify consensus prompt surfaces Phase 1-4 modifier context to Opus."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading.signal_consensus import _build_call_text


def _make_setup(**extras):
    base = {
        "symbol": "BTCUSDT",
        "direction": "Long",
        "entry_zone": {"low": 60000.0},
        "sl_price": 59000.0,
        "tp1_price": 61000.0,
        "tp2_price": 62000.0,
        "rr_ratio": 2.0,
        "trade_type": "trend_continuation",
        "setup_score": 7,
        "_rationale": "EMA stack + MACD bullish",
    }
    base.update(extras)
    return base


def test_prompt_includes_bear_phase_when_set():
    setup = _make_setup(_bear_phase="bear-phase: decline (F&G 25 fear) → -0.3")
    prompt = _build_call_text(setup)
    assert "Phase 1-4 modifier context" in prompt
    assert "bear-phase: decline" in prompt


def test_prompt_includes_cpr_when_set():
    setup = _make_setup(_cpr="CPR: higher_value → +0.3")
    prompt = _build_call_text(setup)
    assert "CPR: higher_value" in prompt


def test_prompt_includes_ib_when_set():
    setup = _make_setup(_ib="IB: above IB high + Long → +0.2")
    prompt = _build_call_text(setup)
    assert "IB: above IB high" in prompt


def test_prompt_includes_all_seven_phase14_fields():
    setup = _make_setup(
        _bear_phase="bear-phase: capitulation → +0.3",
        _hmm_regime="HMM: trending_up + Long → +0.2",
        _cpr="CPR: strong_bull → +0.3",
        _ib="IB: above IB high → +0.2",
        _po3_range="PO3 range: discount (15%) → +0.3",
        _po3_fvg="FVG: bullish FVG below → +0.30",
        _po3_session="kill zone NY AM (+0.2)",
    )
    prompt = _build_call_text(setup)
    assert "bear-phase: capitulation" in prompt
    assert "HMM: trending_up" in prompt
    assert "CPR: strong_bull" in prompt
    assert "IB: above IB high" in prompt
    assert "PO3 range: discount" in prompt
    assert "FVG: bullish FVG below" in prompt
    assert "kill zone NY AM" in prompt


def test_prompt_omits_modifier_block_when_no_fields_set():
    setup = _make_setup()
    prompt = _build_call_text(setup)
    assert "Phase 1-4 modifier context" not in prompt
    # Scanner rationale block must still be present
    assert "Scanner rationale:" in prompt


def test_prompt_only_includes_set_fields():
    setup = _make_setup(_cpr="CPR: lower_value → -0.3")  # only CPR set
    prompt = _build_call_text(setup)
    assert "CPR: lower_value" in prompt
    assert "bear-phase" not in prompt
    assert "HMM:" not in prompt
    assert "IB:" not in prompt


def test_prompt_modifier_block_placed_before_evaluation_instructions():
    setup = _make_setup(_cpr="CPR: foo → +0.3")
    prompt = _build_call_text(setup)
    # Modifier block must appear BEFORE "Please independently evaluate"
    cpr_pos = prompt.index("CPR: foo")
    eval_pos = prompt.index("Please independently evaluate")
    assert cpr_pos < eval_pos
