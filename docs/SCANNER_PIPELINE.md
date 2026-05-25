# Scanner Pipeline

*How the setup scanner turns 300+ raw symbols into a ranked list of
A+ trade setups every 30 minutes. Includes the main path (full
agent pipeline) and the backup path (quick-score fallback).
Updated 2026-05-23.*

---

## The funnel at a glance

```
   ~300 USDT-M symbols  (scanner_watchlist.SYMBOLS)
            │
            ▼   STAGE 1 — Confluence filter
   ~30 candidates  (confluence score above SCANNER_MIN_SCORE)
            │
            ▼   STAGE 2 — Quality gate (1H data enrichment)
   ~30 finalists  (sorted by abs(confluence) descending)
            │
            ▼   STAGE 3a — Haiku quick-score    ◄── BACKUP PATH
   ~30 quick-scored (cheap pre-filter, ~$0.0035 each)
            │
            ├── top 6 (SCANNER_FULL_DETAIL_TOP_N)
            │      │
            │      ▼  STAGE 3b — Sonnet full analysis  ◄── MAIN PATH
            │   ~6 fully-scored setups (Sonnet via 7-agent pipeline)
            │
            └── rest (~24)
                   │
                   ▼  remain "quick_score_only" — no Sonnet, displayed with rationale
            │
            ▼   STAGE 4 — Modifiers (PO3 + bear-phase + RSI mastery + HMM)
            ▼   STAGE 5 — Strategic caps (macro event proximity)
            ▼   STAGE 6 — Score threshold filter
            ▼   STAGE 7 — Output → orchestrator → consensus → sizing → open
```

Total scan time: **~5-8 minutes** (mostly Stage 1's 314 parallel OHLCV fetches).

---

## Stage 1 — Confluence filter

**Module**: `scanner_stages._stage1`
**Cost**: zero (pure compute — no AI, no paid APIs)
**Throughput**: ~1.0s per symbol with 8-worker `ThreadPoolExecutor`

### What it does
For each of ~300 watchlist symbols, fetches 4H + 1D OHLCV (cached when possible) via `ccxt_client`, computes 15-signal confluence via `chart_confluence.confluence_score`, and keeps only symbols where the confluence **percentage** crosses `SCANNER_MIN_SCORE` (default 7).

### Directional decision
Each candidate gets a direction tag (`Long` or `Short`) using the **dominant-side** rule:

```python
if max(bull, bear) >= threshold:
    direction = "Long" if bull > bear else "Short"
```

This was the fix in commit `3437dd0` (2026-05-20). The pre-fix code used `if bull >= t → Long elif bear >= t → Short`, which on a tie defaulted to Long. Pre-fix audit found 422/0 Long/Short ratio historically; post-fix is healthy 60/40.

### Output
List of `(symbol, ctx, confluence_dict, direction)` tuples ranked by `abs(score)` (so a strong Short doesn't sink below a weak Long when we cut to the top-N in Stage 2).

---

## Stage 2 — Quality gate

**Module**: `scanner_stages._stage2` + `enrich_finalists_1h`
**Cost**: zero (more OHLCV fetches, no AI)
**Throughput**: ~30s for top 30 (8-worker `ThreadPoolExecutor`)

### What it does
- Takes Stage 1 candidates sorted by `abs(score)`, keeps top 30
- For each, **fetches 1H candles** + computes 1H indicators + S/R levels
- Builds a `prompt_text` block per timeframe (1D, 4H, 1H) — the
  "TF prompt" string Stage 3 will include verbatim

### Why this stage exists
4H staleness was a quality killer. A 4H bar can be up to 4 hours behind reality. By adding 1H data in Stage 2, the **maximum staleness drops to ~1 hour** — enough precision for tight SL placement and entry timing without re-doing Stage 1's work.

### Output
List of `(sym, ctx_with_1H_added, conf, direction)` finalists.

---

## Stage 3a — Haiku quick-score (the "backup" path)

**Module**: `scanner_prompts._quick_score`
**Cost**: ~$0.0035 per symbol (Haiku 4.5)
**Latency**: ~0.4s per call (~0.2s on Groq fallback)
**Throughput**: 30 calls in ~3-5s with parallel workers + cascade

### What it does
Cheap pre-filter pass on ALL 30 finalists. Haiku gets a compact prompt:
- Per-TF indicator summary (RSI/MACD/EMA/ADX/WaveTrend/CVD/order_flow)
- Multi-TF confluence picture
- A short setup-archetype hint

Returns a JSON `{score: int, reason: str}`. **Used as the score gate** for whether a symbol moves on to Stage 3b.

### Why this is the "backup" tier
- It runs on EVERY finalist (top 30), not just the top 6
- For non-top-N symbols, the Haiku score is the ONLY score — they get marked `quick_score_only: True` and shown on the scanner page with the Haiku score + one-line rationale
- If Stage 3b fails (Anthropic outage, JSON parse error), the Haiku score is still there as a fallback

### The cascade matters here most
Haiku Stage 3a is the highest-frequency AI call in the system. When Anthropic rate-limits, the cascade routes ~33% of these calls to Groq (Llama 4 Scout) at zero cost. From the 7d token usage snapshot:
- `scanner_quick / claude-haiku-4-5`: 4,003 calls, $14.14
- `scanner_quick+groq`: 449 calls, $2.13 (free tier, the dollar amount is the token-cost-equivalent for reference)
- `scanner_quick+cerebras` (qwen + llama): 293 calls
- `scanner_quick+openrouter`: 20 calls
- `scanner_quick+gemini`: 50 calls

### Cancel-aware
After every batch of 8 parallel quick-scores, `_check_cancel()` polls for the scanner cancellation event. If the operator cancels mid-scan, in-flight calls complete but no new ones queue.

---

## Stage 3b — Sonnet full analysis (the "main" path)

**Module**: `ai_scanner._score_finalists_with_agents`
**Cost**: ~$0.014 per setup (Sonnet 4.6, with prompt cache → ~$0.005 cached)
**Latency**: ~1.8s per call
**Throughput**: top 6 (SCANNER_FULL_DETAIL_TOP_N) sequentially or with thread-pool

### What it does
For each of the top 6 finalists by Haiku score, runs the **full 7-agent pipeline** (see [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md)) culminating in a Sonnet `agent_trade_prep.run()` call. The agent_trade_prep prompt assembles:
- All Stage 1 + Stage 2 indicator data
- Stage 3a Haiku quick-score and rationale
- Scanner rulebook context
- Macro / dominance / regime context
- Smart-money (Nansen) signal
- Grok social/news weight
- Personal backtest context for this trader

Sonnet returns:
- `setup_score` (0-10, may differ from Haiku quick-score)
- `direction` (final confirmation)
- `entry_price`, `sl_price`, `tp1_price`, `tp2_price`
- `rr_ratio`
- `key_conditions` (3-5 bullet points the setup is built on)
- `pattern_warnings` (any red flags)
- `chart_pattern` (named pattern if detected)

### Prompt caching
The `ANALYST_INSTRUCTIONS + RISK_INSTRUCTIONS + rulebook + rubric` prefix (~1543 tokens) is sent once with `cache_control: ephemeral`. Subsequent calls within the same batch hit cache at ~74%. Net savings: ~$10 per 6-setup batch.

### Failure fallback
If Sonnet throws (parse error, API error after cascade exhausted), the result is a `_degraded()` `AnalysisResult` from `agent_orchestrator`:
- `setup_score = 0`, `degraded = True`, `error` populated
- This setup is then filtered out by the threshold check in Stage 6

The Haiku quick-score from Stage 3a is still available on the row, so even a totally-down Sonnet doesn't blank the scanner — the operator sees the Haiku scores.

---

## Stage 4 — Modifiers (PO3 / Bear phase / RSI Mastery already in confluence)

**Module**: `ai_scanner._score_finalists_with_agents` (lines 220-280)
**Order**: applied AFTER Stage 3b returns, BEFORE Stage 5 caps

Direction-aware score nudges from the trader-sheet integration wave
(2026-05-23). Each can shift the score by ±0.3:

| Modifier | Magnitude | Logic |
|---|---|---|
| **Premium/Discount** | ±0.3 | Long in discount (bottom third of 40-bar range) = +0.3; Long in premium = -0.3; mirror for Shorts |
| **FVG** | ±0.3 | Same-direction unfilled FVG support = +0.3; opposing FVG resistance within 3% = -0.3 (can sum) |
| **Kill zone** | -0.2 to +0.3 | Silver Bullet 13:30-14:30 UTC = +0.3; London 07-10 / NY AM 12-16 = +0.2; NY PM 18:30-21 = +0.15; Dead hour 16:30-17:30 = -0.2 |
| **Bear-phase alignment** | ±0.3 | Setup direction agrees with classified phase bias = +0.3; fights it = -0.3 |

Already-baked into Stage 3a/3b score via confluence:
- Smart-flow quadrant (±0.5/±0.2) — chart_confluence
- RSI Mastery (regime-aware ±1.0, failure swing ±0.4, divergence ±0.4) — chart_rsi
- 11 base confluence signals — chart_confluence

---

## Stage 5 — Strategic caps

**Module**: `scanner_criteria` + `ai_scanner._score_finalists_with_agents`
**Order**: applied AFTER modifiers

Hard ceilings independent of how well the setup scored.

| Cap | Source | Threshold | Effect |
|---|---|---|---|
| **Macro cap** | `_apply_macro_cap` | VIX > 35 → cap at 6.0 · VIX 25-35 → cap at 7.5 · macro event in 24h → cap at 7.0 | Score = min(score, cap) |

### Removed 2026-05-25 — operator-behavior priors
Two caps were removed: the **personal bad-hour cap** (UTC 13/15/19/20) and the **reversal-archetype cap**. Both were derived from this trader's own 90d loss patterns rather than from market structure. The auto-trader now scores setups purely on market facts + sentiment data; the operator's historical loss patterns no longer bias the algorithm. The constants `PERSONAL_BAD_HOURS_UTC`, `PERSONAL_BAD_HOUR_CAP`, `REVERSAL_CAP` remain in `scanner_criteria.py` as dead references but are no longer called.

---

## Stage 6 — Threshold filter

Setups where the final modified+capped score < `min_score` (default 7) are dropped. Everything else gets a setup dict assembled with the score, level rationale, urgency tag, chart PNG (if generated), etc.

### TP/SL enforcement
Before the setup dict is finalized, the SL/TP go through:
- `trade_utils.enforce_tp_floor` — TP must be ≥ 1× ATR_4H (TP1) and ≥ 2× ATR_4H (TP2) from entry, on the correct side
- `trade_utils.enforce_sl_floor` — SL must be on the correct side of entry, within 0.5×-8× ATR_4H envelope

Adjustments are recorded in `setup['_tp_adjustments']` and `setup['_sl_adjustments']` so the operator/reviewer can see what was changed.

---

## Stage 7 — Output → orchestrator → consensus → sizing → open

The scanner publishes its `setups[]` list to:
1. **`futures_ai_orchestrator.on_scan_completed(setups)`** — for each setup, runs `kill_switch.can_open_new_trade()` → `signal_consensus.evaluate()` → `risk_budget.size_trade()` → `executor.open_real_trade()` (or `paper.open_paper_trade()`)
2. **Scanner scheduler hook** — persists setups + sends Telegram alerts for top scores
3. **Scanner UI** — displays the list with score, R:R, urgency, key conditions, chart popup

See [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md) and the auto-trader docs in `architecture.md` for downstream behavior.

---

## Configuration knobs

| Knob | Default | Source | Effect |
|---|---|---|---|
| `SCANNER_MIN_SCORE` | 7 | `constants.py` | Stage 1 confluence cutoff + final threshold |
| `SCANNER_FULL_DETAIL_TOP_N` | 6 | `constants.py` | How many finalists go through Stage 3b (Sonnet) |
| `SCANNER_CACHE_TTL` | 30 min | `constants.py` | Stale-scan cache lifetime |
| `SCANNER_TOP_N` | 30 | (scanner_stages internal) | How many finalists from Stage 2 |
| `CRITERIA_DEFAULTS` | per-signal weights | `scanner_criteria.py` | Per-signal weight overrides |
| `FUTURES_AI_CONSENSUS_MIN_SCORE` | 7 (env) / 8 (default) | `.env` | Sonnet consensus gate for auto-trader |

---

## Scanner Cadence

- **Auto-scan**: every 30 minutes via `scanner_scheduler._loop()`
- **Manual / forced**: `POST /api/scanner/run?force=1`
- **Cancel**: `POST /api/scanner/cancel` — sets `_cancel_event` in `ai_scanner.py`, no new calls fire after current batch
- **First scan after restart**: 5 min delay (lets the service warm up)

### What happens during a scan
- Service stays responsive — all scanner work runs on background threads with thread pools
- UI shows live progress: `Stage 1 — 240/314 symbols` etc.
- Operator can cancel at any stage; in-flight HTTP completes but no new calls queue
- On completion: setups visible on Scanner page, Telegram alert sent for top score(s), orchestrator hook fires for auto-trader

---

## Empirical performance (2026-05-22 to 2026-05-23 snapshot)

| Metric | Value |
|---|---|
| Symbols scanned per run | 314 |
| Stage 1 pass rate | ~10% (~30 candidates) |
| Stage 2 → 3 throughput | 30 finalists fed to Haiku |
| Stage 3b coverage | top 6 (~20% of finalists get Sonnet) |
| Total scan duration | 287-371s |
| Setups published per scan | 2-6 (depends on market regime) |
| Stage 3b Sonnet cache hit | 74% within batch |

---

## See also
- [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md) — agent pipeline + cascade
- [`DATA_SOURCES.md`](DATA_SOURCES.md) — what feeds the scanner
- [`SCORING_GUIDE.md`](SCORING_GUIDE.md) — what each score (1-10) actually means
- [`architecture.md`](architecture.md) — full system architecture
