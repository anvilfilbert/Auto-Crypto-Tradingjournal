---
name: diagnose-no-trades
description: Use when the auto-trader is idle longer than expected — no consensus_approved events, no new positions opening, equity flat. Triggers on "why no trades", "auto-trader idle", "auto-trader stuck", "scanner producing setups but nothing fires", "still no entries", "no consensus_approved in N hours".
---

# Diagnose: Auto-Trader Has No Trades

We've solved this pattern at least 5 times in 2026-05. Each time the surface
symptom was identical ("no trades") but the root cause was different. This
skill is the **symptom → root-cause flowchart** so the next investigation
takes minutes, not hours.

## Step 0 — Confirm the symptom

Before diagnosing, distinguish three cases:

| What you observe | What it means |
|---|---|
| No `consensus_approved` events in `futures_ai_log` | The auto-trader chain isn't approving setups |
| `consensus_approved` happens but no `real_open` follows | Execution layer is dropping approved trades |
| `real_open` happens but position closed within seconds | The drift abort phantom-trade pattern |

Each branches differently. Get the answer with:

```sql
SELECT event, COUNT(*) FROM futures_ai_log
WHERE ts >= datetime('now','-24 hours')
GROUP BY event ORDER BY COUNT(*) DESC;
```

## Branch 1 — No `consensus_approved` at all

The scanner is producing setups but Opus rejects everything. Check:

### 1.1 — Is the scanner even running?
```
curl http://192.168.1.21:8082/api/scanner/status → status, completed_at
```
- `status=idle` and `completed_at` ancient → scheduler is broken (look for `SCANNER_SCHEDULER=0`, `journal_paused`, service restarted recently)
- `status=running` for >15 min → stage stuck, check journalctl

### 1.2 — Is the scanner producing setups ≥ CONSENSUS_MIN_SCORE?
Setups must clear `CONSENSUS_MIN_SCORE` (env, default 5 since 2026-05-26).
```
curl /api/scanner/status → setups[].setup_score ≥ CONSENSUS_MIN_SCORE?
```
- If max score < threshold → nothing reaches Opus. Either market is genuinely weak, OR scanner modifiers are over-penalising.
- Recent culprits: bear_phase mis-firing on F&G alone (fixed 2026-05-25), HMM modifier contradicting BTC price (fixed 2026-05-25), operator-behavior caps (removed 2026-05-25).

### 1.3 — Is Opus rejecting on Confluence-0.0 (the silent killer)?
**THIS WAS THE BIG ONE.** When `agent_market_sentiment` fails (Grok rate-limit), pre-2026-05-26 code wiped BOTH interpreter + sentiment to empty defaults. Reviewer then sees `confluence_score=0`, emits "Confluence 0.0 — weak multi-signal alignment", Opus penalises → reject.

Diagnostic query:
```sql
SELECT json_extract(payload_json, '$.ai_warnings')
FROM futures_ai_log
WHERE event='consensus_rejected' ORDER BY id DESC LIMIT 10;
```
If `"Confluence 0.0 — weak multi-signal alignment"` shows up frequently AND `journalctl | grep "market_sentiment failed"` has many hits → the fix is in place (verify with `grep "data_interpreter failed\|market_sentiment failed → empty fallback" journalctl`), but Grok rate-limits are happening. Either accept the explicit "sentiment unavailable" prompt addition (deployed 2026-05-26) OR add a Gemini fallback for sentiment.

### 1.4 — Is Opus rejecting on legitimate weakness?
If warnings are things like "RSI 59 — neutral zone", "ADX 18 — no clear trend for breakout", that's Opus reading the chart honestly. The setups ARE marginal. Wait for market conditions to improve, OR tune `SCANNER_MIN_SCORE` higher to filter weaker candidates upstream.

### 1.5 — Is the Phase 1-4 modifier stack stacking penalties?
Some modifiers were too eager and got tuned:
- bear_phase: ±0.15 (was ±0.3) — F&G + BTC confirmation required
- HMM: ±0.2 with BTC-slope sanity check (suppresses when model contradicts price)
- WaveTrend gold_sell: added 2026-05-26 (was Long-biased)

If you see a single setup losing >0.4 to modifiers AND scoring just below CONSENSUS_MIN_SCORE, look at which modifiers stacked.

## Branch 2 — `consensus_approved` happens but no `real_open`

```sql
SELECT event, COUNT(*) FROM futures_ai_log
WHERE ts >= datetime('now','-24 hours') AND event LIKE 'real%' OR event LIKE '%drift%' OR event LIKE 'rejected_%'
GROUP BY event;
```

Outcomes:
- `rejected_killswitch` — concurrent-position cap hit, daily-DD breaker fired, consec-loss breaker fired, or `journal_paused`. Check kill_switch state.
- `rejected_sizing` — risk_budget returned None. SL too tight (< 0.2%) OR score below floor. Check setup's SL distance.
- `rejected_drift_pre_order` — fill price too far from planned entry. Scanner staleness. Reduce SCANNER_MAX_SYMBOLS, or wait for setup to retrace into zone.
- `real_place_failed` — Bitget API error. Check the payload's error text. Common: min-notional, leverage mismatch, insufficient margin.
- `real_entry_drift_aborted` — legacy path (pre-2026-05-26). Should be rare. Pre-flight should have caught it.

## Branch 3 — Phantom trades (open + close in seconds)

The post-fill drift guard fires AFTER the position opens. Pre-flight check (added 2026-05-26) prevents this for new trades. If you still see it:

- `rejected_drift_pre_order` not firing → the pre-flight check isn't reading the right symbol/price. Look at executor.py logs.
- The position appears on Bitget but not in `positions` table → drift-abort path closes without `_insert_open_position`. Audit via `futures_ai_log WHERE event='real_entry_drift_aborted'`.

## Step N — Fast triage queries

```sql
-- 1. Quick health check
SELECT event, COUNT(*) FROM futures_ai_log
WHERE ts >= datetime('now','-2 hours') GROUP BY event;

-- 2. What is Opus actually saying lately?
SELECT symbol,
       json_extract(payload_json,'$.scanner_score') AS sc,
       json_extract(payload_json,'$.ai_score') AS ai,
       substr(json_extract(payload_json,'$.reason'), 1, 80) AS reason
FROM futures_ai_log WHERE event='consensus_rejected'
ORDER BY id DESC LIMIT 10;

-- 3. Are sentiment agents failing?
-- (in journalctl):
sudo journalctl -u trading-journal --since '6 hours ago' | grep -c 'market_sentiment failed'
```

## Recent root causes catalog (2026-05)

| Date | Root cause | Symptom mask | Fix |
|---|---|---|---|
| 2026-05-23 | Operator-behavior caps killing scores | All setups at 5.5/10 | Removed personal_bad_hour + reversal caps |
| 2026-05-24 | Bear_phase classifier mis-firing | Setups losing -0.3 even in flat markets | Require BTC ≤ -1% 24h confirmation |
| 2026-05-25 | HMM contradicting BTC slope | -0.2 in opposite of actual trend | Sanity check vs 24h BTC slope |
| 2026-05-25 | Rulebook chain-isolation broken | API returned same rulebook for both chains | Migration 64 + filter per chain |
| 2026-05-26 | **Sentiment failure wiped interpreter** | All Opus rejects with "Confluence 0.0" | Split try/except in agent_orchestrator |
| 2026-05-26 | WaveTrend Long-biased (no gold_sell) | 99% Long ratio across scans | Added gold_sell mask + symmetric weight |
| 2026-05-26 | Scanner staleness vs pumping alts | 15 phantom drift-aborts/day | Pre-flight drift check before order |
| 2026-05-26 | Watchlist 314 symbols → noise | Stage-2 JSON failures, drift aborts | Tiered curated 63-symbol watchlist |

## Anti-patterns

- **"Just lower CONSENSUS_MIN_SCORE"** — was tried; turned into Opus rejecting at lower threshold instead. Diagnose WHY Opus rejects before tuning.
- **"Restart the service"** — fixes nothing if root cause is data/logic. Quick reset, no real solution.
- **"Disable the consensus call"** — defeats the purpose; equivalent to running unverified scanner setups.
- **"It must be the model — switch back to Sonnet"** — Sonnet rejected the same setups when tested. Model isn't the bottleneck.

## When all else fails

Read the 5 most recent `consensus_rejected` payloads in full. Look at the `ai_summary` field — it's a free-form Opus rationale. Patterns emerge after 5-10 examples (always RSI? always ADX? always premium-zone?). That's where the real signal is.

## See also

- `review-trading-settings` — for tuning thresholds after diagnosis
- `pick-watchlist-coins` — when scanner is producing too much noise
- `add-score-modifier` / `add-confluence-signal` — when adding new logic to suppress a pattern
