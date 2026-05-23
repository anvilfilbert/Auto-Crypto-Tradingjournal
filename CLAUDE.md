# Trading Journal — Claude Code Context

## Project Overview
Self-hosted crypto futures trading journal. Flask 3.1 / Python 3.13 / SQLite WAL.
Runs as a systemd service on a Raspberry Pi 5 (<Pi-IP>). Accessible from any browser on the local network.

## Deployment
- **Pi SSH:** `<user>@<Pi-IP>` (use expect — no BatchMode; credentials in local memory only)
- **Service:** `sudo systemctl restart trading-journal`
- **Pi path:** `/home/<user>/trading-journal`
- **Dev path:** local clone of this repo
- **Port:** 8082

## Database
- **Mode:** SQLite WAL — safe for concurrent reads during sync
- **Migrations:** database.py::init_db() — ALL must be idempotent
- **Tables:** positions, orders, wallet_snapshots, analyzed_calls, pending_limits, trader_rulebook, trade_hindsight, settings, import_log, token_usage, schema_version, paper_positions, futures_ai_log
- **Two-chain isolation:** `positions.chain TEXT DEFAULT 'manual'` (migration 47). Values: `'manual'` (operator + Bitget sync) or `'auto_ai'` (Futures-AI auto-trader). Per-trader queries MUST filter on this column; shared queries (scanner, indicators, macro) MUST NOT.

## Import Graph (safe edit order)
constants.py, prompt_fragments.py, trade_history.py, chart_sr.py, chart_indicators.py — no internal deps, edit freely
token_log.py — token telemetry only; imported by ai_client via helpers re-export
helpers.py, database.py — imported by everything, edit carefully
sync_base.py — SyncDriver protocol, SyncState class, auto_close_calls, retroactive_close_calls
ai_client.py — imports constants + helpers (log_token_usage re-exported from token_log)
chart_candles.py, chart_patterns.py, chart_confluence.py — split from chart_context
chart_context.py — thin facade over chart_candles + chart_patterns + chart_confluence
prompt_builder.py — imports chart_context, ai_rulebook, ai_pattern_detector, nansen_client
agent_types.py — TypedDicts + empty_interpreter/empty_sentiment/empty_reviewer factories
ai_*.py — import ai_client + prompt_builder + trade_history
scanner_watchlist.py — symbol lists; scanner_criteria.py — CRITERIA_DEFAULTS + kill-zone; scanner_prompts.py — prompt builders; scanner_stages.py — Stage 1/2
ai_scanner.py — thin: _state, scan thread, Stage 3, public API (imports scanner_* modules). Stage 3 now also runs `trade_utils.enforce_tp_floor` AND `trade_utils.enforce_sl_floor` on each agent-prepared setup so wrong-side / pathological levels are repaired upstream of the executor.
trading/ — auto-trader chain. Import order:
  trading/config.py (env knobs + state machine, no internal deps)
  trading/kill_switch.py (imports config)
  trading/risk_budget.py (imports config)
  trading/signal_consensus.py (imports ai_client + config + chart_context)
  trading/bitget_trader.py (HMAC-signed Bitget V2 write client, imports config + chart_context for ATR repair)
  trading/paper.py + trading/executor.py (import kill_switch + risk_budget + bitget_trader)
  trading/learner.py (imports ai_client + config)
  trading/orchestrator.py — driver, imports everything in trading/ + ai_scanner hooks
routes/*.py — import helpers + ai_* modules; routes/futures_ai.py exposes the auto-trader API
monitor_scheduler.py — now fetches BOTH bitget_client.get_open_positions() AND (real-mode only) trading.bitget_trader.get_open_positions(), tags each with `chain`, passes the combined list to exposure_monitor.check so concentration alerts cover both books

## AI Pipeline
- Sonnet (claude-sonnet-4-6): call analyzer, advisor, scanner, rulebook, pattern detector, grader
- Haiku (claude-haiku-4-5-20251001): scanner quick-score, hindsight, live trade check, limit analysis
- Token logging: log_token_usage(module, model, in, out, cached) — import from helpers or token_log
- Prompt caching: build_cached_messages() — ephemeral cache on context blocks >= 4096 chars
- Error fallbacks: use empty_interpreter/empty_sentiment/empty_reviewer from agent_types (not private _empty_* functions)
- **Gemini API fallback**: `ai_client.send()` catches `anthropic.APIError` → calls `gemini_client.send_text()` transparently; all modules get fallback with no per-module changes; usage logged as `{module}+gemini / gemini-fallback`
- Data pipeline: agent_data_collector → 15 parallel workers → CollectorResult → prompt_builder → Claude
- Adding a new data source: add fetch_X() to data_sources.py + field to CollectorResult in agent_types.py

## Data Sources (active, wired into 12-worker CollectorResult)
| Layer | Client | Data | Key |
|---|---|---|---|
| 1 — Global Macro | market_context.py | VIX/DXY (yfinance) | none |
| 1 — Global Macro | market_context.py | ES1! — S&P 500 Futures price + 24h change (yfinance ES=F) | none |
| 1 — Global Macro | market_context.py | Fear & Greed (alternative.me) | none |
| 1 — Global Macro | finnhub_client.py | Economic calendar — FOMC/CPI/NFP macro risk flag | FINNHUB_API_KEY |
| 1 — Global Macro | coingecko_client.py | BTC.D, ETH.D, USDT.D, OTHERS.D, TOTAL2, TOTAL3 (market_cap_percentage) | none |
| 1 — Global Macro | coingecko_client.py | MEME.C, STABLE.C, STABLE.C.D — /coins/categories | none |
| 2 — Market Structure | deribit_client.py | BTC/ETH put/call skew — institutional sentiment proxy | none |
| 2 — Market Structure | market_context.py | BTC mempool congestion (blockchain.com) | none |
| 2 — Market Structure | coingecko_client.py | Trending coins (top-10, last 24h) | none |
| 3 — Symbol-Level | ccxt_client.py + market_context.py | Multi-exchange L/S ratio + retail vs smart-money divergence | none |
| 3 — Symbol-Level | coinalyze_client.py | Aggregated OI + funding + liq trend + per-exchange funding spread | COINALYZE_API_KEY |
| 3 — Symbol-Level | liquidation_client.py | Historical liquidations per day (longs_usd/shorts_usd); CSV cache in data/liquidations/{symbol}/ | COINALYZE_API_KEY |
| 3 — Symbol-Level | coingecko_client.py | Cap rank, cap tier, 24h volume | none |
| 3 — Symbol-Level | market_context.py | DefiLlama TVL (DeFi tokens only) | none |
| 3 — Symbol-Level | chart_context.py via ccxt | OHLCV candles (Binance Futures) | none |
| 4 — Trade Intelligence | nansen_client.py | Smart money wallet flows + accumulating/distributing direction | paid |
| 4 — Trade Intelligence | grok_client.py | Social/news context per coin (cap-weighted 0-80%) | XAI_API_KEY |

See Tools → Data Sources page in the UI for the full interactive reference.

## Prompt budget order (prompt_builder.py)
1. Backtest context — most relevant to setup
2. Market context string — pre-fetched by caller
3. All data source blocks (Coinalyze, Fear&Greed, macro regime, L/S divergence, etc.)
4. Rulebook — protected until remaining < 100 chars (was 500)
5. Calibration — protected until remaining < 100 chars
6. Chart context — protected until remaining < 100 chars
7. Grok social — protected until remaining < 150 chars

## Scanner macro layer (scanner_stages.py)
- _get_scan_macro_context() called ONCE per scan: VIX, F&G, Finnhub events, BTC dominance
- _apply_macro_cap(): VIX > 35 → cap 6.0, VIX 25-35 → cap 7.5, macro event in 24h → cap 7.0
- _build_macro_header(): prepended to every Stage 3 scoring prompt — shows VIX | ES1! | F&G | BTC.D | USDT.D | STABLE.D | MEME cap
- macro_ctx stored in _state["macro_ctx"] — visible in scanner status API
- get_macro_regime() now includes ES=F (S&P 500 futures via yfinance): returns es, es_change_pct alongside vix and dxy
- get_global_market() extended: btc_dominance_pct, eth_dominance_pct, usdt_dominance_pct (USDT.D), others_dominance_pct (OTHERS.D), total2_usd (TOTAL2), total3_usd (TOTAL3)
- get_category_caps() in coingecko_client.py: calls /coins/categories → meme_cap_usd (MEME.C), stable_cap_usd, stable_dominance_pct (STABLE.C.D)

## Confluence Signals (chart_confluence.py)
15 signals total → max_per_tf = 6.35 (non-SMT) / 6.65 (SMT):
TF-level: RSI (regime-aware via `chart_rsi.summarize_rsi`),
          MACD (grouped momentum cap ±1.5), EMA, ADX,
          WaveTrend, MFI (grouped oscillator cap ±1.0),
          Stochastic (joins oscillator group),
          CVD, order_flow, volume,
          RSI failure swing ±0.4 (RSI Mastery — reversal signal),
          RSI divergence ±0.4 cap (regular for reversal, hidden for continuation),
          _smt_weight (cross-exchange price divergence ≥0.5%),
          _smt_direction_weight (24h directional divergence vs correlated pair ±0.15)
Symbol-level: liquidation wall ±0.20 (conditional — short-squeeze/cascade within 3%),
              smart-flow quadrant ±0.5/±0.2 (OI × CVD × Price, 4H window; +0.5 New Longs / +0.2 Short Covering / -0.5 New Shorts / -0.2 Long Liquidation; needs Coinalyze OI history — returns 0 when key missing)
Context tags (no direct score, surfaced in `parts` for Sonnet to read):
  - 4H range position (premium/equilibrium/discount) via `range_position()`
  - unfilled FVG count (bullish/bearish, 4H) via `chart_fvg.detect_unfilled_fvgs`
  - RSI regime (bullish/bearish/range) tag from `chart_rsi.classify_regime`

## Catastrophe Hedge (`trading/hedge_manager.py`, 2026-05-23)
Defensive BTC perpetual SHORT that opens automatically when the auto-trader
basket bleeds rapidly during a market-wide flush (the 2026-05-22 23:53
"5 simultaneous stop-out" pattern).

Trigger — ALL three must be true:
- basket unrealised P&L ≤ -3% of equity
- BTC 1h change ≤ -2%
- ≥70% of open notional is Long
- (and no active hedge already open)

Action:
- Opens 1 BTC short, notional = 50% of net long notional, leverage 3×, no SL/TP
- Position is flagged `is_hedge=1` so it doesn't count in MAX_CONCURRENT_POSITIONS,
  consec-loss breaker, or win-streak progression
- Position uses cross margin (already configured) so it offsets margin against longs

Unwind — ANY of:
- BTC recovers to within 1% of its level when hedge opened
- 2 consecutive green BTC 15m candles
- 24h elapsed (safety cap)

Config knobs (all env-tunable):
- `FUTURES_AI_HEDGE_ENABLED=1|0` — master switch (default ON)
- `HEDGE_TRIGGER_UNREAL_PCT`, `HEDGE_TRIGGER_BTC_DROP_PCT`,
  `HEDGE_TRIGGER_LONG_BIAS_PCT`, `HEDGE_RATIO`, `HEDGE_LEVERAGE`,
  `HEDGE_UNWIND_RECOVERY_PCT`, `HEDGE_MAX_DURATION_HOURS` — see config.py

Runs from `orchestrator.on_monitor_cycle()` every 10 min (real mode only).
State persisted in `settings.futures_ai_active_hedge` (JSON) so it survives
restarts. Active hedge surfaced in `kill_switch.evaluate()` snapshot as
`runtime.active_hedge`.

New events in futures_ai_log: `hedge_opened`, `hedge_closed`,
`hedge_open_failed`, `hedge_close_failed`, `hedge_skipped`.

DB migration 48: `positions.is_hedge INTEGER DEFAULT 0`.

## Bear Market Strategy (2026-05-23)
Two pieces from the bear-market framework, scoped to what makes sense
for an intraday futures auto-trader (the spot DCA / portfolio sections
were skipped — we don't hold spot):

- **Graduated drawdown dampener** (`trading.risk_budget._drawdown_dampener`):
  Risk multiplier scales DOWN as total drawdown grows, BEFORE the binary
  -15% breaker trips. 0 to -5% DD → 1.0× | -5 to -10% → 0.75× |
  -10 to -15% → 0.50× | below -15% → 0.25× (and breaker also trips).
  Applied as another multiplicand in `risk_dollars` alongside score_mult
  and streak_mult. Surfaced as `dd_dampener` + `dd_dampener_reason` in
  sizing payload.

- **Bear phase classifier** (`bear_phase.classify_phase` +
  `phase_alignment_weight`): rule-based classifier from F&G + BTC 24h +
  BTC.D + HMM regime → returns one of {distribution, decline, capitulation,
  recovery, unknown} with a Long/Short bias.
    - distribution / decline   → favor Shorts
    - capitulation / recovery  → favor Longs
  Setup direction agreeing with phase bias = +0.3 score modifier; fighting
  it = -0.3. Applied in scanner Stage 3 alongside the PO3 modifiers.
  Surfaced in setup `_bear_phase` field and the `summary` line Sonnet
  consensus reads.

## Profit Compounding Strategy (`trading/risk_budget.py`, 2026-05-23)
Streak-based progressive risk + dynamic notional cap from the trader research
Company Profit Compounding Strategy sheet. Sizing now goes:

  risk_dollars = equity × RISK_PER_TRADE_PCT × score_mult × streak_mult

- **Streak multiplier** (`_streak_multiplier`): counts consecutive winning
  auto_ai closes since last loss / breaker reset.
  - streak 0 or 1 → 1.0× (Trade 1 foundation; Trade 2 lock-and-load at base)
  - streak 2 → 2.0×  (Trade 3 — begin compounding)
  - streak N (N ≥ 2) → min(N, MAX_STREAK_MULTIPLIER)×  (capped at 3 by default)
  - Any loss → streak resets → next trade back at 1.0×
- **Dynamic notional cap**: `max(MAX_NOTIONAL_USDT, equity × MAX_NOTIONAL_PCT)`.
  Position size grows as account compounds — equity $100 → $25 cap;
  equity $200 → $50 cap; equity $500 → $125 cap. Floor of $25 protects
  small accounts.
- **Env knobs**:
  - `FUTURES_AI_COMPOUND_STREAK=1|0` — enable/disable (default ON)
  - `FUTURES_AI_MAX_STREAK_MULT=N` — cap streak multiplier (default 3)

Snapshot adds `consecutive_wins_since_reset` and `streak_multiplier`
so the Futures-AI page can show the current compounding state.

## RSI Mastery (`chart_rsi.py`, 2026-05-23)
Replaces the old static `_rsi_weight()` with regime-aware interpretation per
the trader research RSI Mastery framework:

- **Regime detection** (`classify_regime`): looks at last 20 RSI values.
  Bullish regime when avg > 55 AND 70%+ bars in 40-80 healthy zone.
  Bearish regime: mirror with 20-60. Else range.
- **Regime-aware weight** (`regime_aware_rsi_weight`):
  - Bullish regime: RSI 70 is NOT bearish (trend hot); RSI <40 IS the warning
  - Bearish regime: RSI 30 is NOT bullish (trend cold); RSI >60 IS the warning
  - Range regime: classic 30/70 logic
- **Failure swings** (`detect_failure_swing`): RSI rejected at 30/70 without
  making a new extreme; the most reliable reversal signal per the guide.
  Contributes ±0.4 to confluence.
- **Divergences** (`detect_divergences`): regular (price LL+RSI HL = reversal
  up; mirror) and hidden (price HL+RSI LL = continuation up; mirror).
  ±0.3 (regular) or ±0.2 (hidden); total capped at ±0.4.

## PO3 Score Modifiers (applied in ai_scanner Stage 3, after caps)
Direction-aware modifiers added 2026-05-23 from the trader research
"Power of 3" framework. Each can shift the setup_score by ±0.3 and runs
after the macro / personal-bad-hour / reversal caps. The personal bad-hour
cap is then re-applied as a hard ceiling so the PO3 boosts can't punch
through it.

- **Premium/Discount** (`chart_confluence.directional_range_weight`): Long
  in discount (bottom 1/3 of 40-bar swing) = +0.3; Long in premium = -0.3;
  mirror for Shorts; equilibrium = 0.
- **FVG** (`chart_fvg.nearest_fvg_signal`): same-direction unfilled FVG
  acting as support = +0.3; opposing FVG within 3% acting as resistance =
  -0.3 (can sum to ±0.0/0.3/0.6/-0.3).
- **Kill zone** (`scanner_criteria._apply_kill_zone_modifier`): Silver
  Bullet 13:30-14:30 UTC = +0.3; London 07-10 / NY AM 12-16 = +0.2;
  NY PM 18:30-21 = +0.15; Dead hour 16:30-17:30 UTC = -0.2; off-hours = 0.

Setup dict gains `_po3_range`, `_po3_fvg`, `_po3_session` fields and the
PO3 line is appended to `summary` so the Sonnet consensus call sees it.
VIX multiplier: score × 0.80 when VIX > 30 (5-min cached)
SMT_SYMBOLS = {BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT}
SMT_PAIRS = {BTC↔ETH, SOL→ETH, BNB→BTC, XRP→BTC}

## Testing
- Framework: pytest
- Tests in tests/ directory
- Run: python3 -m pytest tests/ -v
- Fixtures: tests/conftest.py — db (in-memory SQLite), sample_positions

## API Rules
- All routes return {"ok": true/false, "data": ...} via _ok() / _err()
- Never expose exception messages in API responses (CWE-209)
- Never change existing endpoint URLs or response shapes
- Use _safe_float(val) in routes/calls.py for parsing price fields from request JSON
- Validate status fields against VALID_STATUSES allowlist before DB writes (see routes/limits.py)

## Deployment (IMPORTANT)
- **Never rsync `*.db` or `.env` files to Pi** — both contain Pi-only state that local doesn't have.
- The Pi's `.env` carries auto-trader credentials that the Mac never has: `FUTURES_AI_ENABLED`, `FUTURES_AI_MODE`, `FUTURES_AI_STARTING_EQUITY`, `BITGET_TRADER_API_KEY` / `_SECRET_KEY` / `_PASSPHRASE`. Overwriting `.env` silently disables the auto-trader.
- **Always invoke rsync via a bash wrapper, never directly from `expect spawn`.** Tcl word-splitting strips the quotes around `--exclude="*.db"` style args and the pattern silently fails to match — verified 2026-05-23 incident.
- Canonical exclude block (use ALL of these):
  ```bash
  rsync -avz \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='.env' --exclude='.env.*' \
    --exclude='.agents' --exclude='.git' --exclude='__pycache__' \
    --exclude='.venv' --exclude='*.joblib' --exclude='.remember' \
    <local>/ fbauer@<Pi-IP>:/home/<user>/trading-journal/
  ```
- Before any real deploy: dry-run with `-n` and grep the file list for `\.env|trading_journal\.db` — both must be absent.
- Always backup before restart: `bash ~/trading-journal/scripts/backup_db.sh`
- Backups auto-run via ExecStopPost on every systemctl stop/restart (7-day rolling, in backups/)
- Daily cron backup at 04:00 Pi time
- Restore procedure: stop service → cp backups/trading_journal_YYYYMMDD_HHMMSS.db trading_journal.db → start service

## Browser Verification (major UI changes only)

Run when a deploy touches `static/js/*.js`, `templates/*.html`, or adds new UI components.
**Skip** for backend-only deploys, migrations, config changes, and bug fixes.

### Starting a browser test session
```bash
open -a 'Google Chrome' --args \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/claude-chrome-debug \
  'http://192.168.1.21:8082'
sleep 3
```

### Running the test sequence
Read `scripts/browser_test_sequence.json` and execute each phase using chrome-devtools-mcp tools.

**CRITICAL — SPA navigation:** This app uses `showPage('name')` — there is NO URL hash routing.
Tab navigation must use `evaluate_script`, not `navigate_page` to a different URL:
```js
// Switch to dashboard tab:
evaluate_script("showPage('dashboard')")
// Then wait for section to become active:
wait_for("#page-dashboard.active")
```

**Per tab:** `evaluate_script("showPage(name)")` → `wait_for("#page-{name}.active")` → `take_screenshot` → `list_console_messages` → `evaluate_script("document.querySelectorAll('*').length")` (FAIL if < 20)

**Lighthouse:** `lighthouse_audit` on the 4 pages in `phase2_lighthouse`. Thresholds in the JSON.

**Interactions:** navigate to tab → `click` selector → `wait_for` pass selector → record elapsed.

### Generating the report
Collect all results into the JSON shape defined in `scripts/generate_browser_report.py`, save as `/tmp/browser_test_results.json`, then:
```bash
python3 scripts/generate_browser_report.py /tmp/browser_test_results.json
```
Report saved to `scripts/browser_test_report.html`.

### Triage rules
- **FAIL** (console error, DOM < 20 nodes, 5xx, a11y < 80, perf < 50): fix inline, re-test tab, verify clean.
- **WARN obvious** (missing aria-label, button without type): one-liner fix, no re-test.
- **WARN complex** (perf regression, heading hierarchy): append to `scripts/browser_issues.md`.

### Commit when clean
```bash
git add scripts/browser_test_report.html
git commit -m "test: browser check clean — vX.Y.Z"
```

### Full scan (Phase 4 — one-time baseline or after major UI overhaul)
After standard Phases 1–3 pass, run `phase4_full_scan` from the JSON.
Use after: new tab added, major component redesign, or v2.x milestone.

## Calculation Invariants (do not change without updating both sides)
- WaveTrend: n1=10, n2=21, rolling(4) — must match in both chart_indicators.py AND backtest_engine.py
- CVD: Money Flow Multiplier formula v*(2c-l-h)/(h-l) — must match in both chart_indicators.py AND backtest_engine.py
- Sharpe annualization: periods_per_year=2190 for 4H crypto (6 bars/day × 365, 24/7 market)
- SMT weight: +0.15 on divergence (delta >= 0.5%), 0.0 on agreement — signal fires when prices DISAGREE
- SMT direction weight: +0.15 bullish (symbol↑ pair↓), -0.15 bearish (symbol↓ pair↑), threshold ≥1% delta
- Walk-forward split: 70% training / 30% test; end_offset_days prevents data leakage (training ends at now-test_days)
- Sharpe (dashboard): sample variance (N-1 denominator), daily returns, annualize × sqrt(365)
- Calmar (dashboard): max_dd_pct tracked as % of running peak at each step (NOT final all-time peak)
- Wallet snapshot filter: wallet_balance > 1 USDT — excludes dust/zero entries that corrupt return series

## New Tools (Analysis tab)
- Optimizer history: GET /api/backtest/optimizer-history — last 5 runs with Sharpe + params
- Walk-forward test: POST /api/backtest/walk-forward — splits real positions 70/30, tests generalization
- Walk-forward poll: GET /api/backtest/walk-forward/<job_id> — dedicated poll endpoint (not /optimize/)
- Hindsight re-run: POST /api/hindsight/run?n=200 — skips already-scored positions (LEFT JOIN fix), max 200

## Market & Analytics API (routes/market.py, routes/analytics.py)
- GET /api/price/<symbol> — live price via Binance, Bitget fallback
- GET /api/coin/summary/<symbol> — price + 4H/1H indicators (RSI, EMA, WaveTrend, ADX, ATR) + Nansen + Coinalyze + BTC regime + F&G + liquidations_14d
- GET /api/market/dominances — full dashboard: BTC.D, ETH.D, USDT.D, OTHERS.D, TOTAL2, TOTAL3, MEME.C, STABLE.C, STABLE.C.D, ES1! price+change
- GET /api/chart/annotated/<symbol> — on-demand annotated chart PNG (base64); params: direction, entry, entry_high, sl, tp1, tp2, tf
- GET /api/liquidations/<symbol>?days=30 — Coinalyze historical liquidation data (longs_usd, shorts_usd per day); available=false when plan doesn't include it
- POST /api/scanner/cancel — cancel running scan; sets _cancel_event in ai_scanner.py; status becomes "cancelled"

## Data Sources page
- Tools → Data Sources in left nav — lists all 14 sources grouped by macro→micro layer
- Shows: provider, auth requirement, inputs, data returned, pipeline usage

## JS Frontend
- 17 modules static/js/01-utils.js through 16-settings.js
- Bump ?v=X.X in templates/index.html on every JS change
- notify(msg, type) toast function in 01-utils.js

## Scanner Stage 3 — HTF→LTF Breakdown
- `enrich_finalists_1h()` in scanner_stages.py: fetches 1H candles via `compute_indicators()` + `format_for_prompt()` — adds S/R levels + prompt_text to ctx["1H"] for all 30 finalists before Stage 3
- Stage 3 prompts (_build_prompt, _build_batch_prompt, _quick_score) include 1H data and explicit instruction: **1D bias → 4H confirmation → 1H entry/SL → 4H/1D TP**
- Scored output reports `"timeframe": "Multi-TF (1D/4H/1H)"`
- Rationale: closed 4H bars can be up to 4h stale; 1H cuts max staleness to 1h for entry/SL precision

## Chart System

### Static Annotated Chart (agent_chart_draw.py)
Generated as base64 PNG for Telegram alerts + pending limit cards.

```python
agent_chart_draw.draw(
    candles, symbol, direction,
    entry,          # entry zone low (or single entry)
    sl, tp1, tp2,
    criteria=[],    # key_conditions list → top-right text box
    n_candles=60,
    entry_high=None,   # entry zone high → shaded blue band
    sr_levels=[],      # list of {type, price, touches, strength}
)
```

**Visual elements:**
- `▲ LONG` / `▼ SHORT` colored badge (top-left, green/red)
- Entry zone: shaded blue band between entry and entry_high
- SL = red dashed, TP1 = bright green `#26D96B`, TP2 = cyan `#4FC3F7`
- Price labels on right edge of every level line
- S&R zones: green (support) / red (resistance), alpha scales with touches
- Confluence zones: adjacent levels within 0.3% merged into wider band (`_merge_sr()`)
- At-level highlight: zones within 0.5% of current price get brighter fill + border + ⚡ label
- ATR-based zone width: `ATR × 0.15` half-width for singleton zones (F)
- `scanner_scheduler._enrich_and_filter_setups()` passes `entry_zone.low/high`, `key_conditions`, `detect_support_resistance(candles)` as `sr_levels`

### Live Chart Popup (templates/chart.html — LightweightCharts)
- Direction badge chip: `▲ LONG` (green) / `▼ SHORT` (red) shown first in legend
- Trade levels: Entry (blue, solid), SL (red, dashed), TP1 (green, solid), TP2 (cyan, solid) — all with axis labels
- S&R canvas overlay: green support / red resistance (was uniform grey)
- `_mergeSrLevels()`: JS confluence merge (within 0.3%), `_computeAtr()`: ATR from candle array for zone width
- `_startOverlay(wrap, series, mergedLvls, htf_levels, liquidations, atr)`: uses ATR × 0.15 for singleton zone half-width
- At-level zones: 2px border rect + brighter fill + `⚡AT LEVEL` chip in legend
- Weekly S&R stays gold, unchanged
- **? Legend panel** — `#btn-info` button toggles `#legend-info` div (static HTML, no innerHTML); 7 sections explain every abbreviation with color-coded indicators; CSS classes: `.li-head/.li-row/.li-line/.li-box/.li-spacer/.li-lbl/.li-desc`

## Pending Limit Orders (routes/limits.py + static/js/10-pending.js)

### AI Verdict Display (bug fixed)
- `analysis_json` from `ai_limit.py` uses fields: `recommendation`, `setup_quality.score`, `risk_assessment`
- Old code read `verdict`/`setup_score`/`confidence` → showed "undefined". Fixed in 10-pending.js v3.0+
- Color: Keep=green, Cancel=red, Adjust*=yellow

### Bitget Preset SL/TP Sync
- `bitget_client.get_pending_orders()` now parses `presetStopLossPrice` → `preset_sl`, `presetTakeProfitPrice` → `preset_tp`
- `GET /api/live/pending-orders` backfills `sl_price`/`tp1_price` on journal limit rows where NULL, matched by `bitget_order_id`

### Scanner Chart in Limit Card
- `GET /api/limits` JOINs `analyzed_calls.analysis_json` for rows with a `call_id` and extracts `chart_png_b64`
- Pending limit card renders the chart as inline `<img>` below the AI verdict
- Chart container uses `display:block` so image fills full card width
- `↗ Pop Out` button overlaid top-right on chart image; opens `chart.html` popup
- If `analysis_json.summary` starts with `{` (truncated Gemini JSON): tries to extract `entry_reason`; on parse failure shows `⚠ Analysis was truncated — click AI Analysis to retry.`
- `ai_limit.py` max_tokens: 768 → 1024 (Gemini fallback verbosity)

### Delete Route
- `DELETE /api/limits/<id>` now has try/except → returns JSON error instead of HTML 500

### Scanner Timeframe Normalization (14-scanner.js)
- `setup.timeframe` may be `"Multi-TF (1D/4H/1H)"` display label — normalised against `_VALID_TF` Set before chart URL
- Prevents "no candle data" errors on scanner chart popups (Bitget rejects invalid granularity strings)

## Live Trade Analysis (agent_trade_monitor.py)

### Direction-Aware Prompt (bug fixed)
Position dict from `bitget_client.get_open_positions()` uses `direction` / `entry_price` / `mark_price` — the prompt was reading `side` / `openPrice` / `markPrice` (all wrong, all defaulted). Fixed field names.

For Short positions, the prompt now injects a `DIRECTION CONTEXT — SHORT` block:
- Bearish momentum = **favorable** (price moving toward TP)
- SL must be **above** entry (not below)
- TP is **below** entry
- Hard rule: "never swap SL/TP placement for Short positions"

## Hermes Agent (interactive Telegram assistant)
Separate from the scanner alert bot. Two-bot setup:
- **Alert bot** (`TELEGRAM_BOT_TOKEN` in journal .env): one-way push from `telegram_notify.py`
- **Hermes bot** (`~/.hermes/.env`): two-way interactive chat for querying the journal

Hermes runs as a user systemd service (`hermes-gateway.service`) on the Pi, configured to query the journal API at `http://localhost:8082`. SOUL.md documents all key endpoints + response style. MEMORY.md seeds trader profile.

Service commands:
```bash
hermes gateway status
hermes gateway start / stop
journalctl --user -u hermes-gateway -f
```

## Futures-AI Auto-Trader (`trading/` package)

Lives on its own Bitget subaccount (auto-trader subaccount). Activated by setting `FUTURES_AI_ENABLED=1` and `FUTURES_AI_MODE=real` in the Pi `.env`. Below env-level the runtime state (`active` / `pause_after_close` / `pause_now` / `circuit_breaker`) persists in `settings.futures_ai_state` and is toggled by the UI / Telegram. The Futures-AI page is at `/?#futuresai`.

### Pipeline
`ai_scanner.on_scan_completed` → `trading.orchestrator.process_setups` → for each setup: `kill_switch.can_open_new_trade(conn, scanner_score)` → `signal_consensus.evaluate` (if score ≥ CONSENSUS_MIN_SCORE) → `risk_budget.size_trade` → `executor.open_real_trade` (or `paper.open_paper_trade`). 10-min `on_monitor_cycle` reconciles via `bitget_trader.get_position_history` last 24h.

### Risk caps (config.py constants — change requires code + redeploy)
- `RISK_PER_TRADE_PCT = 0.02` (Kelly-scaled by score: 7→1.0×, 8→1.5×, 9→2.0×, 10→2.0×)
- `MAX_NOTIONAL_USDT = 25.0`
- `MAX_LEVERAGE = 10`
- `MAX_CONCURRENT_POSITIONS = 5` (soft cap)
- `MAX_ELITE_POSITIONS = 7` + `ELITE_BYPASS_SCORE = 10` — scanner-verified 10/10 bypasses the soft cap up to the hard cap. Bounded so 7 × 2% = 14% < -15% total-DD breaker.
- `DAILY_DD_BREAKER_PCT = -0.05`, `TOTAL_DD_BREAKER_PCT = -0.15`, `CONSECUTIVE_LOSS_BREAKER = 3`

### Env-driven knobs (Pi `.env`)
- `FUTURES_AI_ENABLED=0|1` — env-level kill switch
- `FUTURES_AI_MODE=paper|real`
- `FUTURES_AI_STARTING_EQUITY=100` — fallback if Bitget balance call fails
- `FUTURES_AI_CONSENSUS_MIN_SCORE=8` — Sonnet skipped below this (budget knob)
- `FUTURES_AI_CONSENSUS_MODEL=sonnet|haiku`
- `BITGET_TRADER_API_KEY` / `BITGET_TRADER_SECRET_KEY` / `BITGET_TRADER_PASSPHRASE` — subaccount creds (NOT the main `BITGET_*` keys)

### Bitget V2 quirks (`trading/bitget_trader.py`)
- SL/TP do NOT live on the position record — they're separate plan orders. Read via `/api/v2/mix/order/orders-plan-pending`, NOT the position object. `bitget_trader.get_open_positions()` enriches positions with their plan-order SL/TP automatically.
- Set leverage BEFORE place-order — `set-leverage` returns 40037 "already at Nx" when no change is needed; the trader treats this as success.
- The actual leverage Bitget records may differ from the requested value when the symbol has a higher minimum leverage. `place_market_order` queries the position post-fill and returns `leverage_requested` + `leverage_actual` + `set_leverage_result`. Mismatch logged as `lev_mismatch` in `futures_ai_log`.
- Tick-size snapping required — Bitget rejects prices not on the symbol's `pricePlace` decimal grid (40430). The trader caches per-symbol specs.
- SL/TP wrong-side or pathological → ATR repair: SL = entry ∓ 1× ATR_4H, TP = entry ± 2× ATR_4H. Mirrored in `scripts/fix_all_unsane_tpsl.py` for retroactive cleanup.

### Two-chain query patterns
```sql
-- Operator's book (manual + Bitget sync)
WHERE chain='manual'
-- Auto-trader's book (auto-trader subaccount)
WHERE chain='auto_ai'
```
Tables that respect chain: `positions`, `trade_hindsight`, `trader_rulebook`, `futures_ai_log`. Tables that are shared (no chain column): `analyzed_calls`, scanner state, indicators, `wallet_snapshots` (main account only), `pending_limits`.

### Decision log (`futures_ai_log`)
Every accept/reject lands here with `(ts, event, payload_json)`. Events: `state_change`, `rejected_killswitch`, `rejected_consensus`, `rejected_sizing`, `consensus_skipped`, `consensus_approved`, `paper_open`, `real_open`, `lev_mismatch`, `paper_close`, `real_close`, `breaker_tripped`. The Futures-AI page reads this for the "Recent decisions" panel.
