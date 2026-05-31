"""
ai_scanner.py — Proactive setup scanner.

Scans a watchlist of USDT-M futures for trade setups scored 6-10/10.

Three-stage pipeline:
  Stage 1 — Confluence filter (parallel, no AI):
             Computes multi-TF RSI/MACD/EMA/ADX signals for all symbols.
             Passes symbols with ≥ 2 signals aligned in one direction.

  Stage 2 — Technical quality gate (no AI, instant):
             Rejects severely overextended RSI, absent S/R structure, flat ADX.

  Stage 3 — AI scoring (parallel Claude calls, finalists only):
             Claude evaluates each finalist and returns scored setups 6-10/10
             with specific entry zone, SL, TP1, TP2 and rationale for each level.
             Setups below 6 are discarded.

Results cached for 30 minutes. Scan runs in a background thread.
"""

import copy
import json
import logging
import os
from prompt_fragments import SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES, DRAW_ON_LIQUIDITY_RULES
from constants import (MODEL, FAST_MODEL,
    SCANNER_MIN_SCORE, SCANNER_FULL_DETAIL_TOP_N, SCANNER_CACHE_TTL,
    SCANNER_MAX_WORKERS, PROMPT_CACHE_MIN_CHARS)
import datetime
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ai_client import send as ai_send
from database import db_conn
from trade_history import get_symbol_summary
from helpers import strip_fence, build_cached_messages
import chart_context
import market_context
import ai_rulebook

logger = logging.getLogger(__name__)
import gemini_client
import agent_orchestrator
import nansen_client

# ── Re-exports from sub-modules ────────────────────────────────────────────────
from scanner_watchlist import _BITGET_WATCHLIST, DEFAULT_WATCHLIST, _get_extended_watchlist, _get_dynamic_watchlist
from scanner_criteria import (
    CRITERIA_DEFAULTS,
    _CRITERIA_DISABLED_LABELS,
    _disabled_criteria_block,
    _is_in_kill_zone,
    _annotate_kill_zone,
)
from scanner_prompts import (
    _build_prompt,
    _build_shared_prefix,
    _build_scanner_stable,
    _quick_score,
    _build_batch_prompt,
)
from scanner_stages import (
    _fetch_one,
    _stage2,
    _get_scan_macro_context,
    _apply_macro_cap,
    enrich_finalists_1h,
)

# ── Watchlist mutable state (kept here so tests can reset ai_scanner.BINANCE_WATCHLIST) ──
# Static symbol data lives in scanner_watchlist.py; the lazy-load cache lives here.
BINANCE_WATCHLIST: list = []
_binance_watchlist_loaded = False


def _get_default_watchlist() -> list:
    """Return merged Bitget+Binance watchlist, fetching Binance on first call."""
    global BINANCE_WATCHLIST, _binance_watchlist_loaded
    if not _binance_watchlist_loaded:
        _binance_watchlist_loaded = True
        try:
            import ccxt_client as _ccxt
            BINANCE_WATCHLIST = _ccxt.get_binance_futures_symbols()
        except Exception:
            BINANCE_WATCHLIST = []
    return list(dict.fromkeys(
        _BITGET_WATCHLIST + [s for s in BINANCE_WATCHLIST if s not in set(_BITGET_WATCHLIST)]
    ))


# ── Scan state ─────────────────────────────────────────────────────────────────

_state: dict = {
    "status":          "idle",   # idle | running | completed | error
    "stage":           0,        # 0=idle, 1=confluence, 2=quality gate, 3=AI scoring
    "stage_label":     "",       # e.g. "Stage 1 — Confluence filter"
    "stage_detail":    "",       # e.g. "42 / 100 symbols processed"
    "stage_progress":  0,        # 0–100 within current stage
    "started_at":      None,
    "completed_at":    None,
    "duration_sec":    None,
    "setups":          [],
    "scanned":         0,
    "after_filter":    0,
    "error":           None,
    "min_score":       SCANNER_MIN_SCORE,
    "macro_ctx":       {},       # macro context fetched once per scan run
}
_state_lock   = threading.Lock()
_cancel_event = threading.Event()   # set to request cancellation; cleared on each new scan

# Completion hooks — called with the setups list when any scan finishes.
# Registered by scanner_scheduler so both manual and scheduled scans trigger TG.
# NOT fired on cancellation.
_completion_hooks: list = []


def register_completion_hook(fn) -> None:
    """Register a callable(setups: list) fired when any scan completes."""
    if fn not in _completion_hooks:
        _completion_hooks.append(fn)


def get_state() -> dict:
    with _state_lock:
        return copy.deepcopy(_state)


def cancel_scan() -> bool:
    """Request cancellation of the running scan. Returns True if a scan was running."""
    with _state_lock:
        if _state["status"] != "running":
            return False
    _cancel_event.set()
    return True


def _update(**kwargs):
    with _state_lock:
        _state.update(kwargs)


# ── Stage 1 wrapper — injects _update for live progress ───────────────────────

def _stage1(symbols: list, min_score: int = SCANNER_MIN_SCORE) -> list:
    """Return [(symbol, ctx, conf, direction)] with enough aligned signals.
    Emits live progress via _update() as futures complete."""
    from scanner_stages import _stage1 as _stage1_impl
    return _stage1_impl(symbols, min_score, _update_fn=_update)


# ── Stage 3: AI scoring ─────────────────────────────────────────────────────────

def _score_finalists_with_agents(finalists: list, conn,
                                 min_score: int = SCANNER_MIN_SCORE,
                                 macro_ctx: dict = None) -> list:
    """
    Run the agent pipeline (DataCollector → Interpreter → Sentiment →
    Reviewer → TradePrep) for each finalist. Replaces the inline Sonnet batch call.

    finalists: list of (sym, ctx, conf, direction, quick_score, rationale) tuples
    Returns list of setup dicts compatible with the scanner output format.
    """
    import agent_data_collector
    import agent_data_interpreter
    import agent_market_sentiment
    import agent_data_reviewer
    import agent_orchestrator

    macro = macro_ctx or {}
    results = []
    for sym, ctx, conf, direction, quick_score, rationale in finalists:
        try:
            collected = agent_data_collector.run({
                "symbol": sym, "direction": direction, "timeframes": ["1H", "4H", "1D"],
            })
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_i = ex.submit(agent_data_interpreter.run, {"collected": collected})
                f_s = ex.submit(agent_market_sentiment.run,
                                {"symbol": sym, "direction": direction,
                                 "collected": collected})
            interpreted = f_i.result()
            sentiment   = f_s.result()
            reviewed = agent_data_reviewer.run({
                "interpreted": interpreted, "symbol": sym,
                "direction": direction, "setup_type": "scanner",
            }, conn)
            prep = agent_orchestrator.run_scanner_prep(
                sym, direction, collected, interpreted, reviewed, sentiment, conn,
            )
            score = prep.get("setup_score", 0)
            # Apply macro regime cap before threshold check
            score, macro_warnings = _apply_macro_cap(float(score), macro)
            if macro_warnings:
                logger.info("macro cap applied to %s: %s", sym, "; ".join(macro_warnings))
            # Operator-behavior caps (personal bad-hour, reversal-archetype)
            # were removed 2026-05-25 per the operator's directive: the auto-
            # trader works with market facts and sentiment data only, not the
            # operator's own historical loss patterns. Kill zones (market
            # session timing) and macro caps (event risk) are KEPT because
            # those are market facts, not operator behavior.
            #
            # Archetype is still detected here for downstream classification
            # (logging / hindsight tags), just no longer used as a score cap.
            try:
                from scanner_prompts import _detect_archetype as _det_arch
                archetype = _det_arch(ctx, direction, symbol=sym)
            except Exception:
                archetype = ""

            # ── PO3 modifiers (added 2026-05-23) ─────────────────────────
            # Direction-aware: Premium/Discount + nearest unfilled FVG +
            # institutional session timing (kill zones). Each can shift the
            # score by ±0.3. Applied AFTER the strategic caps so the personal
            # bad-hour cap (5.5) still wins when it triggers.
            range_label = ""
            fvg_label   = ""
            kz_label    = ""
            try:
                from chart_candles import get_candles
                from chart_confluence import range_position, directional_range_weight
                from chart_fvg import nearest_fvg_signal
                from scanner_criteria import _apply_kill_zone_modifier
                df_4h_for_po3 = get_candles(sym, "4H", limit=40)
                if df_4h_for_po3 is not None and len(df_4h_for_po3) >= 3:
                    # Premium/Discount — directional modifier
                    range_info = range_position(df_4h_for_po3, lookback=40)
                    rng_w = directional_range_weight(range_info, direction)
                    if rng_w != 0:
                        score = max(0.0, min(10.0, score + rng_w))
                        range_label = (f"PO3 range: {range_info.get('label')} "
                                       f"({range_info.get('pct',0)*100:.0f}%) "
                                       f"→ {rng_w:+.1f}")
                        logger.info("range mod applied to %s: %s", sym, range_label)
                    elif range_info:
                        range_label = (f"PO3 range: {range_info.get('label')} "
                                       f"({range_info.get('pct',0)*100:.0f}%)")

                    # Feature 21 — Wyckoff phase classification (context tag only)
                    try:
                        from chart_confluence import classify_wyckoff_phase
                        _inds_4h = (ctx.get("4H") or {}).get("indicators") or {}
                        wp = classify_wyckoff_phase(
                            range_info,
                            _inds_4h.get("adx") or {},
                            _inds_4h.get("ema") or {},
                        )
                        if wp.get("phase") not in (None, "unknown"):
                            range_label = (range_label + " · " + wp.get("label", "")).strip(" ·") \
                                          if range_label else wp.get("label", "")
                    except Exception:
                        pass

                    # FVG — directional modifier
                    cur_px = float(df_4h_for_po3["close"].iloc[-1])
                    fvg_sig = nearest_fvg_signal(df_4h_for_po3, cur_px, direction)
                    if fvg_sig.get("weight"):
                        score = max(0.0, min(10.0, score + fvg_sig["weight"]))
                        fvg_label = f"FVG: {fvg_sig['label']} → {fvg_sig['weight']:+.2f}"
                        logger.info("FVG mod applied to %s: %s", sym, fvg_label)

                # Kill-zone session modifier — independent of direction
                score, kz_warnings = _apply_kill_zone_modifier(score)
                if kz_warnings:
                    kz_label = kz_warnings[0]
                    logger.info("kill zone mod applied to %s: %s", sym, kz_label)
            except Exception as e:
                logger.debug("PO3 modifier error on %s: %s", sym, e)

            # ── Bear-phase alignment modifier (added 2026-05-23) ─────────
            # Classify the current macro phase (distribution/decline/
            # capitulation/recovery) from F&G + BTC 24h change + BTC.D +
            # HMM regime, then apply ±0.3 if the setup direction agrees
            # or fights the phase bias. Sonnet consensus is still the
            # final filter — this is a directional context signal, not
            # a hard gate.
            bp_label = ""
            try:
                from bear_phase import classify_phase, phase_alignment_weight
                from chart_confluence import _get_ticker_change_cached
                # BTC 24h change isn't in macro_ctx — fetch directly (cached
                # for 5min in chart_confluence so this is one HTTP every
                # 5min, not per setup).
                btc_24h = _get_ticker_change_cached("BTCUSDT")
                # F&G pause: set fng=None when FUTURES_AI_FNG_PAUSED=1 (default
                # ON since 2026-06-01). Forces bear_phase to classify using
                # BTC 24h + BTC.D + VIX + HMM only — market structure, not
                # sentiment. Set FUTURES_AI_FNG_PAUSED=0 to re-enable.
                import os as _os
                _fng_paused = _os.environ.get("FUTURES_AI_FNG_PAUSED", "1").strip() == "1"
                bp = classify_phase(
                    btc_change_24h_pct=btc_24h,
                    fng=None if _fng_paused else macro.get("fear_greed"),
                    btc_dom_pct=macro.get("btc_dominance"),
                    vix=macro.get("vix"),
                    hmm_regime=macro.get("hmm_regime") or macro.get("regime"),
                )
                bp_w, bp_reason = phase_alignment_weight(bp.get("phase",""), direction)
                if bp_w != 0:
                    score = max(0.0, min(10.0, score + bp_w))
                    bp_label = f"bear-phase: {bp.get('label','')} → {bp_w:+.1f}"
                    logger.info("bear-phase mod applied to %s: %s", sym, bp_label)
                elif bp.get("phase") and bp["phase"] != "unknown":
                    bp_label = f"bear-phase: {bp.get('label','')}"
            except Exception as e:
                logger.debug("bear-phase modifier error on %s: %s", sym, e)

            # ── HMM regime alignment modifier (added 2026-05-24) ─────────
            # Direct ±0.2 modifier based on macro HMM regime (trending_up/
            # _down/ranging) vs setup direction. Smaller magnitude than
            # bear_phase (±0.3) because the two can stack in aligned cases.
            # Gated on confidence > 0.6 — boundary regimes don't vote.
            hmm_label = ""
            try:
                from market_regime import hmm_alignment_weight, detect_regime
                regime = detect_regime()
                # btc_24h fetched a few lines above for bear_phase — reuse it
                # so HMM can sanity-check its label against actual price.
                hmm_w, hmm_reason = hmm_alignment_weight(regime, direction, btc_24h)
                if hmm_w != 0:
                    score = max(0.0, min(10.0, score + hmm_w))
                    hmm_label = hmm_reason
                    logger.info("HMM mod applied to %s: %s", sym, hmm_label)
                elif hmm_reason:
                    hmm_label = hmm_reason
            except Exception as e:
                logger.debug("HMM modifier error on %s: %s", sym, e)

            # ── CPR alignment modifier (added 2026-05-24) ─────────────────
            # Central Pivot Range from prior day's H/L/C — different math
            # basis (daily levels) and timescale (daily) than PO3 (intraday)
            # and HMM (rolling regime). Direction-aware ±0.3 modifier.
            # Gated on FUTURES_AI_CPR_ENABLED env knob (default ON).
            cpr_label = ""
            try:
                import os
                if int(os.environ.get("FUTURES_AI_CPR_ENABLED", "1")):
                    from chart_candles import get_candles
                    from chart_cpr import (compute_cpr_from_df,
                                            two_day_relationship,
                                            cpr_alignment_weight)
                    df_1d = get_candles(sym, "1D", limit=100)
                    if df_1d is not None and len(df_1d) >= 3:
                        curr_cpr = compute_cpr_from_df(df_1d)
                        prev_cpr = compute_cpr_from_df(df_1d.iloc[:-1])
                        two_day  = two_day_relationship(curr_cpr, prev_cpr)
                        ema_4h   = ((ctx.get("4H") or {}).get("indicators") or {}).get("ema") or {}
                        curr_px  = float(ema_4h.get("current_price") or 0)
                        cpr_w, cpr_reason = cpr_alignment_weight(
                            curr_cpr, curr_px, two_day, direction)
                        if cpr_w != 0:
                            score = max(0.0, min(10.0, score + cpr_w))
                            cpr_label = f"CPR: {two_day.get('label','')} → {cpr_w:+.1f}"
                            logger.info("CPR mod applied to %s: %s", sym, cpr_label)
                        elif two_day.get("label"):
                            cpr_label = f"CPR: {two_day.get('label','')}"
            except Exception as e:
                logger.debug("CPR modifier error on %s: %s", sym, e)

            # ── Initial Balance modifier (added 2026-05-24) ───────────────
            # Price vs first-60min H/L of NYSE session (14:30-15:30 UTC).
            # ±0.2 once IB is_complete: above IB high + Long = +0.2 etc.
            # Gated on FUTURES_AI_IB_ENABLED (default ON).
            ib_label = ""
            try:
                import os as _os
                if int(_os.environ.get("FUTURES_AI_IB_ENABLED", "1")):
                    from chart_candles import get_candles as _get_candles_ib
                    from chart_session import compute_initial_balance, ib_alignment_weight
                    # 1H candles — need enough bars to cover NYSE session window
                    df_1h = _get_candles_ib(sym, "1H", limit=200)
                    if df_1h is not None and len(df_1h) >= 2:
                        ib = compute_initial_balance(df_1h)
                        ema_4h = ((ctx.get("4H") or {}).get("indicators") or {}).get("ema") or {}
                        curr_px = float(ema_4h.get("current_price") or 0)
                        ib_w, ib_reason = ib_alignment_weight(ib, curr_px, direction)
                        if ib_w != 0:
                            score = max(0.0, min(10.0, score + ib_w))
                            ib_label = ib_reason
                            logger.info("IB mod applied to %s: %s", sym, ib_label)
                        elif ib_reason:
                            ib_label = ib_reason
            except Exception as e:
                logger.debug("IB modifier error on %s: %s", sym, e)

            # ── VuManChu unified signal modifier (added 2026-05-27) ────────
            # Combines Cipher A markers (EMA ribbon + yellow_x / blood_diamond /
            # long_ema / short_ema / red_cross / blue_triangle / red_diamond /
            # bull_candle) + Cipher B dots/divergences into a single score in
            # [-1.0, +1.0]. Maps direction-aware to setup_score: bullish VMC
            # boosts Longs / penalises Shorts (and vice versa). Cap ±0.4.
            #
            # MTF EMA bias is intentionally SKIPPED here (include_mtf=False) —
            # it would force 6 extra candle fetches per setup. The MTF bias
            # remains available via /api/chart/vmc-signal for manual study.
            # Gated on FUTURES_AI_VMC_ENABLED (default ON).
            vmc_label = ""
            try:
                import os as _os_v
                if int(_os_v.environ.get("FUTURES_AI_VMC_ENABLED", "1")):
                    import chart_vmc_signals
                    # Use the 4H candle df already fetched for this symbol
                    df_4h = ((ctx.get("4H") or {}).get("candles_df")
                             or (ctx.get("4H") or {}).get("df"))
                    if df_4h is None:
                        # Fallback: refetch (chart_candles is LRU-cached so usually free)
                        from chart_candles import get_candles as _gc_v
                        df_4h = _gc_v(sym, "4H", limit=200)
                    if df_4h is not None and not df_4h.empty:
                        vmc = chart_vmc_signals.compute_unified_signal(
                            sym, df_4h, include_mtf=False)
                        v_score = float(vmc.get("score") or 0.0)
                        # Direction-aware: bullish VMC helps Longs, hurts Shorts
                        sign = 1 if (direction or "").lower() == "long" else -1
                        vmc_w = round(v_score * sign * 0.4, 3)
                        if vmc_w != 0:
                            score = max(0.0, min(10.0, score + vmc_w))
                            # Concise label: list active signals with their weights
                            actives = vmc.get("active_signals") or {}
                            top = sorted(actives.items(),
                                         key=lambda kv: abs(kv[1]), reverse=True)[:3]
                            sigs_str = ", ".join(f"{k}({v:+.2f})" for k, v in top)
                            vmc_label = (f"VMC {vmc.get('label')} "
                                         f"score={v_score:+.2f} → {vmc_w:+.2f} "
                                         f"({sigs_str or 'no active'})")
                            logger.info("VMC mod applied to %s: %s", sym, vmc_label)
            except Exception as e:
                logger.debug("VMC modifier error on %s: %s", sym, e)

            # (Operator-behavior bad-hour re-apply removed 2026-05-25 — see note above.)

            # ── N-3 noise gates (added — Master plan Week 7) ──────────────
            # Wick rejection + ADX <20 + BB squeeze. Modifier-only by default;
            # ADX hard-veto opt-in via FUTURES_AI_ADX_HARD_GATE=1.
            n3_label = ""
            try:
                from trading import noise_gates
                ctx_4h     = ctx.get("4H") or {}
                ind_4h     = ctx_4h.get("indicators") or {}
                adx_value  = (ind_4h.get("adx") or {}).get("value")
                bb_info    = ind_4h.get("bollinger") or {}
                current_bw = bb_info.get("band_width")
                bw_history = ctx_4h.get("bb_widths_history") or []
                last_candle = None
                df_4h_for_n3 = ctx_4h.get("candles_df")
                try:
                    if df_4h_for_n3 is not None and len(df_4h_for_n3) >= 1:
                        last = df_4h_for_n3.iloc[-1]
                        last_candle = {"open": float(last["open"]),
                                        "high": float(last["high"]),
                                        "low": float(last["low"]),
                                        "close": float(last["close"])}
                except Exception:
                    last_candle = None
                arch_hint = (prep.get("archetype") or prep.get("setup_type") or "")
                n3 = noise_gates.evaluate_all(
                    last_candle=last_candle,
                    adx_4h=adx_value,
                    bb_widths_history=bw_history,
                    bb_current_width=current_bw,
                    direction=direction,
                    archetype=arch_hint,
                )
                if n3.get("veto"):
                    logger.info("N-3 hard veto on %s: %s", sym, "; ".join(n3["reasons"]))
                    continue
                if n3["total_delta"] != 0:
                    score = max(0.0, min(10.0, score + n3["total_delta"]))
                    n3_label = "; ".join(n3["reasons"])
                    logger.info("N-3 mod applied to %s: %+.2f (%s)", sym, n3["total_delta"], n3_label)
            except Exception as e:
                logger.debug("N-3 modifier error on %s: %s", sym, e)

            if score < min_score:
                continue
            entry_p = float(prep.get("entry_price", 0) or 0)
            if not entry_p:
                # Fallback: use current price from already-computed 4H chart context
                ema_4h = ctx.get("4H", {}).get("indicators", {}).get("ema") or {}
                entry_p = float(ema_4h.get("current_price") or 0)
            urgency = ("Now" if score >= 9 else
                       "1-4h" if score >= 8 else
                       "Today" if score >= 7 else "1-3 days")

            # Enforce TP1 ≥ 1× ATR_4H, TP2 ≥ 2× ATR_4H. The agent pipeline
            # historically produced very tight TPs that printed on noise.
            # Also enforce SL is on the correct side of entry and within
            # 0.5×-8× ATR_4H. Without the SL floor the executor was the
            # only place that caught wrong-side stops (WLDUSDT had the AI
            # publishing SL above entry on a Long; ATR repair at order
            # time worked but the journal still recorded the bad level).
            tp1_raw = prep.get("tp1_price", 0)
            tp2_raw = prep.get("tp2_price", 0)
            sl_raw  = prep.get("sl_price", 0)
            _tp_notes: list[str] = []
            _sl_notes: list[str] = []
            import trade_utils as _tu
            atr_4h = ((ctx.get("4H", {}).get("indicators", {})
                                   .get("atr") or {}).get("value", 0))
            if atr_4h:
                try:
                    tp1_raw, tp2_raw, _tp_notes = _tu.enforce_tp_floor(
                        entry_p, direction, tp1_raw, tp2_raw, atr_4h)
                    sl_raw, _sl_notes = _tu.enforce_sl_floor(
                        entry_p, direction, sl_raw, atr_4h)
                    # SafeZone SL (Feature 20, 2026-05-24): if SL ended up
                    # near a round number, push it 0.5× ATR further to dodge
                    # stop-hunt clusters. Joins the enforce pipeline.
                    import os as _os
                    if int(_os.environ.get("FUTURES_AI_SAFEZONE_SL_ENABLED", "1")):
                        sz_sl, sz_notes = _tu.safezone_sl(
                            entry_p, direction, sl_raw, atr_4h)
                        if sz_notes:
                            sl_raw = sz_sl
                            _sl_notes.extend(sz_notes)
                    if _sl_notes:
                        logger.info("SL floor applied to %s: %s",
                                    sym, "; ".join(_sl_notes))
                except Exception as e:
                    logger.warning("enforce_*_floor failed for %s: %s — "
                                   "levels left as agent provided", sym, e)
            else:
                logger.warning("enforce skipped for %s: atr_4h=0 — "
                               "wrong-side level repair impossible", sym)

            # Defence in depth: even after enforcement, validate that the
            # final geometry is consistent with the setup direction. Stage 1
            # sets `direction` from confluence; the LLM in agent_trade_prep
            # can emit a different direction silently when building levels.
            # If they disagree, the (direction, sl, tp1, tp2) tuple ends up
            # geometrically inverted (Short with sl<entry and tp>entry, or
            # mirror). The enforce functions can be skipped when ATR is
            # missing, so this validator is the last gate before consensus.
            _ok, _why = _tu.validate_direction_vs_levels(
                direction, entry_p, sl_raw, tp1_raw, tp2_raw)
            if not _ok:
                logger.warning(
                    "stage3_levels_dropped %s %s: %s — entry=%s sl=%s tp1=%s tp2=%s",
                    sym, direction, _why, entry_p, sl_raw, tp1_raw, tp2_raw,
                )
                try:
                    with db_conn() as _conn:
                        _conn.execute(
                            "INSERT INTO futures_ai_log(ts, event, symbol, direction, score, payload_json) "
                            "VALUES (datetime('now'), 'stage3_levels_dropped', ?, ?, ?, ?)",
                            (sym, direction, score, json.dumps({
                                "reason":  _why,
                                "entry":   entry_p,
                                "sl":      sl_raw,
                                "tp1":     tp1_raw,
                                "tp2":     tp2_raw,
                                "atr_4h":  atr_4h,
                                "archetype": archetype,
                            })),
                        )
                        _conn.commit()
                except Exception as _le:
                    logger.debug("stage3_levels_dropped log write failed: %s", _le)
                continue

            # `archetype` was already detected above for the reversal-cap step.

            # Compose a single PO3 + bear-phase summary line for Sonnet's
            # consensus call. Pulled from the labels we built earlier;
            # empty when nothing fired.
            _po3_bits = []
            if kz_label:    _po3_bits.append(kz_label.replace("PO3 session ", "").replace("'", ""))
            if range_label: _po3_bits.append(range_label.replace("PO3 range: ", "range:"))
            if fvg_label:   _po3_bits.append(fvg_label.replace("FVG: ", "FVG:"))
            if bp_label:    _po3_bits.append(bp_label.replace("bear-phase: ", "phase:"))
            if hmm_label:   _po3_bits.append(hmm_label.replace("HMM: ", "hmm:"))
            if cpr_label:   _po3_bits.append(cpr_label.replace("CPR: ", "cpr:"))
            if ib_label:    _po3_bits.append(ib_label.replace("IB: ", "ib:"))
            if vmc_label:   _po3_bits.append(vmc_label.split(" (")[0].replace("VMC ", "vmc:"))
            _po3_summary = f"PO3 [{' · '.join(_po3_bits)}]" if _po3_bits else ""

            base_summary = " · ".join(prep.get("key_conditions", [])[:2])
            full_summary = (base_summary + " · " + _po3_summary).strip(" ·") \
                            if _po3_summary else base_summary

            setup = {
                "_symbol":        sym,
                "symbol":         sym,
                "direction":      direction,
                "setup_score":    score,
                "setup_label":    prep.get("_model", ""),
                "trade_type":     archetype,
                "entry_zone":     {"low": entry_p, "high": entry_p,
                                   "rationale": "Agent pipeline entry level"},
                "sl_price":       sl_raw,
                "tp1_price":      tp1_raw,
                "tp2_price":      tp2_raw,
                "_tp_adjustments": _tp_notes,
                "_sl_adjustments": _sl_notes,
                "_po3_range":     range_label,
                "_po3_fvg":       fvg_label,
                "_po3_session":   kz_label,
                "_bear_phase":    bp_label,
                "_hmm_regime":    hmm_label,
                "_cpr":           cpr_label,
                "_ib":            ib_label,
                "_vmc":           vmc_label,
                "_n3_noise":      n3_label,
                "rr_ratio":       prep.get("rr_ratio", 0),
                "key_conditions": prep.get("key_conditions", []),
                "chart_png_b64":  prep.get("chart_png_b64", ""),
                "summary":        full_summary,
                "_quick_score":   quick_score,
                "_rationale":     rationale,
                "confluence_summary": conf.get("label", ""),
                "chart_pattern":  prep.get("chart_pattern") or None,
                "urgency":        urgency,
                "timeframe":      "Multi-TF (1D/4H/1H)",
            }
            if macro_warnings:
                setup["macro_warnings"] = macro_warnings
            results.append(setup)
        except Exception as e:
            logger.warning("agent scoring failed for %s: %s", sym, e)
    return results


# ── Symbol history helper ───────────────────────────────────────────────────────


# ── Background scan thread ─────────────────────────────────────────────────────

def _check_cancel() -> bool:
    """Returns True if cancellation was requested. Updates state and logs."""
    if _cancel_event.is_set():
        _update(status="cancelled", completed_at=time.time())
        logger.info("[Scanner] Scan cancelled by user request")
        return True
    return False


def _scan_thread(symbols: list, min_score: int = SCANNER_MIN_SCORE, criteria: dict = None):
    cr = criteria or CRITERIA_DEFAULTS
    t0 = time.time()
    _cancel_event.clear()   # reset any previous cancellation request

    # Fetch macro context once at the start — passed to all scoring stages
    macro_ctx = _get_scan_macro_context()
    _update(macro_ctx=macro_ctx)
    if macro_ctx.get("vix") or macro_ctx.get("macro_risk"):
        logger.info("macro ctx: VIX=%s regime=%s macro_risk=%s event=%s in %sh",
                    macro_ctx.get("vix"), macro_ctx.get("regime"),
                    macro_ctx.get("macro_risk"), macro_ctx.get("next_event"),
                    macro_ctx.get("hours_until"))

    _update(
        status="running", started_at=t0, error=None, setups=[], scanned=0, after_filter=0,
        min_score=min_score,
        stage=1, stage_label="Stage 1 — Confluence filter",
        stage_detail=f"Fetching multi-TF data for {len(symbols)} symbols…",
        stage_progress=0,
    )

    try:
        # Stage 1 — confluence filter (emits per-symbol progress internally)
        candidates = _stage1(symbols, min_score)
        passed1 = len(candidates)
        if _check_cancel(): return

        # Stage 2 — technical quality gate
        _update(
            stage=2, stage_label="Stage 2 — Quality gate",
            stage_detail=f"{passed1} symbols passed confluence → applying technical filters…",
            stage_progress=0,
        )
        finalists = _stage2(candidates, min_score, criteria=cr)
        _update(scanned=len(symbols), after_filter=len(finalists),
                stage_detail=f"{passed1} passed confluence · {len(finalists)} passed quality gate",
                stage_progress=100)
        if _check_cancel(): return

        if not finalists:
            _update(status="completed", completed_at=time.time(),
                    duration_sec=round(time.time() - t0, 1),
                    stage=0, stage_label="", stage_detail="No candidates passed the quality gate")
            return

        # Shared context for all finalists
        try:
            mkt_ctx = market_context.get_market_context(
                [s for s, _, _, _ in finalists[:5]]
            )
            mkt_str = market_context.format_for_prompt(mkt_ctx)
        except Exception as e:
            logger.warning("market context fetch failed in scan: %s", e)
            mkt_str = ""

        # Append BTC market regime
        try:
            regime = market_context.get_btc_regime()
            regime_map = {"bull": "📈 BTC is in a BULL regime (EMA50 > EMA200) — favour long setups",
                          "bear": "📉 BTC is in a BEAR regime (EMA50 < EMA200) — favour short setups",
                          "range": "↔ BTC is in a RANGE/transition — both directions valid, be selective"}
            mkt_str = (mkt_str + "\n" if mkt_str else "") + f"BTC MARKET REGIME: {regime_map[regime]}"
        except Exception as e:
            logger.warning("scoring failed: %s", e)
        with db_conn() as conn:
            rulebook_str = ai_rulebook.get_rulebook_for_prompt(conn)
            histories = {s: get_symbol_summary(s, conn) for s, _, _, _ in finalists}

        # Nansen smart money signals — one API call for all finalists combined
        nansen_signals = {}
        if nansen_client.is_configured():
            _update(stage_detail="Fetching Nansen smart money signals…")
            try:
                finalist_syms  = [s for s, _, _, _ in finalists]
                nansen_signals = nansen_client.get_signals_for_symbols(finalist_syms)
                active = sum(1 for v in nansen_signals.values() if v.get("ok"))
                print(f"[Nansen] {active}/{len(finalist_syms)} finalists have smart money signal", flush=True)
            except Exception as e:
                print(f"[Nansen] Signal fetch failed: {e}", flush=True)

        # Enrich finalists with 1H chart data before AI scoring stages
        _update(stage_detail="Fetching 1H data for finalists…")
        finalists = enrich_finalists_1h(finalists)
        if _check_cancel(): return

        # Stage 3a — Quick score all finalists with Haiku (cheap pre-filter pass)
        quick_threshold = max(min_score - 1, 4)
        _update(
            stage=3, stage_label="Stage 3a — Haiku quick-score",
            stage_detail=f"Fast-scoring {len(finalists)} finalist{'s' if len(finalists)!=1 else ''} with Haiku…",
            stage_progress=0,
        )
        # Split cacheable stable_prefix from variable mkt_str. Anthropic caches
        # the stable block across all 30 symbol calls in this cycle (~10x cheaper
        # on input tokens after the 1st miss).
        stable_prefix = _build_scanner_stable(rulebook_str, min_score, criteria=cr)
        quick_results = []
        qs_done = [0]
        qs_total = len(finalists)

        # Cache warm-up: fire ONE call sequentially before the parallel batch
        # so Anthropic's prompt-cache write completes before the others read.
        # Without this, 10 concurrent workers all race the cache and every
        # call registers as a miss (observed 0/132k tokens cached in 24h).
        if finalists:
            warm_sym, warm_ctx, warm_conf, warm_dir = finalists[0]
            try:
                warm_r = _quick_score(warm_sym, warm_ctx, warm_conf, warm_dir,
                                       stable_prefix, mkt_str, quick_threshold)
                if warm_r is not None:
                    quick_results.append((warm_sym, warm_ctx, warm_conf, warm_dir,
                                          warm_r["score"], warm_r.get("reason", "")))
                qs_done[0] = 1
                _update(stage_detail=f"Haiku scoring: 1 / {qs_total} symbols (cache warmed)",
                        stage_progress=int(1 / qs_total * 100))
            except Exception as e:
                logger.warning("scanner_quick warm-up failed for %s: %s", warm_sym, e)
            remaining_finalists = finalists[1:]
        else:
            remaining_finalists = []

        # BUG-015 fix (2026-05-27): concurrency reduced 10 → 2 to stay under
        # free-tier per-minute rate limits when Anthropic is exhausted and
        # the cascade falls to Gemini. With 10 parallel workers all 4 Gemini
        # models cooldown simultaneously ("All cascade providers exhausted")
        # and every symbol returns None → 0/30 Stage 3a pass rate.
        # 2 workers + the natural ~1-2s call latency = ~30-60 calls/min
        # which fits within Gemini free-tier limits (4 models × 15 RPM).
        # Trade-off: Stage 3a takes ~30s instead of ~5s. Acceptable for
        # zero-cost AI. When user adds paid Gemini or new Anthropic credit,
        # this can go back up to 10 without harm (env-tunable).
        import os as _os_qs
        _qs_workers = int(_os_qs.environ.get("SCANNER_QUICK_WORKERS", "2"))
        with ThreadPoolExecutor(max_workers=_qs_workers) as ex:
            fq = {
                ex.submit(_quick_score, sym, ctx, conf, dir_,
                          stable_prefix, mkt_str, quick_threshold): (sym, ctx, conf, dir_)
                for sym, ctx, conf, dir_ in remaining_finalists
            }
            for f in as_completed(fq):
                if _cancel_event.is_set():
                    ex.shutdown(wait=False, cancel_futures=True)
                    _check_cancel()
                    return
                sym, ctx, conf, dir_ = fq[f]
                qs_done[0] += 1
                _update(
                    stage_detail  = f"Haiku scoring: {qs_done[0]} / {qs_total} symbols",
                    stage_progress= int(qs_done[0] / qs_total * 100),
                )
                r = f.result()
                if r is not None:
                    quick_results.append((sym, ctx, conf, dir_, r["score"], r.get("reason", "")))

        if _check_cancel(): return

        # BUG-005 diagnostic (2026-05-26): Stage 3 funnel by direction. If
        # Stage 2 had Shorts but Stage 3 has none, this surfaces WHERE they
        # were lost — Haiku quick-score in current implementation.
        s3_dirs = {"Long": 0, "Short": 0}
        for _, _, _, dir_, _, _ in quick_results:
            s3_dirs[dir_] = s3_dirs.get(dir_, 0) + 1
        # Compare to Stage 2 input (finalists in this scope)
        s2_dirs = {"Long": 0, "Short": 0}
        for _, _, _, dir_ in finalists:
            s2_dirs[dir_] = s2_dirs.get(dir_, 0) + 1
        logger.info("[scanner] Stage3a (Haiku) funnel: %d/%d passed | Long %d/%d Short %d/%d",
                    len(quick_results), len(finalists),
                    s3_dirs["Long"], s2_dirs["Long"],
                    s3_dirs["Short"], s2_dirs["Short"])

        # Sort by quick score, take top N for expensive full-detail pass
        quick_results.sort(key=lambda x: -x[4])
        top_finalists  = quick_results[:SCANNER_FULL_DETAIL_TOP_N]
        rest_finalists = quick_results[SCANNER_FULL_DETAIL_TOP_N:]

        # Stage 3b — Full detail with Sonnet: single batched call for all top-N
        _update(
            stage_label   = "Stage 3b — Sonnet full analysis",
            stage_detail  = f"Batch-scoring top {len(top_finalists)} setup{'s' if len(top_finalists)!=1 else ''} with Sonnet…",
            stage_progress= 0,
        )
        with db_conn() as conn:
            setups = _score_finalists_with_agents(top_finalists, conn, min_score=min_score,
                                                  macro_ctx=macro_ctx)
        _update(stage_progress=100)

        # Add non-top-N setups with Haiku score + one-sentence rationale.
        # BUG-003 fix (2026-05-26): also run the rule-based archetype classifier
        # — it's free (no AI calls, just rules on already-fetched candles), so
        # there's no reason these setups should have trade_type=None while the
        # top-3 do. Without this, the orchestrator's `low_conviction` archetype
        # gate effectively only applies to top-3 setups.
        from scanner_prompts import _detect_archetype as _det_arch
        for sym, ctx, conf, direction, score, reason in rest_finalists:
            inds  = ctx.get("4H", {}).get("indicators", {})
            price = inds.get("ema", {}).get("current_price")
            urg   = ("Now" if score >= 9 else
                     "1-4h" if score >= 8 else
                     "Today" if score >= 7 else "1-3 days")
            try:
                arch_rest = _det_arch(ctx, direction, symbol=sym)
            except Exception:
                arch_rest = ""
            setups.append({
                "symbol":            sym,
                "direction":         direction,
                "setup_score":       score,
                "setup_label":       "Quick score only",
                "trade_type":        arch_rest,
                "why_this_score":    reason or "No rationale (Haiku quick-score pass)",
                "quick_score_only":  True,
                "confluence":        conf.get("label", ""),
                "current_price":     price,
                "chart_pattern":     None,
                "urgency":           urg,
                "timeframe":         "4H",
            })

        # Attach Nansen smart money signal to each setup
        for setup in setups:
            sym = setup.get("_symbol") or setup.get("symbol", "")
            ns  = nansen_signals.get(sym, {})
            if ns.get("ok"):
                setup["nansen"] = {
                    "direction":   ns["direction"],
                    "strength":    ns["strength"],
                    "netflow_usd": ns["netflow_usd"],
                    "nof_traders": ns["nof_traders"],
                    "chain":       ns.get("chain", ""),
                }

        setups.sort(key=lambda x: -x.get("setup_score", 0))
        if _check_cancel(): return

        # Stage 3c — Gemini consensus for top-5 finalists (parallel, non-blocking)
        if setups and gemini_client.is_configured():
            _update(stage_detail="Stage 3c — Gemini consensus scoring top 5…")
            # Build symbol → chart_ctx map from top_finalists (sym, ctx, conf, dir_, score, reason)
            ctx_map = {sym: ctx for sym, ctx, _conf, _dir, _sc, _r in top_finalists}
            try:
                setups = agent_orchestrator.add_gemini_consensus(setups, ctx_map, max_setups=5)
            except Exception as e:
                logger.warning("Gemini consensus step failed: %s", e)

        _update(
            status="completed", setups=setups,
            completed_at=time.time(), duration_sec=round(time.time() - t0, 1),
            stage=0, stage_label="",
            stage_detail=f"{len(setups)} setup{'s' if len(setups)!=1 else ''} found in {round(time.time()-t0,1)}s",
        )

        # Fire completion hooks (registered by scanner_scheduler for TG + entry_watcher)
        for hook in list(_completion_hooks):
            try:
                hook(setups)
            except Exception as hook_err:
                logger.warning("Completion hook failed: %s", hook_err)

    except Exception as e:
        logger.exception("Scan thread failed")
        _update(status="error", error="Scan failed — check server logs",
                completed_at=time.time(), duration_sec=round(time.time() - t0, 1))


# ── Public API ─────────────────────────────────────────────────────────────────

def start_scan(symbols: list = None, min_score: int = SCANNER_MIN_SCORE,
               criteria: dict = None) -> bool:
    """
    Start a background scan. Returns False if already running or results are still
    fresh (< SCANNER_CACHE_TTL seconds old) AND the min_score hasn't changed.
    """
    with _state_lock:
        if _state["status"] == "running":
            return False
        completed_at   = _state.get("completed_at")
        score_unchanged = _state.get("min_score", SCANNER_MIN_SCORE) == min_score
        if completed_at and (time.time() - completed_at) < SCANNER_CACHE_TTL and score_unchanged:
            return False  # still fresh with same threshold

    syms = symbols or _get_dynamic_watchlist()
    cr   = criteria or CRITERIA_DEFAULTS
    t = threading.Thread(target=_scan_thread, args=(syms, min_score, cr), daemon=True)
    t.start()
    return True


def force_scan(symbols: list = None, min_score: int = SCANNER_MIN_SCORE,
               criteria: dict = None) -> bool:
    """Start a scan regardless of cache TTL. Returns False if already running."""
    with _state_lock:
        if _state["status"] == "running":
            return False
    syms = symbols or _get_dynamic_watchlist()
    cr   = criteria or CRITERIA_DEFAULTS
    t = threading.Thread(target=_scan_thread, args=(syms, min_score, cr), daemon=True)
    t.start()
    return True
