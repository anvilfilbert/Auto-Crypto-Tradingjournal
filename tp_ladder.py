"""
tp_ladder.py — Multi-TP ladder reader (Task B, 2026-05-21).

Reads the FULL take-profit ladder you've set on the exchange — up to ~7 TPs
per position — and exposes it as a sorted list. Strictly read-only: the
journal never modifies your exchange orders.

Why a separate module: Bitget and Blofin both expose multiple TPs via
"plan orders" (conditional/trigger orders) attached to a position, not via
the main /positions endpoint. The bitget_client and blofin_client modules
focus on regular limit-order pending + open-positions queries; this module
adds the plan-order layer on top.

Return shape per TP entry:
    {"price": 6.50, "size_pct": 30.0, "hit": False, "hit_at": None,
     "order_id": "1234...", "exchange": "bitget"}

`size_pct` is the percentage of the position this TP would close (Bitget
expresses this as a fraction in the planOrder; we normalise to 0-100). If
unknown, we leave it None.

Computed alongside: first_tp_rr (closest-TP R:R, the "min win") and
last_tp_rr (farthest-TP R:R, the "full run").
"""
import json
from typing import Any

import bitget_client
import blofin_client


# ── Bitget ─────────────────────────────────────────────────────────────────────

# Bitget plan-order endpoint: /api/v2/mix/order/orders-plan-pending
# planType values that correspond to take-profit triggers:
#   - 'profit_plan'  — TP plan order (set manually on an open position)
#   - 'pos_profit'   — TP on a position (newer API alias)
#   - 'normal_plan'  — generic conditional order (could be entry OR exit)
# We fetch normal_plan + profit_plan; pos_profit returns an error on most
# accounts (newer Bitget product line), so we skip it.
_BITGET_PLAN_TYPES = ["normal_plan", "profit_plan"]


def _bitget_plan_orders() -> list[dict]:
    """Pull all pending plan orders from Bitget. Returns flat list across types."""
    out = []
    for plan_type in _BITGET_PLAN_TYPES:
        try:
            data = bitget_client._get(
                "/api/v2/mix/order/orders-plan-pending",
                {"productType": "USDT-FUTURES", "planType": plan_type},
            )
        except Exception:
            continue
        rows = []
        if isinstance(data, dict):
            rows = data.get("entrustedList") or data.get("list") or []
        elif isinstance(data, list):
            rows = data
        for r in rows:
            r["_plan_type"] = plan_type
            out.append(r)
    return out


def _bitget_ladder_for(symbol: str, direction: str, entry_price: float,
                       all_plans: list[dict] | None = None) -> list[dict]:
    """Filter the plan-order pool down to TPs for one (symbol, direction)
    and order them by price (long: ascending, short: descending)."""
    if all_plans is None:
        all_plans = _bitget_plan_orders()
    is_long = (direction or "long").lower().startswith("l")
    entries = []
    for r in all_plans:
        if (r.get("symbol") or "").upper() != symbol.upper():
            continue
        pos_side = (r.get("posSide") or "").lower()
        if pos_side and pos_side[:1] != ("l" if is_long else "s"):
            continue
        # Trigger price for plan orders is the level that fires the order
        trigger = float(r.get("triggerPrice") or r.get("price") or 0) or None
        if trigger is None:
            continue
        # For a long position, a TP sits ABOVE the entry; for short, BELOW.
        # Anything that doesn't fit that pattern is a stop, not a TP.
        if is_long and trigger <= entry_price:
            continue
        if (not is_long) and trigger >= entry_price:
            continue
        size = float(r.get("size") or 0) or None
        # Bitget exposes the planned close fraction differently per plan type.
        # We don't always have a position total handy here, so size_pct is left
        # None when we can't derive it confidently.
        entries.append({
            "price":    trigger,
            "size":     size,
            "size_pct": None,
            "hit":      False,
            "hit_at":   None,
            "order_id": r.get("orderId") or r.get("clientOid") or "",
            "plan_type": r.get("_plan_type"),
            "exchange": "bitget",
        })
    entries.sort(key=lambda e: e["price"], reverse=(not is_long))
    return entries


# ── Blofin ────────────────────────────────────────────────────────────────────

def _blofin_ladder_for(symbol: str, direction: str, entry_price: float) -> list[dict]:
    """Blofin exposes a single TP per open position via fetch_positions; for
    multi-TP it uses algo/conditional orders. CCXT's fetch_open_orders with
    'algo' params surfaces those."""
    if not blofin_client.is_configured():
        return []
    is_long = (direction or "long").lower().startswith("l")
    try:
        from ccxt_client import get_blofin_exchange
        ex = get_blofin_exchange()
        raw = ex.fetch_open_orders(symbol=None, params={"type": "swap"}) or []
    except Exception:
        return []
    entries = []
    for o in raw:
        sym_raw = o.get("symbol") or ""
        # Blofin returns 'BTC/USDT:USDT' — normalise to 'BTCUSDT'
        sym = sym_raw.replace("/USDT:USDT", "USDT").replace("/USD:BTC", "USD")
        if sym.upper() != symbol.upper():
            continue
        info = o.get("info") or {}
        side = (o.get("side") or info.get("side") or "").lower()
        # Determine if this is a TP for the right direction
        # On Blofin, a long-position TP has side='sell' (closing the long)
        if is_long and side != "sell":
            continue
        if (not is_long) and side != "buy":
            continue
        trigger = float(info.get("tpTriggerPrice") or o.get("triggerPrice")
                        or o.get("price") or 0) or None
        if trigger is None:
            continue
        if is_long and trigger <= entry_price:
            continue
        if (not is_long) and trigger >= entry_price:
            continue
        entries.append({
            "price":    trigger,
            "size":     float(info.get("size") or o.get("amount") or 0) or None,
            "size_pct": None,
            "hit":      False,
            "hit_at":   None,
            "order_id": o.get("id") or info.get("algoId") or "",
            "plan_type": "blofin_algo",
            "exchange": "blofin",
        })
    entries.sort(key=lambda e: e["price"], reverse=(not is_long))
    return entries


# ── Public API ────────────────────────────────────────────────────────────────

def get_ladder(symbol: str, direction: str, entry_price: float,
               exchange: str = "bitget") -> list[dict]:
    """Return the active TP ladder for one (symbol, direction) pair, sorted
    nearest-to-furthest from entry. Empty list when none configured or call
    fails. Never raises — TP ladder is informational."""
    if not (symbol and entry_price):
        return []
    exch = (exchange or "bitget").lower()
    try:
        if exch == "blofin":
            return _blofin_ladder_for(symbol, direction, float(entry_price))
        return _bitget_ladder_for(symbol, direction, float(entry_price))
    except Exception:
        return []


def compute_rr_extremes(entry: float, sl: float, ladder: list[dict],
                         is_long: bool) -> tuple[float | None, float | None]:
    """Return (first_tp_rr, last_tp_rr) — the minimum-win and full-run R:R
    multiples implied by the ladder. None when ladder is empty or SL is at
    or beyond entry (invalid)."""
    if not ladder or entry is None or sl is None:
        return None, None
    risk = (entry - sl) if is_long else (sl - entry)
    if risk <= 0:
        return None, None
    prices = sorted([e["price"] for e in ladder], reverse=(not is_long))
    nearest  = prices[0]
    farthest = prices[-1]
    if is_long:
        first_rr = (nearest  - entry) / risk
        last_rr  = (farthest - entry) / risk
    else:
        first_rr = (entry - nearest)  / risk
        last_rr  = (entry - farthest) / risk
    return round(first_rr, 2), round(last_rr, 2)


# ── DB helpers ────────────────────────────────────────────────────────────────

def serialise(ladder: list[dict]) -> str:
    return json.dumps(ladder, default=str) if ladder else ""


def deserialise(blob: str | None) -> list[dict]:
    if not blob:
        return []
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return []


def mark_hit(ladder: list[dict], hit_price: float, is_long: bool,
             ts_iso: str) -> list[dict]:
    """Mark every ladder entry whose price is between entry and hit_price as
    hit. Idempotent — re-running with the same hit_price is safe."""
    if not ladder:
        return ladder
    for entry in ladder:
        already = entry.get("hit")
        # A TP at level X is 'hit' when price has traversed it: for a long,
        # the high reached X; for a short, the low reached X.
        passed = (hit_price >= entry["price"]) if is_long else (hit_price <= entry["price"])
        if passed and not already:
            entry["hit"]    = True
            entry["hit_at"] = ts_iso
    return ladder
