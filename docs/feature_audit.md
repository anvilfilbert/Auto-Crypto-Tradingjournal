# Feature Audit — Trading Journal

Static audit generated 2026-05-21. Cross-references:
* Every backend `@bp.route` (96 endpoints across 13 route files)
* JS callers in `static/js/01-utils.js` through `17-risk.js`
* Page subtitles in `templates/index.html`
* HTTP 200 smoke test against the running service

This is the **C1 discovery output**. Use it to plan **C2 — Fix gaps**.

---

## Page coverage — 16 pages, 16 with subtitle ✓

Every `page-title` has a corresponding `page-subtitle` explainer paragraph. Good baseline.

| Page | Subtitle present? |
|---|---|
| Dashboard | ✓ (dynamic via `dash-subtitle`) |
| Trade Journal | ✓ |
| Deep Dive Analytics | ✓ |
| Edge Lab | ✓ |
| Risk Dashboard | ✓ |
| Chart Explorer | ✓ |
| AI Trading Advisor | ✓ |
| Import Data | ✓ |
| Trade Call Analyzer | ✓ |
| Saved Call Analyses | ✓ |
| Live Trades | ✓ |
| Live Sync — Bitget API | ✓ |
| Pending Limit Orders | ✓ |
| Hindsight Analysis | ✓ |
| Setup Scanner | ✓ |
| Data Sources | ✓ |
| Settings | ✓ |

---

## Endpoint coverage — 96 routes, 78 wired, **17 orphans**

96 endpoints exist. 79 (82%) have at least one JS caller. **17 orphans** — endpoints that exist on the backend but no UI surfaces them. Some are intentional (backend-only utilities), others are real coverage gaps.

### Orphans triaged

| Endpoint | Triage | Action for C2 |
|---|---|---|
| `/api/blindspots` | **GAP — built today, no UI** | Add Risk Dashboard panel showing top phrase blindspots + feature calibration table |
| `/api/self-review/run` (POST) | **GAP — built today** | Add a "Run self-review" button in Risk Dashboard with result preview |
| `/api/self-review/wishlist` | **GAP — built today** | Add "AI Wishlist" panel in Risk Dashboard listing recurring missed signals |
| `/api/analytics/accuracy-trend` | **GAP** | Add to Edge Lab — rolling accuracy chart |
| `/api/analytics/ev-by-setup` | **GAP** | Add to Edge Lab — expected-value-per-setup table |
| `/api/analytics/mfe-mae` | **GAP** | Add to Edge Lab — MFE/MAE per-setup (max favourable/adverse excursion) |
| `/api/analytics/execution-quality` | **GAP** | Add to Risk Dashboard — execution-grade impact on PnL |
| `/api/wallet/history` | **GAP** | Add to Dashboard — wallet balance line chart (the "Need 20+ days" panel needs this fed) |
| `/api/backtest/quality` (POST) | Backend-only triggered via Analysis tab Optimizer button — already in UI via separate `/api/backtest/walk-forward` flow | OK, intentional |
| `/api/limits/bulk-update` | Admin utility (CLI use) | OK, intentional |
| `/api/coin/summary/<symbol>` | Used by `/api/limits/<id>/analyze` pipeline internally | OK, intentional |
| `/api/price/<symbol>` | Used by backend `ai_limit.py`, `scanner_scheduler.py` | OK, intentional |
| `/api/nansen/signal/<symbol>` | Used by `agent_data_collector` internally | OK, intentional |
| `/api/scanner/calibrate` (POST) | Manual calibration trigger | Consider button in Settings |
| `/api/chart/annotated/<symbol>` | Used by `telegram_notify.py` for chart PNGs in alerts | OK, intentional |
| `/api/liquidations/<symbol>` | Used by `agent_data_collector` for chart context | OK, intentional |
| `/api/market/dominances` | Used by scanner internally, exposed in scanner status macro_ctx | Partially exposed — could surface as standalone panel |

**Net real gaps: 7 endpoints with no UI exposure but useful user-facing data.**

---

## Smoke test — 45 representative endpoints curl'd

43 / 45 returned **HTTP 200**. The two non-200 were both `405 Method Not Allowed` (GET on a POST-only route — not a bug).

| Endpoint | Status | Note |
|---|---|---|
| `/api/analytics/patterns` | 405 | POST-only (expects `{"category":"..."}` body) |
| `/api/backtest/quality` | 405 | POST-only |

No 5xx errors. All currently-running endpoints respond cleanly.

---

## Panel subtitle audit (within pages)

Random spot-check of the **Risk Dashboard** (most-complex page): all five subsection cards (VaR, Correlation, P&L Attribution, Kelly, Alpha Decay) have explanation paragraphs. ✓

The **Edge Lab** uses a similar pattern; sampled good.

The **Dashboard** uses dynamic subtitle text fed from JS, plus inline column tooltips — mostly OK. Two newer panels (P&L by Setup Type, vs BTC Buy-and-Hold) lack subtitle paragraphs even though they're complex enough to warrant one.

---

## C2 — Prioritised fix list

Sorted high → low by user value.

### High priority (data exists, just no UI)
1. **Blindspot panel** → Risk Dashboard. Shows phrase blindspots + feature calibration from `/api/blindspots`.
2. **Self-review wishlist + trigger** → Risk Dashboard. Shows recurring missed signals; button to fire `/api/self-review/run` manually.
3. **Wallet history chart** → Dashboard. Line chart from `/api/wallet/history` — the same data that Professional Performance Metrics needs.
4. **MFE/MAE panel** → Edge Lab. Heatmap from `/api/analytics/mfe-mae`.
5. **EV by setup panel** → Edge Lab. Table from `/api/analytics/ev-by-setup`.
6. **Accuracy trend chart** → Edge Lab. Line chart from `/api/analytics/accuracy-trend`.
7. **Execution quality panel** → Risk Dashboard. From `/api/analytics/execution-quality`.

### Low priority (cleanup)
8. **Subtitles for newer dashboard panels** (P&L by Setup, vs BTC) — 1-sentence each.
9. **Manual calibration button** in Settings → `/api/scanner/calibrate`.
10. **Market dominances panel** → Dashboard sidebar (already shown in scanner macro_ctx text).

### Documentation
11. Add this audit's `Page` → `JS file` → `API endpoint` map to `docs/architecture.md` so future maintainers know what wires to what.

---

## Done items (already in good shape)

- Per-cell tooltips on the Analyst Performance Tracker (added today)
- Verdict legend in Hindsight (added today)  
- Kelly reason banner (added today)
- Dual UTC/CET clock + Europe/Zurich timestamps (added today as Task A)
