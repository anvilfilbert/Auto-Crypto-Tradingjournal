"""
trading.hedge_manager — Catastrophe hedge for the auto-trader basket.

When the open auto_ai longs bleed rapidly during a market-wide flush
(the 2026-05-22 23:53 "5 simultaneous stop-out" pattern), this module
opens a single BTC perpetual SHORT sized to half the net long notional.
The hedge offsets further downside while preserving upside optionality
on each individual long (each can still close at its own TP/SL).

Trigger (ALL must be true):
  - basket unrealised < HEDGE_TRIGGER_UNREAL_PCT × equity   (default -3%)
  - BTC 1h change   < HEDGE_TRIGGER_BTC_DROP_PCT            (default -2%)
  - long-share of   ≥ HEDGE_TRIGGER_LONG_BIAS_PCT           (default 70%)
  - no active hedge currently open

Unwind (ANY triggers close):
  - BTC has recovered to within HEDGE_UNWIND_RECOVERY_PCT of its level
    when the hedge was opened (default 1%)
  - 2 consecutive green BTC 15-minute candles since hedge open
  - HEDGE_MAX_DURATION_HOURS elapsed (default 24h, safety cap)
  - operator force-closes via UI / API (future)

Hedges carry positions.is_hedge=1 so they DO NOT count toward
MAX_CONCURRENT_POSITIONS, the consecutive-loss breaker, or the
win-streak progression. Their P&L still affects equity (real Bitget
money) but is excluded from "trade quality" metrics.

Public:
- check_and_open_hedge(conn) — called from monitor cycle, opens if all
  conditions met
- manage_active_hedge(conn) — called from monitor cycle, closes if
  unwind condition met
"""
from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timezone, timedelta

from . import config

_log = logging.getLogger(__name__)

HEDGE_SYMBOL = "BTCUSDT"


# ── State persistence (lightweight — single key in settings table) ─────────────

def _hedge_state(conn) -> dict:
    """Return active hedge state from settings, or empty dict."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key='futures_ai_active_hedge'"
    ).fetchone()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def _save_hedge_state(conn, state: dict) -> None:
    conn.execute("""
        INSERT INTO settings(key, value) VALUES('futures_ai_active_hedge', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (json.dumps(state),))
    conn.commit()


def _clear_hedge_state(conn) -> None:
    conn.execute(
        "DELETE FROM settings WHERE key='futures_ai_active_hedge'"
    )
    conn.commit()


def get_active_hedge(conn) -> dict:
    """Public accessor for UI / status endpoints."""
    return _hedge_state(conn)


# ── Logging ─────────────────────────────────────────────────────────────────────

def _log_event(conn, event: str, payload: dict) -> None:
    try:
        conn.execute("""
            INSERT INTO futures_ai_log(ts, event, symbol, payload_json)
            VALUES (datetime('now'), ?, ?, ?)
        """, (event, HEDGE_SYMBOL, json.dumps(payload)))
        conn.commit()
    except Exception:
        pass


# ── Trigger logic ──────────────────────────────────────────────────────────────

def _basket_long_metrics(conn) -> dict:
    """Returns {n_long, n_short, long_notional, short_notional, unreal_total,
    long_share} for currently open auto_ai non-hedge positions.

    Reads notional + unrealised from the live Bitget trader response so
    figures reflect mark-price, not stale DB values."""
    out = {"n_long": 0, "n_short": 0, "long_notional": 0.0,
            "short_notional": 0.0, "unreal_total": 0.0, "long_share": 0.0}
    try:
        from . import bitget_trader
        live = bitget_trader.get_open_positions() or []
    except Exception as e:
        _log.warning("[hedge] live position fetch failed: %s", e)
        return out

    # Reconcile against DB so we exclude any existing hedge positions
    db_hedges = set()
    try:
        rows = conn.execute(
            "SELECT symbol FROM positions "
            "WHERE chain='auto_ai' AND is_hedge=1 "
            "AND (close_time IS NULL OR close_time='')"
        ).fetchall()
        db_hedges = {r[0] for r in rows}
    except Exception:
        pass

    for p in live:
        sym = p.get("symbol")
        if sym in db_hedges:
            continue   # skip our own hedges from the basket calc
        notional = float(p.get("notional_usdt") or 0)
        unreal   = float(p.get("unrealized_pnl") or 0)
        out["unreal_total"] += unreal
        if (p.get("direction") or "").lower() == "long":
            out["n_long"] += 1
            out["long_notional"] += notional
        else:
            out["n_short"] += 1
            out["short_notional"] += notional

    total_notional = out["long_notional"] + out["short_notional"]
    if total_notional > 0:
        out["long_share"] = out["long_notional"] / total_notional
    return out


def _btc_1h_change_pct() -> float | None:
    """BTC 1h move as a fraction (e.g. -0.025 = -2.5%). Uses 4H candles
    is fine — we have 1H via chart_candles. Returns None on fetch fail."""
    try:
        from chart_candles import get_candles
        df = get_candles(HEDGE_SYMBOL, "1H", limit=2)
        if df is None or len(df) < 2:
            return None
        prev_close = float(df["close"].iloc[-2])
        last_close = float(df["close"].iloc[-1])
        if prev_close <= 0:
            return None
        return (last_close - prev_close) / prev_close
    except Exception as e:
        _log.debug("[hedge] BTC 1h fetch failed: %s", e)
        return None


def _btc_last_two_15m_green() -> bool:
    """True if the last two 15m BTC candles closed green."""
    try:
        from chart_candles import get_candles
        df = get_candles(HEDGE_SYMBOL, "15m", limit=2)
        if df is None or len(df) < 2:
            return False
        last2 = df.iloc[-2:]
        return bool((last2["close"] > last2["open"]).all())
    except Exception:
        return False


# ── Open / Close ───────────────────────────────────────────────────────────────

def check_and_open_hedge(conn) -> dict | None:
    """Called from monitor cycle. If trigger conditions are met and no
    active hedge exists, open one. Returns the hedge state dict on success,
    None when skipped."""
    if not config.HEDGE_ENABLED:
        return None
    if not config.is_real_mode():
        return None
    if _hedge_state(conn):
        return None   # already hedged
    if config.get_state(conn) != "active":
        return None   # respect operator pause / breaker

    metrics = _basket_long_metrics(conn)
    if metrics["n_long"] == 0:
        return None   # nothing to hedge

    # Equity for the unrealised% computation
    from .kill_switch import _equity_now
    eq = _equity_now(conn)
    if eq <= 0:
        return None
    unreal_pct = metrics["unreal_total"] / eq

    btc_change = _btc_1h_change_pct()

    triggered_reasons = []
    if unreal_pct <= config.HEDGE_TRIGGER_UNREAL_PCT:
        triggered_reasons.append(f"basket unreal {unreal_pct*100:.2f}% ≤ {config.HEDGE_TRIGGER_UNREAL_PCT*100:.1f}%")
    if btc_change is not None and btc_change <= config.HEDGE_TRIGGER_BTC_DROP_PCT:
        triggered_reasons.append(f"BTC 1h {btc_change*100:.2f}% ≤ {config.HEDGE_TRIGGER_BTC_DROP_PCT*100:.1f}%")
    if metrics["long_share"] >= config.HEDGE_TRIGGER_LONG_BIAS_PCT:
        triggered_reasons.append(f"long share {metrics['long_share']*100:.0f}% ≥ {config.HEDGE_TRIGGER_LONG_BIAS_PCT*100:.0f}%")

    # Need ALL three to fire
    if len(triggered_reasons) < 3:
        return None

    # All conditions met — fire the hedge
    hedge_notional = metrics["long_notional"] * config.HEDGE_RATIO
    if hedge_notional < 10:   # too small to be useful (Bitget min order)
        _log_event(conn, "hedge_skipped",
                   {"reason": "hedge notional <$10",
                    "long_notional": metrics["long_notional"]})
        return None

    try:
        from . import bitget_trader
        from .kill_switch import _equity_now as _eq
        result = bitget_trader.place_market_order(
            symbol=HEDGE_SYMBOL,
            side="short",
            size_usdt=hedge_notional,
            leverage=config.HEDGE_LEVERAGE,
            sl_price=None,     # no SL — hedges ride the storm
            tp1_price=None,
            tp2_price=None,
            client_oid=f"hedge-{int(time.time())}",
        )
    except Exception as e:
        _log.warning("[hedge] place_market_order failed: %s", e)
        _log_event(conn, "hedge_open_failed", {"error": str(e)[:200]})
        return None

    if not result or not result.get("order_id"):
        _log_event(conn, "hedge_open_failed", {"error": "no order_id returned"})
        return None

    # Persist hedge position to DB with is_hedge=1
    open_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    btc_at_open = float(result.get("mark_at_entry") or 0)
    state = {
        "order_id":         result.get("order_id"),
        "opened_at":        open_time,
        "btc_at_open":      btc_at_open,
        "notional_usdt":    hedge_notional,
        "leverage_actual":  result.get("leverage_actual"),
        "long_notional_at_open":  metrics["long_notional"],
        "unreal_at_open":         metrics["unreal_total"],
        "reasons":          triggered_reasons,
    }

    try:
        conn.execute("""
            INSERT INTO positions
                (chain, is_hedge, symbol, direction, entry_price, size_contracts,
                 leverage, notional_usdt, open_time, bitget_order_id, setup_type,
                 exchange)
            VALUES ('auto_ai', 1, ?, 'Short', ?, ?, ?, ?, ?, ?, 'catastrophe_hedge',
                    'bitget_trader')
        """, (
            HEDGE_SYMBOL,
            btc_at_open,
            float(result.get("size_contracts") or 0),
            int(result.get("leverage_actual") or config.HEDGE_LEVERAGE),
            hedge_notional,
            open_time,
            result.get("order_id"),
        ))
        conn.commit()
    except Exception as e:
        _log.warning("[hedge] DB insert failed: %s", e)

    _save_hedge_state(conn, state)
    _log_event(conn, "hedge_opened", state)
    _log.warning("[hedge] OPENED BTC short notional=$%.2f at $%.2f — reasons: %s",
                 hedge_notional, btc_at_open, "; ".join(triggered_reasons))
    return state


def manage_active_hedge(conn) -> dict | None:
    """Called from monitor cycle. If an active hedge exists, check unwind
    conditions; close it when met. Returns close-result dict or None."""
    state = _hedge_state(conn)
    if not state:
        return None

    btc_at_open = float(state.get("btc_at_open") or 0)
    opened_at  = state.get("opened_at", "")

    # Time-based safety cap
    try:
        opened_dt = datetime.strptime(opened_at, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600
    except Exception:
        age_hours = 0

    unwind_reason = None
    if age_hours >= config.HEDGE_MAX_DURATION_HOURS:
        unwind_reason = f"max duration {age_hours:.1f}h ≥ {config.HEDGE_MAX_DURATION_HOURS}h"

    # BTC recovery check
    if not unwind_reason and btc_at_open > 0:
        try:
            from chart_candles import get_candles
            df = get_candles(HEDGE_SYMBOL, "15m", limit=1)
            if df is not None and len(df):
                btc_now = float(df["close"].iloc[-1])
                recovery_pct = (btc_now - btc_at_open) / btc_at_open
                if recovery_pct >= -config.HEDGE_UNWIND_RECOVERY_PCT:
                    unwind_reason = (f"BTC recovered to {btc_now:.2f} "
                                     f"({recovery_pct*100:+.2f}% of hedge-open)")
        except Exception:
            pass

    # Two consecutive green 15m candles
    if not unwind_reason and _btc_last_two_15m_green():
        unwind_reason = "BTC printed 2 consecutive green 15m candles"

    if not unwind_reason:
        return None   # keep the hedge open

    # ── Close the hedge ─────────────────────────────────────────────────
    try:
        from . import bitget_trader
        close_result = bitget_trader.close_position(
            symbol=HEDGE_SYMBOL, side="short", percentage=100)
    except Exception as e:
        _log.warning("[hedge] close_position failed: %s", e)
        _log_event(conn, "hedge_close_failed", {"error": str(e)[:200]})
        return None

    # Update DB row
    close_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    realized = float(close_result.get("realized_pnl") or 0)
    try:
        conn.execute("""
            UPDATE positions SET close_time=?, close_price=?, realized_pnl=?,
                                  close_reason='hedge_unwind'
            WHERE bitget_order_id=? AND is_hedge=1
        """, (
            close_time, float(close_result.get("close_price") or 0), realized,
            state.get("order_id"),
        ))
        conn.commit()
    except Exception as e:
        _log.warning("[hedge] DB update failed: %s", e)

    payload = {
        "order_id":      state.get("order_id"),
        "opened_at":     opened_at,
        "closed_at":     close_time,
        "unwind_reason": unwind_reason,
        "realized_pnl":  realized,
        "duration_h":    round(age_hours, 2),
    }
    _clear_hedge_state(conn)
    _log_event(conn, "hedge_closed", payload)
    _log.warning("[hedge] CLOSED — %s | realised $%.2f after %.1fh",
                 unwind_reason, realized, age_hours)
    return payload
