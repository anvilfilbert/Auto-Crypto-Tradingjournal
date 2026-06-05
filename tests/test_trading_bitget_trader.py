"""Regression tests for trading/bitget_trader.py.

Focused on the two real-money bugs caught during the 2026-05-23 audit:
1. modify_position_sl used to hit a non-existent V2 endpoint
   (/api/v2/mix/order/modify-position-tpsl → HTTP 404 every time)
2. close_position used place-order + tradeSide=close which fails in
   hedge_mode with HTTP 400 "No position to close" — needs the
   dedicated /close-positions endpoint

These tests use mocked _request so they DON'T hit Bitget live.
"""
import sys

# Earlier test files may have stubbed `trading.bitget_trader` with a
# MagicMock-only module to test executor in isolation. We need the REAL
# implementation to test it here, so evict any stale stub from sys.modules
# BEFORE this module's tests resolve their `from trading import bitget_trader`
# imports (2026-05-24 cross-file pollution fix).
sys.modules.pop("trading.bitget_trader", None)

from unittest.mock import patch, MagicMock
import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_get_open_positions(symbol="BTCUSDT", side="long", size=0.0001,
                              entry=75000.0, mark=75100.0):
    """Return a mock get_open_positions response with one matching position."""
    return [{
        "symbol":         symbol,
        "direction":      side.title(),
        "entry_price":    entry,
        "mark_price":     mark,
        "size_contracts": size,
        "notional_usdt":  size * entry,
        "preset_sl":      entry * 0.95,
        "preset_tp":      entry * 1.10,
    }]


def _mock_pending_plan(symbol="BTCUSDT", side="long",
                       sl_trigger=70000.0, order_id="plan_123"):
    return [{
        "symbol":         symbol,
        "plan_type":      "loss_plan",
        "trigger_price":  sl_trigger,
        "direction":      side.title(),
        "size":           0.0001,
        "order_id":       order_id,
        "source":         "android",
    }]


# ── modify_position_sl ─────────────────────────────────────────────────────────

class TestModifyPositionSL:
    """The old endpoint /api/v2/mix/order/modify-position-tpsl returned 404.
    New impl uses cancel-plan + place-tpsl pattern."""

    def test_calls_correct_endpoints_in_order(self):
        from trading import bitget_trader as bt
        calls = []
        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body") or kwargs.get("params")))
            if path == "/api/v2/mix/market/contracts":
                return [{"pricePlace": "2", "minTradeNum": "0.0001"}]
            return {"orderId": "fake"}
        # _place_sl_with_verify calls get_pending_plan_orders to verify the
        # new SL persisted. Return the OLD plan on the first call (cancel
        # lookup) and the NEW SL on subsequent calls (verify step).
        state = {"placed": False}
        def fake_plans(symbol=None):
            if state["placed"]:
                return [{"symbol": "BTCUSDT", "plan_type": "loss_plan",
                         "trigger_price": 75000.0, "direction": "Long",
                         "size": 0.0001, "order_id": "new_plan_xyz"}]
            return _mock_pending_plan()
        def fake_place(method, path, **kwargs):
            if path == "/api/v2/mix/order/place-tpsl-order":
                state["placed"] = True
            return fake_request(method, path, **kwargs)
        with patch("trading.bitget_trader._request", side_effect=fake_place), \
             patch("trading.bitget_trader.get_pending_plan_orders",
                   side_effect=fake_plans), \
             patch("trading.bitget_trader.get_open_positions",
                   return_value=_mock_get_open_positions()):
            result = bt.modify_position_sl("BTCUSDT", "long", 75000.0)

        # New SL placement must succeed
        assert result.get("ok") is True, f"unexpected: {result}"
        assert result.get("action") == "cancel+replace"

        # MUST NOT call the dead V1 endpoint
        called_paths = [c[1] for c in calls]
        assert "/api/v2/mix/order/modify-position-tpsl" not in called_paths, \
            "modify_position_sl regressed to the broken V1 endpoint"

        # MUST call cancel-plan-order then place-tpsl-order
        assert "/api/v2/mix/order/cancel-plan-order" in called_paths
        assert "/api/v2/mix/order/place-tpsl-order" in called_paths

    def test_returns_dict_with_ok_false_when_no_size_resolvable(self):
        from trading import bitget_trader as bt
        with patch("trading.bitget_trader._request", return_value={}), \
             patch("trading.bitget_trader.get_pending_plan_orders", return_value=[]), \
             patch("trading.bitget_trader.get_open_positions", return_value=[]):
            result = bt.modify_position_sl("BTCUSDT", "long", 70000.0)
        assert result["ok"] is False
        assert "size" in result["reason"].lower()

    def test_rollback_when_new_sl_place_fails(self):
        """When the new SL can't be placed, the OLD SL price is re-attached."""
        from trading import bitget_trader as bt
        calls = []
        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body") or kwargs.get("params")))
            if path == "/api/v2/mix/market/contracts":
                return [{"pricePlace": "2", "minTradeNum": "0.0001"}]
            return {"orderId": "fake"}
        # Always return the OLD plan from get_pending_plan_orders — verify
        # of the new placement never sees the new SL → place_with_verify
        # returns False → rollback kicks in.
        state = {"rb_done": False}
        def fake_plans(symbol=None):
            if state["rb_done"]:
                # After rollback attempt, simulate old SL coming back
                return _mock_pending_plan(sl_trigger=70000.0)
            return _mock_pending_plan(sl_trigger=70000.0)
        def fake_place(method, path, **kwargs):
            if path == "/api/v2/mix/order/place-tpsl-order":
                body = kwargs.get("body") or {}
                # Rollback re-places at the old price; flag it
                if str(body.get("triggerPrice")) == "70000.0":
                    state["rb_done"] = True
            return fake_request(method, path, **kwargs)
        with patch("trading.bitget_trader._request", side_effect=fake_place), \
             patch("trading.bitget_trader.get_pending_plan_orders",
                   side_effect=fake_plans), \
             patch("trading.bitget_trader.get_open_positions",
                   return_value=_mock_get_open_positions()):
            result = bt.modify_position_sl("BTCUSDT", "long", 75000.0)

        assert result["ok"] is False
        assert result.get("rollback_attempted") is True


# ── close_position ─────────────────────────────────────────────────────────────

class TestClosePosition:
    """Hedge-mode requires /close-positions endpoint, not place-order + tradeSide=close."""

    def test_full_close_uses_close_positions_endpoint(self):
        from trading import bitget_trader as bt
        calls = []
        def fake_request(method, path, **kwargs):
            calls.append((method, path))
            if path == "/api/v2/mix/order/close-positions":
                return {"successList": [{"orderId": "abc"}], "failureList": []}
            return {}
        with patch("trading.bitget_trader._request", side_effect=fake_request), \
             patch("trading.bitget_trader.get_open_positions",
                   return_value=_mock_get_open_positions()):
            result = bt.close_position("BTCUSDT", "long", percentage=100.0)

        assert result["ok"] is True
        called_paths = [c[1] for c in calls]
        assert "/api/v2/mix/order/close-positions" in called_paths
        # MUST NOT use place-order for 100% close (broken in hedge_mode)
        assert "/api/v2/mix/order/place-order" not in called_paths, \
            "full close regressed to place-order pattern (broken in hedge_mode)"

    def test_partial_close_uses_place_order_with_holdside(self):
        from trading import bitget_trader as bt
        captured_body = {}
        def fake_request(method, path, **kwargs):
            if path == "/api/v2/mix/order/place-order":
                captured_body.update(kwargs.get("body") or {})
            return {"orderId": "abc"}
        with patch("trading.bitget_trader._request", side_effect=fake_request), \
             patch("trading.bitget_trader.get_open_positions",
                   return_value=_mock_get_open_positions()):
            result = bt.close_position("BTCUSDT", "long", percentage=50.0)

        assert result["ok"] is True
        # holdSide MUST be in the body (hedge_mode requirement)
        assert captured_body.get("holdSide") == "long", \
            f"holdSide missing from partial-close body: {captured_body}"
        assert captured_body.get("tradeSide") == "close"

    def test_returns_ok_false_when_no_matching_position(self):
        from trading import bitget_trader as bt
        with patch("trading.bitget_trader.get_open_positions", return_value=[]):
            result = bt.close_position("BTCUSDT", "long")
        assert result["ok"] is False
        assert "no open" in result["reason"].lower()


# ── _categorize_close_reason ───────────────────────────────────────────────────

class TestCloseReasonCategorisation:
    """The new close_reason categoriser used by _mark_closed."""

    def test_take_profit_long(self):
        from trading.executor import _categorize_close_reason
        # Long: entry 100, close 110, pnl +5 → TP
        assert _categorize_close_reason(5.0, 100.0, 110.0, "Long") == "TP"

    def test_stop_loss_long(self):
        from trading.executor import _categorize_close_reason
        # Long: entry 100, close 90, pnl -10 → SL
        assert _categorize_close_reason(-10.0, 100.0, 90.0, "Long") == "SL"

    def test_take_profit_short(self):
        from trading.executor import _categorize_close_reason
        # Short: entry 100, close 90, pnl +10 → TP (profit on Short = price down)
        assert _categorize_close_reason(10.0, 100.0, 90.0, "Short") == "TP"

    def test_early_close_within_half_pct(self):
        from trading.executor import _categorize_close_reason
        # tiny move → early close
        assert _categorize_close_reason(0.1, 100.0, 100.3, "Long") == "early_close"

    def test_be_trigger_passes_through(self):
        # The close_reason label was renamed BE → BE_stop on 2026-05-24
        # to distinguish "BE-move event" (real_be in the log) from
        # "stop fired at the BE-moved level" (the close_reason).
        from trading.executor import _categorize_close_reason
        assert _categorize_close_reason(0, 100, 100, "Long",
                                          raw_reason="be_trigger fired") == "BE_stop"

    def test_hedge_unwind_preserved(self):
        from trading.executor import _categorize_close_reason
        out = _categorize_close_reason(0, 100, 100, "Short",
                                         raw_reason="hedge_unwind: BTC recovered")
        assert out.startswith("hedge_unwind")
