# Trading Journal — Architecture & Data Flow

*v1.6.0 + Futures-AI · Updated 2026-05-23*

---

## v1.6.0 additions (2026-05-17)

### New Modules
| Module | Purpose |
|--------|---------|
| `liquidation_levels.py` | CCXT Binance USDM forced-liquidation cluster detection; TTL-cached 15 min |
| `order_flow` (chart_indicators.py) | Tick-rule proxy for per-candle buy/sell aggressor delta + divergence |
| `onchain_client.py` | CoinMetrics Community API — MVRV, exchange net-flow; TTL-cached 1 h |
| `market_regime.py` | GaussianHMM 3-state regime classifier on BTC 4H (trending_up/ranging/trending_down); retrained 4 h |
| `signal_scorer.py` | XGBoost win-probability from historical analyzed_calls; 24 h retrain, activates at 20+ outcomes |
| `backtest_quality.py` | PBO (CSCV), Deflated Sharpe (Bailey et al. 2014), Bootstrap Sharpe CI |

### Confluence Signals (12, up from 9)
| Signal | Weight | Notes |
|--------|--------|-------|
| RSI | grouped momentum ±1.5 | with MACD |
| MACD | grouped momentum ±1.5 | capped with RSI |
| EMA stack | ±1.0 | |
| ADX | ±strength | |
| WaveTrend | grouped oscillator ±1.0 | with MFI |
| MFI | grouped oscillator ±1.0 | capped with WT |
| CVD | ±0.4 | |
| order_flow | ±0.15 per-TF | tick-rule delta, new v1.6.0 |
| volume | ±0.5/−0.25 amplifier | |
| smt_weight | +0.15 | cross-exchange price divergence |
| smt_direction_weight | ±0.15 | 24h directional divergence |
| liquidation_wall | ±0.20 symbol-level | conditional on 3% proximity, new v1.6.0 |

max_per_tf = 5.55 (non-SMT) / 5.85 (SMT) + 0.20 symbol-level conditional

### New DB Columns
- `analyzed_calls.regime_label TEXT` (migration 38) — HMM state label at time of analysis
- `analyzed_calls.ml_win_prob REAL` (migration 39) — ML scorer output

### New Endpoints
- `POST /api/backtest/quality` — PBO, Deflated Sharpe, Bootstrap CI on submitted equity curve

### Prompt Injections Added
- HMM regime: `"Market regime (HMM/BTC): trending_up — confidence 78%"`
- On-chain: `"On-chain BTC: MVRV 2.3 | fair_value | exchange outflow $30M"`
- ML scorer: `"ML win probability: 68% (historical pattern match)"`

### Browser Baseline
- 16/16 tabs: zero JS errors, DOM counts 1590–4740
- 4/4 pages: accessibility 100/100 (42 aria-label fixes across HTML + dynamic JS inputs)
- 3/3 interaction checks: Call Analyzer, Scanner, Chart Explorer
- Test suite: 467 passing

---

## Post-v1.6.0 additions (2026-05-18)

### Gemini AI Fallback (`ai_client.py`)
`ai_client.send()` wraps the Anthropic call in a try/except on `anthropic.APIError`. On failure it delegates to `gemini_client.send_text()`, converts the flat prompt, and returns a compatible `(text, cached)` tuple. All 10+ AI modules get transparent fallback with no per-module changes. Token usage is logged as `{module}+gemini / gemini-fallback`.

### Scanner Price Proximity Guard (`scanner_scheduler.py`)
`_enrich_and_filter_setups()` now runs 4 checks before passing setups to the alert pipeline:
1. No `entry_ref` (missing entry_zone + entry_price) → drop
2. `|live_price - entry_ref| / live_price > 20%` → drop (fixes stale KITE-style alerts)
3. Directional drift > 5% (existing guard, kept)
4. Exception in `get_live_price()` → drop (fail-closed)

### Chart Legend Panel (`templates/chart.html`)
`?` button added in the chart header alongside the layer toggles. Clicking expands an inline collapsible panel (max-height 280px, scrollable) with 7 sections: Trade Levels · S/R · Trendlines · Fibonacci · Liquidation · WaveTrend · Volume. Each entry has a color-coded visual indicator (line/box/spacer) + label + plain-English description. All content is static HTML — no `innerHTML` assignment.

### Pending Orders UX (`static/js/10-pending.js`)
- `↗ Pop Out` button overlaid top-right on the chart thumbnail; opens `chart.html` popup
- JSON-in-summary detection: if `summary` starts with `{`, tries to extract `entry_reason`; on parse failure shows `⚠ Analysis was truncated — click AI Analysis to retry.`
- `ai_limit.py` `max_tokens` increased 768 → 1024 to accommodate Gemini fallback verbosity

### Scanner Timeframe Normalization (`static/js/14-scanner.js`)
Normalizes `setup.timeframe` against a `_VALID_TF` set before building the chart URL. Converts `"Multi-TF (1D/4H/1H)"` display labels to a valid Bitget granularity string, preventing "no candle data" errors on scanner-generated chart popups.

---

## Futures-AI Auto-Trader (2026-05-22 → present)

Autonomous trading chain that consumes scanner output and places real Bitget orders without operator intervention. Lives in the `trading/` package and runs on a dedicated subaccount (auto-trader subaccount) so its risk envelope is isolated from the operator's main book.

### Two-chain DB isolation (migration 47)
`positions` gained a `chain TEXT DEFAULT 'manual'` column. The auto-trader writes `chain='auto_ai'`; the manual operator/sync paths keep the default. Every chain-scoped query — kill_switch position counts, equity_now, hindsight, learner, rulebook — filters on this column so:
- Manual hindsight, lessons, and rulebook stay 100% operator-driven
- Auto-trader breakers compute against the AI's own DD, not the operator's history
- Market data, scanner output, indicators, baselines, and macro caps are **shared** (no point duplicating)

The Futures-AI page (`#page-futuresai`, JS in `static/js/18-futures-ai.js`) shows the AI's open positions and recent decisions; the operator's main book continues on Live Trades / Journal / Deep Dive.

### Module map
| Module | Responsibility |
|---|---|
| `trading/config.py` | Env-driven knobs (`FUTURES_AI_ENABLED`, `FUTURES_AI_MODE`, `FUTURES_AI_STARTING_EQUITY`, `FUTURES_AI_CONSENSUS_MIN_SCORE`, `FUTURES_AI_CONSENSUS_MODEL`) + runtime state machine persisted in `settings.futures_ai_state`: `active` / `pause_after_close` / `pause_now` / `circuit_breaker`. `snapshot()` exposes calibration for the UI and `/api/system/health`. |
| `trading/orchestrator.py` | Two entry points: `on_scan_completed(setups)` (scanner hook) and `on_monitor_cycle()` (10-min tick). Walks setups through kill_switch → consensus → sizing → dispatch. Per-setup `futures_ai_log` rows record every accept/reject decision with reason. |
| `trading/kill_switch.py` | Capital-preservation gate. Checks env switch, runtime state, daily DD breaker, total DD breaker, consecutive-loss breaker, then concurrent-position cap (soft 5 / elite 7). Tripped breakers transition state → `circuit_breaker` and persist via `config.set_state`. |
| `trading/signal_consensus.py` | Sonnet second-opinion. Returns `{approved, consensus_score, scanner, ai}`. Approval requires AI score ≥ 7 AND direction match. `consensus_score = min(scanner, ai)`. |
| `trading/risk_budget.py` | Kelly-scaled position sizing. `notional = (equity × 0.02 × multiplier) / sl_distance_pct`, capped at `MAX_NOTIONAL_USDT` ($25). Score multipliers: 7→1.0×, 8→1.5×, 9→2.0×, 10→2.0×. Leverage = ceil(notional / (equity × 0.10)), capped at `MAX_LEVERAGE` (10). |
| `trading/bitget_trader.py` | Bitget V2 REST write client (HMAC-SHA256). Tick-size snapping via cached `pricePlace`. ATR-based SL/TP repair for wrong-side or pathological levels. Plan-order attach via `/api/v2/mix/order/place-tpsl-order` (planType=`loss_plan` / `profit_plan`). Reads SL/TP from `/orders-plan-pending`, not the position record. Returns `leverage_requested` / `leverage_actual` / `set_leverage_result`. |
| `trading/executor.py` | Real-mode dispatch. `open_real_trade()` → `bitget_trader.place_market_order()` → INSERT `positions` row with `chain='auto_ai'`. `manage_real_positions()` reconciles via `bitget_trader.get_position_history(last_24h)` and updates `close_time` / `close_price` / `realized_pnl`. |
| `trading/paper.py` | Paper-mode simulator. Price-walk fills against 1-min candles, identical accounting/SL/TP to real mode. Used during initial validation; gated by `FUTURES_AI_MODE=paper`. |
| `trading/learner.py` | Post-trade reflection. Sends closed-trade summary + chart context to Sonnet, persists lessons into `trade_hindsight` and feeds the chain-scoped rulebook. |

### Pipeline flow
```
ai_scanner.on_scan_completed
        │
        ▼ (per setup, score >= SCANNER_MIN_SCORE)
trading/orchestrator
        │
        ├─► kill_switch.can_open_new_trade(conn, scanner_score=N)
        │     ├─ env enabled? state active? breakers untripped?
        │     └─ effective_cap = ELITE if scanner==10 else SOFT
        │
        ├─► signal_consensus.evaluate(setup)
        │     └─ Sonnet rates the same setup; min(scanner, ai) = consensus_score
        │
        ├─► [elite re-check] if scanner==10 admitted past full cap
        │   but consensus_score < 10 → reject (bypass revoked)
        │
        ├─► risk_budget.size_trade(score, entry, sl, equity)
        │     └─ returns notional, leverage, contracts
        │
        └─► executor.open_real_trade  (or paper.open_paper_trade)
              └─► bitget_trader.place_market_order
                    ├─ set leverage (logged)
                    ├─ market entry
                    ├─ ATR-repaired SL plan order
                    └─ ATR-repaired TP plan order
```

### Risk envelope
| Knob | Value | Source |
|---|---|---|
| Risk per trade | 2% of equity (Kelly-scaled by score) | `RISK_PER_TRADE_PCT` |
| Notional cap | $25 | `MAX_NOTIONAL_USDT` |
| Leverage cap | 10× | `MAX_LEVERAGE` |
| Concurrent cap (soft) | 5 | `MAX_CONCURRENT_POSITIONS` |
| Concurrent cap (elite, scanner+consensus = 10) | 7 | `MAX_ELITE_POSITIONS` |
| Daily DD breaker | -5% | `DAILY_DD_BREAKER_PCT` |
| Total DD breaker | -15% | `TOTAL_DD_BREAKER_PCT` |
| Consecutive losses breaker | 3 | `CONSECUTIVE_LOSS_BREAKER` |

7 elite positions × 2% per-trade risk = 14% — sitting right under the -15% total-DD breaker, so the elite bypass cannot put the AI over its own circuit breaker even in the worst case.

### Elite-setup bypass (2026-05-23)
Scanner-verified 10/10 setups bypass the 5-position soft cap up to a 7-position hard cap. Mechanics:
1. `kill_switch.can_open_new_trade(conn, scanner_score)` uses `effective_cap = MAX_ELITE_POSITIONS` when `scanner_score >= ELITE_BYPASS_SCORE` (10), else `MAX_CONCURRENT_POSITIONS`.
2. Orchestrator passes `scanner_score=setup["setup_score"]` to the killswitch call.
3. After consensus runs, if the bypass was used (`scanner==10 AND n_open >= soft_cap`) but `consensus_score < 10`, the soft cap is reapplied and the trade is rejected with reason `"elite bypass revoked"`. This prevents a non-elite trade from sneaking through the elite slot.

All other breakers (env switch, state, daily DD, total DD, consec losses) still apply unconditionally — the bypass only lifts the concurrent-position cap.

### SL/TP enforcement (2026-05-23)
- **Scanner side** (`ai_scanner._score_finalists_with_agents`):
  - `trade_utils.enforce_tp_floor` — TP1 ≥ 1× ATR_4H, TP2 ≥ 2× ATR_4H, wrong-side repair
  - `trade_utils.enforce_sl_floor` — SL on correct side, 0.5×–8× ATR_4H envelope, default 1× ATR_4H repair when missing/pathological
  - Adjustments recorded in `setup._tp_adjustments` / `setup._sl_adjustments`
- **Executor side** (`bitget_trader.place_market_order`):
  - Pre-flight tick-size snap via cached `pricePlace`
  - If incoming SL/TP fails sanity on the Bitget side, ATR-based repair recomputes from 4H ATR
  - Plan orders submitted separately via `/api/v2/mix/order/place-tpsl-order` (no `presetStopLossPrice` on the order body)
  - SL/TP read back from `/orders-plan-pending`, NOT the position record (Bitget V2 keeps SL/TP on plan orders, not on the position)

### Cross-chain exposure monitor (2026-05-23)
`monitor_scheduler._run_once()` now fetches both:
- `bitget_client.get_open_positions()` — manual chain (operator's main book)
- `bitget_trader.get_open_positions()` — auto chain (auto-trader subaccount)

Each position is tagged with `chain` and the combined list is passed to `exposure_monitor.check()`. Alert keys include the `chains` tuple so a manual-only and auto-only alert with the same symbol set don't collide. The operator's *total* concentration risk (sector clusters, directional overload) is now visible regardless of which book opened the position.

### Auto-trader logging
Every decision lands in `futures_ai_log` with `(ts, event, payload_json)`. Events:
| Event | When |
|---|---|
| `state_change` | UI/Telegram pause/resume |
| `rejected_killswitch` | Killswitch denied (with reason) |
| `rejected_consensus` | Sonnet disagreed |
| `rejected_sizing` | risk_budget returned None |
| `consensus_skipped` | Score below `CONSENSUS_MIN_SCORE` (budget knob) |
| `consensus_approved` | Sonnet agreed |
| `paper_open` / `real_open` | Trade opened |
| `lev_mismatch` | Bitget set a different leverage than requested |
| `paper_close` / `real_close` | Trade closed (with reason: tp1/tp2/sl/manual) |
| `breaker_tripped` | A circuit breaker fired |

The Futures-AI page reads this table for the "Recent decisions" panel.

---

## System Overview

```
                        ┌─────────────────────────────────────────────────────────┐
                        │                  RASPBERRY PI 5                          │
                        │                                                           │
  Browser / Mobile      │   ┌──────────┐    ┌─────────────────────────────────┐  │
  ─────────────────────►│   │  Flask   │    │       BACKGROUND THREADS        │  │
  (local network)       │   │  app.py  │    │  ┌──────────┐  ┌─────────────┐  │  │
                        │   │  :8082   │    │  │  Bitget  │  │   Scanner   │  │  │
                        │   └────┬─────┘    │  │  Sync    │  │  Scheduler  │  │  │
                        │        │          │  │  (5 min) │  │  (30 min)   │  │  │
                        │   9 Flask         │  └────┬─────┘  └──────┬──────┘  │  │
                        │   Blueprints      │  ┌─────────────────────────────┐ │  │
                        │        │          │  │   Monitor Scheduler         │ │  │
                        │        │          │  │   (10 min, positions)       │ │  │
                        │        │          │  └──────────────────────────┘  │  │
                        │        │          └───────────────────────────────────┘  │
                        │        ▼                                                   │
                        │   ┌─────────────────────────────────────────────────┐  │
                        │   │              SQLite WAL Database                  │  │
                        │   │  positions · orders · analyzed_calls              │  │
                        │   │  pending_limits · trader_rulebook                 │  │
                        │   │  trade_hindsight · token_usage · settings         │  │
                        │   └─────────────────────────────────────────────────┘  │
                        └─────────────────────────────────────────────────────────┘
```

---

## 7-Agent Pipeline

```
Call text / scanner symbol / live position
              │
              ▼
   ┌──────────────────────┐
   │   DataCollector      │  agent_data_collector.py
   │   OHLCV · funding    │  → CollectorResult
   │   OI · F&G · FRED    │  (parallel fetches, TTL caches)
   │   Nansen · Grok      │
   └────────┬─────────────┘
            │
      ┌─────┴──────────────────┐  (parallel)
      ▼                        ▼
┌─────────────────┐   ┌─────────────────────────┐
│ DataInterpreter │   │  MarketSentimentAnalyzer │
│ RSI·MACD·EMA    │   │  macro bias · funding    │
│ S/R · WaveTrend │   │  L/S ratio · Grok social │
│ confluence score│   │  contra_signal flag      │
│ → InterpreterResult  → SentimentResult         │
└────────┬────────┘   └─────────────────────────┘
         │
         ▼
┌─────────────────────────┐
│  DataReviewer           │
│  + KPI Generator        │  agent_data_reviewer.py
│  signal quality 0-10    │  → ReviewerResult
│  backtest WR/streak     │
│  trading KPIs from DB   │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│   TradePreparation      │  agent_trade_prep.py
│   (main Claude call)    │  → TradePrepResult
│   assembles all above   │  chart_png_b64 generated here
│   + Gemini consensus    │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│   RiskManagement        │  agent_risk_mgmt.py
│   sizing · correlation  │  → RiskResult
│   ATR SL · Kelly crit.  │  (pure math — no AI call)
└────────┬────────────────┘
         │
         ▼
    AnalysisResult → saved to analyzed_calls
    (risk_verdict_json, chart_png_b64, monitor_alert columns)
         │
   [position opens]
         │
         ▼
┌─────────────────────────┐
│   TradeMonitor          │  agent_trade_monitor.py
│   background thread     │  → MonitorResult
│   Collector→Interp      │  polls every 10 min
│   →Sentiment→Haiku      │  fires Telegram + UI badge
│   on risk_rating ≥ 7    │  on risk_rating ≥ 7 or action ≠ Hold
└─────────────────────────┘
```

---

## Agent Contract Types

All TypedDicts live in `agent_types.py` — single source of truth.

| Type | Description |
|------|------------|
| `CollectorResult` | Raw data: candles, funding_rate, open_interest, long_short, fear_greed, fred_macro, nansen, grok |
| `InterpreterResult` | Signals: by_timeframe, sr_levels, confluence_score, trend_direction, momentum_bias |
| `SentimentResult` | Macro: macro_bias, sentiment_score, funding_bias, crowd_position, contra_signal |
| `ReviewerResult` | Quality: signal_quality, warnings, backtest_context, kpis, rubric |
| `TradePrepResult` | Trade: setup_score, entry/sl/tp prices, key_conditions, cot_reasoning, consensus, chart_png_b64 |
| `RiskResult` | Risk: approved, position_size_usdt, margin_usdt, kelly_fraction, atr_sl_valid |
| `MonitorResult` | Monitor: action, risk_rating, alert_level, tp/sl recommendations |
| `AnalysisResult` | Flat merge of all agent outputs for DB persistence |

---

## Orchestrator Pipeline Functions

`agent_orchestrator.py` wires agents together:

```python
run_call_analysis(call_text, symbol, direction, equity, setup_type, positions, conn)
    → AnalysisResult   # 5-stage pipeline for call analysis

run_scanner_prep(symbol, direction, collected, interpreted, reviewed, sentiment, conn)
    → TradePrepResult  # stage 3b entry for scanner (per finalist)

run_monitor(position, original_prep)
    → MonitorResult    # lightweight chain for background monitor thread
```

---

## Consensus Scoring

```
|Claude - Gemini| ≤ 1 → ✓ Confirmed   (HIGH confidence, avg score)
|Claude - Gemini| ≤ 2 → ~ Aligned     (MED confidence, avg score)
|Claude - Gemini| ≤ 3 → ⚠ Divergent   (LOW confidence, Claude 60% weight)
|Claude - Gemini| > 3 → ⚡ REVIEW      (very_low, keep Claude score)
```

---

## Model Routing Table

```
Task                     Model      Tokens(out)  Rationale
─────────────────────────────────────────────────────────────────────────────
call_analyzer (TradePrep) Sonnet    4096         Complex structured JSON + CoT
scanner_batch (TradePrep) Sonnet    4096/symbol  Per-finalist via agent pipeline
advisor                  Sonnet     4096         Portfolio-level strategy
rulebook                 Sonnet     2048         Synthesis of full history
limit_analyzer           Sonnet     1024         Risk decision (increased for Gemini fallback verbosity)
pattern_detector         Sonnet     1200         Cross-pattern reasoning

scanner_quick            Haiku      120          Score + 1 sentence (fast pre-filter)
hindsight                Haiku      512          Retroactive classification task
live_trade/monitor       Haiku      768          Quick action rec (latency critical)
trade_grader             Haiku      350          A/B/C/D rubric classification

Gemini 2.0 Flash         [parallel] 200          Independent pre-proof score only
Grok 3 Fast              [parallel] 130          Social/news brief (MC-weighted)
```

---

## Scanner Pipeline (every 30 min)

```
DEFAULT_WATCHLIST (100 symbols)
         │
         ▼ Stage 1 — Confluence filter (parallel, no AI, no cost)
         │  chart_indicators: RSI·MACD·EMA·ADX·WaveTrend·CVD per 4H+1D
         │  ✗ Drop if < 2 signals aligned in one direction
         │
         ▼ Stage 2 — Technical quality gate (no AI, instant)
         │  ✗ Drop: overextended RSI, missing S/R, flat ADX, high funding
         │
         ▼ Stage 3a — Haiku quick-score (cheap pre-filter)
         │  Compact indicator prompt → score 0-10 + one-sentence rationale
         │  ✗ Drop if score < threshold (default 6, self-calibrated)
         │
         ▼ Stage 3b — Agent pipeline per finalist (replaces old batch call)
         │  DataCollector → DataInterpreter+MarketSentiment → DataReviewer
         │  → TradePrep (Claude call) + chart generation
         │  Returns: entry_zone, sl_price, tp1, tp2, rr_ratio, key_conditions
         │
         ▼ Stage 3c — Gemini consensus (top-5 only, parallel)
         │  Independent indicator-only score
         │  Consensus confidence flag added to each setup
         │
         ▼ Telegram alert with annotated chart (if any setup ≥ 6/10)
         │  + Save to analyzed_calls (analyst='scanner')
         │  + Auto-link to matching open positions via check-matches
         │
         ▼ Results cached 30 min
```

---

## Monitor Scheduler (every 10 min)

```
App start → wait 2 min → first pass → every 10 min → repeat

Per position that passes filter (unrealized_pct < -5% OR duration > 240 min):
  DataCollector (TTL caches — minimal network cost)
      → DataInterpreter (pure)
      → MarketSentimentAnalyzer (pure)
      → Haiku verdict (768 tokens)

On risk_rating ≥ 7 or action ≠ "Hold":
  → Telegram alert
  → UPDATE analyzed_calls SET monitor_alert=1 (UI badge)
```

---

## Prompt Caching Architecture

```
STABLE BLOCK (cache_control: ephemeral) ← cached across calls
  build_stable_prefix(): rulebook + calibration + pattern strengths
  Changes: at most weekly

DYNAMIC BLOCK (no cache) ← changes every call
  DataReviewer: backtest context + KPIs
  MarketSentiment: macro bias + contra signal
  DataInterpreter: chart indicators per timeframe
  Rubric: setup-type scoring rules
  Signal quality score + warnings

USER PROMPT (never cached)
  call text + account equity + setup type

Expected savings: 40-60% on repeated calls
```

---

## Backtest → Accuracy Feedback Loop

```
Every trade outcome recorded → DB: positions.realized_pnl
         │
         ▼ DataReviewer.get_backtest_context(conn, symbol, direction, setup_type)
         │
  ┌──────────────────────────────────────────────┐
  │  BACKTEST INSIGHTS (injected into TradePrep) │
  │  • Recent form: 72% WR last 20 · streak WWLWW│
  │  • Breakout setups: 100% WR (6 trades) +$7   │
  │  • BTCUSDT Long: 75% WR (12 trades) +$12.50  │
  │  • ⚠ Wednesday: caution (57% WR, -$355)      │
  └──────────────────────────────────────────────┘
         │
         ▼ TradePrep uses this BEFORE scoring the new trade
         │
         ▼ New call scored → outcome recorded → next call gets updated insights
```

---

## DB Schema (analyzed_calls key columns)

| Column | Added | Purpose |
|--------|-------|---------|
| gemini_score | mig 26 | Gemini pre-proof score |
| consensus_score | mig 27 | Claude+Gemini average |
| consensus_flag | mig 28 | ✓/~/⚠/⚡ label |
| risk_verdict_json | mig 29 | Full RiskResult JSON |
| monitor_alert | mig 30 | 1 = monitor fired alert |
| chart_png_b64 | mig 31 | Annotated trade chart |

---

## Data Sources

| Source | What it provides | Cache TTL |
|--------|-----------------|-----------|
| Bitget REST v2 | OHLCV candles, positions, funding rate | 10 min (candles) |
| Anthropic API | Claude Sonnet/Haiku | n/a |
| Google Gemini Flash | Pre-proof consensus scoring | 30 min |
| xAI Grok | X/Twitter sentiment, news (MC-weighted) | 30 min |
| Nansen.ai | On-chain smart money signals | 30 min |
| CoinGecko | Market cap lookup for Grok weight | 24 h |
| alternative.me | Fear & Greed Index | 5 min |
| Binance futures | Open Interest (public) | 5 min |
| Bybit/OKX | Multi-exchange funding rates | 5 min |
| FRED (St. Louis Fed) | Fed rate, treasury yield, CPI, M2 | 6 h |
| ForexFactory mirror | High-impact USD economic events | 1 h |

---

## Token Budget per Operation

| Operation | Stable (cached) | Dynamic (not cached) | Output | Providers |
|-----------|----------------|---------------------|--------|-----------|
| Call analysis | ~1,200 tokens | ~2,800 tokens | 1,200 | Sonnet + Gemini + Grok |
| Scanner quick-score | 800/symbol | 400/symbol | 30 | Haiku |
| Scanner per-finalist | ~1,200 | ~3,500 | 1,200 | Sonnet + Gemini (top-5) |
| Monitor check | — | 800 | 300 | Haiku |
| Hindsight score | — | 800 | 200 | Haiku |
| Trade grade | — | 700 | 100 | Haiku |
| Advisor | ~1,200 | ~2,800 | 1,500 | Sonnet |
| Rulebook regen | — | 3,000 | 800 | Sonnet |
