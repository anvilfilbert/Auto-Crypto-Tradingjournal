---
name: add-risk-gate
description: Use when adding a pre-trade gate to trading/kill_switch.can_open_new_trade (e.g., monthly DD breaker, behavioral gate, sizing modifier). Triggers on "block trades when X", "require Y before entering", "add a guardrail for Z".
---

# Add a Pre-Trade Risk Gate

Pre-trade gates live in `trading/kill_switch.py::can_open_new_trade(conn, scanner_score) → (bool, reason: str)`. Returning `False` blocks the trade and logs `rejected_killswitch` to `futures_ai_log`.

## Reference implementations

| Gate | File | Trigger | Default |
|---|---|---|---|
| Daily DD breaker (-5%) | `kill_switch.py` | `daily_pnl_pct < DAILY_DD_BREAKER_PCT` | ON |
| Total DD breaker (-15%) | `kill_switch.py` | `total_pnl_pct < TOTAL_DD_BREAKER_PCT` | ON |
| 3 consecutive losses | `kill_switch.py` | `consecutive_losses >= 3` | ON |
| Max concurrent | `kill_switch.py` | `open_positions >= MAX_CONCURRENT_POSITIONS` | ON |
| Drawdown dampener | `risk_budget.py::_drawdown_dampener` | graduated 1.0×/0.75×/0.5×/0.25× | ON |
| Streak multiplier (compound) | `risk_budget.py::_streak_multiplier` | grows risk with wins | ON |

## Sizing-layer additions vs gate-layer additions

| Type | Where | Effect |
|---|---|---|
| Hard gate | `kill_switch.can_open_new_trade` | Blocks trade entirely (returns False) |
| Soft multiplier | `risk_budget.size_trade` | Reduces position size but allows trade |

Choose based on severity. Behavioral (Apgar, Readiness) → gate (binary). Volatility-aware sizing → multiplier (continuous).

## Checklist

### 1. Decide gate vs multiplier
- Binary all-or-nothing condition → gate
- Continuous adjustment → multiplier
- If unsure, start as multiplier — easier to validate without locking out trades

### 2. Add config knob
- File: `trading/config.py`
- Pattern: `<NAME>_ENABLED = bool(int(os.environ.get("FUTURES_AI_<NAME>_ENABLED", "1")))`
- ALWAYS make new gates env-toggleable, especially behavioral ones.
- Default ON for hard-data gates (e.g., monthly DD)
- Default OFF for behavioral gates (Apgar, Readiness) until operator validates workflow

### 3. Implement the check function
- For gates: add to `kill_switch.py` as `_<name>_check(conn) → (bool, reason: str)` returning `(True, "")` to allow, `(False, "reason ...")` to block.
- For multipliers: add to `risk_budget.py` as `_<name>_multiplier(conn, state) → (mult: float, reason: str)` returning multiplicand for `size_trade`.
- ALWAYS handle missing data gracefully: return `(True, "skipped: <reason>")` on errors so a broken check doesn't lock out trading.

### 4. Wire into the pipeline
- Gate: `can_open_new_trade()` runs checks in series — order matters. Add new check AFTER existing breakers but BEFORE any AI-cost-incurring step.
- Multiplier: `size_trade()` already chains multiplicands. Add yours in the chain alongside `dd_mult` and `streak_mult`.

### 5. Tests
- File: `tests/test_<name>_gate.py` or `tests/test_risk_budget_<name>.py`
- Cover: enabled+passing, enabled+blocking, disabled (returns True/1.0), missing data, env-knob respected.
- For graduated multipliers: test each tier boundary.

### 6. Walk-forward viability
- Most risk gates CANNOT be walk-forwarded (they're runtime-state-dependent, not candle-derived).
- Validate by comparing the previous month's actual outcomes against "would this gate have blocked X% of trades?".
- For multipliers that are deterministic (vol-aware sizing): walk-forward by toggling on/off and comparing test_sharpe.

### 7. Log the rejection
- File: `trading/orchestrator.py` calls `_log(conn, "rejected_killswitch", setup, reason)` for blocked trades.
- The reason string ends up in `futures_ai_log.payload_json`. Make it specific (`"monthly DD gate: 7.2% / 6%"` not just `"blocked"`).

### 8. Surface in snapshot
- File: `trading/config.py::snapshot()` returns the operator-facing state.
- If the gate has runtime state (e.g., monthly_loss_pct, consecutive_wins), add it under `runtime`.
- If the gate is configurable, add the knob value under `config`.
- This is what the Futures-AI UI page reads.

### 9. Deploy
- Sync via `/tmp/deploy_audit.exp`
- Bytecode cache nuke IF you modified `config.py::snapshot()` (per `feedback_pi_bytecode_cache.md`).
- Else: `restart_pi.exp` is sufficient.
- Verify the gate's state appears in `/api/futures-ai/state` response.

## Red flags

- "I'll skip the env knob" → stop. Every gate must be toggleable for emergency disable.
- "I'll fail open by default" → stop, FOR HARD-DATA GATES. Failing open on a DD gate could let the system blow past safety limits during an outage. Behavioral gates: fail open is fine (don't lock trading on a missing self-report).
- "I'll let exceptions crash the trade pipeline" → stop. Always catch + log, never raise out of a gate check.
- "I'll add this between two other gates without thinking about order" → stop. Order matters: cheap deterministic checks first (max concurrent, DD breakers), then expensive (AI consensus). Don't put a slow gate (e.g., DB query) before a fast one.
