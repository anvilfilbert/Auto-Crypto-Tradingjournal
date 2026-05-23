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
    """
    Auto-trader equity. In real mode, queries the Bitget trader subaccount
    directly so the operator's main-account equity (which lives in
    wallet_snapshots) doesn't pollute the auto-trader's risk calculations.
    In paper mode, uses starting bankroll + accumulated paper P&L.
    """
    if config.is_real_mode():
        try:
            from . import bitget_trader
            bal = bitget_trader.get_balance() or {}
            eq = float(bal.get("equity") or 0)
            if eq > 0:
                return eq
        except Exception:
            pass
        return config.starting_equity()

    # Paper mode — starting equity + sum of paper close P&L
    try:
        r = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl),0) FROM paper_positions "
            "WHERE status='closed'"
        ).fetchone()
        return config.starting_equity() + float(r[0] or 0)
    except Exception:
        return config.starting_equity()


def _daily_pnl_pct(conn, *, since_reset: bool = False) -> float:
    """
    Auto-trader's realized P&L over the last 24h as a fraction of starting
    equity. Restricted to auto_ai chain (real) or paper_positions (paper)
    so manual trades cannot affect the auto-trader's metrics.

    since_reset=False (default, for DISPLAY) → true 24h history regardless
    of operator overrides. This is what the Futures-AI page shows.

    since_reset=True (for BREAKER) → honors the operator-initiated
    breaker_reset_at stamp; losses closed before the reset don't count.
    This is what can_open_new_trade uses so the operator's "I've reviewed"
    decision actually unblocks the chain.
    """
    reset_at = config.breaker_reset_at(conn) if since_reset else None
    try:
        if config.is_real_mode():
            if reset_at:
                r = conn.execute(
                    "SELECT COALESCE(SUM(realized_pnl),0) FROM positions "
                    "WHERE chain='auto_ai' "
                    "AND close_time >= datetime('now','-24 hours') "
                    "AND close_time > ?",
                    (reset_at,),
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT COALESCE(SUM(realized_pnl),0) FROM positions "
                    "WHERE chain='auto_ai' AND close_time >= datetime('now','-24 hours')"
                ).fetchone()
        else:
            if reset_at:
                r = conn.execute(
                    "SELECT COALESCE(SUM(realized_pnl),0) FROM paper_positions "
                    "WHERE status='closed' "
                    "AND closed_at >= datetime('now','-24 hours') "
                    "AND closed_at > ?",
                    (reset_at,),
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT COALESCE(SUM(realized_pnl),0) FROM paper_positions "
                    "WHERE status='closed' AND closed_at >= datetime('now','-24 hours')"
                ).fetchone()
        pnl = float(r[0] or 0)
        return pnl / max(config.starting_equity(), 1)
    except Exception:
        return 0.0


def _consecutive_losses(conn, *, since_reset: bool = False) -> int:
    """Count of consecutive auto-trader losers up to the most recent close.

    since_reset=False (DISPLAY) → true count over the entire history.
    since_reset=True (BREAKER) → only counts trades closed AFTER the
    operator-initiated breaker reset stamp, so an explicit override
    forgives prior losses for breaker purposes. New losses still re-trip
    the breaker if 3 in a row appear post-reset.
    """
    reset_at = config.breaker_reset_at(conn) if since_reset else None
    try:
        if config.is_real_mode():
            if reset_at:
                rows = conn.execute(
                    "SELECT realized_pnl FROM positions "
                    "WHERE chain='auto_ai' "
                    "AND close_time IS NOT NULL AND close_time != '' "
                    "AND close_time > ? "
                    "ORDER BY close_time DESC LIMIT 10",
                    (reset_at,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT realized_pnl FROM positions "
                    "WHERE chain='auto_ai' AND close_time IS NOT NULL AND close_time != '' "
                    "ORDER BY close_time DESC LIMIT 10"
                ).fetchall()
        else:
            if reset_at:
                rows = conn.execute(
                    "SELECT realized_pnl FROM paper_positions "
                    "WHERE status='closed' AND closed_at > ? "
                    "ORDER BY closed_at DESC LIMIT 10",
                    (reset_at,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT realized_pnl FROM paper_positions "
                    "WHERE status='closed' "
                    "ORDER BY closed_at DESC LIMIT 10"
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
    """Currently open auto-trader positions. Real mode counts chain='auto_ai'
    in positions table; paper mode counts open paper_positions."""
    try:
        if config.is_real_mode():
            r = conn.execute("""
                SELECT COUNT(*) FROM positions
                WHERE chain='auto_ai' AND (close_time IS NULL OR close_time='')
            """).fetchone()
        else:
            r = conn.execute("""
                SELECT COUNT(*) FROM paper_positions
                WHERE status='open'
            """).fetchone()
        return int(r[0]) if r else 0
    except Exception:
        return 0


# ── Public decision functions ────────────────────────────────────────────────

def can_open_new_trade(conn, scanner_score: int = 0) -> tuple[bool, str]:
    """
    True/False + reason for the next would-be trade. Called BEFORE every
    signal evaluation so we never even score a setup when the chain is
    halted.

    scanner_score: raw scanner score (pre-consensus). When this hits the
    ELITE_BYPASS_SCORE (10), the concurrent-position soft cap is lifted
    up to MAX_ELITE_POSITIONS — the rationale being that scanner-10
    setups are rare and worth letting through to consensus even when the
    book is full. Sonnet consensus is still applied normally downstream;
    if it disagrees, the trade is rejected there. All other breakers
    (daily DD, total DD, consec loss, state, env switch) still apply.
    """
    if not config.is_enabled():
        return False, "FUTURES_AI_ENABLED=0 (env-level off switch)"

    state = config.get_state(conn)
    if state in ("pause_now", "pause_after_close", "circuit_breaker"):
        return False, f"state={state}"

    # Daily DD breaker — uses since_reset=True so an operator override
    # of the circuit breaker actually unblocks the chain (display still
    # shows the raw 24h truth via the no-arg call in evaluate()).
    dd = _daily_pnl_pct(conn, since_reset=True)
    if dd <= config.DAILY_DD_BREAKER_PCT:
        _trip_breaker(conn, f"daily DD {dd*100:.1f}% ≤ {config.DAILY_DD_BREAKER_PCT*100:.0f}%")
        return False, f"daily DD breaker tripped at {dd*100:.1f}%"

    # Total DD breaker
    eq = _equity_now(conn)
    total_dd = (eq - config.starting_equity()) / max(config.starting_equity(), 1)
    if total_dd <= config.TOTAL_DD_BREAKER_PCT:
        _trip_breaker(conn, f"total DD {total_dd*100:.1f}% ≤ {config.TOTAL_DD_BREAKER_PCT*100:.0f}%")
        return False, f"total DD breaker tripped at {total_dd*100:.1f}%"

    # Consecutive loss breaker — same since_reset semantics as daily DD
    nl = _consecutive_losses(conn, since_reset=True)
    if nl >= config.CONSECUTIVE_LOSS_BREAKER:
        _trip_breaker(conn, f"{nl} consecutive losses")
        return False, f"consecutive-loss breaker tripped ({nl} losses)"

    # Concurrent positions — pure safety cap (capital-preservation).
    # A scanner-verified 10/10 setup may bypass the soft cap up to
    # MAX_ELITE_POSITIONS so we never pass on the rarest signals.
    n_open = _open_position_count(conn)
    is_elite = scanner_score >= config.ELITE_BYPASS_SCORE
    effective_cap = (config.MAX_ELITE_POSITIONS if is_elite
                     else config.MAX_CONCURRENT_POSITIONS)
    if n_open >= effective_cap:
        if is_elite:
            return False, (f"at MAX_ELITE_POSITIONS hard cap "
                           f"({n_open}/{config.MAX_ELITE_POSITIONS}) "
                           f"even with scanner 10/10")
        return False, (f"already at MAX_CONCURRENT_POSITIONS "
                       f"({n_open}/{config.MAX_CONCURRENT_POSITIONS}) "
                       f"— scanner score {scanner_score}/10, need 10 to bypass")

    # NOTE: No day-of-week, symbol, or direction filters here. Strategic
    # decisions (when, where, what to trade) belong in the scoring system
    # — the data-driven rulebook, macro caps, bad-hour score caps, and
    # archetype caps all already see this trader's history and adjust
    # scores accordingly. Adding hard filters at this layer would
    # duplicate the bias the rulebook was just rewritten to remove.

    return True, ""


def _trip_breaker(conn, why: str) -> None:
    """Flip state to circuit_breaker + log. One-way until manually cleared."""
    try:
        config.set_state("circuit_breaker", conn,
                          reason=f"breaker: {why}")
    except Exception:
        pass


def evaluate(conn) -> dict:
    """Snapshot of every rule's current state — for the UI panel.

    daily_pnl_pct / consecutive_losses are TRUE 24h history (since_reset
    OFF) so the UI shows what actually happened. The corresponding
    *_since_reset fields show the breaker-relevant values that
    can_open_new_trade actually checks against — useful for
    understanding why the breaker is or isn't tripping.
    """
    eq = _equity_now(conn)
    dd_day_raw   = _daily_pnl_pct(conn)
    dd_day_reset = _daily_pnl_pct(conn, since_reset=True)
    dd_total     = (eq - config.starting_equity()) / max(config.starting_equity(), 1)
    n_losses_raw   = _consecutive_losses(conn)
    n_losses_reset = _consecutive_losses(conn, since_reset=True)
    n_open = _open_position_count(conn)

    can_trade, reason = can_open_new_trade(conn)

    # Profit Compounding progression — current win streak + multiplier
    try:
        from trading.risk_budget import _consecutive_wins, _streak_multiplier
        wins = _consecutive_wins(conn)
        streak_mult = _streak_multiplier(wins)
    except Exception:
        wins = 0
        streak_mult = 1.0

    return {
        "state":                       config.get_state(conn),
        "can_open_new_trade":          can_trade,
        "reason":                      reason or "ok",
        "equity_usdt":                 round(eq, 2),
        "daily_pnl_pct":               round(dd_day_raw * 100, 2),
        "daily_pnl_pct_since_reset":   round(dd_day_reset * 100, 2),
        "total_pnl_pct":               round(dd_total * 100, 2),
        "consecutive_losses":          n_losses_raw,
        "consecutive_losses_since_reset": n_losses_reset,
        "consecutive_wins_since_reset": wins,
        "streak_multiplier":           round(streak_mult, 1),
        "open_positions":              n_open,
        "max_concurrent":              config.MAX_CONCURRENT_POSITIONS,
        "breaker_reset_at":            config.breaker_reset_at(conn),
    }
