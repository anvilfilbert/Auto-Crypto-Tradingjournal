# Module Map

*One-line index of every Python module + frontend asset in the trading journal.
Updated 2026-05-23 (commit `0ebbee5`). Use this to find code without grepping
when you're new to the codebase.*

Total: ~120 backend modules + 19 JS files + 14 route blueprints + 76 tests.

---

## App entrypoint & core infrastructure

| File | Purpose |
|---|---|
| `app.py` | Flask app factory, blueprint registration, background-thread startup |
| `database.py` | SQLite WAL connection helper + 48-migration framework via `_apply(version, name, sql)` |
| `constants.py` | `MODEL`, `SCANNER_MIN_SCORE`, `KNOWLEDGE_VERSION`, etc. — single source of truth |
| `helpers.py` | Shared utilities + re-exports (`log_token_usage` from `token_log`, `build_cached_messages`, JSON helpers, `_safe_float`) |
| `system_state.py` | Single source of truth for subsystem freshness timestamps (scanner, monitor, rulebook, hindsight, self-review) |
| `journal_paused.py` | Global pause switch for AI-firing automations + alerts |
| `importer.py` | Parse Bitget CSV exports → SQLite (CSV import path) |
| `telegram_notify.py` | Alert-bot push notifications |
| `token_log.py` | AI token usage telemetry → `token_usage` table |

---

## Exchange clients & sync

| File | Purpose |
|---|---|
| `bitget_client.py` | Bitget V2 **read-only** client — operator main account (positions, fills, orders, wallet) |
| `bitget_sync.py` | Pulls Bitget data into `positions` / `orders` / `wallet_snapshots`; 5-min cadence |
| `blofin_client.py` | Blofin read-only client (parallel exchange) |
| `blofin_sync.py` | Blofin sync (positions, orders, wallet) |
| `ccxt_client.py` | Multi-exchange via CCXT — used for liquidation-cluster detection + L/S ratio |
| `sync_base.py` | `SyncDriver` protocol + `SyncState` + shared `auto_close_calls` / `retroactive_close_calls` |

---

## External data clients (15 sources, all fan into `agent_data_collector`)

| File | Purpose | Auth |
|---|---|---|
| `coinalyze_client.py` | Aggregated OI + funding + liquidations + OI history (smart-flow signal) | `COINALYZE_API_KEY` |
| `coingecko_client.py` | BTC.D, ETH.D, USDT.D, OTHERS.D, TOTAL2/3, MEME.C, STABLE.C, trending | none |
| `deribit_client.py` | BTC/ETH put/call skew (institutional sentiment proxy) | none |
| `finnhub_client.py` | Economic calendar — FOMC/CPI/NFP macro risk flag | `FINNHUB_API_KEY` |
| `nansen_client.py` | Smart-money wallet flows + accumulating/distributing direction | paid |
| `grok_client.py` | xAI Grok — social/news context (cap-weighted 0-80%) | `XAI_API_KEY` |
| `liquidation_client.py` | Coinalyze historical liquidations per day; CSV cache in `data/liquidations/` | `COINALYZE_API_KEY` |
| `liquidation_levels.py` | CCXT-based forced-liquidation cluster detection; TTL 15min |  none |
| `onchain_client.py` | CoinMetrics Community API — MVRV, exchange net-flow | none |
| `market_context.py` | VIX/DXY (yfinance), ES1!, F&G, BTC mempool, market regime, dominance helpers | none |
| `data_sources.py` | Aggregation layer — `agent_data_collector` calls fetch_X() functions here |

---

## AI / LLM clients (5-provider cascade)

| File | Purpose |
|---|---|
| `ai_client.py` | Cascade router: Anthropic → Groq → Cerebras×2 → OpenRouter → Gemini; `force_provider()` ctx |
| `openai_compat_client.py` | Shared OpenAI-format base for Groq/Cerebras/OpenRouter; cooldown tracking per (base, model) |
| `cerebras_client.py` | Cerebras (Qwen 235B + Llama 8B) |
| `groq_client.py` | Groq LPU (Llama 4 Scout) |
| `openrouter_client.py` | OpenRouter (DeepSeek V4 free) |
| `gemini_client.py` | Google Gemini 2.0 Flash — consensus pre-proof + Anthropic fallback |

---

## Chart data + indicators

| File | Purpose |
|---|---|
| `chart_candles.py` | `get_candles(symbol, tf, limit)` — cached pandas OHLCV df via CCXT |
| `chart_indicators.py` | RSI/MACD/EMA/ADX/WaveTrend/MFI/Stoch/ATR/Bollinger/CVD via `pandas_ta` |
| `chart_patterns.py` | Chart-pattern detection (H&S, double tops, flags, etc.) |
| `chart_sr.py` | Support/Resistance level detection — touches + strength scoring |
| `chart_fvg.py` | **PO3 — Fair Value Gap detection** (3-candle imbalance, unfilled filter) |
| `chart_rsi.py` | **RSI Mastery — regime classification + failure swings + divergences** |
| `chart_confluence.py` | 15-signal confluence scoring engine — max_per_tf = 6.35 |
| `chart_context.py` | Thin facade — `get_chart_context(symbol, tfs)` orchestrates candles+indicators+patterns+sr |
| `indicators.py` | Canonical indicator series functions (lower-level than chart_indicators) |
| `mfe_mae.py` | Maximum Favorable / Adverse Excursion calculator (per-trade post-mortem) |

---

## AI Agents (7-agent pipeline) — `agent_*.py`

| File | Role |
|---|---|
| `agent_types.py` | TypedDicts + `empty_interpreter()` / `empty_sentiment()` / `empty_reviewer()` factories |
| `agent_data_collector.py` | Stage 1 — fan-out fetch of all data sources in parallel (15 workers) |
| `agent_data_interpreter.py` | Stage 2 — pure math: indicators, S/R, confluence (no AI) |
| `agent_market_sentiment.py` | Stage 3 — macro verdict + `contra_signal` (crowd-against-trade) |
| `agent_data_reviewer.py` | Stage 4 — quality gate + personal backtest context |
| `agent_risk_mgmt.py` | Stage 5 — Kelly sizing + SL validity check |
| `agent_trade_prep.py` | Stage 6 — main Sonnet call: assembles all upstream → final score + SL/TP |
| `agent_trade_monitor.py` | Stage 7 — Haiku-powered background open-position monitor |
| `agent_orchestrator.py` | Drives all 7 stages — `run_call_analysis`, `run_scanner_prep`, `run_monitor` |
| `agent_chart_draw.py` | mplfinance PNG annotated chart — entry zone, SL, TP1/TP2, S/R bands |

---

## AI features (user-facing)

| File | Purpose |
|---|---|
| `ai_call.py` | Trade Call Analyzer — analyst-feed analysis with annotated chart |
| `ai_advisor.py` | AI Trading Advisor — open-position guidance |
| `ai_live_trade.py` | Live trade monitor (Haiku, every 10 min on open positions) |
| `ai_limit.py` | Pending limit-order analyzer |
| `ai_scanner.py` | Setup scanner — thin: `_state`, scan thread, Stage 3 wiring, public API |
| `ai_hindsight.py` | Retroactive trade re-scoring with full chart context at entry time |
| `ai_self_review.py` | Per-trade alpha-leak retrospective |
| `ai_blindspots.py` | Pattern miner over closed-trade analyses → recurring missed signals |
| `ai_rulebook.py` | Personalised rulebook generator + `get_rulebook_for_prompt` |
| `ai_pattern_detector.py` | Chart pattern detection via Sonnet |
| `ai_trade_grader.py` | Post-trade execution-quality grader |
| `consensus.py` | (Legacy) Claude vs Gemini delta-based consensus — *manual chain only* |

---

## Scanner subsystem — `scanner_*.py`

| File | Purpose |
|---|---|
| `scanner_watchlist.py` | Symbol lists (314 USDT-M futures) + Bitget filters |
| `scanner_criteria.py` | `CRITERIA_DEFAULTS`, kill zones (PO3), personal bad-hour cap, reversal cap |
| `scanner_prompts.py` | Stage 3 prompt builders (`_build_prompt`, `_build_batch_prompt`, `_quick_score`) |
| `scanner_stages.py` | Stage 1 (Confluence filter) + Stage 2 (Quality gate) + macro context fetch |
| `scanner_scheduler.py` | 30-min cadence + alert dispatch + price-proximity guards |
| `scanner_invalidator.py` | Mark stale scanner-emitted setups so they stop cluttering Saved Calls |

---

## Risk / Monitoring / Schedulers

| File | Purpose |
|---|---|
| `monitor_scheduler.py` | 10-min thread — runs `position_risk_monitor` + `exposure_monitor` + Futures-AI orchestrator tick + cross-chain merging |
| `position_risk_monitor.py` | Per-position SL-discipline / BE-trigger / MAE-breach checks |
| `exposure_monitor.py` | Sector clustering + directional overload alerts (manual + auto_ai combined) |
| `self_review_scheduler.py` | Daily AI self-review thread |
| `entry_watcher.py` | Active limit/market recommendation queue with invalidation monitoring |
| `bear_phase.py` | **Bear-market phase classifier** (distribution/decline/capitulation/recovery) + directional bias modifier |
| `market_regime.py` | HMM 3-state regime detection on BTC 4H (trending/ranging/volatile) |
| `signal_scorer.py` | XGBoost win-probability scorer (activates at 20+ labeled outcomes) |
| `setup_classifier.py` | Two complementary setup-archetype classifiers |
| `volume_baseline.py` | Rolling per-(symbol, tf) volume baseline (vs baseline ratio in confluence) |
| `tp_ladder.py` | Multi-TP ladder reader (TP1/TP2/TP3 from per-trade JSON) |
| `risk_analytics.py` | Portfolio risk metrics (Binance public data) |
| `analytics.py` | All KPI calcs — win rate, Sharpe, Calmar, drawdown, monthly target |
| `trade_history.py` | Unified `get_symbol_summary` — replaces 4 duplicate private impls |
| `trade_utils.py` | `enforce_tp_floor`, `enforce_sl_floor`, sector definitions, `atr_sl_warning` |

---

## Prompts

| File | Purpose |
|---|---|
| `prompt_builder.py` | Stitches chart_context + rulebook + grok + nansen + macro into Claude prompts |
| `prompt_fragments.py` | Shared prompt text blocks (system prompts, instructions) — every token saved here saves on every AI call |

---

## Auto-trader (`trading/` package) — Futures-AI chain

| File | Purpose |
|---|---|
| `trading/__init__.py` | Package marker |
| `trading/config.py` | Env knobs + runtime state machine (`active`/`pause_after_close`/`pause_now`/`circuit_breaker`) + breaker reset stamp |
| `trading/kill_switch.py` | Capital-preservation gate — env switch, state, daily/total DD, consec-loss, soft/elite cap. `evaluate()` returns full UI snapshot |
| `trading/signal_consensus.py` | Sonnet second-opinion — `consensus_score = min(scanner, ai)`; logs `ai_summary` on rejection |
| `trading/risk_budget.py` | Kelly × score × streak × DD-dampener sizing; dynamic notional cap `max($25, eq×25%)` |
| `trading/bitget_trader.py` | Bitget V2 **write** client (auto-trader subaccount), HMAC sign, tick-snapping, ATR repair, cross margin |
| `trading/executor.py` | Real-mode dispatch — `open_real_trade` + `manage_real_positions` reconcile |
| `trading/paper.py` | Paper-mode simulator — price-walk fills, identical accounting |
| `trading/hedge_manager.py` | **Catastrophe hedge** — auto BTC short during basket-flush (basket -3% + BTC -2% + ≥70% long-biased) |
| `trading/learner.py` | Post-trade Sonnet reflection → feeds chain-scoped rulebook |
| `trading/orchestrator.py` | Driver — `on_scan_completed` (per-setup pipeline) + `on_monitor_cycle` (reconcile + hedge) |

---

## Web routes (`routes/`) — Flask blueprints

| File | Endpoint prefix | Purpose |
|---|---|---|
| `routes/analytics.py` | `/api/analytics/*` | KPIs, accuracy trend, MFE/MAE, EV-by-setup, execution quality |
| `routes/backtest.py` | `/api/backtest/*` | Optimizer, walk-forward, quality (PBO + Deflated Sharpe + Bootstrap CI) |
| `routes/calls.py` | `/api/calls/*`, `/api/call-analysis` | Call Analyzer, saved calls, invalidate-stale |
| `routes/futures_ai.py` | `/api/futures-ai/*` | Auto-trader state, positions, log, force-orchestrate |
| `routes/hindsight.py` | `/api/hindsight/*` | Hindsight runner + results |
| `routes/journal.py` | `/api/journal/*`, `/api/positions/*` | Trade journal CRUD, notes, tags |
| `routes/limits.py` | `/api/limits/*` | Pending limit orders + AI verdict |
| `routes/live.py` | `/api/live/*` | Live positions from Bitget/Blofin |
| `routes/market.py` | `/api/price/*`, `/api/coin/summary/*`, `/api/market/dominances` | Price, coin summary, dominance dashboard |
| `routes/risk.py` | `/api/risk/*`, `/api/self-review/*`, `/api/blindspots` | Risk metrics, self-review, blindspots |
| `routes/scanner.py` | `/api/scanner/*` | Scanner status, run, cancel, calibrate, criteria |
| `routes/settings.py` | `/api/settings`, `/api/system/health`, `/api/token-usage` | Settings, system health, AI cost |
| `routes/sync.py` | `/api/sync/*` | Bitget/Blofin sync, import progress |

---

## Frontend

### Templates
- `templates/index.html` — Main SPA shell, all page sections + JS bundle includes
- `templates/chart.html` — Live chart popup (LightweightCharts v4.1.3 + ? legend panel)

### JS modules (load order matters — sequential by 01-, 02-, ...)
- `01-utils.js` — `api()`, `notify()`, `showPage()`, formatters
- `02-dashboard.js` — KPI cards, charts, monthly target, rolling 30-day strip
- `03-journal.js` — Trade Journal table, filters, manual entry, pagination
- `04-deep-edge.js` — Deep Dive Analytics + Edge Lab
- `05-advisor.js` — AI Trading Advisor
- `06-import.js` — CSV import UI
- `07-calls.js` — Call Analyzer + Saved Call Analyses
- `08-live.js` — Live Trades (real-time positions from Bitget/Blofin)
- `08b-live-calls.js` — Live position ↔ call linking
- `09-analysis.js` — Analysis tab (optimizer, walk-forward, quality)
- `10-pending.js` — Pending Limit Orders + AI verdict cards
- `11-sync.js` — Live Sync — Bitget API
- `12-explorer.js` — Chart Explorer
- `13-init.js` — Application bootstrap, page routing
- `14-scanner.js` — Setup Scanner + 3-stage progress bar + 11-criteria configurator
- `15-hindsight.js` — Hindsight Analysis
- `16-settings.js` — Settings page + AI token usage dashboard
- `17-risk.js` — Risk Dashboard
- `18-futures-ai.js` — Futures-AI auto-trader page (status, positions, log, unrealized pill)

---

## Scripts (`scripts/`) — CLI tools

### Maintenance
- `backup_db.sh` — SQLite online backup (called by systemd ExecStopPost + daily cron at 04:00)
- `backup_pi_to_mac.sh` — Mac-side helper for pulling Pi backups
- `start_mcp_chrome.sh` — Launch headless Chrome for browser-test pipeline

### Diagnostics / debug
- `debug_bitget_position_fields.py` — Inspect raw Bitget V2 position record fields
- `debug_classify_ai.py` — Walk an AI classification step-by-step
- `debug_scanner_cache.py` — Inspect scanner internal cache state
- `inspect_rklb.py` — One-off diagnostic for the RKLBUSDT incident (kept for the pattern)
- `list_pending_plans.py` — Dump all Bitget pending plan orders (SL/TP) with metadata

### Remediation
- `cancel_old_plans.py` — Cancel orphaned plan orders left by old preset-SL/TP path
- `rewrite_broken_tpsl.py` — Replace bad-nudge TPs with ATR-based levels
- `fix_all_unsane_tpsl.py` — Audit every open position; fix R:R < 1:0.5
- `fix_rklb_levels.py` — One-off remediation kept for reference

### Hindsight / classification
- `audit_setup_type_classifier.py` — Compare classifier outputs vs labels
- `backfill_call_links.py` — Match orphan calls to positions; also backfills MFE/MAE
- `backfill_setup_types.py` — Re-tag setup_type on legacy rows
- `classify_one_position.py` — Run setup_classifier on a single position
- `reclassify_setup_types.py` — Bulk re-tag setup types

### Backtest / comparison
- `backtest_consensus.py` — Replay scanner→consensus on historical setups
- `compare_cascades.py` — N forced-provider runs × 12 setups → `docs/cascade_comparison.md`
- `compare_opus_sonnet.py` — Re-score latest scanner setups via Opus 4.7

### Reports / generation
- `generate_architecture_pdf.py` — Render `docs/architecture_detailed.pdf`
- `generate_architecture_pdf_id.py` — ID-only variant (less brand-specific)
- `generate_browser_report.py` — Render `scripts/browser_test_report.html`
- `generate_factsheet_pdf.py` — Render the README factsheet PNG/PDF

### Test loop
- `self_test.py` — Smoke test of core modules
- `test_futures_ai_loop.py` — End-to-end auto-trader loop validation
- `hermes-telegram-bot.py` — Hermes Telegram assistant runtime

---

## Tests (`tests/`)

76 pytest files. Run all: `python3 -m pytest tests/ -v`. Notable groups:
- `tests/conftest.py` — fixtures (in-memory SQLite via `db`, `sample_positions`)
- `tests/test_scanner_*` — scanner logic, price filter, prompts
- `tests/test_agent_*` — 7-agent pipeline contracts
- `tests/test_chart_*` — confluence engine, indicators, patterns
- `tests/test_routes_*` — API blueprint responses
- `tests/test_trading_*` — auto-trader chain (kill_switch, risk_budget, executor)
- `tests/test_security.py` — XSS, SQL injection, _safe_float
- `tests/test_consensus_*` — Claude/Gemini delta scoring

---

## Documentation (`docs/`)

| File | Purpose |
|---|---|
| `architecture.md` | Full architecture + data flow + every feature wave (1500+ lines) |
| `USER_GUIDE.md` | User-facing walkthrough by page (Dashboard, Journal, Scanner, Futures-AI, etc.) |
| `GUIDE.md` | Developer guide — older, contains some legacy info |
| `SCORING_GUIDE.md` | Per-score (1-10) breakdown of what each setup score means |
| `SOUL.md` | Hermes Telegram assistant configuration |
| `RATING_CRITERIA.md` | Initial setup quality criteria reference |
| `RESUME_PROMPT.md` | Conversation continuity prompt for Claude resume sessions |
| `feature_audit.md` | C1 audit — page/endpoint coverage, orphan triage |
| `telegram-hermes-guide.md` | Hermes setup + service commands |
| `MODULE_MAP.md` | **This file** — module index |

PDFs: `architecture_detailed.pdf`, `architecture_detailed_id.pdf`, `trading_journal_factsheet.pdf`.

---

## Quick lookup — "where does X live?"

| If you're looking for... | Start here |
|---|---|
| The auto-trader's risk math | `trading/risk_budget.py` |
| Why a setup got rejected | `futures_ai_log` table + `routes/futures_ai.py` log endpoint |
| The scanner's scoring | `chart_confluence.py` + `ai_scanner._score_finalists_with_agents` |
| A specific Bitget API call | `bitget_client.py` (read) or `trading/bitget_trader.py` (write) |
| HMM regime detection | `market_regime.py` |
| ML win-probability | `signal_scorer.py` |
| FVG / Premium-Discount / Kill Zones | `chart_fvg.py` + `chart_confluence.range_position` + `scanner_criteria._apply_kill_zone_modifier` |
| Hedge logic | `trading/hedge_manager.py` |
| RSI Mastery (regime / failure swing / divergence) | `chart_rsi.py` |
| Bear-market phase classification | `bear_phase.py` |
| Smart-flow (OI×CVD×Price quadrant) | `chart_confluence._smart_flow_weight` |
| Telegram alerts | `telegram_notify.py` (alert bot) + Hermes (interactive, separate service) |
| Schema migrations | `database.py` (`_apply` calls, currently v48) |
| Env-driven knobs | `trading/config.py` (`FUTURES_AI_*`) + `constants.py` (everything else) |

---

## What's intentionally NOT here

- Generated artifacts: `__pycache__/`, `.venv/`, `*.joblib`, `*.db`, `static/cache/`
- `.git/`, `.github/`, `.agents/`, `.remember/`
- `data/liquidations/{symbol}/` — CSV cache for the Coinalyze historical fetcher
