"""
scanner_event_trigger — basic behavioural tests.

The module spawns a daemon thread in production; here we test the pure
helpers (15m move calc + cooldown gate) without spinning a thread.
"""
import sys
import time
import types
from unittest.mock import patch

import pytest

# Stub chart_candles before importing the module-under-test
chart_stub = types.ModuleType("chart_candles")
def _df_with_closes(closes):
    """Minimal dataframe-like with .close.astype(float).tolist()."""
    class _Col:
        def __init__(self, vals):
            self.vals = list(vals)
        def astype(self, _t):
            return self
        def tolist(self):
            return self.vals
    class _DF:
        def __init__(self, c):
            self._closes = _Col(c)
        def __len__(self):
            return len(self._closes.vals)
        def __getitem__(self, k):
            return self._closes if k == "close" else None
    return _DF(closes)
chart_stub.get_candles = lambda sym, tf, limit=3: _df_with_closes([60000, 60100, 60200])
sys.modules["chart_candles"] = chart_stub

import scanner_event_trigger as sct  # noqa: E402


# ── _last_15m_move ───────────────────────────────────────────────────────────

def test_move_calc_uses_two_closed_candles():
    """When 3+ rows are present, use the second-to-last vs third-to-last
    (both fully closed). The last row may be the in-flight current candle."""
    chart_stub.get_candles = lambda *a, **kw: _df_with_closes([100, 102, 999])
    # 100 → 102 = 2% (the 999 is the in-flight, ignored)
    assert sct._last_15m_move("BTCUSDT") == pytest.approx(0.02, rel=1e-6)


def test_move_calc_returns_none_on_empty():
    chart_stub.get_candles = lambda *a, **kw: _df_with_closes([])
    assert sct._last_15m_move("BTCUSDT") is None


def test_move_calc_returns_none_on_single_row():
    chart_stub.get_candles = lambda *a, **kw: _df_with_closes([100])
    assert sct._last_15m_move("BTCUSDT") is None


def test_move_calc_returns_abs_value():
    """Down-moves count the same as up-moves — we trigger on volatility."""
    chart_stub.get_candles = lambda *a, **kw: _df_with_closes([100, 98, 0])
    assert sct._last_15m_move("BTCUSDT") == pytest.approx(0.02, rel=1e-6)


def test_move_calc_returns_none_on_zero_prev():
    """Defensive: avoid divide-by-zero on degenerate data."""
    chart_stub.get_candles = lambda *a, **kw: _df_with_closes([0, 1, 2])
    assert sct._last_15m_move("BTCUSDT") is None


def test_move_calc_two_row_fallback():
    """When the source only returns 2 rows, use them directly."""
    chart_stub.get_candles = lambda *a, **kw: _df_with_closes([100, 101.5])
    assert sct._last_15m_move("BTCUSDT") == pytest.approx(0.015, rel=1e-6)


# ── Config knobs ────────────────────────────────────────────────────────────

def test_default_threshold_is_2pct():
    assert sct._THRESHOLD_PCT == pytest.approx(0.02, rel=1e-9)


def test_default_cooldown_30min():
    assert sct._COOLDOWN_SEC == 1800


def test_default_symbols_are_btc_eth():
    assert sct._TRIGGER_SYMBOLS == ["BTCUSDT", "ETHUSDT"]
