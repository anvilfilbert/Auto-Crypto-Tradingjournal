---
name: add-walk-forward-feature
description: Use when designing a new feature (signal/modifier/sizing rule) that needs walk-forward validation. Triggers on "is this walk-forwardable?", "how do I A/B test feature X", "validate the new signal against OOS data".
---

# Design Features for Walk-Forward Validation

The system ships candle-based walk-forward (`backtest_optimizer.run_walk_forward`) for OOS validation. Any feature meant to improve `setup_score` calibration should be walk-forward-testable BEFORE merging.

## The walk-forward viability rule

A feature is **walk-forwardable** if-and-only-if:
1. It is derived from closed candles (no future leakage)
2. It does NOT depend on runtime/cross-trade state (e.g., not "last 3 win streak")
3. It can be A/B toggled via env knob or boolean parameter

If 1, 2, 3 all hold → walk-forward A/B test. If any fails → operator workflow validation only.

## Decision tree

```
Is the feature a confluence signal or score modifier?
├── YES → walk-forward viable. Implement A/B toggle.
└── NO → continue
        │
        Is it a sizing multiplier (not a hard gate)?
        ├── YES, derived from closed candles → walk-forward viable
        └── NO, or uses runtime state → SKIP walk-forward; validate by operator workflow
```

## Walk-forwardable features (do A/B)

- All confluence signals (RSI/MACD/...) — derived from candles
- All score modifiers (PO3, HMM, kill-zone, bear_phase) — derived from candles or static rules
- Volatility-aware sizing — derived from per-asset ATR (candle-derived)
- Trade-grade computation — derived from open-time channel + close-time price

## NOT walk-forwardable (use other validation)

- Monthly DD breakers — calendar-dependent, runtime state
- Streak-based gates (loss breaker, euphoria dampener) — cross-trade runtime state
- Behavioral gates (Apgar, Readiness) — operator workflow
- Tiered BE moves — trade-lifecycle state
- Hedge logic — depends on basket state at runtime

For these: validate by counterfactual on historical data ("would this gate have blocked X trades last month?"), or by canary monitoring after deploy.

## Walk-forward A/B protocol

### 1. Add env toggle BEFORE walk-forward
- File: `trading/config.py` (or specific module's env block)
- Pattern: `<FEATURE>_ENABLED = bool(int(os.environ.get("FUTURES_AI_<FEATURE>_ENABLED", "1")))`
- Without the toggle, you can't run A/B without code edits.

### 2. Implement the feature
- See `add-score-modifier`, `add-confluence-signal`, or `add-risk-gate` skills for the implementation pattern.

### 3. Tests pass locally
- Unit tests cover the feature in isolation.

### 4. Walk-forward run with feature OFF (baseline)
- Set `FUTURES_AI_<FEATURE>_ENABLED=0`
- POST `/api/backtest/walk-forward` with `{symbol, timeframe, n_trials, days: 180}`
- Record: `train_sharpe`, `test_sharpe`, `train_trades`, `test_trades`, `generalizes`

### 5. Walk-forward run with feature ON
- Set `FUTURES_AI_<FEATURE>_ENABLED=1`
- Same POST. Record same metrics.

### 6. Compare and decide
| Outcome | Decision |
|---|---|
| `test_sharpe(ON) > test_sharpe(OFF) × 1.1` AND same/more trades | Ship with ON default |
| `test_sharpe(ON) ≈ test_sharpe(OFF)` AND ON has fewer false signals | Ship with ON default |
| `test_sharpe(ON) < test_sharpe(OFF) × 0.95` | Revert. Feature is noise. |
| ON halves trade count without Sharpe lift | Over-restrictive. Reduce magnitude. |
| Either OFF or ON shows `train_trades < 10` | Walk-forward isn't viable for this symbol/window. Try different symbol or extend days. |

### 7. Multi-symbol replication
- Run on 3-5 different symbols (different beta, different vol regime).
- Feature should help OR not-hurt on majority. If it helps BTC but hurts altcoins, gate it by liquidity/cap.

### 8. Document the result
- Save the A/B numbers in the feature's commit message OR in `optimizer_runs` history.
- Future calibration depends on this evidence trail.

## Multi-feature interaction problem

If 5 walk-forwarded features each show +5% Sharpe improvement individually, stacking all 5 does NOT give +25%. Often they fire on overlapping setups → diminishing returns. After 2-3 new features, walk-forward the FULL set vs the previous full set, not feature-by-feature.

## Operator-workflow validation (for non-walk-forwardable features)

For risk gates and behavioral features:
1. Ship with `ENABLED=0` default
2. Operator manually validates: "does the gate fire when I expect it to?"
3. Compare to recent history: "would this have blocked the QNT loss on 2026-05-22?"
4. Flip `ENABLED=1` after 1-2 weeks of dry-run monitoring

## Red flags

- "I'll just ship it without walk-forward" → stop. We added the `ai_score_at_open` column and walk-forward infrastructure specifically to validate features against OOS data.
- "Walk-forward shows ON beats OFF by 1%" → not significant. Need ≥10% Sharpe lift OR ≥20% trade count change to call it a real edge given n=~20 trades.
- "Walk-forward shows ON only beats OFF on BTC, not ETH" → don't ship. Single-symbol edge often = overfit. Demand multi-symbol replication.
- "I added 3 features all at once and walk-forwarded the bundle" → harder to attribute. Add and validate one at a time.
