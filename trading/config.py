"""
trading.config — Futures-AI configuration knobs + runtime state.

All long-term knobs are env-driven (loaded by systemd from .env), so a
config change is a one-line edit + service restart. Short-term state
(active / paused / closed) lives in the settings table so it persists
across restarts and is mutable from the UI.

Defaults are deliberately conservative — the system stays OFF until
explicitly turned on by the operator.
"""
from __future__ import annotations

import os
from typing import Optional

# ── env-var knobs (loaded once at import time) ───────────────────────────────

# Global on/off switch. The chain literally cannot place an order unless
# this is "1" AT THE ENV LEVEL (an additional belt over the UI control).
_FUTURES_AI_ENABLED = os.environ.get("FUTURES_AI_ENABLED", "0").strip() == "1"

# "paper" or "real". Paper logs everything but never calls Bitget write APIs.
# Real uses the BITGET_TRADER_* credentials (separate from the journal creds).
_FUTURES_AI_MODE = (os.environ.get("FUTURES_AI_MODE", "paper")
                    .strip().lower())

# Bankroll starting equity ($100 baseline). Used for risk-budget % math
# when the Bitget account balance is unreachable (e.g. during outage).
_STARTING_EQUITY = float(os.environ.get("FUTURES_AI_STARTING_EQUITY", "100"))


# ── risk caps (constants — bumping these requires a code change + redeploy) ──

# Risk-budget per trade, as fraction of equity. Kelly-scaled by score:
#   score 7  → 1.0×  (2%  of equity at risk)
#   score 8  → 1.5×  (3%)
#   score 9  → 2.0×  (4%)
#   score 10 → 2.0×  (capped at 4% to bound max loss)
RISK_PER_TRADE_PCT     = 0.02
RISK_SCORE_MULTIPLIERS = {7: 1.0, 8: 1.5, 9: 2.0, 10: 2.0}

# Hard ceilings — order size never exceeds these regardless of score
MAX_LEVERAGE              = 10
MAX_NOTIONAL_USDT         = 25.0   # $25 notional per trade on $100 bankroll
MAX_CONCURRENT_POSITIONS  = 3

# Circuit breakers (auto-trip → state goes to "circuit_breaker") — these
# are CAPITAL-PRESERVATION rules only. Strategic rules (which day, which
# symbol, which direction) belong in the scoring system, NOT here. The
# rulebook (data-driven, no priors) and the score caps (macro, bad-hour,
# reversal) handle the strategic side; this module handles "stop me from
# losing too much money no matter what the AI thinks".
DAILY_DD_BREAKER_PCT      = -0.05  # -5% in 24h
TOTAL_DD_BREAKER_PCT      = -0.15  # -15% from starting equity
CONSECUTIVE_LOSS_BREAKER  = 3

# Confirmed-invalidation rule — both must fire to auto-close
INVALIDATION_REQUIRE_MAE_BREACH    = True
INVALIDATION_REQUIRE_1H_CLOSE_PAST = True


# ── runtime state (mutable via UI / Telegram) ────────────────────────────────

VALID_STATES = ("active", "pause_after_close", "pause_now", "circuit_breaker")
_DEFAULT_STATE = "pause_now"   # ships paused — operator activates explicitly


def is_enabled() -> bool:
    """Env-level kill switch. If False, NO real or paper orders fire."""
    return _FUTURES_AI_ENABLED


def is_real_mode() -> bool:
    """True only when env says real AND env is enabled. Paper otherwise."""
    return _FUTURES_AI_ENABLED and _FUTURES_AI_MODE == "real"


def get_mode() -> str:
    return _FUTURES_AI_MODE if _FUTURES_AI_ENABLED else "disabled"


def starting_equity() -> float:
    return _STARTING_EQUITY


def get_state(conn=None) -> str:
    """Return current runtime state. Falls back to _DEFAULT_STATE when
    no settings row exists."""
    if conn is None:
        from database import db_conn
        with db_conn() as c:
            return get_state(c)
    row = conn.execute(
        "SELECT value FROM settings WHERE key='futures_ai_state'"
    ).fetchone()
    if not row or not row[0]:
        return _DEFAULT_STATE
    val = str(row[0]).strip().lower()
    return val if val in VALID_STATES else _DEFAULT_STATE


def set_state(new_state: str, conn=None, reason: Optional[str] = None) -> str:
    """Persist a state transition. Returns the new effective state.
    Reason gets logged to the trader audit log if conn is provided."""
    if new_state not in VALID_STATES:
        raise ValueError(f"invalid state {new_state!r}; "
                          f"must be one of {VALID_STATES}")
    if conn is None:
        from database import db_conn
        with db_conn() as c:
            return set_state(new_state, c, reason)

    conn.execute("""
        INSERT INTO settings(key, value) VALUES('futures_ai_state', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (new_state,))
    conn.commit()

    # Record the transition so we have a full audit trail
    try:
        conn.execute("""
            INSERT INTO futures_ai_log(ts, event, payload_json)
            VALUES (datetime('now'), 'state_change', ?)
        """, (
            f'{{"to":"{new_state}","reason":{repr(reason or "user")}}}',
        ))
        conn.commit()
    except Exception:
        pass   # log table may not exist yet on fresh DB

    return new_state


def snapshot() -> dict:
    """Calibration snapshot for /api/system/health + Futures-AI page."""
    return {
        "enabled":                    _FUTURES_AI_ENABLED,
        "mode":                       _FUTURES_AI_MODE,
        "starting_equity_usdt":       _STARTING_EQUITY,
        "risk_per_trade_pct":         RISK_PER_TRADE_PCT,
        "risk_score_multipliers":     RISK_SCORE_MULTIPLIERS,
        "max_leverage":               MAX_LEVERAGE,
        "max_notional_usdt":          MAX_NOTIONAL_USDT,
        "max_concurrent_positions":   MAX_CONCURRENT_POSITIONS,
        "daily_dd_breaker_pct":       DAILY_DD_BREAKER_PCT,
        "total_dd_breaker_pct":       TOTAL_DD_BREAKER_PCT,
        "consecutive_loss_breaker":   CONSECUTIVE_LOSS_BREAKER,
    }
