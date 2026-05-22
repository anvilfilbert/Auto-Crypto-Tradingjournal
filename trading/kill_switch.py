"""
trading.kill_switch — circuit breakers + pause-state evaluation.

A single can_open_new_trade() function consults every active rule and
returns either (True, "") or (False, reason). Used by both paper and
real-trader paths so the safety rules are identical.

Trip → automatic state flip to "circuit_breaker". Requires manual
operator action (re-Activate button) to clear.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from . import config


# ── Bankroll + recent P&L queries (DB-driven) ───────────────────────────────

def _equity_now(conn) -> float:
    """Latest equity reading from wallet_snapshots, or starting bankroll."""
    try:
        r = conn.execute(
            "SELECT wallet_balance FROM wallet_snapshots "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return float(r[0]) if r and r[0] else config.starting_equity()
    except Exception:
        return config.starting_equity()


def _daily_pnl_pct(conn) -> float:
    """Realized P&L over the last 24h as a fraction of starting equity."""
    try:
        r = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl),0) FROM positions "
            "WHERE close_time >= datetime('now','-24 hours')"
        ).fetchone()
        pnl = float(r[0] or 0)
        return pnl / max(config.starting_equity(), 1)
    except Exception:
        return 0.0


def _consecutive_losses(conn) -> int:
    """Count of consecutive losers up to the most recent close."""
    try:
        rows = conn.execute(
            "SELECT realized_pnl FROM positions "
            "WHERE close_time IS NOT NULL AND close_time != '' "
            "ORDER BY close_time DESC LIMIT 10"
        ).fetchall()
        n = 0
        for (pnl,) in rows:
            if (pnl or 0) <= 0:
                n += 1
            else:
                break
        return n
    except Exception:
        return 0


def _open_position_count(conn) -> int:
    """Current open positions (futures-AI chain). We piggyback on the
    journal table — positions inserted by the executor get exchange='bitget_trader'
    so they're separable from manual/journal trades."""
    try:
        r = conn.execute("""
            SELECT COUNT(*) FROM positions
            WHERE close_time IS NULL OR close_time = ''
        """).fetchone()
        return int(r[0]) if r else 0
    except Exception:
        return 0


# ── Public decision functions ────────────────────────────────────────────────

def can_open_new_trade(conn) -> tuple[bool, str]:
    """
    True/False + reason for the next would-be trade. Called BEFORE every
    signal evaluation so we never even score a setup when the chain is
    halted.
    """
    if not config.is_enabled():
        return False, "FUTURES_AI_ENABLED=0 (env-level off switch)"

    state = config.get_state(conn)
    if state in ("pause_now", "pause_after_close", "circuit_breaker"):
        return False, f"state={state}"

    # Daily DD breaker
    dd = _daily_pnl_pct(conn)
    if dd <= config.DAILY_DD_BREAKER_PCT:
        _trip_breaker(conn, f"daily DD {dd*100:.1f}% ≤ {config.DAILY_DD_BREAKER_PCT*100:.0f}%")
        return False, f"daily DD breaker tripped at {dd*100:.1f}%"

    # Total DD breaker
    eq = _equity_now(conn)
    total_dd = (eq - config.starting_equity()) / max(config.starting_equity(), 1)
    if total_dd <= config.TOTAL_DD_BREAKER_PCT:
        _trip_breaker(conn, f"total DD {total_dd*100:.1f}% ≤ {config.TOTAL_DD_BREAKER_PCT*100:.0f}%")
        return False, f"total DD breaker tripped at {total_dd*100:.1f}%"

    # Consecutive loss breaker
    nl = _consecutive_losses(conn)
    if nl >= config.CONSECUTIVE_LOSS_BREAKER:
        _trip_breaker(conn, f"{nl} consecutive losses")
        return False, f"consecutive-loss breaker tripped ({nl} losses)"

    # Concurrent positions
    n_open = _open_position_count(conn)
    if n_open >= config.MAX_CONCURRENT_POSITIONS:
        return False, f"already at MAX_CONCURRENT_POSITIONS ({n_open}/{config.MAX_CONCURRENT_POSITIONS})"

    # Day-of-week guard — no new opens Mon/Tue if ≥2 positions already open
    wd = _dt.datetime.now(_dt.timezone.utc).weekday()   # 0=Mon, 1=Tue
    if wd in (0, 1) and n_open >= 2:
        return False, "Mon/Tue cap: ≥2 positions already open"

    # Bad-hour cap reuses scanner_criteria — already enforced upstream when
    # the scanner produces signals, but we double-check here for safety.
    try:
        from scanner_criteria import _is_in_personal_bad_hour
        if _is_in_personal_bad_hour():
            return False, "UTC bad-hour window (13/15/19/20)"
    except Exception:
        pass

    return True, ""


def _trip_breaker(conn, why: str) -> None:
    """Flip state to circuit_breaker + log. One-way until manually cleared."""
    try:
        config.set_state("circuit_breaker", conn,
                          reason=f"breaker: {why}")
    except Exception:
        pass


def evaluate(conn) -> dict:
    """Snapshot of every rule's current state — for the UI panel."""
    eq = _equity_now(conn)
    dd_day = _daily_pnl_pct(conn)
    dd_total = (eq - config.starting_equity()) / max(config.starting_equity(), 1)
    n_losses = _consecutive_losses(conn)
    n_open = _open_position_count(conn)

    can_trade, reason = can_open_new_trade(conn)

    return {
        "state":                 config.get_state(conn),
        "can_open_new_trade":    can_trade,
        "reason":                reason or "ok",
        "equity_usdt":           round(eq, 2),
        "daily_pnl_pct":         round(dd_day * 100, 2),
        "total_pnl_pct":         round(dd_total * 100, 2),
        "consecutive_losses":    n_losses,
        "open_positions":        n_open,
        "max_concurrent":        config.MAX_CONCURRENT_POSITIONS,
    }
