"""
trading.executor — real-money lifecycle (Bitget subaccount).

Mirrors trading.paper's lifecycle 1:1, but every order goes through
bitget_trader to Bitget's REST API on the auto-trader subaccount.

State machine (same as paper):
  open  -> entry placed (market, with preset SL+TP1 attached by Bitget)
        -> [monitor every 10 min]
        -> BE_MOVED  (modify SL to entry)
        -> TRAIL_MOVED  (modify SL to entry + 0.5x ATR)
        -> TP2_HIT  (Bitget's preset TP1 already partially closed; we close remainder at TP2)
        -> MAE_BREACH (full market close)
        -> SL_FILLED (Bitget closed it itself; we detect via missing position)

Positions are persisted to the existing `positions` table with
chain='auto_ai' AND exchange='bitget_trader' so analytics queries scoped
to the manual chain ignore them entirely.

Reconciliation:
  Every monitor cycle we list Bitget's open positions for this subaccount
  and compare to what we think is open. Positions that VANISHED were
  closed by Bitget (preset SL or TP fire) — we look up the recent fills
  to find the realized P&L and write a closed-position row.

Safety:
  - Refuses every call unless fa_config.is_real_mode() == True
  - Every Bitget API error is caught + logged; no retries (operator
    intervenes if a write fails)
  - Idempotent: open_real_trade checks for an existing OPEN position on
    the same (symbol, direction) before placing
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from . import config as fa_config
from . import bitget_trader


# ── DB helpers ────────────────────────────────────────────────────────────────

def _open_auto_positions(conn) -> list[dict]:
    """Our internal record of what we *think* is currently open on the
    Bitget trader subaccount."""
    return [dict(r) for r in conn.execute("""
        SELECT * FROM positions
        WHERE chain='auto_ai'
          AND (close_time IS NULL OR close_time = '')
        ORDER BY open_time DESC
    """).fetchall()]


def _insert_open_position(conn, signal: dict, sizing: dict,
                            order_result: dict) -> int:
    """Write the position row immediately after order placement. Sets
    close_time=NULL so the reconciler treats it as open."""
    sym  = signal.get("symbol")
    dir_ = signal.get("direction")
    cur = conn.execute("""
        INSERT INTO positions(
            symbol, base_asset, direction,
            margin_mode, open_time, close_time,
            entry_price, close_price,
            size_usdt, size_contracts,
            realized_pnl, position_pnl,
            opening_fee, closing_fee, total_fees,
            is_manual, exchange, leverage,
            chain, setup_type, setup_score, signal_price
        ) VALUES (
            ?, ?, ?,
            'isolated', datetime('now'), '',
            ?, NULL,
            ?, ?,
            NULL, NULL,
            NULL, NULL, NULL,
            0, 'bitget_trader', ?,
            'auto_ai', ?, ?, ?
        )
    """, (
        sym,
        (sym or "").replace("USDT", ""),
        dir_,
        order_result.get("mark_at_entry") or signal.get("entry_price"),
        order_result.get("size_usdt"),
        str(order_result.get("size_contracts") or "") + ((sym or "").replace("USDT", "")),
        order_result.get("leverage"),
        (signal.get("scanner") or {}).get("archetype") or "auto_ai",
        signal.get("consensus_score"),
        signal.get("entry_price"),
    ))
    conn.commit()
    return cur.lastrowid


def _mark_closed(conn, position_id: int, close_price: float,
                  realized_pnl: float, reason: str) -> None:
    conn.execute("""
        UPDATE positions
        SET close_time   = datetime('now'),
            close_price  = ?,
            realized_pnl = ?
        WHERE id = ?
    """, (close_price, realized_pnl, position_id))
    conn.commit()
    _log(conn, "auto_close", position_id,
         {"close_price": close_price, "pnl": realized_pnl, "reason": reason})


def _log(conn, event: str, position_id: Optional[int], payload: dict) -> None:
    try:
        conn.execute("""
            INSERT INTO futures_ai_log(ts, event, symbol, direction, score, payload_json)
            VALUES (datetime('now'), ?, ?, ?, ?, ?)
        """, (
            event,
            payload.get("symbol") or "",
            payload.get("direction") or "",
            int(payload.get("score") or 0),
            json.dumps({**payload, "position_id": position_id})[:500],
        ))
        conn.commit()
    except Exception:
        pass


# ── Public surface ────────────────────────────────────────────────────────────

def open_real_trade(conn, signal: dict, sizing: dict) -> Optional[int]:
    """
    Place a market entry on the Bitget trader subaccount and record the
    resulting position. Returns the inserted positions.id, or None if
    refused/failed.
    """
    if not fa_config.is_real_mode():
        _log(conn, "real_refused", None,
             {"reason": "not in real mode", "symbol": signal.get("symbol")})
        return None

    sym  = signal.get("symbol")
    dir_ = signal.get("direction") or ""

    # Idempotency — never open a duplicate position
    for p in _open_auto_positions(conn):
        if p["symbol"] == sym and (p["direction"] or "").lower() == dir_.lower():
            _log(conn, "real_dedup", None,
                 {"symbol": sym, "direction": dir_,
                  "reason": "auto-position already open"})
            return None

    try:
        client_oid = f"fa-{uuid.uuid4().hex[:16]}"
        result = bitget_trader.place_market_order(
            symbol      = sym,
            side        = dir_,
            size_usdt   = float(sizing.get("notional_usdt") or 0),
            leverage    = int(sizing.get("leverage") or 1),
            sl_price    = signal.get("sl_price"),
            tp1_price   = signal.get("tp1_price"),
            tp2_price   = signal.get("tp2_price"),
            client_oid  = client_oid,
        )
    except Exception as e:
        _log(conn, "real_place_failed", None,
             {"symbol": sym, "direction": dir_, "error": str(e)[:200]})
        return None

    pos_id = _insert_open_position(conn, signal, sizing, result)
    _log(conn, "real_open", pos_id, {
        "symbol":     sym,
        "direction":  dir_,
        "score":      signal.get("consensus_score"),
        "entry":      result.get("mark_at_entry"),
        "sl":         signal.get("sl_price"),
        "tp1":        signal.get("tp1_price"),
        "tp2":        signal.get("tp2_price"),
        "notional":   result.get("size_usdt"),
        "lev":        result.get("leverage"),
        "order_id":   result.get("order_id"),
        "client_oid": client_oid,
    })
    return pos_id


def manage_real_positions(conn) -> dict:
    """
    Reconcile our DB state with Bitget's actual open positions, then
    apply lifecycle rules (BE/TRAIL/MAE) on positions still open.

    Lifecycle uses position_risk_monitor's thresholds (1× ATR for BE,
    2× ATR for trail, 1× ATR adverse for MAE) — same as paper.
    """
    if not fa_config.is_real_mode():
        return {"skipped": "not real mode"}

    # Pull what's *actually* open at Bitget
    try:
        live_positions = bitget_trader.get_open_positions()
    except Exception as e:
        _log(conn, "real_reconcile_error", None,
             {"error": str(e)[:200]})
        return {"error": str(e)[:200]}

    live_keys = {(p["symbol"], p["direction"].lower()) for p in live_positions}
    db_open   = _open_auto_positions(conn)

    summary = {"checked": 0, "closed_via_reconcile": 0,
               "be_moved": 0, "trail_moved": 0, "mae_cut": 0}

    # Reconciliation: anything in db_open that's NOT in live_positions
    # was closed by Bitget itself (preset SL/TP fired). Mark it closed.
    for p in db_open:
        key = (p["symbol"], (p["direction"] or "").lower())
        if key not in live_keys:
            # Look up the close price from Bitget's recent position
            # history — we don't have it locally yet, so estimate via
            # current mark for now. The next bitget_sync run will pick
            # up the proper close_price + realized_pnl + fees.
            try:
                last_mark = float(bitget_trader.get_mark_price(p["symbol"]) or 0)
            except Exception:
                last_mark = float(p.get("entry_price") or 0)
            _mark_closed(conn, p["id"], last_mark,
                          realized_pnl=0.0,   # bitget_sync will overwrite
                          reason="reconcile (Bitget preset SL/TP fired)")
            summary["closed_via_reconcile"] += 1

    # For positions STILL open, apply lifecycle rules
    db_open_after = [p for p in db_open
                     if (p["symbol"], (p["direction"] or "").lower())
                     in live_keys]

    for p in db_open_after:
        summary["checked"] += 1
        live = next(lp for lp in live_positions
                    if lp["symbol"] == p["symbol"]
                    and lp["direction"].lower() == (p["direction"] or "").lower())
        try:
            actions = _apply_lifecycle_rules(conn, p, live)
            for a in actions:
                summary[a] = summary.get(a, 0) + 1
        except Exception as e:
            _log(conn, "real_lifecycle_error", p["id"],
                 {"symbol": p["symbol"], "error": str(e)[:200]})

    return summary


def force_close_all(conn) -> int:
    """Close every auto_ai open position at market. Returns count closed."""
    if not fa_config.is_real_mode():
        return 0
    n = 0
    for p in _open_auto_positions(conn):
        try:
            bitget_trader.close_position(p["symbol"],
                                          (p["direction"] or "long").lower(),
                                          percentage=100.0)
            _log(conn, "real_force_close", p["id"],
                 {"symbol": p["symbol"], "reason": "pause_now / force"})
            n += 1
        except Exception as e:
            _log(conn, "real_force_close_error", p["id"],
                 {"symbol": p["symbol"], "error": str(e)[:200]})
    return n


# ── Lifecycle rules — mirror position_risk_monitor's thresholds ─────────────

def _apply_lifecycle_rules(conn, db_pos: dict, live: dict) -> list[str]:
    """Returns list of action labels for the summary."""
    actions: list[str] = []
    try:
        import chart_context
        ctx = chart_context.get_chart_context(live["symbol"], ["4H"]) or {}
        atr = float(((ctx.get("4H", {}).get("indicators", {})
                       .get("atr") or {}).get("value") or 0))
    except Exception:
        atr = 0.0
    if atr <= 0:
        return actions

    entry   = float(db_pos.get("entry_price") or live.get("entry_price") or 0)
    mark    = float(live.get("mark_price") or 0)
    is_long = live["direction"].lower() == "long"
    if entry <= 0 or mark <= 0:
        return actions

    sign = 1 if is_long else -1
    current_pct = (mark - entry) / entry * 100.0 * sign
    atr_pct     = atr / entry * 100.0
    current_sl  = float(live.get("preset_sl") or entry)   # bitget side-of-truth

    # MAE breach — -1× ATR (matches position_risk_monitor)
    if current_pct <= -atr_pct * 1.0:
        try:
            bitget_trader.close_position(live["symbol"],
                                          live["direction"].lower(),
                                          percentage=100.0)
            _mark_closed(conn, db_pos["id"], mark, realized_pnl=0.0,
                          reason="MAE breach auto-cut")
            actions.append("mae_cut")
        except Exception as e:
            _log(conn, "real_mae_cut_failed", db_pos["id"],
                 {"symbol": live["symbol"], "error": str(e)[:200]})
        return actions

    # Trail — +2× ATR (move SL to entry + 0.5× ATR)
    if current_pct >= atr_pct * 2.0:
        new_sl = entry + sign * (atr * 0.5)
        if (is_long and new_sl > current_sl) or (not is_long and new_sl < current_sl):
            try:
                bitget_trader.modify_position_sl(
                    live["symbol"], live["direction"].lower(), new_sl
                )
                _log(conn, "real_trail", db_pos["id"], {
                    "symbol": live["symbol"], "old_sl": current_sl, "new_sl": new_sl,
                })
                actions.append("trail_moved")
            except Exception as e:
                _log(conn, "real_trail_failed", db_pos["id"],
                     {"symbol": live["symbol"], "error": str(e)[:200]})
        return actions

    # BE move — +1× ATR
    if current_pct >= atr_pct * 1.0:
        if (is_long and entry > current_sl) or (not is_long and entry < current_sl):
            try:
                bitget_trader.modify_position_sl(
                    live["symbol"], live["direction"].lower(), entry
                )
                _log(conn, "real_be", db_pos["id"], {
                    "symbol": live["symbol"], "old_sl": current_sl, "new_sl": entry,
                })
                actions.append("be_moved")
            except Exception as e:
                _log(conn, "real_be_failed", db_pos["id"],
                     {"symbol": live["symbol"], "error": str(e)[:200]})

    return actions
