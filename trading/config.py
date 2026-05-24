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

# Cost-control knobs.
#   CONSENSUS_MIN_SCORE — only run the Sonnet consensus call on scanner
#     setups at or above this score. Setups with score >= SCANNER_MIN_SCORE
#     but below this threshold skip consensus entirely and feed paper
#     directly. Defaults to 8 — saves ~50% of consensus spend during the
#     paper-validation period. Bump down to 7 once budget allows.
#   CONSENSUS_MODEL — 'opus' (default, calibrates 5/5 on real rejections),
#     'sonnet' (legacy), or 'haiku' (cheapest, lowest quality). Also accepts
#     full Anthropic model IDs (e.g. 'claude-opus-4-7'). Switched from Sonnet
#     to Opus on 2026-05-23 — empirical re-review showed Opus would have
#     approved 5/5 score-6 setups that Sonnet rejected, and hindsight showed
#     4 of 5 hit TP. Cost: +$40/mo at ~26 consensus calls/day.
CONSENSUS_MIN_SCORE = int(os.environ.get("FUTURES_AI_CONSENSUS_MIN_SCORE", "8"))
CONSENSUS_MODEL     = os.environ.get("FUTURES_AI_CONSENSUS_MODEL", "opus").lower()


# ── Multi-TP splits (operator-defined, 2026-05-23) ───────────────────────────
#
# When Opus emits N take-profit levels via the consensus override path, the
# system applies the matching split. Percentages MUST sum to 100. The two
# baseline cases (1 and 2 TPs) are the legacy scanner-only behaviour and stay
# binary (100% close at TP1 / 60% at TP1 + 40% at TP2 — the latter is what
# the manual Call Analyzer flow uses).
#
# Bitget USDT-M futures min notional is symbol-dependent but ~$5 typical.
# `pick_max_tp_count()` clamps Opus's suggested count to what notional supports.

TP_SPLITS = {
    1: [100],
    2: [60, 40],
    3: [40, 40, 20],
    4: [40, 30, 20, 10],
    5: [30, 25, 20, 15, 10],
    6: [30, 25, 15, 15, 10, 5],
    7: [25, 20, 15, 15, 10, 10, 5],
}

# Bitget min-notional safety floor (USDT). Sub-orders smaller than this are
# rejected by the exchange — auto-trader caps tp_count so the smallest slice
# is still fillable.
MIN_TP_SLICE_USDT = float(os.environ.get("FUTURES_AI_MIN_TP_SLICE_USDT", "5.0"))


# ── Break-even SL buffer ─────────────────────────────────────────────────────
#
# When the lifecycle moves SL to "break-even" we cannot snap it to the raw
# entry price — hitting an SL at exactly entry locks in a SMALL LOSS because
# we still owe the closing taker fee and a small slippage allowance. The
# buffer is small but compounding: 0.12% × 100 trades = 12% of account.
#
# Defaults (operator-tunable via env), recalibrated 2026-05-24 after the
# NXPCUSDT BE_stop closed at -$0.006 because the SL fill slipped 0.04% below
# the trigger price (Bitget stop-market orders always take the next available
# price, which is worse than trigger on fast moves):
#   - Bitget USDT-M futures taker fee:  0.06% (open) + 0.06% (close) = 0.12%
#   - Observed slippage on stop fills:   ~0.04% one-side → 0.08% allowance
#   - Safety cushion:                    0.05%
#   - Total round-trip buffer:           0.25%
#
# Applied direction-aware: Long → SL placed ABOVE entry; Short → BELOW entry.
BE_BUFFER_PCT = float(os.environ.get("FUTURES_AI_BE_BUFFER_PCT", "0.0025"))


# ── Entry-drift guard ────────────────────────────────────────────────────────
#
# Scanner Stage 1/2 derives signal["entry_price"] from a closed-candle reference
# (4H/1H/scanner output). The orchestrator then sends a MARKET order to Bitget,
# which fills at the *current* mark — which can be meaningfully different on
# fast/illiquid moves between scan-time and execution-time (observed: QNTUSDT
# +7.3% drift 2026-05-24 00:02 UTC; ARKMUSDT +21% drift 2026-05-24 08:09 UTC).
# The TP ladder is anchored to the scanner entry; on a Long with up-drift,
# TP1/TP2 end up BELOW the actual fill — they'd fire as partial losses if
# Phase-2 plan-order execution were live.
#
# Guard: in `executor.open_real_trade`, if abs(fill - signal.entry) / signal.entry
# exceeds MAX_ENTRY_DRIFT_PCT, the position is closed immediately and a
# `real_entry_drift_aborted` event logged. The trade is refused, not retried.
#
# Set to 0 (or empty) to disable the guard entirely — discouraged but possible
# if the operator wants to opt out for testing.
MAX_ENTRY_DRIFT_PCT = float(os.environ.get("FUTURES_AI_MAX_ENTRY_DRIFT_PCT", "0.02"))


def be_price_for(entry: float, is_long: bool, buffer_pct: float = None) -> float:
    """Return the break-even SL price that covers round-trip fees + slippage.

    For a Long, returns entry × (1 + buffer) so an SL fill exits net ≥ $0.
    For a Short, returns entry × (1 - buffer).
    """
    if not entry:
        return entry
    buf = buffer_pct if buffer_pct is not None else BE_BUFFER_PCT
    return entry * (1.0 + buf) if is_long else entry * (1.0 - buf)


def pick_max_tp_count(notional_usdt: float, ideal: int = 3) -> int:
    """Return the largest tp_count from TP_SPLITS where the SMALLEST slice
    is ≥ MIN_TP_SLICE_USDT, capped at `ideal`. Used to clamp Opus's suggested
    count to what the position size actually supports.

    Example: notional=25, ideal=7 → returns 3 (5%×25=$1.25 < $5 floor for 7,
    but 20%×25=$5 hits the floor for 3-TPs).
    """
    ideal = max(1, min(int(ideal or 1), 7))
    for n in range(ideal, 0, -1):
        smallest_pct = min(TP_SPLITS[n])
        smallest_slice = notional_usdt * smallest_pct / 100.0
        if smallest_slice >= MIN_TP_SLICE_USDT - 1e-9:
            return n
    return 1


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
MAX_NOTIONAL_USDT         = 25.0   # FLOOR for the dynamic cap (see below)
# Dynamic notional cap — Profit Compounding Strategy: position size grows
# with accumulated equity. Effective cap per trade is:
#   max(MAX_NOTIONAL_USDT, equity × MAX_NOTIONAL_PCT)
# So a $100 starting equity → $25 cap; equity grows to $200 → $50 cap.
# Floor of $25 ensures small accounts still get tradeable sizes.
MAX_NOTIONAL_PCT          = 0.25
MAX_CONCURRENT_POSITIONS  = 5      # raised 3→5 (2026-05-22) — operator request

# Profit Compounding Strategy — streak-based risk progression. After N
# consecutive winning auto_ai trades since the last loss (or breaker
# reset), risk per trade is multiplied by min(N, MAX_STREAK_MULTIPLIER).
# Resets to 1× on any loss. Disabled with COMPOUND_STREAK_ENABLED=0 in env.
COMPOUND_STREAK_ENABLED   = os.environ.get(
    "FUTURES_AI_COMPOUND_STREAK", "1").strip() == "1"
MAX_STREAK_MULTIPLIER     = int(os.environ.get(
    "FUTURES_AI_MAX_STREAK_MULT", "3"))   # cap at 3× by default

# Catastrophe hedge — opens a single BTC perpetual SHORT to neutralise
# the basket's downside during rapid sell-offs (the "23:53 simultaneous
# stop-out" pattern from 2026-05-22). Defensive only — never extends
# upside. Disabled with FUTURES_AI_HEDGE_ENABLED=0 in env.
HEDGE_ENABLED             = os.environ.get(
    "FUTURES_AI_HEDGE_ENABLED", "1").strip() == "1"
# Trigger when total unrealised across auto_ai longs drops below this
# % of equity within HEDGE_TRIGGER_WINDOW_MIN minutes AND BTC drops at
# least HEDGE_TRIGGER_BTC_DROP_PCT in the same window.
HEDGE_TRIGGER_UNREAL_PCT       = -0.03   # -3% unrealised
HEDGE_TRIGGER_BTC_DROP_PCT     = -0.02   # -2% BTC 1h move
HEDGE_TRIGGER_LONG_BIAS_PCT    = 0.70    # 70%+ of notional must be Long
HEDGE_RATIO                    = 0.50    # hedge = 50% of net long notional
HEDGE_LEVERAGE                 = 3       # conservative — ride out the storm
# Unwind: any of {BTC recovers to within HEDGE_UNWIND_RECOVERY_PCT of
# its level when hedge opened, 2 consecutive green 15m BTC candles, or
# 24h elapsed}
HEDGE_UNWIND_RECOVERY_PCT      = 0.010   # within 1% of hedge-open BTC price
HEDGE_MAX_DURATION_HOURS       = 24

# Elite-setup bypass — a "verified 10/10" setup (scanner==10 AND Sonnet
# consensus==10) is rare enough that we will not pass on it even when
# the soft cap (MAX_CONCURRENT_POSITIONS) is full. The hard ceiling
# MAX_ELITE_POSITIONS bounds total simultaneous risk: 7 × 2% per-trade
# risk = 14% if every stop fires together, sitting right under the
# -15% total-DD breaker so the elite bypass can't put us over.
ELITE_BYPASS_SCORE        = 10
MAX_ELITE_POSITIONS       = 7

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
    Reason gets logged to the trader audit log if conn is provided.

    Operator override semantics — when transitioning out of
    `circuit_breaker` to `active`, also stamp `futures_ai_breaker_reset_at`
    with the current UTC time. The killswitch's consecutive-loss and
    daily-DD calculations honor this stamp by only counting trades closed
    AFTER it, so the operator's explicit "I've reviewed" decision forgives
    the past losses for breaker purposes. New losses post-reset still
    count normally, so 3 fresh losses will re-trip the breaker.
    """
    if new_state not in VALID_STATES:
        raise ValueError(f"invalid state {new_state!r}; "
                          f"must be one of {VALID_STATES}")
    if conn is None:
        from database import db_conn
        with db_conn() as c:
            return set_state(new_state, c, reason)

    prev_state = get_state(conn)

    conn.execute("""
        INSERT INTO settings(key, value) VALUES('futures_ai_state', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (new_state,))

    # Operator-initiated recovery from circuit_breaker → active stamps
    # the reset timestamp so the killswitch's history-based breakers
    # start counting fresh from this moment.
    breaker_reset = False
    if (prev_state == "circuit_breaker"
            and new_state == "active"):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO settings(key, value) VALUES('futures_ai_breaker_reset_at', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (now,))
        breaker_reset = True

    conn.commit()

    # Record the transition so we have a full audit trail
    try:
        payload = f'{{"to":"{new_state}","from":"{prev_state}",' \
                  f'"reason":{repr(reason or "user")},' \
                  f'"breaker_reset":{str(breaker_reset).lower()}}}'
        conn.execute("""
            INSERT INTO futures_ai_log(ts, event, payload_json)
            VALUES (datetime('now'), 'state_change', ?)
        """, (payload,))
        conn.commit()
    except Exception:
        pass   # log table may not exist yet on fresh DB

    return new_state


def breaker_reset_at(conn=None) -> Optional[str]:
    """Returns the UTC timestamp ('YYYY-MM-DD HH:MM:SS') of the last
    operator-initiated breaker reset, or None if never reset. Used by
    kill_switch to filter loss history."""
    if conn is None:
        from database import db_conn
        with db_conn() as c:
            return breaker_reset_at(c)
    row = conn.execute(
        "SELECT value FROM settings WHERE key='futures_ai_breaker_reset_at'"
    ).fetchone()
    return row[0] if row and row[0] else None


def snapshot() -> dict:
    """Calibration snapshot for /api/system/health + Futures-AI page."""
    try:
        from constants import KNOWLEDGE_VERSION as _kv
    except Exception:
        _kv = "(unknown)"
    return {
        "knowledge_version":          _kv,
        "enabled":                    _FUTURES_AI_ENABLED,
        "mode":                       _FUTURES_AI_MODE,
        "starting_equity_usdt":       _STARTING_EQUITY,
        "risk_per_trade_pct":         RISK_PER_TRADE_PCT,
        "risk_score_multipliers":     RISK_SCORE_MULTIPLIERS,
        "max_leverage":               MAX_LEVERAGE,
        "max_notional_usdt":          MAX_NOTIONAL_USDT,
        "max_concurrent_positions":   MAX_CONCURRENT_POSITIONS,
        "max_elite_positions":        MAX_ELITE_POSITIONS,
        "elite_bypass_score":         ELITE_BYPASS_SCORE,
        "max_notional_pct":           MAX_NOTIONAL_PCT,
        "compound_streak_enabled":    COMPOUND_STREAK_ENABLED,
        "max_streak_multiplier":      MAX_STREAK_MULTIPLIER,
        "hedge_enabled":              HEDGE_ENABLED,
        "hedge_trigger_unreal_pct":   HEDGE_TRIGGER_UNREAL_PCT,
        "hedge_trigger_btc_drop_pct": HEDGE_TRIGGER_BTC_DROP_PCT,
        "hedge_ratio":                HEDGE_RATIO,
        "hedge_leverage":             HEDGE_LEVERAGE,
        "consensus_min_score":        CONSENSUS_MIN_SCORE,
        "consensus_model":            CONSENSUS_MODEL,
        "daily_dd_breaker_pct":       DAILY_DD_BREAKER_PCT,
        "total_dd_breaker_pct":       TOTAL_DD_BREAKER_PCT,
        "consecutive_loss_breaker":   CONSECUTIVE_LOSS_BREAKER,
    }
