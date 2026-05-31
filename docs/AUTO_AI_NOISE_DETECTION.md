# Auto-AI Noise Detection — Concept

**Status:** design proposal · 2026-05-31
**Scope:** the `auto_ai` chain. Companion to
`AUTO_AI_LEARNING_ARCHITECTURE.md`, `AUTO_AI_SPECIALIZED_AGENTS.md`,
and `AUTO_AI_RESEARCH_FINDINGS.md`.

> **Phase code in this doc:** `N-1` through `N-4` (Noise).
> Cross-doc references use the prefix codes: `R-N` (Research), `L-N` (Learning), `A-X` (Agents).

## 0. Cross-doc dependencies

This document is one of four in the auto_ai concept set:
- `AUTO_AI_RESEARCH_FINDINGS.md` — VPIN (R-2.1) and outlier detection (R-1.7) referenced here
- `AUTO_AI_LEARNING_ARCHITECTURE.md` — the learner reads N-N rejection stats from `futures_ai_log`
- `AUTO_AI_SPECIALIZED_AGENTS.md` — the Red-Team agent (A-A) uses N-6 consensus variance as a veto reason

**Hard dependencies on other docs:**
- `N-2` (FDR correction) **should ship before** `L-2` (Cheap learners) — without FDR, the learner discovers false-positive patterns
- `N-4` (VPIN pipeline) **shared build with** `A-E` (Cascade Predictor) and `L-6` (Pattern learners) — single ingest pipeline, three consumers
- `N-6` (consensus variance gate) **feeds** `A-A` (Red-Team) as veto input

**Shared infrastructure:**
- `futures_ai_log` — written by all noise gates with rejection categories (e.g., `rejected_high_vpin`, `rejected_consensus_variance`)
- `learned_params` — noise gates read tunable thresholds from here (VPIN cutoff, ADX threshold, etc.) once `L-0` ships

---

## 1. Premise

"Noise" is not one thing. The word covers six structurally different
phenomena, each requiring its own detector. The auto-trader's robustness
is the sum of how well each is filtered before it influences a decision.

Today's defenses are partial — most types have *some* filter, none have
a *complete* one. This doc inventories the gaps and proposes targeted
additions that ship in a single ~1-week sprint.

---

## 2. The six kinds of noise

### 2.1 Signal noise (single-indicator false positives)

> RSI flips above 70 for one bar then reverts. CVD diverges for 3 bars
> then re-converges. Without a stability rule, every transient flicker
> becomes a "signal" that gets counted into confluence.

| Today | Gap |
|---|---|
| 15+ confluence signals must agree (Stage 3 scoring) | No **per-signal stability check** — a one-bar flicker counts the same as a multi-bar persistent signal |
| Score threshold ≥8 | Threshold operates at the aggregate level; doesn't catch one signal that's genuinely false-positive every time |

**Fix:** every signal gets a `persistence_bars` requirement — the
condition must hold for ≥N consecutive bars before it contributes its
weight to confluence. Default N=2 (one current + one prior). Trade off:
slightly later entries vs much fewer fakeouts.

### 2.2 Statistical noise (small-sample patterns)

> "Saturday is bad" generated from 18 trades. With 5% α and 50 buckets
> checked, expect 2-3 false positives. Some "rules" in the rulebook are
> almost certainly noise.

| Today | Gap |
|---|---|
| Operator manually reads rulebook and decides | No **multiple-testing correction** on the rule generator |
| Planned: Wilson CI per bucket (architecture doc) | Wilson per-bucket doesn't account for the dozens of buckets checked |

**Fix:** Benjamini-Hochberg FDR correction at the rule-generation step.
Lets through fewer "rules" but the survivors are real. Pair with a
minimum effect-size threshold (e.g., |Δ WR| ≥ 10pp) to avoid technically
significant but operationally meaningless findings.

### 2.3 Market-microstructure noise (wicks, fakeouts, stop hunts)

> BTCUSDT prints a $1000 wick to $77,500 then closes back at $78,200.
> An overzealous level-break detector counts the wick as a break.

| Today | Gap |
|---|---|
| Multi-timeframe confirmation (1D → 4H → 1H) | A 1H signal can fire on intra-bar movement that doesn't survive the close |
| SMT cross-exchange divergence | Catches single-venue manipulation, not whole-market wicks |
| Coinalyze multi-venue OI | Helps but doesn't filter the candle itself |

**Fix (two-part):**
- **Wick filter:** any signal that depends on price crossing a level
  must require the bar to *close* across, not just touch. ~20-line
  rewrite in scanner_stages.
- **VPIN gate:** when VPIN > 0.7 on the symbol (research findings 2.1),
  skip new entries entirely. Toxicity > opportunity in those windows.
  Requires the Binance aggTrades pipeline from research findings 2.1.

### 2.4 Data noise (exchange glitches, bad ticks)

> One venue prints a bogus $80,000 print for BTC for half a second.
> Coinalyze aggregates include it. An indicator briefly goes haywire.

| Today | Gap |
|---|---|
| SMT cross-exchange agreement check | Helps when only one venue is glitched |
| `_validate_candles` rejects obviously malformed rows | No **statistical outlier detection** at ingest |

**Fix:** Tukey-fence filter at ingest — flag any candle whose high or
low is >5 IQR from the rolling 24h median range. Flagged candles get a
quarantine flag (kept in DB but excluded from indicator computation for
1 hour). Cheap to implement, eliminates whole class of garbage-in
problems.

### 2.5 Regime noise (choppy markets producing false trend signals)

> ADX is 14 (no trend). Price oscillates in a 2% range. Every momentum
> signal fires repeatedly with opposite direction, generating churn.

| Today | Gap |
|---|---|
| HMM regime classifier | Detects state but doesn't gate trend-following setups specifically |
| Bear-phase classifier | Direction-aware but coarse |

**Fix:**
- **ADX <20 hard filter** for trend-following archetypes (breakout,
  continuation). Skip entries entirely below this threshold. Reversals
  and range-bound setups exempt.
- **Bollinger-band squeeze detection** — when BB width is at a 90-day
  low, mark regime as "compression". Setup expects breakout direction
  but the *timing* of the breakout is uncertain; require an extra
  confirmation candle. Adds Bollinger Squeeze indicator (10 lines via
  pandas-ta).

### 2.6 Behavioral / model noise (LLM voter disagreement)

> Sonnet scores the setup 9. Opus scores it 6. Mean is 7.5 — clears the
> threshold. But the underlying disagreement is a strong signal that
> the setup is ambiguous.

| Today | Gap |
|---|---|
| Mean score across voters must clear threshold | **Variance across voters is ignored** |
| Consensus uses both Sonnet and Opus | High-disagreement setups go through anyway |

**Fix:** consensus variance gate — if `stdev(scores)` across voters
exceeds 1.5 (out of 10), treat as low-confidence regardless of mean.
Reject. Logs as `rejected_consensus_variance` in futures_ai_log.

---

## 3. The four high-priority additions

Summarized for the sprint.

### A. Signal persistence gate (Type 1) — 1 dev day

**Module:** modify `chart_confluence.py`. Each signal-emitter gets an
optional `persistence_bars` kwarg (default 2). The emitter returns 0
weight if the underlying condition hasn't been true for that many
consecutive bars.

**Backfill:** existing signals get N=2 by default. Reversal signals
(failure swing, divergence) get N=1 since they're inherently momentary.

**Test:** replay last 60 days of scans with and without; expect 10-15%
fewer setups passing Stage 3 but higher per-setup WR.

### B. FDR correction at rulebook generation (Type 2) — 1 dev day

**Module:** modify `ai_rulebook.py` before passing buckets to the LLM.

```text
1. Compute Wilson CI for each bucket
2. List all buckets where lower_CI > 50% (or upper_CI < 50% for losing)
3. Apply Benjamini-Hochberg correction at α=0.05 to the resulting p-values
4. Only buckets surviving correction get sent to the LLM as "candidate rules"
```

**Effect:** rulebook has fewer rules but each is statistically
defensible. Operator gets quality over quantity.

### C. Wick filter + VPIN gate (Type 3) — 1 week (combined)

**Module 1 — wick filter:** rewrite level-break detection in
`scanner_stages.py` to use close prices, not bar high/low, for any
"price crossed X" signal.

**Module 2 — VPIN pipeline:**
- New service: `data_ingest/binance_aggtrades.py` — WebSocket subscriber
  to Binance Futures `aggTrade` stream for the top 20 symbols
- VPIN computation per symbol on rolling 50-bucket window
- Stored to a new `vpin_snapshot` table updated every 1m
- Scanner Stage 2 rejection: `if vpin > 0.7: reject_setup` with reason
  `rejected_high_vpin`

Most of the time investment is the WebSocket pipeline — VPIN computation
itself is ~50 lines.

### D. Consensus variance gate + Tukey outlier filter (Types 4, 6) — 1 day combined

**Variance gate:** modify `signal_consensus.py` — after collecting
scores from voters, compute stdev. Reject if > 1.5.

**Tukey outlier:** modify `chart_candles.py` `get_candles` — after
fetching, compute rolling 24h IQR on (high-low) range, flag candles
where high or low exceeds 5×IQR from median. Flagged candles get a
`quarantined=true` flag in memory only (not written to DB cache);
downstream indicators skip them.

---

## 4. Integration with the existing architecture

Each noise gate runs at a specific pipeline stage:

```
RAW CANDLES (ingested every 1m)
    │
    ▼  ── (2.4) Tukey outlier filter ── quarantines bad candles
    │
SIGNAL COMPUTATION (per indicator)
    │
    ▼  ── (2.1) Signal persistence gate ── requires N bars
    │
SETUP ASSEMBLY (scanner Stage 1-3)
    │
    ▼  ── (2.3) Wick filter ── require close confirmations
    │
    ▼  ── (2.5) ADX + BB-squeeze regime filters
    │
    ▼  ── (2.3) VPIN gate ── skip when toxicity high
    │
CONSENSUS VOTE
    │
    ▼  ── (2.6) Variance gate ── reject high-disagreement setups
    │
TRADE EXECUTION (existing)
    │
    ▼
RULEBOOK REGENERATION (post-close, weekly)
    │
    ▼  ── (2.2) FDR correction ── only statistically real rules survive
```

The gates compound. A setup must pass **all** layers — but each
individual layer's job is narrow, so the rejection logic is auditable.
Every rejection is logged to `futures_ai_log` with the rejection
category (e.g., `rejected_high_vpin`, `rejected_consensus_variance`).

---

## 5. Phased rollout

A single ~1-week sprint, ordered by independence (each phase ships
without blocking the others):

| Phase | Days | Items |
|---|---|---|
| N-1 | 1 | Persistence gate (item A) + consensus variance gate (part of D) |
| N-2 | 1 | FDR correction (item B) + Tukey outlier (part of D) |
| N-3 | 1 | Wick filter + ADX hard filter + BB squeeze detection (parts of item C + §2.5) |
| N-4 | 4 | VPIN pipeline + integration (rest of item C) — **shared build with L-6 and A-E** |

After Phase N-1 to N-3 ship, expect:
- Setup volume drops 10-20%
- Per-setup WR rises (proportional to the bad setups now filtered)
- Rulebook shrinks by ~30% (fewer "rules" that were just statistical
  artifacts)
- Telegram noise drops sharply (consensus-variance rejections never
  reach the alert layer)

After Phase N-4 (VPIN) ships, expect additional 5-10% improvement during
volatile/cascade periods.

---

## 6. Observability — how to know it's working

Every gate writes structured rejection events to `futures_ai_log`.
Aggregated counts surface on the Futures-AI Stats page as a new "Noise
gates" panel:

| Gate | Today | 7d ago | 30d ago | Trend |
|---|---|---|---|---|
| persistence_gate | rejected 4 | 7 | 5 | flat |
| consensus_variance | rejected 1 | 3 | 0 | up |
| wick_filter | rejected 0 | 2 | 1 | flat |
| vpin_high | rejected 2 | 5 | n/a | new |
| adx_too_low | rejected 6 | 8 | 4 | up |
| tukey_outlier | quarantined 3 candles | 0 | n/a | new |

Trend analysis tells the operator when a particular noise type is
flaring (e.g., increasing ADX rejections = market is choppier than
usual).

---

## 7. Where DSPy fits in (newly installed)

DSPy (just installed on Pi 21) doesn't replace the noise gates but
helps two adjacent prompt-engineering tasks downstream:

- **Rulebook generation prompt** — once FDR correction is in place, the
  rule generator's prompt can be DSPy-optimized against the metric
  *"the rule correctly predicts outcome on held-out trades"*. Training
  data: closed trades labeled with which rules fired. Metric: predictive
  accuracy.
- **Future red-team agent prompt** (per `AUTO_AI_SPECIALIZED_AGENTS.md`
  §2.1) — DSPy can tune the veto-quality prompt against the metric
  *"vetoed setups have below-average WR"*.

Both are ~2-3 day projects each after the noise gates ship. Not in this
sprint.

---

## 8. Open questions for the operator

1. **Persistence bars default:** I proposed N=2 (current + prior bar).
   Aggressive option = 1 (slightly more setups, slightly more noise).
   Conservative = 3. Confirm or adjust.
2. **Variance threshold (Type 6):** I proposed stdev > 1.5 on a 0-10
   score. Stricter (1.0) means more rejections; looser (2.0) means
   fewer.
3. **VPIN threshold:** literature uses 0.7 as "high toxicity". For your
   specific symbols, the right threshold may differ. Plan: ship at 0.7,
   monitor for 14 days, calibrate from rejection-vs-outcome data.
4. **ADX threshold:** 20 is standard. Some traders use 25 for tighter
   filtering. Default 20; reviewable.
5. **Tukey IQR multiplier:** 5×IQR is conservative (only catches very
   clear outliers). Standard Tukey fence is 1.5–3×. Default 5 to avoid
   false-flagging legitimate volatility spikes.
6. **Quarantine vs reject:** Tukey-flagged candles get *quarantined*
   (memory only, no propagation to indicators) rather than *rejected*
   (overwritten). Reversible. Acceptable trade-off?

---

## 9. Out-of-scope / non-goals

- **Replacing existing confluence scoring** — these are gates that run
  *before* and *after* the scoring, not a replacement.
- **News-event noise** — different problem (covered by Finnhub macro
  event filter elsewhere). Not addressed here.
- **Operator-behavior noise** — the personal-bad-hour cap was
  intentionally removed (per CLAUDE.md memory). Not re-introducing.
- **Manual chain** — these gates are auto_ai only. Manual trades aren't
  filtered through this pipeline.

---

## 10. Companion documents

- `docs/AUTO_AI_RESEARCH_FINDINGS.md` — VPIN and outlier detection both
  appear there with deeper references
- `docs/AUTO_AI_SPECIALIZED_AGENTS.md` — the Red-Team agent specifically
  should treat consensus variance as a veto reason (alignment with
  §2.6 here)
- `docs/AUTO_AI_LEARNING_ARCHITECTURE.md` — the learner reads
  rejection-rate statistics from `futures_ai_log` and can auto-tune the
  thresholds in this doc (Phase 2 of that plan)
