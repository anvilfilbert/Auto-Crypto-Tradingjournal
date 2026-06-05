# Self-Learning Architecture

Shipped 2026-05-31 (option-a, 12-week master plan in one day, commit `1a76c00`).
11 background loops · 5 specialised agents · 6 learners · 4 noise gates ·
daily Telegram report · auto_ai only.

> **Read also:** [`AUTO_AI_MASTER_PLAN.md`](AUTO_AI_MASTER_PLAN.md) (sequencing &
> reminders), [`AUTO_AI_LEARNING_ARCHITECTURE.md`](AUTO_AI_LEARNING_ARCHITECTURE.md)
> (deeper design), [`AUTO_AI_SPECIALIZED_AGENTS.md`](AUTO_AI_SPECIALIZED_AGENTS.md)
> (A-A → A-E), [`AUTO_AI_NOISE_DETECTION.md`](AUTO_AI_NOISE_DETECTION.md)
> (N-1 → N-4).

---

## Background loops (`monitor_scheduler.py`)

All daemon threads. Spawn order is staggered so initial bursts don't collide.

| # | Thread | Cadence | What | Writes |
|---|---|---|---|---|
| 1 | `sync`              | 5 min  | Bitget auto-sync (manual chain) | `positions`, `wallet_snapshots` |
| 2 | `blofin-sync`       | 5 min  | Blofin sync (manual chain) | `positions` |
| 3 | `monitor`           | 10 min | Live trade monitor + hedge_manager | `futures_ai_log`, `settings` |
| 4 | `daily-report`      | first cycle ≥ 09:00 UTC | Daily Telegram digest | Telegram |
| 5 | `r3-backfill`       | 1 h    | funding_paid_usd + liq_distance_atr | `positions` |
| 6 | `learner-symbol`    | 6 h    | L-0 per-symbol modifier | `learned_params` |
| 7 | `learner-time`      | 6 h    | L-2 session / DoW / hour | `learned_params` |
| 8 | `learner-threshold` | daily  | L-3 consensus_min_score (A-B gated) | `learned_params` |
| 9 | `post-mortem`       | 1 h    | A-C Haiku failure-mode classifier | `positions.postmortem_*` |
| 10 | `vpin`             | 5 min  | N-4 VPIN snapshot, top-20 watchlist | `vpin_snapshot` |
| 11 | `learner-tpsl`     | daily  | L-4 TP / SL ATR distance | `learned_params` |
| 12 | `learner-risk`     | daily  | L-5 Kelly risk + max_notional + time_stop | `learned_params` |
| 13 | `exec-quality`     | 1 h    | A-D slippage 7d aggregate | `settings.exec_quality_summary` |
| 14 | `self-review`      | 24 h   | Legacy architecture audit | `settings` |

---

## Daily Telegram report — 7 sections

Built by `trading/daily_report.py`. Each section pulls live data:

- **Performance** — `kill_switch.evaluate()` → 24h / 7d / 30d realised P&L + WR
- **Reminders** — Red-Team review (+14d) · Strategy Selector revisit (+30d) · DSPy follow-on
- **Learner activity (24h)** — `learner_log` last 24h applied / skipped / rejected
- **Noise gates (24h rejections)** — `futures_ai_log` rejected_* events grouped
- **Edge-decay watch** — `edge_decay.alerts_only` — CUSUM + Page-Hinkley per archetype
- **Top loss patterns (7d)** — `post_mortem.top_recurring_tags` — 10-class taxonomy
- **Execution quality (7d)** — `exec_quality.daily_report_line` — slippage avg / median / max bps

---

## Schema additions

All idempotent ALTER TABLE; old rows stay NULL and are skipped by the gates.

| Migration | Table / column | Purpose |
|---|---|---|
| 68 | `positions.funding_paid_usd` | R-3 funding-cost backfill |
| 69 | `positions.liq_distance_atr` | R-3 — skipped under cross margin |
| 70 | `learned_params` (new) | L-0 key/value store with pin + revert + R-5 metadata |
| 71 | `learner_log` (new) | Audit trail of every applied / skipped / rejected change |
| A-C | `positions.postmortem_{tag,severity,reason,evidence,done,cost_usd}` | A-C outputs |
| L-4 | `positions.{mfe_atr_4h,mae_atr_4h}` | MFE/MAE in ATR units — TP/SL learner input |
| A-D | `positions.{intended_entry,slippage_bps}` | Exec-Quality Monitor input |
| N-4 | `vpin_snapshot` (new) | ts, symbol, vpin, n_buckets, bucket_volume, window_minutes |

---

## Reading the system live

- **Futures-AI Statistics page** → bottom panels show every learner write,
  every noise-gate rejection, every edge-decay alert.
- **Daily Telegram digest** → 09:00 UTC condensed summary of all of the above.
- `GET /api/futures-ai/l7-panels` → JSON of learned_log + noise_gates +
  reminders + edge_decay (5s cache, &lt;200ms).
- `GET /api/futures-ai/learned-params` → full key/value snapshot for
  operator review.
- `POST /api/futures-ai/learner/symbol` → manual trigger for the per-symbol
  learner.
- `POST /api/futures-ai/r3-backfill` → manual trigger for funding/liq
  backfill.

---

## Operator overrides

`learned_params` rows accept three operator actions:

- `pinned=1` — freezes the value against further learner writes.
- `revert(key)` — restores the most recent prior value; `revert_count` and
  `last_revert_at` track auto-reverts (the validator can flip back to the
  prior value if a metric tanks after a change).
- `unpin(key)` — allows learners to resume writing.

---

## Order of pre-order vetos (do not reorder lightly)

In `signal_consensus.evaluate`:

1. `low_score` (below min)
2. **N-1** — consensus variance (|scanner − ai| > 2.5)
3. **N-4** — VPIN toxicity (≥ 0.70)
4. **A-E** — Cascade Predictor (risk ≥ 0.75 + side-at-risk)
5. AI direction mismatch
6. AI critical warning
7. *(approval)*
8. **A-A** — Red-Team (last because it spends Haiku tokens)

Cheaper / static checks always run first.

---

## See also

- [`AI_ARCHITECTURE.md`](AI_ARCHITECTURE.md) — agent pipeline & cascade
- [`SCANNER_PIPELINE.md`](SCANNER_PIPELINE.md) — Stage-3 N-3 modifiers,
  post-consensus chain
- [`DATA_SOURCES.md`](DATA_SOURCES.md) — Layer 5 (VPIN, cascade fusion)
- [`AUTO_AI_MASTER_PLAN.md`](AUTO_AI_MASTER_PLAN.md) — 12-week roadmap
  (compressed to one day for option-a)
- [`architecture.md`](architecture.md) — full system architecture
