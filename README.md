# Trading Journal

> **Disclaimer:** Vibe-coded with [Claude Code](https://claude.ai/code). Not reviewed by professional security experts. Use at your own risk.

> **Disclaimer: Automatic AI trading**
> I recommend to use  paper trading (integrated feature; no real money), to get a good knowledge about scoring decisions and workflows. Risk (if hit at hard SL) ist hard-coded: max. 1% of port-size on single entry, max. 2% of port-size if DCA strategy is activated.
> **This tool was made for learning purposes. If you use the trading feature with real money - your decision...**

Self-hosted crypto futures trading journal with live exchange sync, a 7-agent AI pipeline, interactive Telegram assistant, and deep performance analytics. Runs on a Raspberry Pi 5 (or any Linux box).
<br>

<p align="center">
  <a href="docs/architecture.md">Architecture Doc</a> ·
  <a href="docs/USER_GUIDE.md">User Guide</a> ·
  <a href="docs/MODULE_MAP.md">Module Map</a>
</p>

---

## 🎬 Feature Tour

The autonomous Bitget chain — equity, breakers, open positions, decision log, and the catastrophe-hedge state all in one screen:

<p align="center">
  <img src="docs/images/showcase/01-futures-ai.png" alt="Futures-AI auto-trader page" width="900">
</p>

<table>
<tr>
<td width="50%" valign="top">

### 🔭 Setup Scanner

Three-stage pipeline filters 300+ symbols every 30 min. Surfaces only high-conviction setups (score ≥ 7) with R:R, urgency, and the confluence reasoning that fired each pick.

<img src="docs/images/showcase/02-scanner.png" alt="Setup Scanner" width="100%">

</td>
<td width="50%" valign="top">

### 🛡 Risk Dashboard

Institutional-grade portfolio metrics — VaR, position correlation, P&L attribution vs BTC benchmark, and Kelly-criterion bet sizing. All computed from free Binance public data.

<img src="docs/images/showcase/03-risk.png" alt="Risk Dashboard" width="100%">

</td>
</tr>
<tr>
<td colspan="2" valign="top">

### 📖 Trade Journal

Full history across both exchanges with rich filters: search · symbol · direction · result · setup-type · date range. Click any row to add notes, tags, or grade the execution.

<img src="docs/images/showcase/04-journal.png" alt="Trade Journal" width="100%">

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧠 AI Architecture

7-agent pipeline + 5-provider fallback cascade + model-to-task routing. **Opus 4.7 consensus gate** for real-money trades, **multi-TP ladders** (3-7 tiers), notional-aware slicing, and **shadow logs** that snapshot every decision for cohort analysis.

<img src="docs/images/showcase/06-ai-architecture.png" alt="AI Architecture docs page" width="100%">

</td>
<td width="50%" valign="top">

### 💱 DODEX Integration (preparation)

Persistent reminder + documentation hub for the planned [Acki Nacki / DODEX](https://www.dex.do/) dark-order DEX module. Phase tracker, locked-in decisions, installed components (Node sidecar probe + upstream watcher), open questions, and cross-links to the canonical references in [`docs/dodex/`](docs/dodex/).

<img src="docs/images/showcase/05-dodex.png" alt="Acki Nacki / DODEX Integration page" width="100%">

</td>
</tr>
</table>

<details>
<summary><b>📊 What else is in the box</b> (click to expand)</summary>

- **Dashboard** — KPIs, monthly target tracker, equity curve, wallet history, current streak, recent trades
- **Deep Dive Analytics** — breakdown by symbol / month / hour / setup type / **skill cohort** (consensus model · bear phase · archetype · PO3 stacking · TP-count)
- **Edge Lab** — setup-type analysis, execution-grade impact, AI pattern detector, planned-vs-realized R:R
- **Chart Explorer** — LightweightCharts popup with S/R overlay, WaveTrend pane, at-level highlights, FVG zones, **multi-TP ladder** rendering (one line per Bitget plan order)
- **Call Analyzer** — paste an analyst call → instant 7-agent AI scoring + annotated chart
- **Hindsight Analysis** — retroactive trade re-scoring with full chart context at entry time
- **Pending Limit Orders** — pop-out chart per order + AI verdict with retry hint on truncated responses
- **Live Sync** — Bitget USDT-M + Blofin every 5 min; CSV import for historical
- **Docs section** — three dedicated reference pages: Data Sources, AI Architecture, Scanner Pipeline, plus the new Acki Nacki — DoDEX prep page
- **Settings** — token-usage dashboard with per-module spend, prompt-cache hit rate, runway estimate
- **Topbar scan timer** — countdown to next scan / stopwatch while running, next to the dual clock

> Numbers shown in screenshots are illustrative — wired to local demo data, not real account state.

</details>

---

## Features

- **Trade Journal** — Bitget USDT-M + Blofin sync (5 min cadence), CSV import, per-trade notes/tags/setup type
- **Dashboard & Analytics** — P&L, win rate, Sharpe, Calmar, drawdown overlay, Deep Dive breakdown by symbol/month/hour/setup
- **7-Agent AI Pipeline** — DataCollector → Interpreter → Sentiment → Reviewer → RiskMgmt → TradePrep → TradeMonitor; typed contracts, parallel fetch
- **Setup Scanner** — 100+ USDT-M symbols, 3-stage pipeline (confluence → quality gate → Haiku/Sonnet), HTF→LTF (1D/4H/1H) breakdown, Telegram alerts with annotated chart, cancel button
- **Annotated Charts** — mplfinance PNG: entry zone band, S/R zones (A-F, color-coded), direction badge, TP1/TP2 colors, ATR-based width, confluence merging
- **Live Chart Popup** — LightweightCharts with direction badge, S/R overlay, WaveTrend pane, at-level highlights
- **Dominance Dashboard** — BTC.D, ETH.D, USDT.D, OTHERS.D, TOTAL2, TOTAL3, MEME.C, STABLE.C.D, ES1! via `/api/market/dominances`
- **Backtester + Optimizer** — vectorized 4H backtest, Optuna Bayesian optimizer, walk-forward validation
- **AI Learning** — personalised rulebook, hindsight scoring, token usage dashboard, prompt caching (40-60% savings)
- **Hermes Bot** — interactive Telegram assistant (separate from alert bot); queries journal API, sends charts, runs scans, tracks behavioral stats
- **12-Signal Confluence Engine** — liquidation cluster walls (11th signal), order flow delta/divergence (12th signal); HMM 3-state regime detection (trending/ranging/volatile) injected into every AI prompt
- **On-Chain Metrics** — MVRV, exchange net-flow via CoinMetrics Community API (keyless); injected as macro context
- **ML Win-Probability Scorer** — XGBoost trained on historical outcomes; predicts win probability per setup, injected into prompts after 20+ labeled trades
- **Backtesting Quality** — PBO (Probability of Backtest Overfitting), Deflated Sharpe, Bootstrap CI via `POST /api/backtest/quality`
- **Structured AI Rubrics** — 6-section technical analyst template (TREND/MOMENTUM/STRUCTURE/SIGNAL COUNT/BIAS/CONFIDENCE) + explicit risk decision table in agent_trade_prep.py
- **Browser Accessibility Baseline** — 16/16 tabs clean, 4/4 pages 100% accessibility score, 42 aria-label fixes across all form inputs
- **Gemini AI Fallback** — `ai_client.send()` transparently retries on any Anthropic API error; all 10+ AI modules get fallback with no code changes required
- **Chart Legend Panel** — `?` button in every chart popup opens a toggleable reference panel explaining all abbreviations (S/R, Fib, WaveTrend, liquidation, trendlines, etc.) with color-coded visual indicators
- **Scanner Stale-Alert Guard** — 4-layer price proximity filter drops setups where entry is missing, >20% from current price, >5% directional drift, or price fetch fails; fixes false Telegram alerts on stale scanner setups
- **Pending Orders UX** — pop-out button on chart thumbnail opens full interactive chart; AI verdict JSON display fix with retry hint on truncated Gemini responses
- **Futures-AI Auto-Trader** — autonomous Bitget chain (separate subaccount) consuming scanner output. Pipeline: scanner → Sonnet consensus → kill-switch → Kelly-scaled sizing → live Bitget order with ATR-based SL/TP plan orders → BE/Trail/MAE lifecycle → post-trade reflection. Two-chain DB isolation (`positions.chain = 'manual' | 'auto_ai'`) keeps manual hindsight/rulebook/learnings separate from AI ones while sharing market data, scanner, and baselines.
- **Auto-Trader Risk Envelope** — 2% risk-per-trade (Kelly-scaled 1.0×/1.5×/2.0× by score), $25 notional cap, 10× max leverage, 5 concurrent positions (soft cap), 7 hard cap when a scanner-verified 10/10 setup unlocks the elite-bypass slot. Circuit breakers: -5% daily DD, -15% total DD, 3 consecutive losses. All decisions land in `futures_ai_log` with full audit trail.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13 / Flask 3.1 / SQLite WAL |
| Frontend | Vanilla JS SPA (17 modules, no build step) |
| Charts | LightweightCharts v4.1.3 + mplfinance |
| AI — analysis | Claude Sonnet 4.6 |
| AI — fast scoring | Claude Haiku 4.5 |
| AI — consensus | Google Gemini 2.0 Flash |
| AI — social | xAI Grok (X/Twitter sentiment) |
| On-chain | Nansen.ai smart money |
| Market data | Binance · Bitget · Bybit · OKX · Coinalyze · CoinGecko · yfinance |
| ML / Regime | `hmmlearn` · `scikit-learn` · `xgboost` · `joblib` |
| Alerts | Telegram Bot API |
| Host | Raspberry Pi 5 / systemd |

---

## Key Modules

- `ai_scanner.py` + `scanner_stages.py` — 3-stage scanner with cancel event, macro cap, HTF→LTF
- `agent_chart_draw.py` — annotated PNG with entry zone, S/R bands, direction badge
- `liquidation_client.py` — Coinalyze historical liquidations, CSV cache in `data/liquidations/`
- `coingecko_client.py` — dominance indexes (TOTAL2/3, USDT.D, OTHERS.D, MEME.C, STABLE.C.D)
- `market_context.py` — macro regime: VIX, DXY, ES1!, F&G, BTC regime
- `liquidation_levels.py` — CCXT-based forced liquidation cluster detection (TTL-cached)
- `onchain_client.py` — CoinMetrics Community MVRV + exchange flow
- `market_regime.py` — GaussianHMM 3-state regime classifier on BTC 4H data
- `signal_scorer.py` — XGBoost win-probability from historical analyzed_calls
- `backtest_quality.py` — PBO + Deflated Sharpe + Bootstrap CI (Bailey et al. 2014)
- Hermes agent — `~/.hermes/` on Pi; `hermes-gateway.service` (user systemd)
- `trading/` package — auto-trader chain:
  - `trading/config.py` — knobs (env-driven) + runtime state machine (active / pause_after_close / pause_now / circuit_breaker)
  - `trading/orchestrator.py` — scan-hook + monitor-tick driver wiring kill_switch → consensus → sizing → dispatch
  - `trading/kill_switch.py` — capital-preservation gate; daily/total DD, consec-loss, soft+elite concurrent caps, state machine
  - `trading/signal_consensus.py` — Sonnet second-opinion (`consensus_score = min(scanner, ai)`)
  - `trading/risk_budget.py` — Kelly-scaled sizing × win-streak compounding × drawdown dampener (dynamic notional cap)
  - `trading/bitget_trader.py` — V2 REST write client (HMAC-SHA256, tick-size snapping, ATR-based SL/TP repair, plan-order attach, cross margin mode)
  - `trading/hedge_manager.py` — catastrophe BTC-short hedge during basket-flush events
  - `trading/executor.py` — real-mode order placement + Bitget history reconciliation
  - `trading/paper.py` — paper-mode simulator (price-walk fills, identical accounting)
  - `trading/learner.py` — Sonnet post-trade reflection feeding rulebook
- `chart_fvg.py` — Fair Value Gap detection (3-candle imbalance, unfilled detection)
- `chart_rsi.py` — RSI Mastery: regime-aware weighting + failure swings + regular/hidden divergences
- `bear_phase.py` — Bear-market phase classifier (distribution/decline/capitulation/recovery) with directional bias

---

## Recent Additions

**2026-05-23 / 2026-05-24 sprint (single-day, ~20 commits):**

- **Opus 4.7 consensus gate** — futures-ai consensus model switched from Sonnet → Opus (`FUTURES_AI_CONSENSUS_MODEL=opus`) after a hindsight pass found Opus correctly approved 5/5 score-6 setups that Sonnet rejected (4 of 5 hit TP). Sub-agents (sentiment/reviewer = Haiku) stay on their cheaper models — only TradePrep upgrades. Cost: +$40/mo at ~26 consensus calls/day. (commit `4e84922`)
- **Multi-TP ladder (Phase 1)** — Opus can now emit 3-7 TPs per setup. New `positions.tp_levels` JSON column stores the full ladder; `agent_chart_draw` renders all levels colour-coded. **Default: 3 TPs (40/40/20 split)**. Orchestrator synthesises TP3 from TP1→TP2 gap when Opus omits the ladder so every approved trade shows ≥3 TPs. Notional-aware clamping (smallest slice ≥ $5 Bitget min). (commits `72e9105`, `b015afd`)
- **Skill provenance tagging** — 6 new `positions` columns (`consensus_model_used`, `bear_phase_at_open`, `archetype_at_open`, `po3_total`, `opus_had_overrides`, `tp_levels_count`) populated at trade-open. 6 new analytics aggregations + AI Advisor prompt extension — Advisor can now cite *skills* (e.g. *"low-conviction archetype: 0% WR over 7 trades — stop accepting"*) not just symbols/hours. (commit `73e1ea1`)
- **Shadow logs on every consensus event** — `consensus_rejected` / `consensus_approved` / orchestrator rejections embed the full setup snapshot (entry/SL/TP/scanner_score/ai_score/po3_*/bear_phase/rationale). Lets hindsight cohort analysis run without joining against the lossy `analyzed_calls` dedup. (commit `7b435fd`)
- **BE fee+slippage buffer** — break-even SL placed at entry × (1 + 0.15%) for Long / × (1 − 0.15%) for Short — covers Bitget's 0.12% round-trip taker fee + ~0.03% slippage. Hitting a BE SL now nets ≥ $0 instead of locking a sub-cent loss. New `BE_stop` close_reason categorises these correctly. Live-entry source + 0.05% epsilon guard prevent spurious double-fires from tick-rounding mismatch. (commits `2444bba`, `e333295`)
- **Live chart multi-TP** — `bitget_client.get_open_positions()` now fetches plan orders + groups by position so the live `/chart` popup renders **every TP plan order** Bitget knows about (HYPE et al). Fallback to single `takeProfit` when no ladder exists. (commit `87072d9`)
- **Fibonacci role-based palette** — anchors white, shallow retracements blue, equilibrium gold, golden pocket orange, OTE 0.66 red, last-defence 0.786 deep red, extensions green-family. Key entry zones thicker. Per-level color/weight/dash carried on each fib JSON entry. (commit `6d9b5f2`)
- **Watchlist 500-cap + event-driven mini-scans** — chosen as the cheap alternative to a 15-min cadence (+87% cost). Watchlist expanded 304 → 500 symbols (env-tunable). New `scanner_event_trigger.py` daemon polls BTC + ETH 15m and force-scans when either moves ≥2% (30-min cooldown). Combined cost impact +$15/mo (5.6%) vs +$235/mo for halving the cadence. (commit `5de0571`)
- **Scan-timer pill in topbar** — countdown to next scheduled scan / stopwatch while running. Auto-switches mode based on `/api/scanner/status`. (commit `25f542a`)
- **DODEX / Acki Nacki Phase 0+1 prep** — 7 knowledge docs in [`docs/dodex/`](docs/dodex/), Phase 1 read-only Node sidecar probe installed on Pi (not active), upstream watcher script for protocol change detection, new in-app nav section + dashboard page, `dodex-research` skill for cheap session re-entry. (commits `722ebc6`, `64cd697`)

**Earlier this week:**

- **12-signal confluence engine** — order flow delta (12th signal), liquidation wall (11th signal)
- **HMM market regime** — 3-state GaussianHMM (trending/ranging/volatile), injected into every prompt
- **On-chain: MVRV, exchange net-flow** — CoinMetrics Community API, keyless, macro context block
- **ML win-probability scorer** — XGBoost, activates after 20 labeled outcomes
- **Backtest quality** — PBO, Deflated Sharpe, Bootstrap CI (`POST /api/backtest/quality`)
- **Structured agent prompts** — 6-section analyst template + risk decision rubric in agent_trade_prep.py
- **Browser baseline** — 16/16 tabs clean, 42 aria-label fixes, 4/4 pages 100% accessibility score
- **467 tests** — up from 351 at v1.5.0; +25 since v1.6.0 (Gemini fallback + scanner price filter)
- **Futures-AI auto-trader (2026-05-22)** — live on a dedicated Bitget auto-trader subaccount, real-mode trading at 2% risk/trade. New `trading/` package, two-chain DB (`positions.chain`), AI-opened trades surfaced on the Futures-AI page.
- **Elite-setup bypass (2026-05-23)** — scanner-verified 10/10 setups bypass the 5-position soft cap up to a 7-position hard cap, so the rarest signals are never missed. Hard cap is bounded by the -15% total-DD breaker (7 × 2% risk = 14% if every stop fires simultaneously).
- **Scanner SL floor (2026-05-23)** — `trade_utils.enforce_sl_floor` repairs wrong-side / too-tight / too-wide SLs upstream so the journal records sane levels without relying on the executor's last-mile ATR repair.
- **Cross-chain exposure monitor (2026-05-23)** — `monitor_scheduler` now feeds combined manual + auto_ai positions to `exposure_monitor.check`, so sector clustering and directional-overload alerts cover the operator's full book.
- **Leverage logging (2026-05-23)** — `bitget_trader.place_market_order` returns `leverage_requested` / `leverage_actual` / `set_leverage_result`; mismatches surface as `lev_mismatch` events in `futures_ai_log` for audit.
- **Operator-activate clears breaker (2026-05-23)** — clicking ▶ Activate from circuit_breaker stamps `breaker_reset_at`; killswitch history checks honor the stamp so past losses are forgiven for breaker purposes (new losses post-reset still re-trip).
- **Cross margin mode (2026-05-23)** — new positions open in cross margin, enabling future hedging where offsetting positions reduce required margin instead of doubling it.
- **Consensus rationale logging (2026-05-23)** — `consensus_rejected` events now include Sonnet's `ai_summary` + top warnings so the operator can see WHY a setup was killed, not just that it was.
- **Smart-flow quadrant signal (2026-05-23)** — OI × CVD × Price 4-quadrant classification per trader research sheet: ±0.5 / ±0.2 confluence weight differentiating new longs from short covering, etc.
- **PO3 framework: FVG + Premium/Discount + Kill Zones (2026-05-23)** — Fair Value Gap detection as a 13th signal; range-position score modifier (Long in discount/premium = ±0.3); institutional session timing (Silver Bullet +0.3, NY AM/London +0.2, dead hour -0.2).
- **RSI Mastery (2026-05-23)** — regime-aware RSI weighting (RSI 70 isn't bearish in a bullish regime), failure swing detection (most reliable reversal signal), regular + hidden divergence detection.
- **Profit Compounding Strategy (2026-05-23)** — streak-based risk progression: consecutive wins multiply risk (capped at 3×, resets on loss); dynamic notional cap = max($25, equity × 25%) so position size grows with the account.
- **Bear-market phase classifier + graduated DD response (2026-05-23)** — auto-classify current phase (distribution / decline / capitulation / recovery) from F&G + BTC + dominance; ±0.3 score modifier when setup direction aligns with phase. Drawdown dampener scales risk DOWN as DD grows (×0.75 at -5 to -10%, ×0.50 at -10 to -15%) BEFORE the binary breaker trips.
- **Catastrophe hedge (2026-05-23)** — `trading/hedge_manager.py` auto-opens a BTC perpetual short during basket-flush events (basket -3% + BTC -2% in 1h + ≥70% long-biased). Hedge is `is_hedge=1`, excluded from breakers/concurrency. Unwinds when BTC recovers within 1% or two consecutive green 15m candles.

---

## Setup

See [CLAUDE.md](CLAUDE.md) for full deployment details (Pi SSH, rsync rules, DB backup, service commands).

```bash
git clone https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal.git
cd Auto-Crypto-Tradingjournal
pip3 install -r requirements.txt
cp .env.example .env  # fill in credentials
python3 app.py        # or: sudo systemctl enable --now trading-journal
```

Access at `http://<host>:8082`.

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
