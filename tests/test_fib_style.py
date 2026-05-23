"""
Regression: FIB_STYLE dict is well-formed.

Catches dropped entries or stale colour codes when someone tweaks the
fib palette without checking every ratio is still styled.
"""
import sys
import types

import pytest

# Stub pandas_ta — chart_patterns imports it at module load
sys.modules.setdefault("pandas_ta", types.ModuleType("pandas_ta"))


from chart_patterns import FIB_LEVELS, FIB_LABELS, FIB_STYLE, FIB_COLORS  # noqa: E402


def test_every_level_has_style():
    """Adding a new ratio to FIB_LEVELS without a FIB_STYLE entry would cause
    chart_patterns.detect_fibonacci() to emit a level with color=None — the
    chart.html renderer would then fall back to the legacy palette, which
    isn't what the operator chose. Guard."""
    missing = [r for r in FIB_LEVELS if r not in FIB_STYLE]
    assert not missing, f"FIB_STYLE missing: {missing}"


def test_every_level_has_label():
    missing = [r for r in FIB_LEVELS if r not in FIB_LABELS]
    assert not missing, f"FIB_LABELS missing: {missing}"


def test_swing_anchors_share_colour():
    """0% and 100% are visual anchors — by design they use the same colour
    so the eye groups them as the swing's frame."""
    assert FIB_STYLE[0.0]["color"] == FIB_STYLE[1.0]["color"]


def test_ote_is_red():
    """0.66 is the ICT OTE marker — must be red, not the golden-pocket
    orange. Color regression would weaken the visual signal."""
    color = FIB_STYLE[0.66]["color"].lower()
    assert color.startswith("#ef") or color.startswith("#e0"), color


def test_golden_pocket_is_orange():
    """0.618 = golden pocket = primary long entry; should be orange-family
    so it pops vs the cooler shallow-retracement blues."""
    color = FIB_STYLE[0.618]["color"].lower()
    assert color.startswith("#ff"), color


def test_extensions_are_green_family():
    """Extensions are profit-target zones — green family by design."""
    for ratio in (1.618, 2.618, 3.618, 4.236):
        assert ratio in FIB_STYLE, ratio
        color = FIB_STYLE[ratio]["color"].lower()
        # Green family: starts with #4, #66, #38, #2e (all green-ish)
        assert color[1] in "234567", f"{ratio} → {color} not green-ish"


def test_backward_compat_alias():
    """FIB_COLORS must mirror FIB_STYLE[*]['color']."""
    for ratio, spec in FIB_STYLE.items():
        assert FIB_COLORS[ratio] == spec["color"]


def test_dash_values_are_valid():
    """Only 3 dash styles map to Lightweight Charts lineStyle codes."""
    for ratio, spec in FIB_STYLE.items():
        assert spec["dash"] in ("solid", "dotted", "dashed"), \
            f"{ratio} dash={spec['dash']}"


def test_weights_in_range():
    for ratio, spec in FIB_STYLE.items():
        assert 1 <= spec["weight"] <= 3, f"{ratio} weight={spec['weight']}"
