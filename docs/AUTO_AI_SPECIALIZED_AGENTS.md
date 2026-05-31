# Auto-AI Specialized Agents — Concept

**Status:** design proposal · 2026-05-31
**Scope:** the `auto_ai` chain. Companion document to
`AUTO_AI_LEARNING_ARCHITECTURE.md`.

> **Phase code in this doc:** `A-A` through `A-E` (Agents).
> Cross-doc references use the prefix codes: `R-N` (Research), `L-N` (Learning), `N-N` (Noise).

## 0. Cross-doc dependencies

This document is one of four in the auto_ai concept set:
- `AUTO_AI_RESEARCH_FINDINGS.md` — raw signals & KPIs used by the agents
- `AUTO_AI_LEARNING_ARCHITECTURE.md` — self-tuning loop the agents gate
- `AUTO_AI_NOISE_DETECTION.md` — filter gates that pre-process before agents see anything

**Hard dependencies on other docs:**
- `A-B` (Backtest Validator) **must ship before** `L-3` (Score & threshold learners) — every learner change must be replayed first
- `A-A` (Red-Team) **uses** `N-6` (consensus variance) as one of its veto reasons — variance signal from noise gates is a veto input
- `A-E` (Cascade Predictor) **shares the VPIN pipeline** with `N-3` (VPIN gate) and `L-6` (Pattern learners) — built once in N-3, consumed in A-E and L-6
- `A-C` (Post-Mortem) **enriches** the rulebook input that `L-2` and `L-3` learners read

**Shared infrastructure:**
- `futures_ai_log` — written by all agents (vetoes, alerts, validations) and read by Stats UI
- `trade_hindsight` table — written by `A-C`, read by rulebook generator

---

## 1. Premise

Today's pipeline already uses LLM specialization **by cost tier**:

| Tier | Model | Roles |
|---|---|---|
| Expensive / rare | Opus | Final consensus voting on setups that pass the Sonnet gate |
| Workhorse | Sonnet | Call analyzer, advisor, scanner, rulebook generator, pattern detector, grader |
| Cheap / frequent | Haiku | Scanner quick-score, hindsight, live trade check, limit analysis |

This is vertical specialization (cost-by-task-importance). What's missing
is **horizontal specialization** — agents with narrow, well-defined roles
that fill specific decision-pipeline gaps.

The principle that guides which agents to add:
> **Add an agent only if there's a current gap no existing agent
> covers — and the agent's "veto" or "alert" prevents a class of error
> the operator currently catches manually (or doesn't catch at all).**

---

## 2. High-ROI agents to add

### 2.1 🔴 Red-Team / Devil's Advocate (HIGHEST PRIORITY)

**Role:** Given a setup that passed all existing gates (kill switch,
score threshold, consensus vote, sizing), this agent argues ONLY against
taking it. Runs as the final pre-execution check.

**Why this fills a gap:** Today, all consensus voters (Sonnet, Opus)
score setups on absolute merit. None explicitly red-teams. When two
voters both score 8/10, there's no adversarial check — the trade goes
through. A dedicated red-team prompt asks "what would have to be true
for this to lose?" and lists those scenarios with probabilities.

**Implementation:**
- New module: `trading/red_team_agent.py`
- Called from `orchestrator._evaluate_setup` AFTER consensus, BEFORE
  `bitget_trader.place_market_order`
- Prompt receives full setup context + consensus output + recent rulebook
- Returns: `{veto: bool, confidence: 0-1, reasons: [str]}` plus a
  per-reason severity score
- Two configurable modes:
  - **Soft mode** (default for first 2 weeks): adds a score penalty
    (e.g., -0.5 per high-severity veto reason); setup may still execute
    if remaining score clears threshold
  - **Hard mode** (after operator trust): a `veto=true` blocks the
    trade outright, logged to `futures_ai_log` as `red_team_veto`

**Model:** Haiku (cheapest, fast).

**Cost:** ~5 calls/day × ~3k tokens × Haiku rates ≈ $0.20/day.

**Build effort:** 2 dev days.

**Success metric:** after 30 days, compare "what would have happened if
we'd accepted vetoed trades" — if vetoed setups had below-avg WR, the
agent is earning its keep.

---

### 2.2 📊 Backtest Validator

**Role:** Before any learner change (per the AUTO_AI_LEARNING_ARCHITECTURE
plan) goes live, this agent replays the proposed parameter change on the
last 30 days of historical candles and reports whether the change would
have helped, hurt, or been neutral.

**Why this fills a gap:** The learner has hold-out validation as a guard,
but hold-out is on simple metrics (mean P&L over 20%). A backtest replay
shows trade-by-trade what would have changed: which trades would have
been taken differently, what new trades would have been entered, what
SLs would have moved.

**Implementation:**
- New module: `trading/backtest_validator_agent.py`
- Triggered by the learner before writing to `learned_params`
- Replays scanner decisions over last 30d (candles already cached) with
  both old and new parameter values
- LLM reads the diff (e.g., "3 trades would have been skipped with new
  threshold; of those, 1 won +$X and 2 lost -$Y") and recommends
  approve / reject / send-to-operator-review
- Result logged to `learner_log` with full reasoning

**Model:** Sonnet (need reasoning on tabular comparison).

**Cost:** ~1 call per learner change × ~1 change/week × ~5k tokens
≈ $0.05/day.

**Build effort:** 5 dev days (mostly the candle-replay infrastructure;
LLM call is the easy part).

**Success metric:** zero learner changes that the operator manually
reverts within 7 days.

---

### 2.3 🔍 Post-Mortem Investigator

**Role:** After every closed trade, deep-analyzes what went right or
wrong with full context (regime, news, structure, related trades).
Produces a 2-3 paragraph narrative that feeds back into the rulebook
generator.

**Why this fills a gap:** Today's `trade_hindsight` is short and
surface-level (single field, single LLM call with limited context).
Rulebook regeneration consumes raw stats but doesn't have rich
per-trade narratives.

**Implementation:**
- Replace / extend `agent_trade_monitor.py` post-close hook
- Prompt receives: closed trade row + entry-time market snapshot +
  related concurrent trades + recent rulebook
- Writes structured fields to `trade_hindsight`:
  - `narrative` (TEXT, 200-400 words)
  - `lesson_category` (e.g., "tp_too_tight", "regime_mismatch",
    "good_pattern_bad_execution")
  - `would_reduce_size` (boolean)
  - `would_skip_setup` (boolean)
- Categorized lessons aggregate into rulebook regeneration prompts

**Model:** Sonnet (narrative quality matters).

**Cost:** ~1 call per close × ~8 closes/day × ~4k tokens ≈ $0.30/day.

**Build effort:** 2 dev days.

**Success metric:** rulebook rules generated from hindsight categories
score higher in operator manual review than rules generated from raw
stats.

---

### 2.4 ⚡ Execution Quality Monitor

**Role:** Watches slippage, fill-vs-signal gaps, time-to-fill, partial
fill rates across the auto-trader. Alerts on degradation.

**Why this fills a gap:** Recurring pattern in the journal's history is
~10% scan→execute drift (positions opened at meaningfully different
prices than the signal). Currently no agent specifically watches this;
operator notices after the fact.

**Implementation:**
- New background job (5-min interval)
- Compares last N orders' `signal_price` vs `entry_price` (already in
  the schema)
- Computes rolling slippage distribution
- LLM call only fires when an anomaly is detected (e.g., 3 trades in a
  row with slip > 2σ of historical average)
- Output: Telegram alert + `futures_ai_log` entry

**Model:** Haiku (anomaly classification, not deep reasoning).

**Cost:** ~1-2 calls/day on average ≈ $0.10/day.

**Build effort:** 3 dev days.

**Success metric:** post-deploy, slippage incidents flagged before the
operator independently notices them. Tracked as "alert lead time" vs
operator-flag time.

---

### 2.5 🌊 Liquidation Cascade Predictor

**Role:** Specialized agent that monitors funding spreads, OI changes,
top-of-book depth shifts, and warns of cascade conditions BEFORE they
trigger.

**Why this fills a gap:** Complements the planned VPIN signal (research
item 2.1 in AUTO_AI_RESEARCH_FINDINGS.md). Pattern-recognition role
that current modules cover only partially (Coinalyze gives data but no
synthesis).

**Implementation:**
- New module: `agents/cascade_predictor.py`
- Runs on every 5m candle close
- Pulls: per-venue funding spread, OI delta 1h, top-of-book depth z-score
- Sleeps silently 99% of the time
- When ≥2 of 3 conditions cross thresholds, fires LLM call that
  synthesizes the picture and outputs a risk-band
- High risk band → orchestrator scales position sizes down 50% for the
  next 60 min

**Model:** Haiku.

**Cost:** ~3 calls/day (only when conditions met) × ~3k tokens ≈ $0.20/day.

**Build effort:** 1 week (most of the work is the data pipeline; LLM is
the synthesis layer).

**Success metric:** when cascade events do happen, the predictor fired
≥10 minutes before the cascade.

---

## 3. Agents to SKIP

| Agent | Why skip |
|---|---|
| **Regime Classifier (LLM)** | Already covered mechanically by HMM + bear_phase classifier. LLM version adds cost without measurable accuracy gain. |
| **News / Sentiment Specialist (LLM)** | Already done by Grok integration. Duplicating wastes money. |
| **Pattern Recognition (TA patterns)** | Sonnet already does this inside the scanner pipeline (via VuManChu + scanner_stages). Splitting adds latency, marginal gain. Skip unless we find specific failure cases. |
| **Strategy Selector ("apply breakout playbook")** | Has theoretical merit but requires committing to discrete playbooks first. Defer until learner Phase 3 (per-archetype thresholds) ships — then revisit. |

---

## 4. Pipeline integration diagram

```
SCAN ─→ Sonnet quick-score
         │
         ▼
       Sonnet/Opus consensus
         │
         ▼
       🔴 Red-team agent ◀───────────── NEW gate
         │
         ▼  (if not vetoed)
       Sizing (Kelly fraction etc.)
         │
         ▼
       bitget_trader.place_market_order
         │
         ▼
       Position open
         │
         ▼  (monitored continuously by)
       ⚡ Execution monitor   ──→ Telegram alert on slippage drift
         │
         ▼  (when position closes)
       🔍 Post-mortem investigator
         │
         ▼
       trade_hindsight + lesson categories
         │
         ▼
       Rulebook regeneration (existing)
         │
         ▼
       Learner (per Architecture doc)
         │
         ▼
       📊 Backtest validator ◀───────── NEW gate before any param change
         │
         ▼  (if approved)
       learned_params
         │
         ▼  (back to next SCAN)

Background (5-min loop):
  🌊 Cascade predictor → orchestrator sizing modifier
```

Two new pre-execution gates (red-team, backtest validator). One enhanced
writer (post-mortem). Two background watchers (execution monitor,
cascade predictor).

---

## 5. Cost summary

| Agent | Calls/day | Model | $/day est. |
|---|---|---|---|
| Red-team | 5 | Haiku | $0.20 |
| Backtest validator | 0.1 | Sonnet | $0.05 |
| Post-mortem | 8 | Sonnet | $0.30 |
| Execution monitor | 2 | Haiku | $0.10 |
| Cascade predictor | 3 | Haiku | $0.20 |
| **Total extra** | **~18 calls/day** | | **~$0.85/day** |

Cushion estimate: ~$1.50–2.50/day in practice. Easily covered by one
prevented losing trade per month.

---

## 6. Framework choice

| Option | Verdict |
|---|---|
| **Raw Anthropic SDK + plain Python orchestration** | ✓ RECOMMENDED. No new dependencies. 5 agents in a known pipeline don't need framework overhead. |
| **DSPy** (already on Hermes) | Future Phase: programmatic prompt optimization. Could auto-tune the red-team prompt against "trades it correctly vetoed". 2-week investment after agents are stable. |
| **LangGraph** | Overkill for 5 agents. Adds dependency + lock-in. Defer until ≥10 agents or complex stateful flows. |
| **CrewAI** | Lower-code role-based; less control. Good for prototyping; not for production. |
| **AutoGen (Microsoft)** | Heavier than DSPy. Skip unless we move to conversation-based reasoning. |

Decision: **raw SDK**. Saves dependency surface, keeps the auto-trader
auditable in plain Python.

---

## 7. Phased rollout

### Phase A-A — Red-Team Pilot (2 days)
- Build red_team_agent.py, wire as soft-mode score modifier
- Run for 14 days; track vetoed setups
- Decision point: if veto-quality validated, switch to hard mode

### Phase A-B — Backtest Validator (5 days)
- Built alongside the learner (Phase 0-2 of architecture doc)
- Becomes mandatory pre-write gate for ANY learner change

### Phase A-C — Post-Mortem (2 days)
- Replaces shallow `agent_trade_monitor` post-close hook
- Backfills lesson_category for last 60 days of closes

### Phase A-D — Execution Monitor (3 days)
- Background job; Haiku-only
- Telegram alerts on slippage drift

### Phase A-E — Cascade Predictor (5 days)
- Data pipeline first, then LLM synthesis layer
- Hook into orchestrator sizing only after 14-day silent validation

**Total: ~17 dev days** spread over 4-6 weeks.

---

## 8. Open questions for the operator

1. **Red-Team first as 2-day pilot?** Or wait until architecture doc
   review is complete?
2. **Cost ceiling per day for new agents?** Suggested default: $5/day
   combined extra spend across all 5.
3. **Stay raw Anthropic SDK or adopt a framework?** Strong vote: raw SDK.
4. **Red-Team veto: hard (block) or soft (score penalty)?** Soft for
   first 2 weeks, then re-evaluate.
5. **Do we add Strategy Selector** if learner Phase 3 ships and we end
   up with discrete per-archetype playbooks? Currently "defer";
   confirm or upgrade to "queued".

---

## 9. Out-of-scope / non-goals

- LLM fine-tuning. Same reasoning as in AUTO_AI_LEARNING_ARCHITECTURE.md.
- Agent-to-agent debate / negotiation. Each agent runs once per trigger,
  produces a structured output. No multi-turn agent chat.
- Agent that adjusts other agents' prompts at runtime. (DSPy could later,
  but as an off-cycle optimization, not a runtime feature.)
- Replacing operator strategic judgement. Same constraint as architecture
  doc.

---

## 10. Companion documents

- `docs/AUTO_AI_LEARNING_ARCHITECTURE.md` — the self-learning loop design
- `docs/AUTO_AI_RESEARCH_FINDINGS.md` — top-10 quant-research adds (VPIN,
  GARCH, fractional Kelly, etc.) — many of these are SIGNALS the
  specialized agents could synthesize
