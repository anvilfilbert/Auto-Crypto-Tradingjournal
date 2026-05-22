"""
trading — Futures-AI auto-trader package.

Architecture (Phase A — new code only, root files untouched):

  trading/
    __init__.py        public surface: enabled(), state(), mode(), pause modes
    config.py          env-var loading, risk caps, knobs
    kill_switch.py     global state machine (active / pause-after-close / pause-now / circuit-breaker-tripped)
    risk_budget.py     Kelly-scaled per-trade sizing with hard caps
    signal_consensus.py scanner ≥7 + Sonnet agree → emit signal; otherwise reject
    bitget_trader.py   isolated read+write Bitget client (separate creds from journal)
    paper.py           full lifecycle simulator (no real orders)
    executor.py        real-trader: places entry + SL + TP1 + TP2 via bitget_trader
    manager.py         live management: BE move, trail, MAE cut, confirmed invalidation
    journal.py         per-decision audit log → DB

Default state: DISABLED. The chain only activates when FUTURES_AI_ENABLED=1
in the environment AND no circuit breaker is tripped AND the user pressed
the ▶ Activate button.
"""
from .config import (
    is_enabled,
    is_real_mode,
    get_state,
    set_state,
    starting_equity,
)

__all__ = [
    "is_enabled",
    "is_real_mode",
    "get_state",
    "set_state",
    "starting_equity",
]
