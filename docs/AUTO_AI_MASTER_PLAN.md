# Auto-AI Master Implementation Plan

**Status:** active · 2026-05-31
**Source decisions:** `AUTO_AI_OPEN_QUESTIONS.md` answered 2026-05-31
**Supersedes phased-rollout sections of:** `AUTO_AI_RESEARCH_FINDINGS.md`, `AUTO_AI_LEARNING_ARCHITECTURE.md`, `AUTO_AI_SPECIALIZED_AGENTS.md`, `AUTO_AI_NOISE_DETECTION.md`

The individual concept docs remain as **reference material** (architecture, rationale, scientific methods). This doc is the single source of truth for **what gets built, in what order, with what dependencies**.

---

## 1. Scope confirmed

- **Chain:** `auto_ai` only. Manual chain untouched.
- **Mode:** Full auto with safety guards (no per-change operator approval queue).
- **Phase scope:** `R-1..R-10` + `L-0..L-5` + `A-A..A-E` + `N-1..N-4`. `L-6` (Pattern learners — k-means, decision tree) deferred.
- **Framework:** Raw Anthropic SDK + plain Python orchestration. No LangGraph/CrewAI/AutoGen.
- **DSPy:** Installed and ready; tuning starts with the setup classifier prompt after A-A and a couple of learners are running.
- **Daily ops:** A daily status Telegram report runs at 09:00 UTC with progress + active reminders.

## 2. Locked-in defaults

| Parameter | Default value | When learned values take over |
|---|---|---|
| Persistence bars | 2 | `L-2` per-signal learner (later) |
| Consensus variance threshold | stdev > 1.5 | `L-2` (after 30+ rejections logged) |
| VPIN threshold | 0.7 | `L-2` (after 14d of monitoring) |
| ADX threshold (trend setups) | 20 | `L-2` |
| Tukey IQR multiplier | 5× | static (no learning planned) |
| Outlier candle handling | Quarantine (not reject) | static |
| Red-Team veto mode | Soft (score penalty) for first 14 days | Operator review at +14d → maybe Hard |
| Learner-pause DD trigger | -8% pauses learner only (trading continues) | static |
| Agents combined cost ceiling | $5/day | static |

## 3. Reminders & countdowns (powered by daily report)

These appear at the top of the daily Telegram report until resolved.

> **Update 2026-05-31:** Compressed timeline — option (a) shipped Weeks 3–11 in one day.
> Several reminders now start counting from today.

| Reminder | Fires when | Action expected | Status |
|---|---|---|---|
| ~~Smallest-proof status~~ | — | — | ✅ RESOLVED 2026-05-31 morning (L-0 shipped) |
| Red-Team soft→hard review | **+14d from 2026-05-31** | Compare WR of soft-mode-vetoed setups vs accepted; decide hard mode | ⏰ countdown active |
| Strategy Selector revisit | **+30d from 2026-05-31** | Reassess whether per-archetype playbooks have emerged | ⏰ countdown active |
| VPIN rejection-rate monitor | Daily from 2026-05-31 | Track rejection % trend; alert if rate jumps >2σ from rolling avg | ⏰ active today |
| DSPy active prompts | Daily after first DSPy tuning ships | Show prompt being optimized, current accuracy vs baseline, next candidate prompt |
| **R-3-B Cross-margin risk metric** | At `L-5` (Week 9) | Replace skipped `liq_distance_atr` (only valid in isolated mode) with a meaningful cross-mode equivalent — "max single-position adverse move before basket liquidation given current open positions." Currently auto-trader runs cross margin; `FUTURES_AI_MARGIN_MODE=cross` env gates the metric off. |

## 4. Phased timeline (12 weeks)

Each week is ~5 dev days. The phases respect cross-doc hard dependencies (see §6 graph).

### Week 1 — Independent quant wins (R-1 to R-5)
Foundational metrics and detectors that benefit everything downstream. All can ship in parallel.

| Item | Days | What | Deliverable |
|---|---|---|---|
| R-1 | 1 | Install quantstats, surface DSR / K-ratio / Ulcer / Omega / Tail / GPR / Information Ratio on Stats page | New "Advanced KPIs" panel below current tiles |
| R-2 | 1 | Volatility-targeting position sizer (size = target_vol / position_ATR_pct) | New `risk_budget._size_by_vol_target()` |
| R-3 | 1 | Funding-rate-adjusted P&L column + liquidation-distance score at open | New `positions.funding_paid_usd` + `positions.liq_distance_atr` |
| R-4 | 1 | CUSUM + Page-Hinkley monitors on per-setup expectancy | New `trading/edge_decay.py` |
| R-5 | 1 | Bayesian credible interval helper (Beta-Binomial posterior) | New `trading/bayes.py` |

**Exit criterion:** all five visible on the Stats page or in the audit log. No behavior change to the auto-trader yet — these are inputs for later phases.

---

### Week 2 — Foundation + smallest proof (L-0)
Stand up the parameter store and accessor layer, then build ONE learner end-to-end to validate the loop.

| Item | Days | What | Deliverable |
|---|---|---|---|
| L-0 schema | 1 | Create `learned_params`, `learner_log` tables | Migrations 68, 69 |
| L-0 accessors | 1 | `trading/learned.py` with `get(key, default)` and `get_or(archetype=...)` | Module + tests |
| Per-symbol learner | 3 | Smallest-proof learner: scan closed trades, penalize symbols with ≥10 trades + WR<30% + total_pnl<-3% equity. Writes `symbol_modifier.{symbol}` to `learned_params`. Cron every 6h. | `trading/learner_symbol.py` + cron entry |

**Exit criterion:** at least 1 symbol gets a learned modifier within the first run. The modifier is read by the scanner Stage 3 score calc. Audit-log entry visible on the Stats page.

---

### Week 3 — Cheap noise gates + Red-Team agent + Daily Report
First defenses online + the agent with the highest asymmetric payoff + the ops loop you asked for.

| Item | Days | What | Deliverable |
|---|---|---|---|
| N-1 | 1 | Signal persistence gate (default 2 bars); consensus variance gate (default stdev>1.5) | Modify `chart_confluence.py` + `signal_consensus.py` |
| N-2 | 1 | FDR (Benjamini-Hochberg) correction in `ai_rulebook.py` candidate-rule filter | Module update + test |
| A-A Red-Team | 2 | New `trading/red_team_agent.py`. Soft-mode score penalty, Haiku-backed. Wired after consensus, before order placement. | Module + integration + 14-day soft-mode timer |
| Daily report | 1 | New cron at 09:00 UTC sends Telegram message with: 24h/7d/30d P&L (existing); reminders table; recent learner_log entries; phase progress | `monitor_scheduler.py` extension |

**Exit criterion:** daily report fires once successfully. Red-Team has logged at least one veto reason (even if soft-mode allows the trade). FDR correction reduces rulebook candidate count visibly.

---

### Week 4 — Read-path refactor + cheap learners (L-1, L-2)
Convert remaining constants to learned_params reads; spin up the session/dow/hour learners.

| Item | Days | What | Deliverable |
|---|---|---|---|
| L-1 read path | 2 | Replace `CONSENSUS_MIN_SCORE`, `MAX_NOTIONAL_USDT`, kill-zone modifiers etc. with `learned.get_or(...)` calls. Defaults match current constants → no behavior change. | Multi-file refactor + tests |
| L-2 cheap learners | 3 | Per-session, per-DoW, per-hour, per-(session×DoW) modifiers. Each with Wilson CI + minimum sample size + max-Δ-per-cycle bound. | `trading/learner_time.py` |

**Exit criterion:** Pre-refactor and post-refactor scanner output identical on a 7-day replay. Each L-2 learner has computed at least one bucket with sufficient sample to apply a modifier.

---

### Week 5 — Backtest Validator (A-B)
Pre-flight check for every learner change. Blocks L-3 from going live without validation.

| Item | Days | What | Deliverable |
|---|---|---|---|
| A-B candle replay | 3 | Build replay infrastructure: for a given param change, walk last 30 days of candles through the scanner with old vs new value, log every diff | `trading/backtest_validator.py` (no LLM yet) |
| A-B LLM gate | 2 | Sonnet-backed gate reads the replay diff, returns approve/reject/escalate. Result written to `learner_log` and required before any L-3+ writes hit `learned_params`. | Integration into learner pipeline |

**Exit criterion:** an L-2 mod proposal runs through A-B, gets approved or rejected, audit-logged. Replay finishes in under 60s for a single param change.

---

### Week 6 — Score & threshold learners (L-3) + Post-Mortem (A-C)
The high-impact learner. Now backstopped by A-B.

| Item | Days | What | Deliverable |
|---|---|---|---|
| L-3 score/threshold | 4 | Per-archetype `consensus_min_score` learner. Calibration-driven: lower threshold when calibration shows lower-score WR sufficient. Hold-out 80/20 validation + A-B gate. | `trading/learner_threshold.py` |
| A-C post-mortem | 1 (start) | Replace `agent_trade_monitor.py` close hook with a Sonnet-backed narrative generator. Writes `trade_hindsight.narrative`, `lesson_category`, `would_skip_setup`, `would_reduce_size`. | Module + schema migration (continued next week) |

**Exit criterion:** at least one consensus_min_score modifier learned for one archetype (likely breakout — high WR). Operator reviewable in the daily report.

---

### Week 7 — Post-Mortem completion + N-3 wick/ADX + N-4 VPIN start
Cleanup A-C, ship the structural noise filters, begin the VPIN pipeline (shared build).

| Item | Days | What | Deliverable |
|---|---|---|---|
| A-C complete | 1 | Backfill `trade_hindsight.lesson_category` for last 60 days. Wire into rulebook regen. | Backfill script + rulebook integration |
| N-3 wick filter | 0.5 | Close-confirmation requirement on level-break signals | Modify `scanner_stages.py` |
| N-3 ADX filter | 0.5 | Hard ADX<20 filter for trend-following archetypes; reject `rejected_adx_too_low` | Modify `scanner_stages.py` |
| N-3 BB squeeze | 1 | Bollinger-band squeeze detection: 90-day low width = "compression" tag | New `chart_bb.py` |
| N-4 VPIN ingest | 2 | Binance aggTrades WebSocket subscriber for top 20 symbols. New `vpin_snapshot` table. | `data_ingest/binance_aggtrades.py` |

**Exit criterion:** wick filter actively rejecting at least one previously-counted signal in dry-run logs. VPIN snapshots populating for top 20 symbols every minute.

---

### Week 8 — VPIN integration + TP/SL learners (L-4)
Connect VPIN to the gate, ship the placement learners.

| Item | Days | What | Deliverable |
|---|---|---|---|
| N-4 VPIN gate | 1 | Scanner Stage 2: `reject_setup if vpin > 0.7` with reason `rejected_high_vpin`. Daily report tracks rejection rate. | Modify `scanner_stages.py` |
| L-4 TP/SL learners | 4 | TP1/TP2/TP3 ATR multiplier per archetype (driven by MFE/MAE history per R-6 GARCH-informed bounds). SL ATR multiplier per archetype. TP ladder percentages per archetype. | `trading/learner_placement.py` |

**Exit criterion:** L-4 produces a learned modifier for at least one archetype's TP1 multiplier. VPIN rejection-rate visible in daily report.

---

### Week 9 — Risk learners (L-5) + Execution Monitor start (A-D)
Risk learners use R-2 (vol targeting), R-9 (Kelly), R-1 (DSR).

| Item | Days | What | Deliverable |
|---|---|---|---|
| L-5 risk learners | 3 | Dynamic `risk_per_trade_pct` (scales down on rolling Sharpe degradation). Dynamic `max_notional_usdt` (scales down on VaR breach). Time-stop per archetype (auto-close at N hours when time decay detected). Post-loss cooldown if hot-hand check fails. Fractional Kelly per archetype. | `trading/learner_risk.py` |
| L-5 vol targeting | 1 | Wire R-2 (already shipped) as a sizing modifier under learner control | Integration |
| A-D start | 1 | Slippage tracker + anomaly detector. Background job; LLM only fires on anomaly. | `agents/execution_monitor.py` (continued) |

**Exit criterion:** at least one risk parameter has moved from its default. L-5 daily-report panel shows current values vs defaults.

---

### Week 10 — A-D complete + Cascade Predictor (A-E)
A-E uses VPIN built in N-4 + the funding/OI/depth signals already pulled.

| Item | Days | What | Deliverable |
|---|---|---|---|
| A-D complete | 2 | Anomaly thresholds calibrated from week-9 data; Telegram alert format finalized; integration into daily report's "alerts" section | Module finalized |
| A-E cascade predictor | 3 | New `agents/cascade_predictor.py`. Reads VPIN + funding spreads + OI delta + top-of-book depth. Haiku-backed synthesis only when ≥2 of 3 conditions cross thresholds. High-risk band → orchestrator scales position sizes down 50% for next 60 min. | Module + orchestrator integration |

**Exit criterion:** A-E silently observes for 7 days; at least one synthesis call has fired (even if false alarm). Operator reviews the synthesized warning text format.

---

### Week 11 — Stats UI integration (L-7) + DSPy start
Surface everything visually + start prompt optimization.

| Item | Days | What | Deliverable |
|---|---|---|---|
| L-7 stats panels | 2 | New panels on Futures-AI Stats: "Recent auto-adjustments" (last 20 learner_log entries), "Pinned parameters" (operator overrides), "Noise gates" (rejection counts per category), "Reminders" (active countdowns) | Modify `static/js/19-futures-ai-stats.js` + endpoint |
| DSPy classifier tune | 3 | Wire DSPy `BootstrapFewShot` to optimize the setup classifier prompt. Training data: 138 closed positions with current AI labels. Metric: classifier-consistency on a held-out 20% set. Output: new prompt deployed if accuracy > baseline. | `setup_classifier.py` extension |

**Exit criterion:** Stats page shows all four new panels. DSPy run completes; new prompt either deployed or rejected with audit log.

---

### Week 12 — DSPy continuation + retrospective
Extend DSPy to next prompt; assess what shipped; gather operator feedback.

| Item | Days | What | Deliverable |
|---|---|---|---|
| DSPy rulebook tune | 3 | Apply DSPy to rulebook-generator prompt with metric "rule predicts outcome on held-out trades" | Updated `ai_rulebook.py` |
| Retrospective | 2 | Operator review meeting (async via this chat). Adjust master plan for next quarter based on what worked / didn't. | This doc updated; next-quarter roadmap appended |

**Exit criterion:** rulebook rules generated under tuned prompt score higher in operator review than baseline. Retrospective complete with next-quarter direction set.

---

## 5. Daily Status Report — spec

Cron at 09:00 UTC. Telegram message format:

```
🤖 Auto-AI Daily Status — YYYY-MM-DD

📊 Performance:
  24h: +X.XX% ($XX.XX)
  7d:  +X.XX% ($XX.XX)  WR XX% (NN/MM)
  30d: +X.XX% ($XX.XX)  WR XX% (NN/MM)

📈 Phase progress:
  Current week: N of 12 (Phase X-Y in flight)
  Last completion: Phase X-Y on YYYY-MM-DD
  Next milestone: Phase X-Y on YYYY-MM-DD

⏰ Reminders:
  [ ] Red-Team soft→hard review in N days
  [ ] Strategy Selector revisit in N days (after L-3)
  [ ] VPIN rejection rate: X.X% (trending: ↑/↓/=)
  [ ] DSPy: optimizing <prompt_name>, baseline XX%, current XX%

🔄 Recent learner activity (last 24h):
  • <param_name>: <old> → <new> (n=NN, p=<value>) ✓ applied
  • <param_name>: proposed change rejected (reason)

🚫 Noise gates (24h rejections):
  persistence_gate: NN  consensus_variance: NN  wick_filter: NN
  vpin_high: NN  adx_too_low: NN  tukey_outlier: NN

⚠️ Alerts:
  <if any A-D execution-quality anomalies or A-E cascade warnings>

📝 Action needed:
  <if any operator decision required, listed here>
```

Implementation: extends existing `monitor_scheduler.py` cron infrastructure. Telegram already wired.

---

## 6. Dependency graph (visual)

```
                Week 1            Week 2           Week 3            Week 4
              ┌────────┐       ┌────────┐       ┌────────┐        ┌────────┐
              │  R-1..R-5 │ ───▶│  L-0  │ ────▶│N-1,N-2│ ──────▶│L-1,L-2│
              │ (5 indep) │      │ +1 lrn │     │ A-A    │        │       │
              └────────┘       └────────┘       │+Daily  │        └───┬────┘
                                                 └────────┘            │
                                                                       │
                Week 5           Week 6           Week 7           Week 8
              ┌────────┐       ┌────────┐       ┌────────┐        ┌────────┐
              │  A-B   │ ────▶ │L-3+A-C│ ────▶│A-C done│ ─────▶│N-4 VPIN│
              │backtest│       │       │       │ +N-3  │        │+ L-4   │
              │validatr│       │       │       │+N-4 sta│       │        │
              └────────┘       └────────┘       └────────┘        └───┬────┘
                                                                       │
                Week 9           Week 10          Week 11          Week 12
              ┌────────┐       ┌────────┐       ┌────────┐        ┌────────┐
              │L-5 risk│ ────▶ │A-D done│ ────▶│L-7 UI  │ ─────▶│DSPy   │
              │+ A-D st│       │+ A-E   │       │+ DSPy  │        │rulebk │
              │        │       │cascade │       │class.  │        │+retro │
              └────────┘       └────────┘       └────────┘        └────────┘

Hard dependencies (cross-week):
  L-3 ──BLOCKED-BY──> A-B (Backtest Validator)
  A-A ──CONSUMES──> N-1 (consensus variance signal)
  A-E ──CONSUMES──> N-4 (VPIN pipeline)
  L-6 (deferred)
  L-2 ──BENEFITS-FROM──> N-2 (FDR correction)
  L-4 ──USES──> R-6 (GARCH for vol forecast)
  L-5 ──USES──> R-2 (vol targeting), R-9 (Kelly)
  Daily report ──READS──> learner_log, futures_ai_log, learned_params
```

## 7. What ships when — milestone view

| Milestone | Week | Operator-visible change |
|---|---|---|
| Advanced KPIs visible (DSR, Ulcer, Omega…) | 1 | New panel on Stats page |
| Daily Telegram report fires for first time | 3 | 09:00 UTC Telegram message |
| Red-Team agent vetoes first trade | 3 | New row in `futures_ai_log` |
| First learner-driven param change | 4 | New `learner_log` entry visible in daily report |
| Backtest Validator gates its first L-3 proposal | 5 | Audit log shows replay diff |
| First learned consensus_min_score modifier | 6 | Daily report shows non-default per-archetype threshold |
| Wick filter actively rejecting setups | 7 | Daily report shows `wick_filter` rejection count > 0 |
| VPIN pipeline live, rejection rate trending | 8 | Daily report panel populated |
| Risk learner actively scaling notional | 9 | Daily report shows `max_notional_usdt` ≠ default |
| Cascade Predictor fires first warning | 10 | Telegram alert outside daily-report window |
| All Stats UI panels populated | 11 | Stats page complete |
| DSPy-tuned rulebook prompt deployed | 12 | Rulebook quality improvement measurable |

## 8. Risks & contingencies

| Risk | Likelihood | Mitigation |
|---|---|---|
| A-B Backtest Validator candle-replay slower than expected → blocks L-3 | Medium | Pre-cache candles in week 4; profile early |
| VPIN data feed (Binance WebSocket) drops frequently | Medium | Reconnection logic + fallback to 1m bars + alert if down > 5 min |
| DSPy tuning produces worse prompt | Medium | Auto-revert via held-out test; baseline always preserved |
| Operator capacity drops below 5d/wk on dev | High | Each week independent → can pause between weeks without breaking state |
| Auto-trader real-money behavior degrades during a phase | High | Learner-pause at -8% DD; safety circuit auto-reverts last change |
| Phase X-Y discovers it depends on something not in scope | Medium | Each week explicit dependencies declared (§6); flagged in daily report |

## 9. Operator commitments

- **Weekly review** (one short Telegram/chat exchange per Friday): which phase shipped, any blockers, decisions for next week.
- **Daily report acknowledgement** (just read it): catches drift early.
- **Red-Team soft→hard decision at week 5–6** (after 14d soft-mode data).
- **Strategy Selector revisit decision at week 12 or later** (30d after L-3 ships).
- **DSPy extension decisions** (weekly after week 11): which prompt next.

## 10. Non-goals & deferred

- LLM fine-tuning (not in scope; we're API-only)
- Reinforcement learning (`L-6` deferred per Q3)
- Manual chain inclusion (per Q19)
- Auto-changes that bypass kill switch / DD breaker (per Q2 hard guard)
- NautilusTrader full port (deferred per Research §lower-priority)
- Paid data sources (Glassnode/CryptoQuant/Hyblock) until free-tier exhausted (per Research)

## 11. How to use this doc

- Read once for shape.
- Daily: glance at "Reminders" + "Phase progress" in the auto-generated Telegram report.
- Weekly: come back to §4 (timeline) to confirm the current week's deliverable.
- At end of week 12: write the next-quarter plan (whatever ships from L-6 if revived, plus operator-feedback adjustments).

## 12. Companion documents (reference only)

These remain as architecture / rationale / scientific references. They are NOT updated as the master plan evolves — `AUTO_AI_MASTER_PLAN.md` (this doc) is the single source of truth for sequence and acceptance criteria.

- `AUTO_AI_RESEARCH_FINDINGS.md` — quant research; cited as `R-N`
- `AUTO_AI_LEARNING_ARCHITECTURE.md` — learner design; cited as `L-N`
- `AUTO_AI_SPECIALIZED_AGENTS.md` — agent design; cited as `A-X`
- `AUTO_AI_NOISE_DETECTION.md` — noise gate design; cited as `N-N`
- `AUTO_AI_OPEN_QUESTIONS.md` — decisions log; archived once master plan signed off
