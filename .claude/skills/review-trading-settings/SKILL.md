---
name: review-trading-settings
description: Use when reviewing or tuning auto-trader, scanner, hedge, or risk-gate settings — env vars in .env, constants in trading/config.py / constants.py, runtime knobs. Triggers on "review my settings", "is the config sane", "tune X", "should I change Y", "diagnose why no trades opening", "diagnose drift aborts", "loosen/tighten the gates", "what does FUTURES_AI_X do".
---

# Trading Settings Review

The system has **~40 user-tunable settings** spread across `.env`, `trading/config.py`, and `constants.py`. Most have defaults that ship reasonable but accumulate drift as the operator iterates. This skill encodes:

1. **What each setting actually controls** (not just the comment)
2. **Healthy ranges** and red-flag thresholds
3. **Symptom → likely tunable** mapping (when something is misbehaving, which knob)
4. **Tuning ROUTINES** — review cadence, paired settings that must move together

---

## How to use this skill

1. If the operator describes a SYMPTOM ("no trades opening", "too many drift aborts", "auto-trader idle for hours"), jump to §10 — Symptom → settings map.
2. If the operator asks for a holistic review, walk §2-§9 in order and flag any setting outside its healthy range.
3. After ANY tuning change, recommend backup + restart + monitor next scan cycle.
4. Never tune more than 2 settings at a time without a checkpoint — you'll lose causality.

---

## 1. Settings inventory (where things live)

| Layer | File | Notes |
|---|---|---|
| Operator-tunable runtime | `.env` (Pi only) | Most-frequently-changed |
| Code-level defaults / hardcoded | `trading/config.py` | Auto-trader-specific. Changes require deploy + restart |
| Cross-system constants | `constants.py` | Versions, cache TTLs, model IDs |
| Per-trade runtime state | `settings` table (DB) | journal_paused, telegram_paused, futures_ai_state |

**Read order when diagnosing**: env → config.py constants → DB settings table.

---

## 2. Risk gates (the absolute caps — change with EXTREME care)

These define maximum permissible exposure. Too loose = blow up the account. Too tight = never trade.

| Setting | Location | Default | Healthy range | Symptom of "too tight" | Symptom of "too loose" |
|---|---|---|---|---|---|
| `RISK_PER_TRADE_PCT` | `trading/config.py:151` | `0.02` (2%) | 0.005-0.03 | Sub-25 USDT positions even with leverage | Per-trade loss > 3% of equity |
| `MAX_LEVERAGE` | `trading/config.py:155` | `10` | 5-20 | Cannot meet exchange's min-notional on small accounts | Liquidations on routine pullbacks |
| `MAX_NOTIONAL_USDT` | `trading/config.py:156` | `25.0` (floor) | match exchange min-notional + ~50% | Notional cap rejections in log | Single position > 25% of equity |
| `MAX_NOTIONAL_PCT` | `trading/config.py:162` | `0.25` (25% of equity) | 0.15-0.30 | Equity grows but position size stays at 25 | One-trade drawdown > 5% of equity |
| `MAX_CONCURRENT_POSITIONS` | `trading/config.py:163` | `5` (soft) | 3-7 | "rejected_killswitch: max concurrent" events when you want a 6th | Aggregate exposure > 50% of equity |
| `MAX_ELITE_POSITIONS` | `trading/config.py` | `7` (hard) | 5-10 | Score-10 setups bypassed even rarely | Same as too-loose MAX_CONCURRENT |
| `ELITE_BYPASS_SCORE` | `trading/config.py` | `10` | 9-10 | Frequent rare-elite rejections at full cap | Score-9 setups also bypass when shouldn't |

**Hard rule**: `MAX_CONCURRENT_POSITIONS × RISK_PER_TRADE_PCT < |TOTAL_DD_BREAKER_PCT|`. Currently 5 × 2% = 10% < 15% breaker → safe. If you raise one, raise the other or the breaker will trip first.

---

## 3. Breakers (when to STOP trading)

The escape hatches. Tighter = pauses on small losses (preserves capital but stops compounding); looser = rides out bigger drawdowns.

| Setting | Location | Default | Healthy range | When to TIGHTEN | When to LOOSEN |
|---|---|---|---|---|---|
| `DAILY_DD_BREAKER_PCT` | `trading/config.py` | `-0.05` (-5%) | -0.03 to -0.08 | Recent days bleed > 5% | Stops kicking in on intraday noise |
| `TOTAL_DD_BREAKER_PCT` | `trading/config.py` | `-0.15` (-15%) | -0.10 to -0.20 | Series of bad weeks; want forced reset | Account healthy but breaker tripped on stale fills |
| `CONSECUTIVE_LOSS_BREAKER` | `trading/config.py` | `3` | 2-5 | Three consecutive losses each week | Streak bumps but win/loss is just sequencing noise |

**Recovery rule**: After breaker trips, do NOT just loosen — investigate WHY first. Drawdowns from real reasons (strategy gone stale, regime shift) shouldn't be papered over.

---

## 4. Consensus + execution gates

These determine whether a SCANNER setup actually becomes a TRADE.

| Setting | Location | Default | Healthy range | "Too tight" symptom | "Too loose" symptom |
|---|---|---|---|---|---|
| `SCANNER_MIN_SCORE` | `constants.py:29` | `7` | 6-8 | Scanner produces nothing for hours | Many score-6 setups stop out fast |
| `CONSENSUS_MIN_SCORE` | `trading/config.py:44` (env `FUTURES_AI_CONSENSUS_MIN_SCORE`) | `8` (default) / `6` (current Pi) | 6-9 | All consensus skipped → no trades opened | Opus calls on every borderline setup → cost spike |
| `CONSENSUS_MODEL` | `trading/config.py:45` (env `FUTURES_AI_CONSENSUS_MODEL`) | `opus` | `opus`/`sonnet` | Opus rejects too conservatively | Sonnet less stringent — more trades but lower quality |
| `MAX_ENTRY_DRIFT_PCT` | `trading/config.py:112` (env `FUTURES_AI_MAX_ENTRY_DRIFT_PCT`) | `0.02` (2%) | 0.015-0.05 | Many `rejected_drift_pre_order` events; no fills | Filling deep into pumps; entries chase tops |

### Consensus tuning logic (the most-asked-about set)

If 24h shows **0 `consensus_approved` events**:
1. Are there `consensus_rejected` events? → consensus is firing, Opus is rejecting on merit. Investigate the rationale (check `payload_json.ai_summary` + `ai_warnings`).
2. Are there 0 events at all? → scanner setups aren't reaching the orchestrator. Check `SCANNER_MIN_SCORE` vs actual setup scores produced.

If many `rejected_drift_pre_order` (NEW post-2026-05-26) or `real_entry_drift_aborted` (legacy):
1. Setups are reaching execution but the entry zone is stale by then.
2. **Don't just loosen MAX_ENTRY_DRIFT_PCT** — that lets the system chase pumps.
3. Either: (a) reduce scanner latency (smaller watchlist, smaller batch), (b) shorten consensus model (Opus → Sonnet), or (c) accept that pumping alts won't be filled and let calmer setups come through.

---

## 5. Scanner breadth + cadence

These shape WHAT the scanner sees and HOW OFTEN.

| Setting | Location | Default | Healthy range | Tighter for | Looser for |
|---|---|---|---|---|---|
| `SCANNER_MAX_SYMBOLS` | `.env` | `500` | 80-300 | Small accounts, quality focus | Discovery / breadth |
| `SCANNER_MIN_VOL_USD` | `.env` | `3000000` ($3M) | 5M-50M | Reduce slippage, drift aborts | Small-cap rotation phases |
| `SCANNER_MIN_OI_USD` | `.env` | `1500000` ($1.5M) | 1M-25M | Stable liquidity needed | Edge cases / new tokens |
| `SCANNER_INTERVAL` (sec) | `.env` | `1800` (30 min) | 900-3600 | Need fresher entries for fast alts | API cost discipline |
| `SCANNER_FIRST_DELAY` (sec) | `.env` | `300` (5 min) | 60-600 | Faster startup after restarts | Avoid pre-warmup partial fetches |
| `SCANNER_FULL_DETAIL_TOP_N` | `constants.py:32` | `6` | 4-15 | Anthropic rate-limit budget tight | More Sonnet coverage = better picks |
| `SCANNER_MAX_WORKERS` | `constants.py:33` | `4` (Pi 4-core) | 2-8 | Pi CPU saturated | More parallelism on better hardware |

**Quick-win recipe for fewer drift aborts**:
```env
SCANNER_MAX_SYMBOLS=120
SCANNER_MIN_VOL_USD=10000000
SCANNER_MIN_OI_USD=3000000
SCANNER_INTERVAL=1200      # 20 min instead of 30
```
Cross-reference: see `pick-watchlist-coins` skill for the tier methodology behind these numbers.

---

## 6. Hedge manager (catastrophe protection)

The auto-opened BTC short when the basket bleeds.

| Setting | Location | Default | Healthy range | "Too tight" (over-hedges) | "Too loose" (no protection) |
|---|---|---|---|---|---|
| `HEDGE_ENABLED` (env `FUTURES_AI_HEDGE_ENABLED`) | `trading/config.py:257` | `1` (ON) | 0/1 | Hedge fires on routine pullback | Single-day flush wipes basket |
| `HEDGE_TRIGGER_UNREAL_PCT` | `trading/config.py:262` | `-0.03` (-3%) | -0.02 to -0.05 | Fires on normal MAE | Doesn't fire until -10%, then it's too late |
| `HEDGE_TRIGGER_BTC_DROP_PCT` | `trading/config.py:263` | `-0.02` (-2%) | -0.015 to -0.04 | Triggers on local BTC noise | Misses the real flushes |
| `HEDGE_TRIGGER_LONG_BIAS_PCT` | `trading/config.py:264` | `0.70` (70%) | 0.50-0.85 | Won't fire on balanced book during real crashes | Fires when basket is already mostly Short |
| `HEDGE_RATIO` | `trading/config.py:265` | `0.50` (50%) | 0.30-0.75 | Over-hedges; eats fees with no real protection | Insufficient buffer in real crash |
| `HEDGE_LEVERAGE` | `trading/config.py:266` | `3` | 2-5 | Hedge position itself blows up | Margin tied up with little protection per $ |
| `HEDGE_MAX_DURATION_HOURS` | `trading/config.py` | `24` | 12-48 | Premature unwind in extended bear | Forgotten hedge in calm market |

---

## 7. Compounding / streak sizing

| Setting | Location | Default | Healthy range | "Too aggressive" | "Too conservative" |
|---|---|---|---|---|---|
| `COMPOUND_STREAK_ENABLED` (env) | `trading/config.py:169` | `1` (ON) | 0/1 | Win streak → catastrophic blow-up on inevitable loss | Same flat size after 10 wins |
| `MAX_STREAK_MULTIPLIER` (env `FUTURES_AI_MAX_STREAK_MULT`) | `trading/config.py:171` | `3` | 2-5 | 3× on 8th consecutive win = oversize before regime shift | Win-streak edge unused |

**Rule of thumb**: streak compounding works on stationary edges. If you're in calibration mode (changing scoring/modifiers), turn it OFF — you'll attribute lucky streaks to the new tweak and oversize before the next loss.

---

## 8. Phase 1-4 feature toggles (the experiments)

These are mostly env-gated features added during 2026-05 tuning. Default ON.

| Toggle (env) | Default | What it does | When to disable |
|---|---|---|---|
| `FUTURES_AI_CPR_ENABLED` | `1` | Central pivot range modifier (±0.3) | Suspect false bull bias from CPR |
| `FUTURES_AI_IB_ENABLED` | `1` | Initial-balance breakout modifier | IB drives over-trading on choppy days |
| `FUTURES_AI_VOL_DAMPENER_ENABLED` | `1` | Reduce size when current ATR > reference | Killing trades that work fine at high vol |
| `FUTURES_AI_SAFEZONE_SL_ENABLED` | `1` | SL nudged off round numbers | SL placement obviously wrong (round-number magnet) |
| `FUTURES_AI_TIERED_BE_ENABLED` | `1` | Tiered break-even moves | BE moves cost trail profits in trends |
| `APGAR_GATE_ENABLED` | env | Apgar pre-session readiness | Operator skipping the daily Apgar entry |
| `READINESS_GATE_ENABLED` | env | Sleep/mood gate | Same — operator not entering daily check |
| `MONTHLY_GATE_ENABLED` | env | Monthly risk budget cap | Monthly metric stale or unreliable |

**General rule**: when 2+ feature toggles look "wrong", disable ONE at a time, restart, wait one full scan cycle, observe, then change the next.

---

## 9. Operational / observability

| Setting | Location | Default | Notes |
|---|---|---|---|
| `TG_PAUSED` (env) | `monitor_scheduler` | empty | Silences Telegram only; scanner + auto-trader still run |
| `journal_paused` (settings table) | DB | false | Stops scanner + auto-trader entirely |
| `telegram_paused` (settings table) | DB | false | DB-level Telegram pause (preferred over env in production) |
| `futures_ai_state` (settings table) | DB | `active` | `active`/`pause_after_close`/`pause_now`/`circuit_breaker` |
| `SCANNER_SCHEDULER` (env) | scanner_scheduler.py | unset | Set to `0` to disable scheduler (forced/manual scans still work) |
| `SELF_REVIEW_*` (env) | self_review.py | various | Cadence + threshold for self-review scheduler |

---

## 10. Symptom → settings map (FAST DIAGNOSTIC)

When something is misbehaving, jump straight here:

| Symptom | Most-likely tunable(s) | First step |
|---|---|---|
| No trades opening for 24+ hours | `CONSENSUS_MIN_SCORE`, `SCANNER_MIN_SCORE`, breakers tripped, `journal_paused`, `futures_ai_state` | Check `/api/futures-ai/state` runtime |
| Many `rejected_drift_pre_order` | scanner staleness — see §5 quick-win recipe | Reduce SCANNER_MAX_SYMBOLS |
| Many `real_entry_drift_aborted` (legacy) | Pre-flight check missing — deploy fix | Check executor.py has pre-flight |
| `consensus_rejected` rate > 80% | Opus too conservative OR setups genuinely weak | Read `ai_warnings` in payloads |
| All setups Long (99% bias) | Structural confluence bias (see WaveTrend gold_sell fix), watchlist composition | Run `pick-watchlist-coins` review |
| Sub-tier liquidity events ("rejected_killswitch min notional") | `MAX_NOTIONAL_USDT` floor too low for exchange min | Raise floor to 30-50 |
| `lev_mismatch` warnings | Bitget enforces higher min leverage than requested | No tuning needed; verify position math handles actual leverage |
| Hedge fires on routine days | `HEDGE_TRIGGER_UNREAL_PCT` too tight, `HEDGE_TRIGGER_BTC_DROP_PCT` too loose | Tighten BTC trigger first |
| Hedge missed a real flush | Triggers too loose | Tighten UNREAL_PCT, BTC_DROP_PCT |
| 0 `consensus_approved` over a week | Either Opus is too strict OR setups all weak — investigate Opus rationale, not just lower threshold | Read 5 most-recent `consensus_rejected` payload_json |

---

## 11. Cross-setting safety checks (run before any major tune)

These pairs MUST stay coherent. Verify before declaring a tuning change done:

```
[ ] MAX_CONCURRENT_POSITIONS × RISK_PER_TRADE_PCT < |TOTAL_DD_BREAKER_PCT|
[ ] CONSENSUS_MIN_SCORE >= SCANNER_MIN_SCORE  (otherwise consensus skipped)
[ ] MAX_NOTIONAL_USDT >= exchange's symbol min-notional  (else fill rejection)
[ ] MAX_NOTIONAL_PCT × starting_equity >= MAX_NOTIONAL_USDT  (else floor never binds)
[ ] HEDGE_TRIGGER_LONG_BIAS_PCT × HEDGE_RATIO < 1.0  (else hedge bigger than basket)
[ ] SCANNER_INTERVAL > scan_duration  (else overlapping scans)
[ ] MAX_STREAK_MULTIPLIER × RISK_PER_TRADE_PCT < |DAILY_DD_BREAKER_PCT|
```

The current defaults satisfy all of these. Verify after any change.

---

## 12. Tuning routine

**Per-change protocol**:
1. State the symptom + hypothesis ("too many drift aborts → scanner is too slow → reduce SCANNER_MAX_SYMBOLS from 500 to 120")
2. Backup DB before deploy (`bash scripts/backup_db.sh`)
3. Change ONE setting (or two paired settings) in `.env` or `trading/config.py`
4. Deploy: rsync → nuke __pycache__ → restart → verify service active
5. Wait at least ONE full scan cycle (30 min default)
6. Check the symptom metric: did the targeted event count change?
7. Check NO regression in other metrics (`consensus_approved` count, error rate, hedge events)
8. Document the tuning + outcome in CLAUDE.md if material

**Cadence**:
- **Weekly review** (Sundays): Walk §2-§9 once, check breakers haven't tripped, verify Pi service uptime is < 7 days (restart if older to clear leaks)
- **Monthly review**: Recompute the cross-setting safety checks §11 — defaults drift as the operator iterates
- **After incident**: Tighten one breaker temporarily for 1 week, then loosen back if calm

---

## 13. Anti-patterns to flag

Push back on these:

- **"Increase MAX_LEVERAGE to 25"** → unless the win-rate calibration data supports it AND the operator accepts the wider liquidation distance, no. Crypto's intra-bar volatility means leverage > 10 routinely liquidates on routine wicks.
- **"Disable the daily DD breaker"** → never. The breaker exists for the bad day you can't predict.
- **"Lower CONSENSUS_MIN_SCORE to 4"** → Opus on every score-4 setup is wasteful AND Opus will reject most of them anyway. Better: investigate why scanner isn't finding 7+ setups.
- **"Raise RISK_PER_TRADE_PCT to 5%"** → 5 × 5% = 25% of equity at risk with 5 concurrent positions. One bad correlated move wipes a quarter of the account.
- **"Disable all Phase 1-4 toggles to simplify"** → those toggles encode learned behavior. Disabling them all returns to a known-worse state. Disable selectively after evidence one is harmful.
- **"Hedge ratio to 1.5×"** → hedge bigger than the basket isn't a hedge, it's a Short bet wearing a hedge mask.

---

## 14. Quick reference — current Pi values (snapshot 2026-05-26)

Verify these on the live Pi any time via `GET /api/futures-ai/state` → `data.config`:

```
MAX_LEVERAGE              = 10
MAX_NOTIONAL_USDT         = 25 (floor) | 25% of equity (dynamic)
MAX_CONCURRENT_POSITIONS  = 5 (soft) / 7 (elite bypass)
RISK_PER_TRADE_PCT        = 0.02 (2%)
RISK_SCORE_MULTIPLIERS    = {7:1.0, 8:1.5, 9:2.0, 10:2.0}
DAILY_DD_BREAKER_PCT      = -0.05
TOTAL_DD_BREAKER_PCT      = -0.15
CONSECUTIVE_LOSS_BREAKER  = 3
CONSENSUS_MIN_SCORE       = 6 (env override; default 8)
CONSENSUS_MODEL           = opus
MAX_ENTRY_DRIFT_PCT       = 0.02 (2%)
SCANNER_MAX_SYMBOLS       = 500   ← high, consider tightening
SCANNER_MIN_VOL_USD       = 3M    ← low, consider tightening
SCANNER_MIN_OI_USD        = 1.5M  ← low, consider tightening
SCANNER_INTERVAL          = 1800 (30 min)
HEDGE_ENABLED             = 1
HEDGE_TRIGGER_UNREAL_PCT  = -0.03
HEDGE_TRIGGER_BTC_DROP    = -0.02
HEDGE_RATIO               = 0.50
HEDGE_LEVERAGE            = 3
COMPOUND_STREAK_ENABLED   = 1
MAX_STREAK_MULTIPLIER     = 3
```

Three settings flagged as "consider tightening" → see `pick-watchlist-coins` skill for the recipe.

---

## References

- Defaults derived from `trading/config.py` (auto-trader risk + execution layer)
- Breaker logic in `trading/kill_switch.py` + `trading/risk_budget.py`
- Hedge logic in `trading/hedge_manager.py`
- See also: `pick-watchlist-coins` skill for watchlist construction
- See also: `add-score-modifier` skill for adding new modifiers behind feature flags
