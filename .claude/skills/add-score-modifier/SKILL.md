---
name: add-score-modifier
description: Use when adding a ±0.X score modifier to scanner Stage 3 (PO3/bear_phase/HMM-style). Triggers on tasks like "add a setup-score adjustment based on regime X", "boost score when condition Y".
---

# Add a Score Modifier to Scanner Stage 3

A *score modifier* is a ±0.X adjustment applied to `setup_score` in `ai_scanner.py` Stage 3, based on some context signal (regime, time-of-day, structural pattern). It's the standard pattern used by `bear_phase`, `HMM`, `PO3 range`, `PO3 FVG`, `kill_zone`.

## When to use this pattern vs a confluence signal

- **Score modifier** (this skill): a single ±0.X bump applied AFTER the 15-signal confluence is computed. For *contextual* adjustments (regime, time, structural state).
- **Confluence signal** (see `add-confluence-signal`): one of the 15 signals summed into the base score. For *directional* technical signals.

If the signal answers "does this setup belong in this market regime?" → modifier.
If it answers "is the chart bullish or bearish?" → confluence signal.

## Reference implementations

| Modifier | File | Pattern function |
|---|---|---|
| Bear-phase alignment | `bear_phase.py` | `phase_alignment_weight(phase, direction) → (float, str)` |
| HMM regime alignment | `market_regime.py` | `hmm_alignment_weight(regime, direction) → (float, str)` |
| PO3 range | `chart_confluence.py` | `directional_range_weight(...) → (float, str)` |
| Kill-zone session | `scanner_criteria.py` | `_apply_kill_zone_modifier(score) → (float, [warnings])` |

## Checklist

### 1. Implement the weight function
- Location: extend an existing module (e.g., `chart_wyckoff.py`) or create a new one.
- Signature: `<feature>_alignment_weight(state, direction) → (weight: float, reason: str)`
- Magnitude: ±0.2 (small, additive) or ±0.3 (standalone, distinct from existing signals). NEVER exceed ±0.3 — stacking risk.
- Return `(0.0, "")` on missing data or low confidence; never raise.
- Add unit tests in `tests/test_<feature>_alignment.py` covering: positive case, negative case, both directions, edge cases (None inputs, missing fields, confidence threshold).

### 2. Wire into Stage 3
- File: `ai_scanner.py` around line 295 (after `bear_phase`, before personal-bad-hour cap)
- Pattern:
  ```python
  feature_label = ""
  try:
      from <module> import <feature>_alignment_weight, <state_fetcher>
      state = <state_fetcher>(...)
      w, reason = <feature>_alignment_weight(state, direction)
      if w != 0:
          score = max(0.0, min(10.0, score + w))
          feature_label = reason
          logger.info("<feature> mod applied to %s: %s", sym, feature_label)
      elif reason:
          feature_label = reason
  except Exception as e:
      logger.debug("<feature> modifier error on %s: %s", sym, e)
  ```
- The `try/except` is mandatory — modifiers must never crash Stage 3.
- Re-apply personal-bad-hour cap AFTER (line ~329, already in place).

### 3. Surface in setup dict (for Sonnet visibility)
- Add to setup dict: `"_<feature>": feature_label,`
- Add to PO3 summary line in `ai_scanner.py` around line 421:
  ```python
  if feature_label:   _po3_bits.append(feature_label.replace("<Prefix>: ", "<short>:"))
  ```

### 4. Surface in consensus log payload
- File: `trading/signal_consensus.py` around line 290
- Add: `"<feature>": setup.get("_<feature>"),`
- Without this, the modifier fires but the field is invisible in `futures_ai_log` payloads (lesson from 2026-05-24 HMM wiring gap).

### 5. Walk-forward validation
- Walk-forward A/B test: keep the modifier OFF for run 1, ON for run 2, compare test_sharpe.
- If `test_sharpe(ON) ≤ test_sharpe(OFF) × 0.95`, the modifier is noise — revert.

### 6. Deploy
- Sync via `/tmp/deploy_audit.exp`
- If only Stage 3 logic changed: `restart_pi.exp` (no snapshot/config touch).
- Verify via post-deploy log: next `consensus_rejected` event should contain the new field.

## Red flags

- "I'll just stack 5 modifiers all at ±0.3" → stop. Total modifier stack should not exceed ±0.6 net. Anything more = score inflation, calibration breaks.
- "I'll add the modifier with no walk-forward" → stop. Every modifier must justify itself via OOS Sharpe delta.
- "I'll skip the consensus payload step" → stop. Without it, the calibration data has a blind spot.
