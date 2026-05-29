"""
trading.bitget_trader — write-enabled Bitget V2 client for the auto-trader
chain. Margin mode is controlled by the MARGIN_MODE constant below
(default: "crossed" — wallet collateral shared across positions, supports
hedging).

Uses BITGET_TRADER_* env vars (separate from BITGET_* used by the
read-only journal client). Every method is defensive about Elite-account
restrictions — when an endpoint returns "not authorized" we log and
degrade gracefully rather than crash the scheduler.

Methods (only what the auto-trader actually needs):

  read:
    get_balance()          → {available, equity, margin_used}
    get_open_positions()   → list of position dicts
    get_mark_price(symbol) → float
    test_connection()      → {ok, latency_ms, error?}

  write:
    place_market_order(symbol, side, size_usdt, leverage,
                       sl_price, tp1_price, tp2_price)
                          → {order_id, filled_price, sizing}
    modify_position_sl(symbol, side, new_sl_price)
                          → {ok, modified}
    close_position(symbol, side, percentage=100)
                          → {ok, exit_price, realized_pnl}
    cancel_all_orders(symbol=None)
                          → {cancelled_count}

All write calls require FUTURES_AI_ENABLED=1 AND is_real_mode() AND
state=='active' — checked at the dispatcher level (executor.py), not
here. This module trusts the caller.

Idempotency: each place_market_order accepts a client_oid so the
caller can retry safely.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


BASE_URL     = "https://api.bitget.com"
API_KEY      = os.environ.get("BITGET_TRADER_API_KEY", "")
SECRET_KEY   = os.environ.get("BITGET_TRADER_SECRET_KEY", "")
PASSPHRASE   = os.environ.get("BITGET_TRADER_PASSPHRASE", "")
PRODUCT_TYPE = "USDT-FUTURES"

# Margin mode for newly opened auto-trader positions. "crossed" shares
# wallet collateral across all positions on the subaccount, which is the
# right choice when we want to hedge (an offsetting position reduces
# required margin, and a single bad position can't liquidate the book
# in isolation). Existing positions keep whatever mode they were opened
# with — this constant only affects NEW openings. Flip to "isolated"
# here if you ever want to revert.
MARGIN_MODE  = "crossed"


class TraderAPIError(Exception):
    """Raised on any Bitget API failure for the trader chain."""


# ── Signing + transport ──────────────────────────────────────────────────────

def _sign(ts: str, method: str, path: str, body_or_qs: str = "") -> str:
    msg = ts + method.upper() + path + body_or_qs
    return base64.b64encode(
        _hmac.new(SECRET_KEY.encode(), msg.encode(),
                  hashlib.sha256).digest()
    ).decode()


def _headers(ts: str, sig: str) -> dict:
    return {
        "ACCESS-KEY":        API_KEY,
        "ACCESS-SIGN":       sig,
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type":      "application/json",
        "locale":            "en-US",
    }


def _request(method: str, path: str, params: Optional[dict] = None,
             body: Optional[dict] = None, timeout: int = 15) -> dict:
    if not API_KEY or not SECRET_KEY or not PASSPHRASE:
        raise TraderAPIError(
            "BITGET_TRADER_* env vars not set — refusing to call API"
        )

    method = method.upper()
    ts     = str(int(time.time() * 1000))

    qs = urllib.parse.urlencode(params or {}) if params else ""
    body_str = json.dumps(body, separators=(",", ":")) if body else ""

    # Signing differs: GET signs the query string with leading "?",
    # POST signs the body string.
    if method == "GET":
        sig_payload = ("?" + qs) if qs else ""
    else:
        sig_payload = body_str

    sig = _sign(ts, method, path, sig_payload)
    url = BASE_URL + path + (("?" + qs) if qs else "")

    req = urllib.request.Request(
        url, headers=_headers(ts, sig),
        method=method,
        data=body_str.encode("utf-8") if body_str else None,
    )

    socket.setdefaulttimeout(timeout)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        txt = ""
        try: txt = e.read().decode("utf-8", errors="replace")[:300]
        except Exception: pass
        raise TraderAPIError(f"HTTP {e.code} on {method} {path}: {txt}")
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        raise TraderAPIError(f"network failure on {method} {path}: {e}")

    code = resp.get("code", "")
    if code != "00000":
        msg = resp.get("msg") or resp.get("message") or ""
        raise TraderAPIError(f"Bitget code={code} on {method} {path}: {msg}")
    return resp.get("data", {})


# ── Read methods ─────────────────────────────────────────────────────────────

def test_connection() -> dict:
    """Verify creds work + endpoint reachable + measure latency.
    Used by the Futures-AI page health probe before the chain activates."""
    if not API_KEY:
        return {"ok": False, "error": "BITGET_TRADER_API_KEY not set"}
    t0 = time.time()
    try:
        d = _request("GET", "/api/v2/mix/account/account", params={
            "symbol":      "BTCUSDT",
            "productType": PRODUCT_TYPE,
            "marginCoin":  "USDT",
        })
        return {
            "ok":             True,
            "latency_ms":     int((time.time() - t0) * 1000),
            "account_id":     d.get("userId"),
            "margin_coin":    d.get("marginCoin"),
            "available":     float(d.get("available", 0) or 0),
            "locked":        float(d.get("locked", 0) or 0),
        }
    except TraderAPIError as e:
        return {"ok": False, "error": str(e)[:200]}


def get_balance() -> dict:
    """Total USDT equity + free margin on the trader subaccount."""
    d = _request("GET", "/api/v2/mix/account/accounts", params={
        "productType": PRODUCT_TYPE,
    })
    rows = d if isinstance(d, list) else []
    for r in rows:
        if r.get("marginCoin") == "USDT":
            return {
                "available":   float(r.get("available", 0) or 0),
                "equity":      float(r.get("accountEquity") or
                                     r.get("equity", 0) or 0),
                "margin_used": float(r.get("locked", 0) or 0),
                "unrealized_pnl": float(r.get("unrealizedPL", 0) or 0),
            }
    return {"available": 0.0, "equity": 0.0, "margin_used": 0.0,
            "unrealized_pnl": 0.0}


def get_pending_plan_orders(symbol: Optional[str] = None) -> list:
    """Active TPSL plan orders (the actual SL/TP for our positions).
    Returns normalised list of {symbol, plan_type, trigger_price,
    direction, size, order_id, source}."""
    try:
        d = _request("GET", "/api/v2/mix/order/orders-plan-pending", params={
            "productType": PRODUCT_TYPE,
            "planType":    "profit_loss",
        })
        rows = (d or {}).get("entrustedList") or []
    except Exception:
        return []
    out = []
    for o in rows:
        if symbol and o.get("symbol") != symbol:
            continue
        out.append({
            "symbol":         o.get("symbol"),
            "plan_type":      o.get("planType"),  # 'loss_plan' or 'profit_plan'
            "trigger_price":  float(o.get("triggerPrice") or 0),
            "direction":      "Long" if o.get("posSide") == "long" else "Short",
            "size":           float(o.get("size") or 0),
            "order_id":       o.get("orderId"),
            "source":         o.get("enterPointSource"),
        })
    return out


def get_open_positions() -> list:
    """All open positions on the trader subaccount, normalised shape.
    Enriched with the SL/TP trigger prices from pending plan orders."""
    d = _request("GET", "/api/v2/mix/position/all-position", params={
        "productType": PRODUCT_TYPE,
        "marginCoin":  "USDT",
    })
    rows = d if isinstance(d, list) else []
    # Fetch all pending plans ONCE so we can match per position
    plans = get_pending_plan_orders()
    out = []
    for r in rows:
        try:
            total = float(r.get("total") or 0)
            if total <= 0:
                continue
            mark = float(r.get("markPrice") or 0)
            entry = float(r.get("openPriceAvg") or 0)
            # The position-level stopLoss/takeProfit fields are usually
            # empty on Bitget V2 — SL/TP live as separate plan orders.
            # Look them up from the plans list we fetched above.
            sym       = r.get("symbol")
            direction = "Long" if r.get("holdSide") == "long" else "Short"
            sl_plan   = next((p for p in plans
                              if p["symbol"] == sym
                              and p["direction"] == direction
                              and p["plan_type"] == "loss_plan"), None)
            tp_plans  = [p for p in plans
                         if p["symbol"] == sym
                         and p["direction"] == direction
                         and p["plan_type"] == "profit_plan"]
            # BUG-013 fix (2026-05-27): include the FULL list of pending TP
            # trigger prices, not just the first one. The Phase-2 TP-fill
            # detector (`executor._detect_tp_fills`) reads `live.tp_levels`
            # to compare against the originally-placed ladder — without this
            # field it sees an empty list, then marks EVERY tier as "filled"
            # (because no DB price is found in the empty set). That cascaded
            # into the BE-move → SL-orphan loop observed on AZTEC/INJ/TIA.
            tp_levels = [{"price": p["trigger_price"], "size": p.get("size")}
                         for p in tp_plans]
            sl_price  = sl_plan["trigger_price"] if sl_plan else None
            tp1_price = tp_plans[0]["trigger_price"] if tp_plans else None
            out.append({
                "symbol":         sym,
                "direction":      direction,
                "entry_price":    entry,
                "mark_price":     mark,
                "size_contracts": total,
                "notional_usdt":  total * mark if mark else 0,
                "leverage":       int(float(r.get("leverage") or 1)),
                "unrealized_pnl": float(r.get("unrealizedPL") or 0),
                # Two field-name styles for SL/TP — the Bitget-flavoured
                # `preset_*` (matches main-account client) and the short
                # `sl`/`tp1` aliases that downstream consumers expect.
                # Without the aliases, `p.get("sl")` always returned None
                # even when the plan-order was live on Bitget (2026-05-29
                # diagnostic, repeated in `restore_orphan_sls.py`).
                "preset_sl":      sl_price,
                "preset_tp":      tp1_price,
                "sl":             sl_price,
                "tp1":            tp1_price,
                "tp_levels":      tp_levels,   # BUG-013 fix
                "liquidation":    float(r.get("liquidationPrice") or 0) or None,
                "break_even":     float(r.get("breakEvenPrice") or 0) or None,
            })
        except Exception:
            continue
    return out


def get_mark_price(symbol: str) -> float:
    """Live mark price for a symbol. Used by paper.manage to step
    open positions in sync mode without rate-limiting Bitget."""
    d = _request("GET", "/api/v2/mix/market/ticker", params={
        "symbol":      symbol,
        "productType": PRODUCT_TYPE,
    })
    rows = d if isinstance(d, list) else [d]
    if rows and rows[0]:
        return float(rows[0].get("markPrice") or rows[0].get("lastPr") or 0)
    return 0.0


# ── Contract metadata + price snapping ──────────────────────────────────────

_CONTRACT_CACHE: dict[str, dict] = {}


def get_contract_spec(symbol: str) -> dict:
    """
    Return contract metadata: tick size (pricePlace), min/max order
    size, etc. Cached for the process lifetime — these don't change.

    Bitget returns pricePlace = number of decimal places; tick =
    10^(-pricePlace). E.g. pricePlace=4 → tick=0.0001.
    """
    if symbol in _CONTRACT_CACHE:
        return _CONTRACT_CACHE[symbol]
    try:
        d = _request("GET", "/api/v2/mix/market/contracts", params={
            "productType": PRODUCT_TYPE,
            "symbol":      symbol,
        })
        rows = d if isinstance(d, list) else [d]
        if rows and rows[0]:
            spec = rows[0]
            pp = int(spec.get("pricePlace") or 4)
            vp = int(spec.get("volumePlace") or 0)
            _CONTRACT_CACHE[symbol] = {
                "tick_size":    10 ** (-pp),
                "price_place":  pp,
                "vol_place":    vp,
                "size_step":    10 ** (-vp) if vp > 0 else 1,
                "min_size":     float(spec.get("minTradeNum") or 0),
            }
            return _CONTRACT_CACHE[symbol]
    except Exception as e:
        # Fall through to default
        pass
    # Defensive default — 6 decimal places, no min
    return {"tick_size": 0.000001, "price_place": 6, "vol_place": 4,
            "size_step": 0.0001, "min_size": 0}


def _fetch_atr_4h(symbol: str) -> float:
    """4H ATR from the shared chart_context cache. Used by ATR-based
    SL/TP repair when the scanner's levels are pathological. Returns 0
    on any failure so the caller can fall back to passing-through the
    original (possibly bad) levels — the executor will fail loudly at
    Bitget's validator rather than silently submitting noise."""
    try:
        import chart_context
        ctx = chart_context.get_chart_context(symbol, ["4H"]) or {}
        atr = (ctx.get("4H", {}).get("indicators", {}).get("atr") or {})
        return float(atr.get("value") or 0)
    except Exception:
        return 0.0


def _snap_price(price: float, decimals: int) -> float:
    """Snap a price to the symbol's tick grid by rounding to N decimal
    places. Bitget rejects orders whose price/SL/TP aren't multiples of
    the symbol's tick size; rounding to pricePlace decimals is the
    canonical fix."""
    if price is None or price == 0:
        return price
    return round(float(price), decimals)


# ── Write methods — DEFENSIVE ────────────────────────────────────────────────

def place_market_order(symbol: str, side: str, size_usdt: float,
                        leverage: int,
                        sl_price: Optional[float] = None,
                        tp1_price: Optional[float] = None,
                        tp2_price: Optional[float] = None,
                        tp_levels: Optional[list] = None,
                        client_oid: Optional[str] = None) -> dict:
    """
    Place a market entry with preset SL + TP. Bitget V2 lets you bundle
    SL/TP into the place-order request as preset fields — saves us
    making 3 round-trips on every entry.

    Returns {order_id, sizing}. Caller is responsible for verifying the
    position appears via get_open_positions() — Bitget Elite accounts
    sometimes reject the order silently.

    NB: this is the ONE method that takes real money action. Wrap every
    call in a try/except at the caller and log the outcome.
    """
    side = side.lower()
    if side not in ("long", "short"):
        raise TraderAPIError(f"invalid side {side!r}")

    # Set the leverage first (Bitget requires leverage be configured
    # on the symbol/hold-side BEFORE the order). Log success/failure
    # explicitly — silent failures here were causing trades to open at
    # the account-default 10x instead of the Kelly-derived value
    # (observed 22:28 batch: requested 3x, got 10x on 4 of 5 positions).
    import logging
    _log = logging.getLogger(__name__)

    # Switch the symbol's margin mode to MARGIN_MODE if not already.
    # Bitget V2 rejects place-order if the body's marginMode disagrees
    # with the symbol's current mode, so we must align first. Idempotent
    # — Bitget returns success when already in the target mode.
    margin_mode_result = "untried"
    try:
        _request("POST", "/api/v2/mix/account/set-margin-mode", body={
            "symbol":      symbol,
            "productType": PRODUCT_TYPE,
            "marginCoin":  "USDT",
            "marginMode":  MARGIN_MODE,
        })
        margin_mode_result = "ok"
    except TraderAPIError as e:
        emsg = str(e).lower()
        if "already" in emsg or "same" in emsg or "no need" in emsg:
            margin_mode_result = f"already {MARGIN_MODE}"
        else:
            margin_mode_result = f"refused: {str(e)[:100]}"
            _log.warning("[bitget_trader] set-margin-mode %s -> %s failed: %s",
                          symbol, MARGIN_MODE, str(e)[:120])

    leverage_set_result = "untried"
    try:
        _request("POST", "/api/v2/mix/account/set-leverage", body={
            "symbol":      symbol,
            "productType": PRODUCT_TYPE,
            "marginCoin":  "USDT",
            "leverage":    str(int(leverage)),
            "holdSide":    side,
        })
        leverage_set_result = "ok"
    except TraderAPIError as e:
        emsg = str(e).lower()
        if "already" in emsg or "same" in emsg:
            leverage_set_result = f"already at {leverage}x"
        else:
            # Bitget refused — most common cause is symbol's minimum
            # leverage > our request. Log + continue (the order will
            # open at whatever leverage Bitget keeps for the symbol).
            leverage_set_result = f"refused: {str(e)[:100]}"
            _log.warning("[bitget_trader] set-leverage %s %sx %s failed: %s",
                          symbol, leverage, side, str(e)[:120])

    # Compute size in base units (contracts). Bitget expects "size" in
    # contract units, not USDT. Get mark price for the conversion.
    mark = get_mark_price(symbol)
    if mark <= 0:
        raise TraderAPIError(f"can't fetch mark price for {symbol}")

    # Fetch symbol's contract spec for tick-size + size-step snapping.
    # Cached after first lookup so this is essentially free.
    spec = get_contract_spec(symbol)
    pp   = spec["price_place"]
    vp   = spec["vol_place"]

    size_contracts = round(size_usdt / mark, vp)

    # --- Pre-flight validation: SL + TP must be sensible ---
    # Old approach: nudge invalid TP to mark*1.005. That produced 0.5%
    # TPs with deep SLs → R:R 1:0.03 = catastrophic. New approach: when
    # the scanner-supplied levels are invalid (TP on wrong side, or
    # absent), REPLACE with ATR-based defaults so R:R stays sensible.
    is_long = (side == "long")

    # SL refusal: if scanner's SL is already past mark, the trade thesis
    # is broken — refuse the order outright rather than rescue it.
    if sl_price:
        if is_long and sl_price >= mark:
            raise TraderAPIError(
                f"SL {sl_price} is already past mark {mark} for long {symbol} — refusing"
            )
        if not is_long and sl_price <= mark:
            raise TraderAPIError(
                f"SL {sl_price} is already past mark {mark} for short {symbol} — refusing"
            )

    # TP on wrong side → switch to ATR-based default (2× ATR_4H from mark).
    # Keep the scanner SL if it's wider than 1× ATR (preserves operator
    # intent on the risk side); otherwise widen to 1× ATR floor.
    atr_4h = _fetch_atr_4h(symbol)
    if atr_4h > 0:
        # Repair TP if invalid
        tp_invalid = (
            not tp1_price or
            (is_long and tp1_price <= mark) or
            (not is_long and tp1_price >= mark)
        )
        if tp_invalid:
            tp1_price = mark + (2 * atr_4h if is_long else -2 * atr_4h)
        else:
            # If TP would only fire on a microscopic move, replace it
            # with a meaningful ATR-based target.
            tp_distance_pct = abs(tp1_price - mark) / mark
            if tp_distance_pct < 0.015:   # < 1.5% TP is suspect
                tp1_price = mark + (2 * atr_4h if is_long else -2 * atr_4h)

        # Repair SL if absent or too tight
        if not sl_price:
            sl_price = mark - (atr_4h if is_long else -atr_4h)
        else:
            sl_distance = abs(mark - sl_price)
            if sl_distance < atr_4h * 0.5:    # tighter than 0.5× ATR = noise
                sl_price = mark - (atr_4h if is_long else -atr_4h)

    # --- Snap every price to the symbol's tick grid ---
    sl_price  = _snap_price(sl_price,  pp)
    tp1_price = _snap_price(tp1_price, pp)
    tp2_price = _snap_price(tp2_price, pp)

    body = {
        "symbol":       symbol,
        "productType":  PRODUCT_TYPE,
        "marginMode":   MARGIN_MODE,
        "marginCoin":   "USDT",
        "size":         str(size_contracts),
        "side":         "buy" if is_long else "sell",
        "tradeSide":    "open",
        "orderType":    "market",
        "force":        "gtc",
    }
    if client_oid:
        body["clientOid"] = client_oid
    # IMPORTANT: don't pass presetStopLossPrice / presetStopSurplusPrice
    # here. Bitget V2 creates SEPARATE plan orders for those, which then
    # duplicate the ones we attach via place-tpsl-order below. Observed
    # on the 23:46 RKLBUSDT trade: 4 plan orders existed (2 from preset,
    # 2 from our explicit attach). Clean approach is to ONLY use the
    # explicit attach path.

    data = _request("POST", "/api/v2/mix/order/place-order", body=body)
    order_id = data.get("orderId") or data.get("clientOid")

    # Bitget v2 IGNORED the presetStopLossPrice / presetStopSurplusPrice
    # fields on market-order entries — observed via /all-position dump:
    # stopLoss and takeProfit came back empty even though we passed them.
    # Fix: attach SL/TP as SEPARATE plan orders via place-tpsl-order
    # immediately after the entry fills.
    #
    # 2026-05-27 — SL ordering + verify-and-retry (BUG-011 workaround):
    # AZTECUSDT position 132 opened with attached_sl=true but NO actual
    # loss_plan on Bitget. _try_place_tpsl returned True (Bitget echoed an
    # orderId) but the order never persisted. Hypothesis: Bitget V2 has a
    # brief settling window after a market fill — SL placed inside that
    # window gets ack'd but silently dropped. TPs placed ~50ms later
    # consistently succeed because state has caught up.
    # Fix has two layers:
    #   (1) place SL AFTER all TPs so Bitget state is settled
    #   (2) verify the SL appears in pending plans; retry once on miss
    # Layer (2) is a workaround for the underlying race; layer (1) reduces
    # how often the race triggers. The deeper root cause needs a controlled
    # experiment — see project_review_2026_05_28.md.
    attached_sl  = False
    attached_tp1 = False
    tp_attach_results: list = []   # [{idx, price, pct, size, ok}] per tier

    # ── Multi-TP plan-order placement (Phase 2) ────────────────────────────
    # When tp_levels is provided (list of {idx, price, pct, ...}), place ONE
    # profit_plan per tier sized at size_contracts × pct/100. Falls back to
    # the legacy single-TP path (tp1_price) when tp_levels is None or empty
    # so older callers + tests keep working.
    if tp_levels:
        # Snap each tier's price to tick + size to step. Skip tiers where
        # the slice is below the symbol's min order size — Bitget would
        # reject and we'd leak the order.
        remaining = size_contracts
        for i, lvl in enumerate(tp_levels):
            try:
                price_raw = float(lvl.get("price") or 0)
                pct_raw   = float(lvl.get("pct") or 0)
            except (TypeError, ValueError):
                continue
            if price_raw <= 0 or pct_raw <= 0:
                continue
            # For the LAST tier we close whatever's left over so rounding
            # noise doesn't leave a sliver of position un-closed (Bitget
            # then refuses the close because <min_size).
            if i == len(tp_levels) - 1:
                slice_size = round(remaining, vp)
            else:
                slice_size = size_contracts * pct_raw / 100.0
                # Snap to volumePlace precision so Bitget accepts
                slice_size = round(slice_size, vp)
            if slice_size <= 0:
                continue
            # Refuse sub-min-size slices — Bitget rejects them silently
            min_sz = float(spec.get("min_size") or 0)
            if min_sz and slice_size < min_sz:
                continue
            tp_price = _snap_price(price_raw, pp)
            ok = _try_place_tpsl(symbol, side, tp_price,
                                  slice_size, plan_type="profit_plan")
            tp_attach_results.append({
                "idx":   int(lvl.get("idx") or i + 1),
                "price": tp_price,
                "pct":   pct_raw,
                "size":  slice_size,
                "ok":    ok,
            })
            if ok and i == 0:
                attached_tp1 = True  # back-compat flag
            remaining = max(0.0, remaining - slice_size)
    elif tp1_price:
        # Legacy single-TP path — preserved for backward compat
        attached_tp1 = _try_place_tpsl(symbol, side, tp1_price,
                                        size_contracts, plan_type="profit_plan")

    # ── SL placement (AFTER TPs, with verify+retry — BUG-011 workaround) ───
    # Place SL after the TP burst so Bitget's position-state settling window
    # is past. Then verify the SL actually persists; retry once on miss.
    if sl_price:
        attached_sl = _place_sl_with_verify(symbol, side, sl_price,
                                             size_contracts)
        if not attached_sl:
            _log.warning(
                "[bitget_trader] SL failed to persist on %s @ %s after retry — "
                "position is OPEN WITHOUT STOP-LOSS. Operator must place manually.",
                symbol, sl_price
            )

    # Query the actual leverage Bitget recorded on the position so the
    # caller knows whether set-leverage worked or fell back. Best-effort
    # — if the position isn't visible yet (rare race), we report the
    # requested value with a note.
    actual_leverage = leverage
    try:
        live = get_open_positions()
        match = next((p for p in live
                      if p["symbol"] == symbol
                      and p["direction"].lower() == side), None)
        if match:
            actual_leverage = int(match.get("leverage") or leverage)
            if actual_leverage != leverage:
                _log.warning(
                    "[bitget_trader] leverage mismatch on %s: requested %sx, "
                    "Bitget set %sx — likely symbol minimum > request",
                    symbol, leverage, actual_leverage
                )
    except Exception:
        pass

    return {
        "order_id":             order_id,
        "size_contracts":       size_contracts,
        "size_usdt":            size_usdt,
        "leverage_requested":   leverage,
        "leverage_actual":      actual_leverage,
        "leverage":             actual_leverage,   # back-compat field
        "set_leverage_result":  leverage_set_result,
        "mark_at_entry":        mark,
        "sl":                   sl_price,
        "tp1":                  tp1_price,
        "tp2":                  tp2_price,
        "attached_sl":          attached_sl,
        "attached_tp1":         attached_tp1,
        "tp_attach_results":    tp_attach_results,   # Phase 2 — per-tier outcomes
    }


def _try_place_tpsl(symbol: str, side: str, trigger_price: float,
                     size_contracts: float, plan_type: str) -> bool:
    """
    Attach a position-level SL or TP plan order.
      plan_type='loss_plan'    → stop-loss
      plan_type='profit_plan'  → take-profit
    Returns True if Bitget accepted the order.
    """
    side = side.lower()
    body = {
        "symbol":        symbol,
        "productType":   PRODUCT_TYPE,
        "marginMode":    MARGIN_MODE,
        "marginCoin":    "USDT",
        "planType":      plan_type,
        "triggerPrice":  str(trigger_price),
        "triggerType":   "fill_price",
        "executePrice":  "",                  # market-close on trigger
        "holdSide":      side,
        "size":          str(size_contracts), # close 100% of position
    }
    try:
        _request("POST", "/api/v2/mix/order/place-tpsl-order", body=body)
        return True
    except TraderAPIError as e:
        # Log via Python logging — caller's order_id is still valid even
        # if SL/TP attach fails. Operator alerted via the futures_ai_log
        # 'real_tpsl_failed' event from the caller side.
        import logging
        logging.getLogger(__name__).warning(
            "Failed to attach %s on %s @ %s: %s",
            plan_type, symbol, trigger_price, str(e)[:200]
        )
        return False


def _place_sl_with_verify(symbol: str, side: str, sl_price: float,
                            size_contracts: float,
                            max_retries: int = 2,
                            settle_delay_sec: float = 0.6,
                            extended_wait_sec: float = 2.0) -> bool:
    """
    Place a stop-loss plan order and verify it actually persisted on Bitget.

    BUG-011 (AZTECUSDT pos 132, 2026-05-27): /place-tpsl-order returned a
    successful orderId but the SL never appeared in pending-plan listings.
    Hypothesis: Bitget V2 has a brief window after a market fill where SL
    placements are ack'd but silently dropped. TPs that fire ~50ms later
    succeed because position state has caught up by then.

    Hardening 2026-05-30:
      - max_retries bumped 1→2 (up to 3 placement attempts)
      - two-phase verify: 0.6s quick check, then another 2.0s wait if missing
        before declaring silent-drop. Eliminates false-positive retries that
        were only delayed propagation, not actual drops — those caused
        duplicate SL plans on Bitget.
      - exponential backoff between attempts (1× → 2× → 4× of settle delay)

    Returns True only when the loss_plan is verified live on Bitget.
    """
    import time, logging
    _log = logging.getLogger(__name__)

    def _sl_visible():
        try:
            plans = get_pending_plan_orders(symbol)
        except Exception as e:
            _log.warning("[bitget_trader] SL verify query failed for %s: %s",
                         symbol, str(e)[:120])
            return False
        for p in plans:
            if (p.get("plan_type") == "loss_plan"
                and p.get("trigger_price") is not None
                and abs(float(p["trigger_price"]) - sl_price) / max(sl_price, 1e-9) < 0.001):
                return True
        return False

    for attempt in range(max_retries + 1):
        ok = _try_place_tpsl(symbol, side, sl_price,
                              size_contracts, plan_type="loss_plan")
        if not ok:
            # Bitget refused outright; back off and try again.
            if attempt < max_retries:
                time.sleep(settle_delay_sec * (2 ** attempt))
                continue
            return False
        # Quick check (covers normal propagation)
        time.sleep(settle_delay_sec)
        if _sl_visible():
            return True
        # Extended wait before declaring silent-drop. Eliminates the duplicate-
        # SL hazard from retrying too eagerly when Bitget's listing was just slow.
        time.sleep(extended_wait_sec)
        if _sl_visible():
            return True
        if attempt < max_retries:
            _log.warning(
                "[bitget_trader] SL ack'd but invisible after %.1fs for %s @ %s "
                "(attempt %d/%d) — retrying placement",
                settle_delay_sec + extended_wait_sec,
                symbol, sl_price, attempt + 1, max_retries + 1,
            )
    return False


def attach_sl_tp_to_existing(symbol: str, side: str,
                               sl_price: Optional[float] = None,
                               tp_price: Optional[float] = None) -> dict:
    """
    Retro-attach SL/TP to an already-open position. Used when a previous
    entry didn't attach the levels (e.g. the now-fixed Bitget V2 preset
    bug) and the operator needs to remediate.
    """
    positions = get_open_positions()
    match = next((p for p in positions
                  if p["symbol"] == symbol
                  and p["direction"].lower() == side.lower()), None)
    if not match:
        raise TraderAPIError(f"no open {side} position on {symbol}")
    size = float(match["size_contracts"])

    # Re-snap to tick
    spec = get_contract_spec(symbol)
    pp = spec["price_place"]
    sl_price = _snap_price(sl_price, pp) if sl_price else None
    tp_price = _snap_price(tp_price, pp) if tp_price else None

    result = {"symbol": symbol, "side": side, "size_contracts": size}
    if sl_price:
        result["attached_sl"] = _try_place_tpsl(symbol, side, sl_price,
                                                  size, "loss_plan")
    if tp_price:
        result["attached_tp"] = _try_place_tpsl(symbol, side, tp_price,
                                                  size, "profit_plan")
    return result


def get_position_history(start_ms: int, end_ms: int,
                           symbol: Optional[str] = None,
                           limit: int = 20) -> list:
    """
    Closed position history. Used by the executor's reconcile path to
    fill in the realized_pnl for auto-trader trades that closed via
    Bitget's preset SL/TP.
    """
    params = {
        "productType": PRODUCT_TYPE,
        "startTime":   str(start_ms),
        "endTime":     str(end_ms),
        "pageSize":    str(limit),
    }
    if symbol:
        params["symbol"] = symbol
    d = _request("GET", "/api/v2/mix/position/history-position", params=params)
    if isinstance(d, dict) and "list" in d:
        rows = d.get("list") or []
    else:
        rows = d if isinstance(d, list) else []
    out = []
    for r in rows:
        out.append({
            "symbol":          r.get("symbol"),
            "direction":       "Long" if r.get("holdSide") == "long" else "Short",
            "open_price":      float(r.get("openAvgPrice") or 0),
            "close_price":     float(r.get("closeAvgPrice") or 0),
            "size_contracts":  float(r.get("closeTotalPos") or r.get("openTotalPos") or 0),
            "open_ms":         int(r.get("ctime") or 0),
            "close_ms":        int(r.get("utime") or 0),
            "pnl":             float(r.get("pnl") or 0),
            "net_profit":      float(r.get("netProfit") or 0),
            "open_fee":        float(r.get("openFee") or 0),
            "close_fee":       float(r.get("closeFee") or 0),
            "total_funding":   float(r.get("totalFunding") or 0),
            "position_id":     r.get("positionId"),
        })
    return out


def modify_position_sl(symbol: str, side: str, new_sl_price: float) -> dict:
    """
    Move the position's SL. Called by the BE-trigger and trail rules.

    Bitget V2 stores SL/TP as separate plan orders — there's no
    'modify-position-tpsl' endpoint (the old code called that, got
    HTTP 404 every time, and BE never actually moved). The V2 pattern:
      1. Look up the existing loss_plan for (symbol, side)
      2. Cancel it via cancel-plan-order
      3. Place a fresh loss_plan at the new price via place-tpsl-order

    Tick-size snapping is applied to new_sl_price before submission.
    Returns {ok: bool, action: str, ...} with diagnostic fields.
    """
    side = side.lower()
    # Snap to the symbol's price grid (otherwise Bitget rejects 45115)
    try:
        spec = get_contract_spec(symbol)
        new_sl_price = _snap_price(float(new_sl_price), spec["price_place"])
    except Exception:
        pass

    # BUG-012 fix (2026-05-27): validate new SL is on the correct side of
    # current mark BEFORE cancelling the existing SL. Without this, a BE
    # move triggered when TP1 was incorrectly marked hit would cancel the
    # protective SL and then fail to place the new one (Bitget rejects SL
    # placed past mark with error 40834), leaving the position completely
    # unprotected for the next 10 minutes until the monitor retries
    # (and fails again the same way). Observed today: AZTEC/TIA/INJ all
    # lost their SLs through this cancel+fail+retry loop.
    try:
        mark = get_mark_price(symbol)
        if mark > 0:
            is_long = (side == "long")
            new_sl_invalid = ((is_long and new_sl_price >= mark)
                              or (not is_long and new_sl_price <= mark))
            if new_sl_invalid:
                return {
                    "ok": False,
                    "reason": (f"refusing to move SL — new_sl={new_sl_price} "
                               f"is on wrong side of mark={mark} for {side} "
                               f"position. Existing SL preserved."),
                }
    except Exception:
        # Mark fetch failed — better to bail than risk a cancel+orphan.
        return {"ok": False, "reason": "could not fetch mark for SL validation"}

    # Find the existing loss_plan for this side
    plans  = get_pending_plan_orders(symbol)
    existing = next((p for p in plans
                     if p["plan_type"] == "loss_plan"
                     and (p.get("direction") or "").lower() == side),
                    None)
    old_sl_price = float(existing.get("trigger_price") or 0) if existing else 0.0

    # Resolve size from the LIVE position — not from the stale plan order.
    # BUG fix 2026-05-29: after a TP1 partial close, the old loss_plan still
    # encodes the pre-TP1 full size. Placing a new SL with that stale size
    # was rejected by Bitget code 43023 ("Insufficient position") and the
    # cancel had already succeeded → positions silently orphaned without SL.
    # Observed on XPL/MMT/TIA/AZTEC/DEXE 2026-05-24..05-29.
    positions = get_open_positions()
    match = next((p for p in positions
                  if p["symbol"] == symbol
                  and (p.get("direction") or "").lower() == side),
                 None)
    size = float(match.get("size_contracts") or 0) if match else 0.0
    if not size:
        return {"ok": False, "reason": f"could not resolve live size for {symbol} {side}"}

    # Cancel the existing loss_plan, if any (best-effort)
    if existing:
        try:
            _request("POST", "/api/v2/mix/order/cancel-plan-order", body={
                "symbol":      symbol,
                "productType": PRODUCT_TYPE,
                "marginCoin":  "USDT",
                "planType":    "loss_plan",
                "orderIdList": [{"orderId": existing["order_id"], "clientOid": ""}],
            })
        except TraderAPIError as e:
            # Cancel failed — don't try to place new (Bitget will reject a
            # second loss_plan); leave the existing SL in place.
            return {"ok": False, "reason": f"cancel old SL failed: {str(e)[:100]}"}

    # Place the new loss_plan; verify+retry to defeat the BUG-011 race.
    placed = _place_sl_with_verify(symbol, side, new_sl_price, size)
    if placed:
        return {"ok": True, "action": "cancel+replace", "new_sl": new_sl_price,
                "cancelled_old": bool(existing)}

    # Rollback: new SL failed to place. Re-attach the OLD SL price (best
    # effort) so the position isn't left orphaned. This protects against
    # transient Bitget rejections (size mismatch race, tick-snap, etc.).
    if old_sl_price > 0:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "modify_position_sl: new SL %s failed on %s — rolling back to old SL %s",
            new_sl_price, symbol, old_sl_price,
        )
        try:
            rb = _snap_price(old_sl_price, spec["price_place"])
        except Exception:
            rb = old_sl_price
        rollback_ok = _place_sl_with_verify(symbol, side, rb, size)
        return {"ok": False, "reason": "place new loss_plan failed",
                "rollback_attempted": True,
                "rollback_ok": bool(rollback_ok),
                "rolled_back_to": rb if rollback_ok else None}

    return {"ok": False, "reason": "place new loss_plan failed",
            "rollback_attempted": False}


def close_position(symbol: str, side: str,
                    percentage: float = 100.0) -> dict:
    """Market-close (full or partial). Used by paper's force_close and
    by MAE-breach auto-cuts.

    Bitget V2 accounts default to **hedge_mode** where positions track
    holdSide explicitly. The old `place-order` with `tradeSide=close`
    path fails with code 22002 "No position to close" in hedge_mode
    even when the position is visible — Bitget requires the dedicated
    `close-positions` endpoint for hedged accounts.

    For full close (100%) we use /close-positions which works in BOTH
    one-way and hedge mode. For partial close we fall back to
    place-order with the holdSide field added.
    """
    side = side.lower()
    positions = get_open_positions()
    match = next((p for p in positions
                  if p["symbol"] == symbol and
                  p["direction"].lower() == side), None)
    if not match:
        return {"ok": False, "reason": f"no open {side} position on {symbol}"}

    # Full close — use /close-positions (works in hedge_mode + one_way)
    if percentage >= 100.0:
        body = {
            "symbol":      symbol,
            "holdSide":    side,
            "productType": PRODUCT_TYPE,
        }
        try:
            data = _request("POST", "/api/v2/mix/order/close-positions", body=body)
        except TraderAPIError as e:
            return {"ok": False, "reason": f"close-positions failed: {str(e)[:120]}"}
        success = (data or {}).get("successList") or []
        if not success:
            return {"ok": False, "reason": "close-positions returned no successList",
                    "raw": data}
        return {"ok": True, "order_id": success[0].get("orderId"),
                "closed_size_contracts": match["size_contracts"],
                "exit_price_approx": match.get("mark_price")}

    # Partial close — place-order with holdSide explicit
    qty = round(match["size_contracts"] * (percentage / 100.0), 6)
    body = {
        "symbol":       symbol,
        "productType":  PRODUCT_TYPE,
        "marginMode":   MARGIN_MODE,
        "marginCoin":   "USDT",
        "size":         str(qty),
        "side":         "sell" if side == "long" else "buy",
        "tradeSide":    "close",
        "holdSide":     side,        # REQUIRED in hedge_mode
        "orderType":    "market",
        "force":        "gtc",
    }
    try:
        data = _request("POST", "/api/v2/mix/order/place-order", body=body)
    except TraderAPIError as e:
        return {"ok": False, "reason": f"partial close failed: {str(e)[:120]}"}
    return {"ok": True, "order_id": data.get("orderId"),
            "closed_size_contracts": qty,
            "exit_price_approx": match.get("mark_price")}


def cancel_all_orders(symbol: Optional[str] = None) -> dict:
    """Cancel all open plan/limit orders. Used when the chain pauses."""
    body = {"productType": PRODUCT_TYPE, "marginCoin": "USDT"}
    if symbol:
        body["symbol"] = symbol
    data = _request("POST", "/api/v2/mix/order/cancel-all-orders",
                     body=body)
    return {"ok": True, "cancelled": data}
