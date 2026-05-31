# AI Architecture

*How the trading journal uses language models — the 7-agent pipeline,
the 5-provider cascade, model routing, prompt caching, and where each
AI feature plugs in. Updated 2026-05-23.*

---

## TL;DR

- **Primary model**: Claude Sonnet 4.6 for deep analysis, Haiku 4.5 for fast scoring
- **Fallback**: 5-provider cascade (Groq → Cerebras×2 → OpenRouter → Gemini) on Anthropic outage
- **Compute pattern**: 7 specialized agents with typed contracts, parallel data fan-out, single Sonnet call at the end
- **Cost control**: ephemeral prompt caching (~74% hit rate on stable-prefix calls)
- **Cascade in production**: ~33% of scanner-quick calls served by free providers when Anthropic rate-limits

---

## The 7-Agent Pipeline

Replaces a monolithic "send everything to Claude and pray" approach with
typed handoffs. Each agent has one responsibility, returns a TypedDict,
and can be tested independently.

```
                  ┌────────────────────────────────┐
                  │  agent_orchestrator             │
                  │  drives the whole pipeline      │
                  └─────────────┬──────────────────┘
                                │
        ┌───────────────────────┴───────────────────────┐
        ▼                                               ▼
┌─────────────────────┐                  ┌───────────────────────┐
│ 1. DataCollector    │                  │   parallel fetch (15) │
│ agent_data_collector│ ─────────────►   │   to data_sources.py   │
└──────────┬──────────┘                  │   Coinalyze · CoinGecko│
           │                              │   Nansen · Grok · etc │
           │ CollectorResult              └───────────────────────┘
           ▼
┌─────────────────────┐
│ 2. DataInterpreter  │  pure math — no AI
│ agent_data_interpreter│ RSI/MACD/EMA/ADX/WaveTrend/S&R/confluence
└──────────┬──────────┘
           │
           │ InterpreterResult
           ▼
┌─────────────────────────┐    ┌───────────────────────────┐
│ 3. MarketSentiment      │    │ runs in parallel with     │
│ agent_market_sentiment   │ ←──│ DataReviewer (step 4)     │
│ macro verdict + crowd    │    │ both consume Interpreter  │
│ contra_signal            │    └───────────────────────────┘
└──────────┬──────────────┘
           │
           ▼ SentimentResult
┌─────────────────────────┐
│ 4. DataReviewer         │  quality-gates the technical picture
│ agent_data_reviewer      │  pulls personal backtest context
└──────────┬──────────────┘
           │ ReviewerResult
           ▼
┌─────────────────────────┐
│ 5. RiskManagement       │  Kelly sizing (0.05-0.25 fraction)
│ agent_risk_mgmt          │  SL validity + correlation checks
└──────────┬──────────────┘
           │ RiskResult
           ▼
┌─────────────────────────┐
│ 6. TradePrep            │  THE main Claude call
│ agent_trade_prep         │  Sonnet 4.6 default · Opus for cross-check
│                          │  Assembles all 5 upstream outputs
│                          │  into one prompt → final score + SL/TP
└──────────┬──────────────┘
           │ TradePrepResult
           ▼
┌─────────────────────────┐
│ 7. TradeMonitor         │  Haiku-powered open-position monitor
│ agent_trade_monitor      │  Fires every 10 min on live positions
└─────────────────────────┘
```

### Per-agent contract

| Agent | Module | Input | Output | Model |
|---|---|---|---|---|
| DataCollector | `agent_data_collector.py` | `{symbol, direction, timeframes}` | `CollectorResult` | none (15 parallel HTTP) |
| DataInterpreter | `agent_data_interpreter.py` | `CollectorResult` | `InterpreterResult` (indicators + confluence + trend label) | none (pandas-ta math) |
| MarketSentiment | `agent_market_sentiment.py` | symbol + direction + collected | `SentimentResult` (macro verdict, contra_signal, crowd positioning) | Haiku |
| DataReviewer | `agent_data_reviewer.py` | interpreted + setup_type | `ReviewerResult` (quality score 0-10, warnings, personal backtest context) | Haiku |
| RiskManagement | `agent_risk_mgmt.py` | sizing context | Kelly fraction, position_size_usdt, margin, correlation check | none (math) |
| TradePrep | `agent_trade_prep.py` | all upstream | `TradePrepResult` (final score, entry/SL/TP, key_conditions, rationale) | **Sonnet 4.6** |
| TradeMonitor | `agent_trade_monitor.py` | live position + original_prep | verdict + action + risk_rating | Haiku |

### Empty fallbacks
Each agent has an `empty_*()` factory in `agent_types.py` (`empty_interpreter`,
`empty_sentiment`, `empty_reviewer`) returning safe defaults. When data
collection fails, downstream agents still produce a result rather than
crashing the pipeline.

---

## The 5-Provider Cascade

`ai_client.send()` wraps the Anthropic call in a try/except. On
`anthropic.APIError`, it walks `_PROVIDER_CASCADE` sequentially.
Each entry is skipped if `openai_compat_client.is_in_cooldown()`
reports an active rate-limit cooldown for that (provider, model).

### Order + rationale

| # | Provider | Model | Why this position |
|---|---|---|---|
| 0 | **Anthropic** | Sonnet 4.6 / Haiku 4.5 | Primary — best quality, prompt caching, structured JSON adherence |
| 1 | Groq | `meta-llama/llama-4-scout-17b-16e-instruct` | LPU = sub-second latency, free tier (~14,400 RPD), good JSON adherence |
| 2 | Cerebras | `qwen-3-235b-a22b-instruct-2507` | 235B-parameter quality at free-tier cost |
| 3 | Cerebras | `llama3.1-8b` | Smaller fallback when Qwen is rate-limited |
| 4 | OpenRouter | `deepseek/deepseek-v4-flash:free` | Free, reasonable quality |
| 5 | Gemini 2.0 Flash | (internal 4-model cascade) | Last-resort; Gemini has its own internal cascade |

### Empirical ranking
Order derived from a 12-setup × 8-provider comparison logged in
`docs/cascade_comparison.md` (2026-05-19). Decision metric was
**score deviation from Sonnet baseline**, weighted by JSON parse-failure rate.

### Force-provider context (testing)
```python
from ai_client import force_provider
with force_provider("cerebras", "qwen-3-235b-a22b-instruct-2507"):
    # every send() inside this block routes through Cerebras
    result = ai_call.analyze_call(text, chart)
```
Implemented via `contextvars.ContextVar` so it propagates correctly into
threads spawned by `ThreadPoolExecutor` (the `copy_context().run()` pattern
in `agent_trade_prep` and `agent_orchestrator`).

### Production reality (2026-05-23)
- **~540 fallback calls / 24h** when Anthropic credit is depleted or rate-limited
- Cost: ~$3-5/week extra vs all-Anthropic, system stays 100% functional
- Logged in `token_usage` table as `{module}+{provider}` (e.g. `scanner_quick+groq`)

---

## Model Routing Table

Which task uses which model, with rationale.

| Task | Module | Model | Max Tokens | Why |
|---|---|---|---|---|
| Call analyzer (deep analysis) | `ai_call.py` | Sonnet 4.6 | 4096 | Deep reasoning needed, multi-input synthesis |
| Trade-prep agent (scanner Stage 3b) | `agent_trade_prep.py` | Sonnet 4.6 | 1200 | Final scoring, must be consistent |
| Advisor | `ai_advisor.py` | Sonnet 4.6 | 4096 | Live position management |
| Rulebook updater | `ai_rulebook.py` | Sonnet 4.6 | 2048 | Pattern extraction from history |
| Pattern detector | `ai_pattern_detector.py` | Sonnet 4.6 | 1200 | Visual + technical pattern match |
| Limit-order analyzer | `ai_limit.py` | Sonnet 4.6 | 1024 | Pending-order quality check |
| Setup-type classifier | `setup_classifier.py` | Sonnet 4.6 | 350 | Short classification task |
| Sonnet consensus | `trading.signal_consensus` | Sonnet 4.6 | 1200 | Second-opinion on scanner setups |
| Scanner quick-score (Stage 3a) | `scanner_prompts._quick_score` | Haiku 4.5 | 120 | Cheap pre-filter (~$0.0035/call) |
| Hindsight scorer | `ai_hindsight.py` | Haiku 4.5 | 512 | Retroactive batch — quantity over depth |
| Live trade monitor | `ai_live_trade.py` | Haiku 4.5 | 768 | Frequent calls, simple verdict |
| Trade grader | `ai_trade_grader.py` | Haiku 4.5 | 350 | Execution quality scoring |
| Cross-check (validation) | `scripts/compare_opus_sonnet.py` | **Opus 4.7** | 4096 | Highest-quality baseline for accuracy audits |
| Gemini consensus pre-proof | `gemini_client.py` | Gemini 2.0 Flash | 200 | Parallel sanity check on scanner top-5 |
| Social/news context | `grok_client.py` | xAI Grok 3 Fast | 130 | X/Twitter sentiment |

### Why Sonnet over Haiku for scanner Stage 3b
The scanner publishes setups with SL/TP that real money trades against.
Score-7 setups under the old Haiku-only path lost money historically
(see commit `81206e3` analysis). Sonnet costs ~10× more per call but cuts
false positives roughly in half. Net: cheaper than the trades it prevents.

---

## Prompt Caching Architecture

Anthropic supports ephemeral cache_control on prompt prefix blocks ≥1024 tokens.
We exploit this aggressively because scanner runs the same prefix 6× per scan
(stage 3b on top 6 finalists).

### Stable-prefix construction
```
build_cached_messages(
  context        = "",
  prompt         = per_trade_specifics,  # short, varies per call
  stable_prefix  = ANALYST_INSTRUCTIONS + RISK_INSTRUCTIONS + rulebook + rubric,
                   # ~1543 tokens, identical across the batch
)
```

### Where it lives
- `helpers.build_cached_messages()` — constructs the Anthropic messages with
  `cache_control: ephemeral` on the stable_prefix block
- `agent_trade_prep.py` line 100ish — merges `ANALYST_INSTRUCTIONS + RISK_INSTRUCTIONS`
  into a single ~1543-token block (was ~520 tokens before commit `232c558`,
  below the cache minimum and getting 0% hit rate)
- `ai_hindsight._batch_thread()` builds a single `stable_prefix` once for the
  whole batch of trades

### Measured impact (2026-05-22)
- call_analyzer: **47.3% cache hit rate** · ~$10 saved per ~50 calls
- agent_trade_prep batch: **74% hit rate** within a single 6-setup scan
- scanner_quick (Haiku): **0% cache** (prefix too short — under 1024-token threshold)

### Why not cache scanner_quick
Haiku quick-score uses a deliberately tiny prompt (`~120 tokens output`) so
the prefix can't exceed the 1024-token cache threshold. Caching only helps
when the prefix is long; for scanner_quick, the call is cheap enough that
the cascade fallback to Groq is the better optimization.

---

## AI Features Map

How each user-facing feature plugs into the agents above.

| Feature | Page | Agents invoked | Triggered by |
|---|---|---|---|
| Trade Call Analyzer | Call Analyzer | full 7-agent pipeline | "Analyze Call" button |
| Saved Call Analyses | Saved Calls | none (renders cached results) | page load |
| Live Trade Monitor | Live Trades | `agent_trade_monitor` (Haiku, every 10 min) | `monitor_scheduler` background thread |
| AI Advisor | AI Advisor | `agent_orchestrator.run_call_analysis` | "Get Advice" |
| Setup Scanner | Setup Scanner | Stage 3a: `scanner_prompts._quick_score` (Haiku). Stage 3b: full agent pipeline (Sonnet) on top-N | every 30 min scheduler + manual scan |
| Pending Limit Analyzer | Pending Orders | `ai_limit.py` | new pending order |
| Hindsight Analyzer | Hindsight | `ai_hindsight._analyze_one` (Haiku batch) | `POST /api/hindsight/run` |
| AI Pattern Detector | Edge Lab | `ai_pattern_detector` | "Detect Patterns" |
| Personal Rulebook | (background) | `ai_rulebook.update_rulebook` (Sonnet, daily) | daily cron at 04:00 + manual trigger |
| AI Self-Review | Risk Dashboard | `ai_self_review` (Haiku, on alpha-leak trades) | `self_review_scheduler` daily |
| AI Blindspots | Risk Dashboard | `ai_blindspots` (pattern miner) | manual + scheduled |
| Trade Grader | Journal | `ai_trade_grader` (Haiku) | trade close |
| Futures-AI Consensus | Futures-AI | `trading.signal_consensus.evaluate` (Sonnet) | every scanner setup with score ≥ `CONSENSUS_MIN_SCORE` |
| Futures-AI Learner | (background) | `trading.learner` (Sonnet post-trade) | every auto_ai trade close |

---

## Token Budget per Operation

Approximate input + output tokens. Times in seconds at typical latency.

| Operation | Input | Output | Sonnet $ | Haiku $ | Latency |
|---|---|---|---|---|---|
| Scanner Stage 3a (per symbol) | ~3000 | ~120 | — | $0.0035 | 0.4s (Haiku) / 0.2s (Groq fallback) |
| Scanner Stage 3b (per top-N setup) | ~4500 | ~1200 | $0.014 | — | 1.8s |
| Call analyzer | ~3500 | ~3500 | $0.014 (first call) → $0.005 (cache hit) | — | 4s |
| Live trade monitor | ~2200 | ~600 | — | $0.003 | 1.2s |
| Hindsight (per trade in batch) | ~2400 | ~400 | — | $0.005 | 0.8s |
| Sonnet consensus (per setup) | ~3000 | ~800 | $0.013 | — | 1.5s |
| Pattern detector | ~3800 | ~1200 | $0.018 | — | 2s |
| Rulebook update (daily) | ~6000 | ~2000 | $0.030 | — | 3s |

### Cost-control knobs (env)
- `FUTURES_AI_CONSENSUS_MIN_SCORE=7` — skip Sonnet consensus for scanner scores < 7. Bump to 8 to cut consensus spend ~50% during budget-tight periods.
- `FUTURES_AI_CONSENSUS_MODEL=haiku` — use Haiku for consensus (80% cheaper, less accurate)
- `SCANNER_FULL_DETAIL_TOP_N` (constants.py) — how many Stage 3b setups per scan. 6 = current. 12 = pre-cost-tuning (commit `81206e3`).

### Weekly spend (2026-05-22 snapshot)
- Total: **$38/week** across Anthropic + fallbacks
- Top modules: call_analyzer Sonnet ($18) + scanner_quick Haiku ($14) + call_analyzer Opus ($2.5)
- Cascade savings: ~33% of scanner_quick volume served by free providers when Anthropic rate-limits

---

## Failure Modes & Recovery

| Mode | Behavior | Recovery |
|---|---|---|
| Anthropic 429 / 503 | `ai_client.send()` catches `APIError` → walks cascade | Automatic, transparent |
| Free provider 429 | `openai_compat_client.mark_cooldown(base, model)` for 60s | Provider skipped in cascade for cooldown window |
| All providers exhausted | Gemini fallback (last in cascade has its own internal 4-model cascade) | Worst case: error logged, agent returns empty defaults |
| JSON parse failure | Caught in each AI feature, returns empty result + logs `raw output` | Self-review picks up the pattern; cascade tries next provider |
| Gemini empty parts | `thinkingConfig: {thinkingBudget: 0}` forces content return (fix in commit `bc7a0a8`) | Permanent — fix is in `gemini_client.send_text()` |
| `KNOWLEDGE_VERSION` mismatch | Subsystems flag stale rows in `/api/system/health` | Operator re-runs rulebook / hindsight |

---

## See also
- [`SCANNER_PIPELINE.md`](SCANNER_PIPELINE.md) — full scanner stage-by-stage flow
- [`DATA_SOURCES.md`](DATA_SOURCES.md) — what the agents fan-in to
- [`architecture.md`](architecture.md) — full system architecture
- [`MODULE_MAP.md`](MODULE_MAP.md) — module index

---

## Specialized Agents (A-A → A-E, added 2026-05-31)

Five adversarial / verification agents wrap the consensus call:

| Code | Agent | When | Effect |
|---|---|---|---|
| A-A | Red-Team (`trading/red_team_agent.py`) | After consensus approves, before order placement | SOFT: score_penalty logged · HARD: veto blocks trade |
| A-B | Backtest Validator (`trading/backtest_validator.py`) | Every L-3/L-4/L-5 param change submits OLD vs NEW | recommend approve / reject / neutral / insufficient |
| A-C | Post-Mortem (`trading/post_mortem.py`) | Hourly — picks up un-analysed losses (≤ −$1) | tag + severity + evidence written to `positions.postmortem_*` |
| A-D | Exec-Quality (`trading/exec_quality.py`) | On every fill — captures (intended_entry, actual_entry, direction) | 7d avg / median / max slippage_bps → daily report |
| A-E | Cascade Predictor (`trading/cascade_predictor.py`) | Pre-order, side-aware | veto when risk ≥ 0.75 AND direction = side-at-risk |

## Self-Learning Ladder (L-0 → L-5)

| Code | Learner | Cadence | Gate |
|---|---|---|---|
| L-0 | `learned.py` + `learner_symbol.py` | 6 h | R-5 Bayesian posterior |
| L-1 | Read-path accessors in `config.py` | — | learned_params lookup → constant fallback |
| L-2 | `learner_time.py` (session / DoW / hour) | 6 h | R-5 |
| L-3 | `learner_threshold.py` (consensus_min_score) | daily | R-5 + A-B |
| L-4 | `learner_tpsl.py` (TP1 / SL ATR distance) | daily | R-5 + A-B |
| L-5 | `learner_risk.py` (Kelly + max_notional + time-stop) | daily | R-5 + A-B + DD-pause |

## Noise Detection (N-1 → N-4)

| Code | Where | Rule |
|---|---|---|
| N-1 | `signal_consensus.evaluate` | abs(scanner_score − ai_score) > 2.5 → veto |
| N-2 | `fdr_correction.py` | Benjamini-Hochberg helper for multiple-testing correction |
| N-3 | `noise_gates.py` (in scanner Stage 3) | wick rejection −0.4 · ADX < 20 −0.3 · BB squeeze +0.2 boost |
| N-4 | `vpin.py` (in `signal_consensus.evaluate`) | VPIN ≥ 0.70 → veto |

See [`SELF_LEARNING.md`](SELF_LEARNING.md) for the full architecture.
