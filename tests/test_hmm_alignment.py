"""Tests for market_regime.hmm_alignment_weight — the standalone HMM gate."""
import sys
import os
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Stub heavy deps before importing market_regime (avoids ccxt + hmmlearn imports)
if "ccxt" not in sys.modules:
    sys.modules["ccxt"] = types.ModuleType("ccxt")
if "pandas" not in sys.modules:
    _pd = types.ModuleType("pandas")
    _pd.DataFrame = MagicMock()
    sys.modules["pandas"] = _pd

from market_regime import hmm_alignment_weight


class TestHmmAlignmentWeight:
    def test_trending_up_long_boost(self):
        regime = {"ok": True, "label": "trending_up", "confidence": 0.8}
        w, reason = hmm_alignment_weight(regime, "Long")
        assert w == 0.20
        assert "trending_up" in reason
        assert "Long" in reason
        assert "+0.2" in reason

    def test_trending_up_short_penalty(self):
        regime = {"ok": True, "label": "trending_up", "confidence": 0.8}
        w, reason = hmm_alignment_weight(regime, "Short")
        assert w == -0.20
        assert "Short" in reason

    def test_trending_down_long_penalty(self):
        regime = {"ok": True, "label": "trending_down", "confidence": 0.8}
        w, reason = hmm_alignment_weight(regime, "Long")
        assert w == -0.20
        assert "trending_down" in reason

    def test_trending_down_short_boost(self):
        regime = {"ok": True, "label": "trending_down", "confidence": 0.8}
        w, reason = hmm_alignment_weight(regime, "Short")
        assert w == 0.20

    def test_ranging_returns_zero(self):
        regime = {"ok": True, "label": "ranging", "confidence": 0.8}
        w_long, _  = hmm_alignment_weight(regime, "Long")
        w_short, _ = hmm_alignment_weight(regime, "Short")
        assert w_long == 0.0
        assert w_short == 0.0

    def test_low_confidence_skipped(self):
        """Confidence below 0.6 — don't bet on boundary regimes."""
        regime = {"ok": True, "label": "trending_up", "confidence": 0.5}
        w, reason = hmm_alignment_weight(regime, "Long")
        assert w == 0.0
        assert "below threshold" in reason

    def test_confidence_at_threshold_works(self):
        """conf == 0.60 should be included (>= not >)."""
        regime = {"ok": True, "label": "trending_up", "confidence": 0.60}
        w, _ = hmm_alignment_weight(regime, "Long")
        assert w == 0.20  # exactly at threshold = pass

    def test_regime_not_ok_returns_zero(self):
        """HMM model didn't run — no signal."""
        regime = {"ok": False, "reason": "hmmlearn not installed"}
        w, reason = hmm_alignment_weight(regime, "Long")
        assert w == 0.0

    def test_none_regime_returns_zero(self):
        w, reason = hmm_alignment_weight(None, "Long")
        assert w == 0.0

    def test_empty_direction_returns_zero(self):
        regime = {"ok": True, "label": "trending_up", "confidence": 0.8}
        w, _ = hmm_alignment_weight(regime, "")
        assert w == 0.0

    def test_unknown_direction_returns_zero(self):
        regime = {"ok": True, "label": "trending_up", "confidence": 0.8}
        w, _ = hmm_alignment_weight(regime, "Sideways")
        assert w == 0.0

    def test_case_insensitive_direction(self):
        regime = {"ok": True, "label": "trending_up", "confidence": 0.8}
        w_lower, _ = hmm_alignment_weight(regime, "long")
        w_upper, _ = hmm_alignment_weight(regime, "LONG")
        assert w_lower == 0.20
        assert w_upper == 0.20
