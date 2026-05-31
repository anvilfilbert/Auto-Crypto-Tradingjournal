# Auto-AI Self-Learning Architecture

**Status:** design proposal · 2026-05-31
**Scope:** the `auto_ai` chain only (Futures-AI auto-trader). Manual chain untouched.

> **Phase code in this doc:** `L-0` through `L-7` (Learning).
> Cross-doc references use the prefix codes: `R-N` (Research), `A-X` (Agents), `N-N` (Noise).

## 0. Cross-doc dependencies

This document is one of four in the auto_ai concept set:
- `AUTO_AI_RESEARCH_FINDINGS.md` — raw signals & KPIs the learner can tune over
- `AUTO_AI_SPECIALIZED_AGENTS.md` — LLM agents that read/write at decision time
- `AUTO_AI_NOISE_DETECTION.md` — filter gates upstream of the learner

**Hard dependencies on other docs:**
- `L-3` (Score & threshold learners) **requires** `A-B` (Backtest Validator agent) — every learner change must be replayed on historical data first
- `L-2` (Cheap learners) **benefits from** `N-2` (FDR correction) — without FDR the learner will "discover" false-positive patterns
- `L-5` (Risk learners) **uses** `R-1.1`, `R-1.4`, `R-5.2` (DSR, Omega, volatility targeting from Research)
- `L-6` (Pattern learners) **shares the VPIN pipeline** with `N-3` (VPIN gate) and `A-E` (Cascade Predictor) — build once, used three ways

**Shared infrastructure tables:**
- `learned_params` — written by learner, read by scanner / orchestrator / noise gates
- `learner_log` — written by learner, read by Stats UI
- `futures_ai_log` — written by learner, noise gates, and agents; read by everyone

---

## 1. Goals · Non-goals

### Goals
1. Every observation surfaced on the Futures-AI Stats page **also feeds an
   automatic parameter update** in the auto-trader.
2. Updates run **on a schedule**, not on-demand. The system gets quietly
   better between operator check-ins.
3. **Every change is auditable.** Operator can see what changed, when, why,
   and what the sample / confidence was.
4. **Safety first.** Auto-changes are bounded per cycle, gated on minimum
   sample size + statistical significance, and reversible.
5. **No more hardcoded magic numbers in code.** All tunable behavior reads
   from a versioned `learned_params` table the orchestrator queries at
   decision time.

### Non-goals
- LLM fine-tuning. We're using Claude/Gemini via API. Adjusting weights is
  not on the table.
- Reinforcement learning loops that adjust the LLM itself.
- Auto-changes that bypass the kill switch / DD breaker. Those stay hard.
- Replacing the operator's strategic judgement. The system tunes
  *coefficients*, not *strategy direction*.

---

## 2. Core principle

> **Code holds the algorithm; the database holds the parameters.**

Today: `CONSENSUS_MIN_SCORE = 8` is a constant in `trading/config.py`.
After this: `config.get_consensus_min_score(archetype="breakout")` reads
from a `learned_params` row that was updated last night by the learner
because breakout score-7 trades won 65% (sample 67, p<0.01).

The orchestrator and scanner are unchanged in logic — they just call
parameter accessors instead of importing constants.

---

## 3. High-level architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Trading Journal                          │
│                                                                 │
│  ┌────────────┐    closes      ┌────────────────────────┐     │
│  │ Auto-trader│ ─────────────▶ │  positions table       │     │
│  │ (orchest.) │                │  (closed trades)       │     │
│  └─────▲──────┘                └──────────┬─────────────┘     │
│        │                                  │                    │
│        │ reads at scan time               │ source of truth    │
│        │                                  ▼                    │
│  ┌─────┴──────────────┐   reads   ┌────────────────────┐     │
│  │ learned_params     │◀──────────│      learner       │     │
│  │ table (KV store)   │  writes   │   (background job) │     │
│  └────────────────────┘           └──────────┬─────────┘     │
│                                              │                  │
│                                              │ logs            │
│                                              ▼                  │
│                                   ┌────────────────────┐       │
│                                   │  learner_log table │       │
│                                   │  (audit trail)     │       │
│                                   └──────────┬─────────┘       │
│                                              │                  │
│                                              ▼                  │
│                                   ┌────────────────────┐       │
│                                   │  Futures-AI Stats  │       │
│                                   │  UI (shows updates)│       │
│                                   └────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

Components:

1. **`learned_params` table** — versioned key-value store. Single row per
   tunable parameter. JSON values support per-archetype / per-symbol /
   per-session structure.
2. **`learner` daemon** — runs every 6h (and on-demand via API). For each
   tunable, computes the new value from closed-trades data and writes only
   if confidence threshold is met.
3. **Accessor layer** — `trading/learned.py` exposes
   `get(key, default)` and `get_or(key, archetype=None, default=None)`. The
   scanner/orchestrator/executor call these instead of importing constants.
4. **`learner_log` table** — every change recorded with before/after,
   sample size, p-value, applied/skipped, reason.
5. **Stats UI panel** — "Recent auto-adjustments" section on Futures-AI
   Stats showing the last N learner-log entries.
6. **Safety circuit** — `learner_safety.py` monitors post-change outcome.
   If 7d Sharpe drops > X% within 7 days of a change, auto-revert and lock
   the parameter for operator review.

---

## 4. Learnable parameters (full inventory)

Every panel from the "A list" maps to one or more learnable parameters.
Each row: **panel** → **what gets observed** → **what gets updated** →
**guard conditions** → **safety bound per cycle**.

### 4.1 Score thresholds (highest-impact group)

| Panel | Observation | Updates | Guard | Max Δ/cycle |
|---|---|---|---|---|
| Per-score WR / EV (already on stats page) | Calibration: at score N, WR=X% | `consensus_min_score.{archetype}` per archetype | ≥30 trades at score N AND lower bound of 95% CI on WR ≥ 55% | ±1.0 |
| By-archetype WR / EV | Breakout 83% WR vs continuation 37% | `archetype_score_modifier.{archetype}` | ≥20 trades per archetype | ±0.3 |
| Bootstrap CI | Reduces over-fitting to small samples | Gates ALL other updates | n/a | gate only |

### 4.2 SL / TP placement (TP-take-rate + MFE/MAE driven)

| Panel | Observation | Updates | Guard | Max Δ/cycle |
|---|---|---|---|---|
| MFE giveback % | Trades give back 40% of peak unrealized | `tp1_atr_multiplier` per archetype | ≥20 closed trades AND giveback>25% | ±0.2 ATR |
| MAE vs SL distance | Avg MAE only 0.3× SL distance | `sl_atr_multiplier` per archetype | ≥20 trades AND MAE/SL<0.5 | ±0.2 ATR |
| TP1→TP2 hit rate | 80% hit TP1 / 20% hit TP2 → ladder too back-loaded | `tp_ladder_percentages.{archetype}` (e.g., 40/40/20 → 60/30/10) | ≥15 TP1-hits per archetype | ±10 pct points |
| Stop placement quality | Trades stopped within 0.5× ATR of entry = stop too tight | `sl_min_atr_multiplier` (floor) | n=30 | ±0.1 |

### 4.3 Per-symbol modifiers

| Panel | Observation | Updates | Guard | Max Δ/cycle |
|---|---|---|---|---|
| Per-symbol WR + total PnL | Symbol X has -$50 across 12 trades, WR 25% | `symbol_modifier.{symbol}` (added to score) | ≥10 trades on symbol AND total PnL negative AND lower CI bound on WR <50% | -0.5 (penalize), +0.3 (boost) |
| Auto-blocklist | Severe underperformer | `symbol_blocklist` (boolean) | ≥20 trades AND WR<30% AND total<-3% equity | n/a (binary flag) |

### 4.4 Time-of-day / session

| Panel | Observation | Updates | Guard | Max Δ/cycle |
|---|---|---|---|---|
| By-session table | NY-Overlap loses despite 67% WR | `session_modifier.{session}` | ≥30 trades per session | ±0.3 |
| By-DoW table | Saturday catastrophic | `dow_modifier.{day}` | ≥20 trades per day | ±0.5 |
| Hourly P&L (UTC) | Hour 19 loses badly | `hour_modifier.{utc_hour}` | ≥15 trades per hour | ±0.3 |
| Day × session heatmap | Specific combo bad | `cell_modifier.{day}.{session}` (only set if 2D signal stronger than 1D marginals) | ≥10 trades per cell | ±0.4 |

### 4.5 Hold time

| Panel | Observation | Updates | Guard | Max Δ/cycle |
|---|---|---|---|---|
| Hold-time histogram | Trades held >24h lose | `time_stop_hours` (auto-close at N hours) | ≥20 trades >24h AND WR<40% | -6h or +24h |
| Hold-time × outcome | Longer = worse | `max_hold_hours.{archetype}` | n=20 per archetype | ±12h |

### 4.6 Regime-conditional

| Panel | Observation | Updates | Guard | Max Δ/cycle |
|---|---|---|---|---|
| VIX-band performance | High VIX → Shorts may outperform, Longs may suffer | `vix_band_modifier.{band}.{dir}` (e.g., very_high × short) — both directions tracked separately | ≥15 trades per cell | ±0.4 |
| F&G zone | Extreme greed → contrarian Shorts; extreme fear → contrarian Longs | `fear_greed_modifier.{zone}.{dir}` — both dirs per zone | ≥15 trades per cell | ±0.3 |
| Bull/bear phase | One full matrix of (phase × direction): e.g. decline+Short works as theory predicts, capitulation+Long underperforms | `phase_dir_modifier.{phase}.{dir}` (10 cells: 5 phases × 2 dirs) | ≥10 per cell | ±0.3 |
| BTC regime (HMM) | Trending vs ranging | `hmm_regime_modifier.{regime}.{dir}` | ≥10 per regime-dir | ±0.3 |

### 4.7 Risk / sizing

| Panel | Observation | Updates | Guard | Max Δ/cycle |
|---|---|---|---|---|
| Rolling 30d Sharpe | Sharpe trending down | `risk_per_trade_pct` (scale down) | 7d MA Sharpe drop ≥30% vs 30d | -10% relative |
| VaR / CVaR | 95% VaR exceeds tolerance | `max_notional_usdt` (scale down) | n=30, VaR>10% equity | -20% relative |
| Underwater % | >40% of days in DD | `risk_per_trade_pct` (scale down) | last 14d | -10% relative |
| Win/loss size asymmetry | Avg loss > 2× avg win | `take_profit_multiplier` (widen TPs) OR `risk_per_trade_pct` (smaller bets) | n=30 | +0.2 ATR or -10% risk |

### 4.8 Behavioral / meta

| Panel | Observation | Updates | Guard | Max Δ/cycle |
|---|---|---|---|---|
| Hot-hand: post-loss WR | WR drops after losses | `post_loss_cooldown_hours` (skip new entries for N hours) | n=30 each side, gap≥10pp | 0 → 4h → 12h |
| Edge decay (t-test) | Last 7d sig worse than prior 23d | TRIGGER `state=pause_after_close` + alert | p<0.05 with effect size>0.3 | binary |
| Required WR vs actual | WR=60, breakeven=72 | Flag `risk_per_trade_pct` for review (suggest -25%) | always evaluated | suggest only, no auto |
| Skipped-setup outcome shadow | Track what rejected setups would have done; if they would have won, lower threshold | `consensus_min_score` per archetype | n=20 rejected with simulated outcomes | ±0.5 |

### 4.9 Advanced pattern recognition

| Panel | Observation | Updates | Guard | Max Δ/cycle |
|---|---|---|---|---|
| K-means cluster (15-dim entry context) | Auto-discovered cluster #3 wins 78%, cluster #7 loses 60% | `cluster_modifier.{cluster_id}` | ≥10 trades per cluster | ±0.4 |
| Decision-tree top splits | Tree finds: `vix<22 AND hold_time<4h` → 78% WR | `tree_gate_rules` (additive boolean modifiers) | Tree accuracy on held-out test set >65% | replace whole ruleset |
| HMM equity regime | "Bad regime detected" 3 days running | TRIGGER `pause_after_close` | confidence>0.7 | binary |
| Survival analysis (Kaplan-Meier) | Hazard rate spikes at hour 6 | Same as `max_hold_hours` | n=40 | ±2h |

---

## 5. Self-learning loop — step by step

```
                          every 6 hours (cron)
                                │
                                ▼
            ┌───────────────────────────────────┐
            │  learner.run_all()                │
            │                                   │
            │  For each tunable parameter:      │
            │                                   │
            │    1. Pull closed trades from     │
            │       positions table where       │
            │       chain='auto_ai',            │
            │       window = last 30d           │
            │                                   │
            │    2. Compute proposed new value  │
            │       + sample size               │
            │       + confidence/p-value        │
            │                                   │
            │    3. Apply guards:               │
            │       - sample ≥ threshold ?      │
            │       - statistical sig OK ?      │
            │       - inside max-Δ bound ?      │
            │       - not on operator hold ?    │
            │                                   │
            │    4. Hold-out validation:        │
            │       - Train on 80% of recent    │
            │       - Test new param on 20%     │
            │       - Reject if test worse      │
            │                                   │
            │    5. If all pass:                │
            │       - Write to learned_params   │
            │       - Log to learner_log        │
            │       - Send Telegram alert       │
            │                                   │
            │  6. Run safety circuit:           │
            │     - For changes >7 days old,    │
            │       check post-change outcome   │
            │     - If WR/Sharpe degraded,      │
            │       auto-revert + lock          │
            │                                   │
            └───────────────────────────────────┘
                                │
                                ▼
                       Next scan reads
                       updated params
```

**Cycle frequency:** every 6 hours, plus on-demand via `POST /api/learner/run`.

**On-the-fly check:** when a new position closes, mark the affected
buckets dirty. If 5+ buckets dirty AND >2h since last run, trigger an
early run.

**Manual override:** operator can pin any param to a fixed value via
`POST /api/learner/pin {key, value}`. Learner respects pin.

---

## 6. Scientific methods toolbox

For correct statistics (not eyeball comparisons):

| Method | Use case | Library |
|---|---|---|
| **Wilson score interval** | WR confidence bounds for binomial outcomes | `scipy.stats.binom` or hand-coded |
| **Bootstrap (10k resamples)** | CI for mean P&L, expectancy, Sharpe | `numpy.random.choice` |
| **Mann-Whitney U** | "Is bucket A's P&L significantly different from B's" (non-parametric, robust to outliers) | `scipy.stats.mannwhitneyu` |
| **Kolmogorov-Smirnov** | Distribution shift detection (regime change) | `scipy.stats.ks_2samp` |
| **Welch's t-test** | Mean comparison when variances differ | `scipy.stats.ttest_ind(equal_var=False)` |
| **Chi-squared** | Categorical: archetype × outcome | `scipy.stats.chi2_contingency` |
| **k-means clustering** | Auto-discover setup clusters | `sklearn.cluster.KMeans` |
| **Decision tree (CART)** | Explainable rule extraction | `sklearn.tree.DecisionTreeClassifier` |
| **HMM (Gaussian)** | Equity-curve regime detection (good vs bad periods) | `hmmlearn.hmm.GaussianHMM` (already used elsewhere per memory) |
| **Kaplan-Meier survival** | Hold-time hazard analysis | `lifelines.KaplanMeierFitter` |
| **Spearman correlation** | Rank correlation: score → P&L | `scipy.stats.spearmanr` |
| **Sequential probability ratio test** | Online edge-decay detection | hand-coded |

---

## 7. External dependencies needed

| Package | What for | Justification |
|---|---|---|
| `scipy` ≥1.11 | All statistical tests | Standard, already a numpy dep |
| `scikit-learn` ≥1.4 | k-means, decision tree | Wide community support, well-tested |
| `lifelines` | Survival analysis | Smaller dep — could skip if survival not pursued |
| `hmmlearn` | HMM | Already used per CLAUDE.md memory — no new dep |
| `statsmodels` (optional) | If we want ARIMA forecast of Sharpe trend | Defer until needed |

Existing Hermes skill `dspy` is also worth surfacing here:
- DSPy can **programmatically optimize LLM prompts** (e.g., the rulebook
  generation prompt) against an objective metric. So instead of
  hand-tuning the rule-generation prompt, DSPy tunes it against "rules
  that actually predict outcomes." Future Phase 4 candidate.

---

## 8. Phased rollout

Each phase ships independently. Operator can stop after any phase.

### Phase L-0 — Foundation (2 days)
- `learned_params` table + accessor module `trading/learned.py`
- `learner_log` table + audit trail UI panel
- Manual override / pin API
- **No behavior changes yet** — accessors return current hardcoded
  defaults. Just plumbing.

### Phase L-1 — Read path (2 days)
- Refactor scanner / orchestrator / executor to use accessors instead of
  constants. Every existing knob (CONSENSUS_MIN_SCORE,
  MAX_NOTIONAL_USDT, etc.) becomes a `learned.get(...)` call.
- Still no learner running. Defaults unchanged. Pure refactor.
- Tests: orchestrator behavior identical before/after.

### Phase L-2 — Cheap learners (3 days)
- Per-symbol modifier
- Per-session modifier
- Per-day-of-week modifier
- Per-hour modifier
- All with Wilson score CI gating + max-Δ bound + sample-size threshold
- Cron scheduler runs every 6h
- Safety circuit (auto-revert if 7d Sharpe drops post-change)
- Telegram alert on every applied change

### Phase L-3 — Score & threshold learners (4 days)
- Per-archetype `consensus_min_score`
- Hold-out validation (80/20 split)
- Skipped-setup outcome shadow tracking (log rejected setups, simulate
  outcomes via candle replay, feed into threshold tuner)

### Phase L-4 — TP/SL learners (4 days)
- TP1/TP2/TP3 ATR multipliers per archetype
- SL ATR multiplier per archetype
- TP ladder percentages
- Backfill from MFE/MAE history

### Phase L-5 — Risk learners (3 days)
- Dynamic `risk_per_trade_pct` based on rolling Sharpe + DD
- Dynamic `max_notional_usdt` based on VaR
- Time-stop (auto-close at N hours when archetype shows time decay)
- Post-loss cooldown

### Phase L-6 — Pattern learners (5 days, optional)
- K-means clustering of entry contexts → cluster modifier
- Decision tree gates
- HMM regime detection on equity curve → auto-pause trigger
- Edge-decay t-test (sequential)

### Phase L-7 — Stats UI integration (2 days)
- Add "Recent auto-adjustments" panel on Futures-AI Stats
- Add "Pinned parameters" panel showing operator overrides
- Add rolling Sharpe / edge-decay alert banner
- Add hold-out test results panel ("last 5 changes: 4 OK, 1 reverted")

**Total estimate:** ~25 dev days spread over 5-6 weeks.

---

## 9. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Overfitting** — learner adapts to noise, makes things worse | High | Hold-out validation, sample-size thresholds, max-Δ bounds, safety circuit auto-revert |
| **Curse of dimensionality** — too many params learned on too few samples | High | Phase staged rollout; only enable a learner when enough data exists for it |
| **Auto-revert flapping** — change, revert, change, revert | Medium | Lock parameter for 7 days after revert; require operator unlock |
| **Sample size manipulation** — learner picks favorable windows | Medium | All windows fixed (last 30 closed trades or last 30 days, whichever smaller) |
| **Catastrophic auto-pause** — bad-regime detector pauses during recoverable dip | Medium | Operator can immediately un-pause; auto-pause expires after 24h max |
| **Manual chain interference** — operator's manual trades leak into auto_ai stats | Low | Explicit `chain='auto_ai'` filter on every learner query |
| **DB write contention** — learner runs during a high-frequency scan | Low | Learner uses brief transactions, runs at scheduled off-peak times (UTC 03/09/15/21) |

---

## 10. Modules / plugins / external apps to consider

- **scipy + sklearn + statsmodels** — required (above)
- **DSPy (already on Hermes)** — for self-tuning the rule-generation
  prompt. Phase 4+ candidate.
- **MLflow (optional)** — to track learner experiments / model versions.
  Defer until we have ≥5 learners running.
- **Optuna (optional)** — Bayesian hyperparameter search if we want to
  jointly optimize many parameters at once (Phase 6+).
- **Telegram alerts (already wired)** — extend to learner notifications.
- **Prometheus + Grafana (optional)** — time-series persistence of
  rolling Sharpe / WR / risk metrics. Better than re-computing on every
  page load. Adds ops complexity; defer until basic loop works.

---

## 11. Open questions for the operator

1. **Auto vs auto-suggest?** Default is full-auto with safety guards. Or
   should every change require operator confirmation in a "pending
   adjustments" queue? (Full-auto is the design above; suggest-mode adds
   ~2 days work.)
2. **Risk-of-ruin tolerance:** what's the max % of starting equity you'd
   accept losing while the learner is calibrating? Currently the hard
   breaker is -15% — should the learner have a tighter "stop learning,
   pause" trigger (e.g., -8%)?
3. **Phase scope:** all 7 phases or stop after Phase 3 (where threshold
   learning is in place)? Each phase ships independently.
4. **Manual chain inclusion:** the manual rulebook already exists. Should
   the learner also tune manual-chain parameters? (Currently scoped
   auto_ai only.)
5. **Backtest validation requirement:** before a learner change goes live
   on real money, should it pass a backtest on the last 30d of held-out
   data? Adds ~5 days work but huge safety boost.

---

## 12. What to ship FIRST if you want a quick proof

If 25 days is too much commitment, the **smallest demonstrable closed
loop** is Phase 0 + a single Phase 2 learner (per-symbol modifier):

- ~3 days work
- Picks one symbol the system trades poorly, auto-applies a -0.3 score
  penalty
- Surfaces "applied 2026-06-03: PYTH symbol penalized -0.3 (n=14, WR=21%,
  p<0.05)" on the stats page + Telegram alert
- After 30 more closes, learner re-evaluates and either keeps, deepens,
  or reverts the penalty
- Demonstrates the full loop end-to-end with minimal blast radius

If that proves out, expand to other learners on the same scaffolding.
