# Data Sources

*Complete reference for every external data source the trading journal pulls from.*
*Updated 2026-05-23.*

The journal aggregates data from **15 external clients** across 4 conceptual layers,
plus the **5-provider AI cascade** (documented separately in
[`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md)). All non-AI clients are imported by
`data_sources.py` and fanned in by `agent_data_collector.py` into a single
`CollectorResult` dict that downstream agents read.

---

## At a glance

| Layer | What | Modules |
|---|---|---|
| **1. Global macro** | VIX, DXY, F&G, ES futures, economic calendar, BTC dominance | `market_context`, `finnhub_client`, `coingecko_client` |
| **2. Market structure** | Options skew, mempool, trending coins | `deribit_client`, `market_context`, `coingecko_client` |
| **3. Symbol-level** | OI, funding, liquidations, L/S, cap tier, TVL, candles | `coinalyze_client`, `ccxt_client`, `coingecko_client`, `liquidation_client`, `market_context`, `chart_context` |
| **4. Trade intelligence** | Smart-money flows, social sentiment | `nansen_client`, `grok_client` |
| **5. On-chain** | MVRV, exchange net-flow | `onchain_client` |

---

## Layer 1 — Global macro (fetched once per scan, shared across all symbols)

### `market_context.py` — yfinance-backed global indicators
| Field | Source | Refresh | Auth |
|---|---|---|---|
| `vix` | yfinance `^VIX` | 5 min cache | none |
| `dxy` | yfinance `DX=F` | 5 min cache | none |
| `es`, `es_change_pct` | yfinance `ES=F` (S&P futures) | 5 min cache | none |
| `fear_greed` | alternative.me public API | 5 min cache | none |
| `btc_mempool` (congestion) | blockchain.com mempool API | 5 min cache | none |
| `defi_tvl` (DeFi tokens only) | DefiLlama public API | per symbol | none |

Used by:
- Scanner Stage 3 macro cap (`_apply_macro_cap`) — VIX > 30 caps scores at 6, F&G > 75 caps at 7
- Bear-phase classifier (`bear_phase.classify_phase`) — F&G + BTC.D + VIX
- Confluence VIX regime multiplier — VIX > 30 multiplies confluence by 0.80

### `finnhub_client.py` — Economic calendar
| Field | Source | Refresh | Auth |
|---|---|---|---|
| `events` (FOMC/CPI/NFP) | Finnhub `/calendar/economic` | hourly | `FINNHUB_API_KEY` |
| `macro_risk` (bool) | derived | per fetch | — |
| `next_event`, `hours_until` | derived | per fetch | — |

Used by Stage 3 macro cap when `hours_until < 24h` — caps score at 7.

### `coingecko_client.py` — Dominance + categories
| Field | Source | Refresh | Auth |
|---|---|---|---|
| BTC.D, ETH.D, USDT.D, OTHERS.D | `/global` | 5 min cache | none |
| TOTAL2, TOTAL3 (alt-coin market caps) | `/global` derived | 5 min cache | none |
| MEME.C, STABLE.C, STABLE.C.D | `/coins/categories` | 1 hour cache | none |
| Trending coins (top-10 24h) | `/search/trending` | 1 hour cache | none |
| `cap_rank`, `cap_tier`, `volume_24h_usd` per symbol | `/coins/{id}` | per symbol | none |

Used by Stage 3 macro context, bear-phase classifier, scanner macro_ctx, dominance dashboard.

---

## Layer 2 — Market structure (institutional positioning)

### `deribit_client.py` — Options skew (BTC/ETH only)
| Field | Source | Refresh | Auth |
|---|---|---|---|
| `put_call_ratio` | Deribit public API | 5 min cache | none |
| `iv_skew` | derived | 5 min cache | — |
| `sentiment` (bullish/bearish/neutral) | derived | 5 min cache | — |
| `near_term_iv` | derived | 5 min cache | — |

Used by: `agent_market_sentiment` — institutional sentiment proxy for BTC/ETH setups.

---

## Layer 3 — Symbol-level (per-coin fundamentals + chart data)

### `coinalyze_client.py` — Aggregated derivatives data
| Field | Source | Refresh | Auth |
|---|---|---|---|
| `oi_coins` (current OI) | `/open-interest` | per fetch | `COINALYZE_API_KEY` |
| **`oi_change_pct`** (4h delta) | `/open-interest-history` (seconds, not ms!) | per scan | same |
| `funding_rate`, `funding_sentiment` | `/funding-rate` | per fetch | same |
| Per-exchange funding spread | `/funding-rate` (multi) | per fetch | same |
| `liq_long_usd`, `liq_short_usd`, `liq_total_usd` | `/liquidation-history` | per fetch | same |
| `long_short_ratio` | `/long-short-ratio` | per fetch | same |

**Special**: `oi_change_pct` is the second input to the **smart-flow quadrant signal**
(OI × CVD × Price) in `chart_confluence`. Coinalyze's history endpoint
takes **unix seconds, not milliseconds** — silent failure trap.

### `liquidation_client.py` — Historical liquidations (cached CSV)
| Field | Source | Refresh | Auth |
|---|---|---|---|
| Per-day longs_usd / shorts_usd, last 30d | `/liquidation-history` | daily, CSV cached | `COINALYZE_API_KEY` |

Cache lives in `data/liquidations/{symbol}/`. Feeds the liquidation cluster
weight in confluence scoring.

### `ccxt_client.py` — Multi-exchange OHLCV + L/S
| Field | Source | Refresh | Auth |
|---|---|---|---|
| OHLCV candles (Binance Futures primary) | ccxt unified | per request | none |
| Multi-exchange L/S consensus | ccxt unified (Binance / Bybit / OKX) | per scan | none |
| Forced-liquidation cluster detection | derived from large red candles | 15 min cache | none |

The L/S **divergence** read (retail vs smart-money positioning) is built here.

### `chart_context.py` — Indicator + S/R orchestrator
Not an external source, but the **single entry point** that fans out to
`ccxt_client` (candles) + `chart_indicators` (RSI/MACD/EMA/ATR via pandas-ta)
+ `chart_patterns` + `chart_sr` + `chart_confluence`. Returns the assembled
chart context per timeframe.

---

## Layer 4 — Trade intelligence

### `nansen_client.py` — Smart-money on-chain flows
| Field | Source | Refresh | Auth |
|---|---|---|---|
| `smart_money_bias` (accumulating/distributing) | Nansen Smart Alerts | 5 min cache | paid Nansen API key |
| `signal` text (e.g. "15 wallets accumulating, +$39k net") | derived | 5 min cache | — |
| Scanner-finalist mode: 1 credit per scan (not per symbol) | special caller pattern | per scan | — |

Used by Stage 3 prompt builder and the scanner's Smart Money panel.

### `grok_client.py` — xAI Grok social/news context
| Field | Source | Refresh | Auth |
|---|---|---|---|
| `text` (one-paragraph news/social summary) | xAI Grok API | per call | `XAI_API_KEY` |
| `weight` (0-80%, cap-weighted by symbol size) | derived | per call | — |

Used by `agent_market_sentiment` for the Stage 3 prompt. Larger-cap coins
get more weight (social signal is noisier on micro-caps).

> Note: `XAI_API_KEY` is invalid for `/chat/completions`, only works for
> Grok's specific `/responses` social-intel feature.

---

## Layer 5 — On-chain

### `onchain_client.py` — CoinMetrics Community API
| Field | Source | Refresh | Auth |
|---|---|---|---|
| `mvrv` (Market Value / Realized Value ratio) | CoinMetrics `CapMVRVCur` | 1 hour cache | none (community tier) |
| `exchange_netflow_usd` (24h) | CoinMetrics `FlowInExNtv` − `FlowOutExNtv` | 1 hour cache | none |
| `valuation` label (overvalued/fair/undervalued from MVRV bands) | derived | 1 hour cache | — |

Injected into prompts as `"On-chain BTC: MVRV 2.3 | fair_value | exchange outflow $30M"`.
BTC/ETH only — other coins fall through with `{}`.

---

## Exchange clients (read-only sync, not "data sources" per se)

### `bitget_client.py` — Operator main account (read-only)
- Position list, fills, orders, wallet snapshots
- Synced by `bitget_sync.py` every 5 min
- Auth: `BITGET_API_KEY` / `_SECRET_KEY` / `_PASSPHRASE`

### `blofin_client.py` — Parallel exchange (read-only)
- Same shape as `bitget_client`
- Synced by `blofin_sync.py`
- Auth: `BLOFIN_API_KEY` / `_SECRET_KEY` / `_PASSPHRASE`

> `trading/bitget_trader.py` is the **write** client used by the Futures-AI
> auto-trader chain. It hits a separate Bitget subaccount via its own
> `BITGET_TRADER_*` env vars and is **not** considered a data source.

---

## AI providers (cascade — see [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md))

| Client | Role | Auth |
|---|---|---|
| `ai_client.py` | Cascade router (Anthropic primary → 4 fallbacks) | — |
| Anthropic | Sonnet 4.6 / Haiku 4.5 / Opus 4.7 | `ANTHROPIC_API_KEY` |
| `groq_client.py` | Groq LPU (Llama 4 Scout) — free workhorse | `GROQ_API_KEY` |
| `cerebras_client.py` | Cerebras (Qwen 235B + Llama 8B) | `CEREBRAS_API_KEY` |
| `openrouter_client.py` | OpenRouter (DeepSeek V4 free) | `OPENROUTER_API_KEY` |
| `gemini_client.py` | Gemini 2.0 Flash (with internal 4-model cascade) | `GEMINI_API_KEY` |
| `openai_compat_client.py` | Shared base for Groq/Cerebras/OpenRouter (OpenAI-format) | — |

---

## Adding a new data source

1. Create `<name>_client.py` exposing pure functions (no global state)
2. Add a `fetch_<name>()` wrapper in `data_sources.py` that handles errors gracefully (return `{}` on failure)
3. Add the new field to `CollectorResult` in `agent_types.py` (TypedDict — be precise)
4. Add the fetch to `agent_data_collector.py`'s parallel-fetch list
5. Add a downstream consumer somewhere (interpreter, sentiment, scanner prompt, etc.)
6. Update this doc

The pattern is intentionally additive — new sources don't require touching the agents
already in production.

---

## Cadence summary

| Source | Refresh | Why |
|---|---|---|
| VIX, DXY, ES, F&G | 5 min cache | yfinance rate limits |
| BTC dominance, market caps | 5 min cache | CoinGecko free tier |
| Categories (MEME.C, STABLE.C.D) | 1 hour cache | infrequent change, save calls |
| Coinalyze OI / funding / liq | per scan, no cache | per-scan accuracy matters more than savings |
| Coinalyze OI history (4h) | per scan, 5 min TTL via `_get_oi_change_cached` | smart-flow signal |
| Nansen (scanner finalists) | 1 credit per scan total | paid tier |
| Deribit options | 5 min cache | low-frequency institutional signal |
| Mempool | 5 min cache | rarely actionable short-term |
| CoinMetrics on-chain | 1 hour cache | daily-resolution data |
| ccxt candles | request-time (chart_candles 5 min internal cache) | per-page freshness |
