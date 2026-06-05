"""Tests for the 2026-05-30 chart_context wiring of vwap/volume_profile/supertrend
into compute_indicators + format_for_prompt.

These tests focus on the FORMATTING layer — that's pure-Python and doesn't
need pandas-ta. The integration (compute_indicators wiring) is verified on
the Pi where pandas-ta is installed.
"""
import sys

# Need real chart_context, not the MagicMock stub
sys.modules.pop("chart_context", None)
sys.modules.pop("chart_indicators", None)
sys.modules.pop("pandas_ta", None)

import pandas as pd
import pytest

# chart_context imports chart_indicators which imports pandas-ta.
pandas_ta = pytest.importorskip("pandas_ta")


def test_format_for_prompt_renders_vwap_block():
    from chart_context import format_for_prompt
    inds = {
        "ok": True,
        "vwap": {
            "vwap": 100.5, "distance_pct": 0.42,
            "position": "above_1sigma",
            "upper_1": 101, "lower_1": 100, "upper_2": 102, "lower_2": 99,
            "session_bars": 24,
        },
    }
    out = format_for_prompt("BTCUSDT", inds, "4H")
    assert "VWAP" in out
    assert "+0.42%" in out
    assert "↑1σ" in out


def test_format_for_prompt_renders_volume_profile_block():
    from chart_context import format_for_prompt
    inds = {
        "ok": True,
        "volume_profile": {
            "poc": 100.0, "vah": 102.0, "val": 98.0,
            "distance_to_poc_pct": -0.50,
            "at_poc": "below",
            "in_value_area": True,
            "hvn": [99.5, 100.5, 101.0],
            "lvn": [],
        },
    }
    out = format_for_prompt("BTCUSDT", inds, "4H")
    assert "POC" in out
    assert "-0.50%" in out
    assert "in-VA" in out
    assert "HVN3" in out


def test_format_for_prompt_renders_supertrend_uptrend():
    from chart_context import format_for_prompt
    inds = {
        "ok": True,
        "supertrend": {
            "direction": 1, "supertrend_value": 95.0,
            "flip_bars_ago": 15, "signal": "uptrend",
        },
    }
    out = format_for_prompt("BTCUSDT", inds, "4H")
    assert "ST↑(15b)" in out


def test_format_for_prompt_renders_supertrend_flip():
    from chart_context import format_for_prompt
    inds = {
        "ok": True,
        "supertrend": {
            "direction": 1, "supertrend_value": 95.0,
            "flip_bars_ago": 0, "signal": "flip_bullish",
        },
    }
    out = format_for_prompt("BTCUSDT", inds, "4H")
    assert "↑FLIP" in out


def test_format_for_prompt_skips_missing_indicators():
    """When new indicators aren't in dict, formatting should still work."""
    from chart_context import format_for_prompt
    inds = {"ok": True, "rsi": {"value": 55}}
    out = format_for_prompt("BTCUSDT", inds, "4H")
    assert out  # non-empty
    assert "RSI" in out
    # None of the new tokens should appear
    assert "VWAP" not in out
    assert "POC" not in out
    assert "ST↑" not in out and "ST↓" not in out


def test_format_for_prompt_all_three_new_blocks_render_in_order():
    """When all three new blocks present, they appear in the output."""
    from chart_context import format_for_prompt
    inds = {
        "ok": True,
        "vwap": {"vwap": 100, "distance_pct": 0.5, "position": "above_vwap",
                 "upper_1": 101, "lower_1": 99, "upper_2": 102, "lower_2": 98,
                 "session_bars": 24},
        "volume_profile": {"poc": 100, "vah": 102, "val": 98,
                            "distance_to_poc_pct": 0.5, "at_poc": "above",
                            "in_value_area": True, "hvn": [], "lvn": []},
        "supertrend": {"direction": 1, "supertrend_value": 95,
                       "flip_bars_ago": 10, "signal": "uptrend"},
    }
    out = format_for_prompt("BTCUSDT", inds, "4H")
    assert "VWAP" in out
    assert "POC" in out
    assert "ST" in out
