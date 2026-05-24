---
name: add-confluence-signal
description: Use when adding a new entry to the 15-signal confluence stack in chart_confluence.py (RSI/MACD/EMA/ADX/WT/MFI/Stoch/CVD/order_flow/volume/SMT/liquidations/smart-flow). Triggers on "add Bollinger as a signal", "include OBV in confluence", "Wyckoff Spring as a 16th signal".
---

# Add a Confluence Signal

The system has 15 numerical signals summed into a directional confluence score. Each signal contributes a small weight (typically ±0.2 to ±0.4) capped by a grouping cap. The output is `setup_score` 0-10 with directional bias.

## When this pattern vs a score modifier?

- **Confluence signal** (this skill): a directional technical reading (bullish/bearish/neutral) summed with 14 others. For *technical* signals.
- **Score modifier** (see `add-score-modifier`): a context-only ±0.X bump. For *regime/timing/structural* signals.

## Architecture overview

```
chart_indicators.compute_<X>(df) → indicator value + metadata
  ↓
chart_confluence._<X>_weight(symbol, tf, ind_value, ctx) → (weight, reason_label)
  ↓
chart_confluence._get_tf_weights(symbol, tf, ctx) → sums all signal weights → tf_score
  ↓
confluence_score(symbol, timeframes, ctx) → aggregates across TFs → final confluence
```

## Reference implementations

| Signal | File | Function | Weight magnitude |
|---|---|---|---|
| RSI (regime-aware) | `chart_rsi.py` | `regime_aware_rsi_weight()` | ±0.4 |
| MACD | `chart_confluence.py` | `_macd_weight()` | ±0.3, momentum-grouped cap |
| WaveTrend | `chart_confluence.py` | `_wt_weight()` | ±0.3 |
| FVG (signal version) | `chart_fvg.py` | `nearest_fvg_signal()` | ±0.3 |
| SMT divergence | `chart_confluence.py` | `_smt_weight()` | ±0.15 fixed |

## Grouping caps (read first, important)

To prevent multiple correlated indicators from over-contributing:

| Group | Members | Cap |
|---|---|---|
| Momentum (oscillator) | MACD, MFI, Stochastic | ±1.0 total |
| Trend (slow) | EMA, ADX | no cap (independent) |
| Volume/flow | CVD, order_flow, volume | ±0.6 total |
| Cross-exchange | SMT divergence, SMT direction | ±0.3 total |

New signals must declare their group and respect the cap.

## Checklist

### 1. Define the indicator
- Location: extend `chart_indicators.py` if it's a standard indicator, or new module if novel (e.g., `chart_wyckoff.py`).
- Function signature: `compute_<X>(df, **params) → {"value": float, "trend": str, "strength": float, ...}`
- Return a structured dict, NOT a raw float — keeps room for future extensions.
- Use `pandas_ta` library where available (already imported).

### 2. Define the weight function
- Location: `chart_confluence.py` (or wherever the related family lives — keep co-located).
- Signature: `_<X>_weight(symbol, tf, ind_dict, ctx=None) → (weight: float, reason: str)`
- Weight magnitude: 0.2-0.4. Larger = more impactful, but consider correlation with other signals.
- ALWAYS return `(0.0, "")` on missing data; never raise. The `_get_tf_weights` summation is fragile to exceptions.

### 3. Wire into _get_tf_weights
- File: `chart_confluence.py`, function `_get_tf_weights`.
- Add the weight calculation alongside existing signals.
- Append to `parts[]` list with `{"name": "<X>", "weight": w, "reason": reason}` so Sonnet sees it in the prompt.
- If joining a group: apply the group cap AFTER summing within the group.

### 4. Update CLAUDE.md
- File: `CLAUDE.md`, "Confluence Signals" section (currently lists 15).
- Bump count: "16 confluence signals total → max_per_tf = X.YY".
- Append a line describing the new signal.

### 5. Tests
- File: `tests/test_chart_confluence_<X>.py`
- Cover: bullish case, bearish case, neutral case, missing-data case, edge boundaries (e.g., RSI exactly 30 or 70).
- For grouped signals: test that the cap is enforced when multiple group members fire.

### 6. Walk-forward validation
- Critical for confluence signals (more so than modifiers — they directly change the base score).
- A/B test: walk-forward with signal weighted=0 vs weighted=normal. Compare test_sharpe + trade count.
- If new signal halves trade count without proportional Sharpe lift → over-restrictive. Reduce magnitude.

### 7. Deploy
- Sync via `/tmp/deploy_audit.exp`
- Restart: `restart_pi.exp` (signal code only, no schema/snapshot).
- Watch first 5 scans post-deploy for the new field in `parts[]` via futures_ai_log payload.

## Red flags

- "I'll add this without grouping" → stop. The 15-signal architecture explicitly groups for cap reasons. Skipping the group → score inflation.
- "I'll skip the parts[] entry" → stop. Sonnet reads parts[] in the prompt; missing entries reduce its visibility.
- "This signal is highly correlated with MACD" → reconsider. Two correlated signals = double-counting. Either group them or use ONE.
