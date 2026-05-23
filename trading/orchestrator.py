"""
trading.orchestrator — the brain.

Two hooks, called from existing schedulers:

  on_scan_completed(scanner_state)
    Called by scanner_scheduler after each scan cycle finishes.
    Walks the scanner's score-7+ setups; for each:
      1. can_open_new_trade(conn)? else log + skip
      2. signal_consensus.evaluate(setup, conn)? else log + skip
      3. risk_budget.size_trade(score, entry, sl)? else log + skip
      4. paper mode → paper.open_paper_trade
         real mode  → executor.open_real_trade (defers to bitget_trader)

  on_monitor_cycle()
    Called by monitor_scheduler every 10 min.
    - paper mode → paper.manage_paper_positions(mark_price_lookup)
    - real mode  → executor.manage_real_positions()
    Also runs the global Pause Now / Pause After Close enforcement.

Mode is determined by trading.config.is_real_mode(); when paper, no
Bitget write calls happen — strictly observation. The real-mode
executor is built once paper has run for 3-7 days and the operator
flips FUTURES_AI_MODE=real.

This module is the single chokepoint where 'a real order might fire'.
Every state transition writes to futures_ai_log so the operator can
reconstruct the exact decision chain after the fact.
"""
from __future__ import annotations

import json
import threading
from typing import Optional

from . import config as fa_config
from . import kill_switch
from . import risk_budget
from . import signal_consensus
from . import paper


# ── Idempotency — don't re-evaluate the same scanner setup twice ────────────

_evaluated_setups: set[tuple[str, str, str]] = set()   # (symbol, dir, scan_ts)
_lock = threading.Lock()


def _setup_key(setup: dict, scan_ts: str) -> tuple:
    return (setup.get("symbol") or "",
            (setup.get("direction") or "").lower(),
            str(scan_ts))


# ── Hook 1: scanner-side ──────────────────────────────────────────────────────

def on_scan_completed(scanner_state: dict) -> dict:
    """
    Called after each scanner cycle. Evaluates every score-≥-threshold
    setup against the kill switch + consensus + sizing pipeline, opens
    paper positions when all checks pass.

    Returns a summary dict for logging:
      {evaluated, rejected_killswitch, rejected_consensus, rejected_sizing, opened}
    """
    from database import db_conn

    if not fa_config.is_enabled():
        return {"reason": "FUTURES_AI_ENABLED=0", "skipped": True}

    setups = scanner_state.get("setups") or []
    scan_ts = scanner_state.get("completed_at") or ""

    summary = {
        "evaluated":             0,
        "rejected_killswitch":   0,
        "rejected_consensus":    0,
        "rejected_sizing":       0,
        "opened":                0,
        "errors":                0,
    }

    # Concurrent-position cap is enforced via kill_switch._open_position_count
    # which queries the relevant table (paper_positions in paper mode,
    # positions WHERE chain='auto_ai' in real mode). Each open path
    # commits before returning, so subsequent iterations see the
    # updated count. No within-batch counter needed.

    with db_conn() as conn:
        for setup in setups:
            try:
                key = _setup_key(setup, scan_ts)
                with _lock:
                    if key in _evaluated_setups:
                        continue
                    _evaluated_setups.add(key)
                    # Bound the set so it doesn't grow forever
                    if len(_evaluated_setups) > 500:
                        _evaluated_setups.clear()

                score = int(setup.get("setup_score") or 0)
                from constants import SCANNER_MIN_SCORE
                if score < SCANNER_MIN_SCORE:
                    continue
                summary["evaluated"] += 1

                # 1. kill switch — _open_position_count already reflects
                # newly-opened positions in this batch because each open
                # path (paper.open_paper_trade / executor.open_real_trade)
                # commits the INSERT before returning. The earlier
                # opened_this_batch addition double-counted those, halving
                # the effective cap. Now we trust the DB count alone.
                # Pass scanner_score so the elite bypass (scanner==10) can
                # let rare setups through even when the soft cap is full.
                can_trade, reason = kill_switch.can_open_new_trade(
                    conn, scanner_score=score)
                if not can_trade:
                    summary["rejected_killswitch"] += 1
                    _log(conn, "rejected_killswitch", setup, reason)
                    continue

                # 2. consensus — gated by CONSENSUS_MIN_SCORE for cost.
                # Setups between SCANNER_MIN_SCORE and CONSENSUS_MIN_SCORE
                # skip the Sonnet second-opinion entirely and feed paper
                # directly. This is a budget knob during paper validation;
                # bump CONSENSUS_MIN_SCORE down to 7 once Anthropic credit
                # has runway.
                if score >= fa_config.CONSENSUS_MIN_SCORE:
                    verdict = signal_consensus.evaluate(setup, conn)
                    if not verdict["approved"]:
                        summary["rejected_consensus"] += 1
                        # consensus.evaluate already logged the rejection
                        continue
                    # Elite-bypass guard — scanner==10 admitted this setup
                    # past a full soft cap on the promise of being a true
                    # 10/10. If Sonnet didn't verify it at 10, re-apply
                    # the normal cap so a non-elite trade can't sneak
                    # through the elite slot.
                    n_open_now = kill_switch._open_position_count(conn)
                    used_bypass = (score >= fa_config.ELITE_BYPASS_SCORE
                                   and n_open_now >= fa_config.MAX_CONCURRENT_POSITIONS)
                    if used_bypass and verdict["consensus_score"] < fa_config.ELITE_BYPASS_SCORE:
                        summary["rejected_killswitch"] += 1
                        _log(conn, "rejected_killswitch", setup,
                             f"elite bypass revoked — scanner {score}/10 but "
                             f"consensus {verdict['consensus_score']}/10 not "
                             f"verified, soft cap {fa_config.MAX_CONCURRENT_POSITIONS} "
                             f"applies")
                        continue
                else:
                    # Build a synthetic verdict so downstream sizing/open
                    # sees the same shape. consensus_score = scanner score
                    # since there's no AI second opinion.
                    _log(conn, "consensus_skipped", setup,
                         f"score {score} < CONSENSUS_MIN_SCORE "
                         f"{fa_config.CONSENSUS_MIN_SCORE}")
                    verdict = {
                        "approved":        True,
                        "consensus_score": score,
                        "reason":          "consensus skipped (budget knob)",
                        "scanner":         {
                            "score": score,
                            "direction": setup.get("direction"),
                            "archetype": setup.get("trade_type") or "—",
                        },
                        "ai":              None,
                    }

                # 3. sizing
                from trading.kill_switch import _equity_now
                equity = _equity_now(conn)
                sizing = risk_budget.size_trade(
                    score   = verdict["consensus_score"],
                    entry   = setup.get("entry_zone", {}).get("low") or setup.get("entry_price"),
                    sl      = setup.get("sl_price"),
                    equity_usdt = equity,
                    conn    = conn,
                )
                if not sizing:
                    summary["rejected_sizing"] += 1
                    _log(conn, "rejected_sizing", setup,
                         "risk_budget returned None — SL too tight or score below floor")
                    continue

                # 4. dispatch — paper or real
                # Apply Opus consensus overrides when present (signal_consensus
                # populates `_override_*` keys on the setup dict). Safety: SL
                # override is already pre-validated to be tighter than scanner's,
                # entry can shift up to 2% drift, TP ladder is monotonic.
                ov_entry = setup.get("_override_entry")
                ov_sl    = setup.get("_override_sl")
                ov_tps   = setup.get("_override_tp_prices") or []
                scanner_entry = setup.get("entry_zone", {}).get("low") or setup.get("entry_price")
                entry_px = ov_entry or scanner_entry
                sl_px    = ov_sl    or setup.get("sl_price")
                tp1_px   = (ov_tps[0] if ov_tps else None) or setup.get("tp1_price")
                tp2_px   = (ov_tps[1] if len(ov_tps) >= 2 else None) or setup.get("tp2_price")

                # Build the tp_levels ladder. Cap count by notional so smallest
                # slice is fillable on Bitget (≥$5). Apply matching TP_SPLITS row.
                from trading.config import TP_SPLITS, pick_max_tp_count
                notional = float(sizing.get("notional_usdt") or 0)
                tps_for_ladder = ov_tps if ov_tps else [p for p in (tp1_px, tp2_px) if p]
                desired_count = len(tps_for_ladder) or 1
                allowed_count = pick_max_tp_count(notional, ideal=desired_count)
                tps_capped = tps_for_ladder[:allowed_count]
                splits = TP_SPLITS.get(allowed_count, [100])
                tp_levels = [
                    {"idx": i + 1, "price": float(p), "pct": float(splits[i]),
                     "hit": False, "hit_at": None}
                    for i, p in enumerate(tps_capped)
                ]

                # Skill provenance — captured at decision time so the position
                # row carries enough context for later "is this skill working?"
                # cohort analysis. Bear-phase + PO3 modifiers come from the
                # scanner setup dict (populated by ai_scanner Stage 3). Opus
                # override flag is true when consensus produced any of
                # _override_entry / _override_sl / _override_tp_prices.
                po3_total = sum([
                    float(setup.get("_po3_range") or 0),
                    float(setup.get("_po3_fvg")   or 0),
                    float(setup.get("_po3_session") or 0),
                ])
                opus_had_overrides = bool(ov_entry or ov_sl or ov_tps)

                # Normalise bear_phase to just the classification keyword.
                # The scanner emits a verbose description like
                # "bear-phase: decline (F&G 28 fear, BTC drifting) → -0.3".
                # Storing the whole string makes the analytics GROUP BY useless
                # since every position has a slightly different one.
                raw_phase = (setup.get("_bear_phase") or "")
                _PHASE_KEYWORDS = ("distribution", "decline", "capitulation",
                                   "recovery", "unknown")
                _lower = raw_phase.lower()
                bear_phase_normalised = next(
                    (kw for kw in _PHASE_KEYWORDS if kw in _lower),
                    raw_phase[:32] if raw_phase else None,
                )

                signal = {
                    "symbol":          setup.get("symbol"),
                    "direction":       setup.get("direction"),
                    "consensus_score": verdict["consensus_score"],
                    "entry_price":     entry_px,
                    "sl_price":        sl_px,
                    "tp1_price":       tp1_px,
                    "tp2_price":       tp2_px,
                    "tp_levels":       tp_levels,
                    "scanner":         verdict["scanner"],
                    "ai":              verdict["ai"],
                    # Skill provenance (persisted by executor._insert_open_position)
                    "consensus_model_used": fa_config.CONSENSUS_MODEL,
                    "bear_phase_at_open":   bear_phase_normalised,
                    "archetype_at_open":    setup.get("trade_type") or (verdict["scanner"] or {}).get("archetype"),
                    "po3_total":            round(po3_total, 3),
                    "opus_had_overrides":   1 if opus_had_overrides else 0,
                    "tp_levels_count":      len(tp_levels),
                }
                if fa_config.is_real_mode():
                    opened_ok = _open_real(conn, signal, sizing)
                    if opened_ok:
                        summary["opened"] += 1
                else:
                    pid = paper.open_paper_trade(conn, signal, sizing)
                    if pid:
                        summary["opened"] += 1
            except Exception as e:
                summary["errors"] += 1
                _log(conn, "orchestrator_error", setup, str(e)[:200])

    return summary


# ── Hook 2: monitor-side ─────────────────────────────────────────────────────

def on_monitor_cycle() -> dict:
    """Called every monitor cycle (~10 min). Manages open positions."""
    from database import db_conn

    if not fa_config.is_enabled():
        return {"skipped": "disabled"}

    with db_conn() as conn:
        state = fa_config.get_state(conn)

        # Pause-now path: close everything immediately
        if state == "pause_now":
            return _close_all(conn, reason="pause_now")

        # Otherwise manage normally
        if fa_config.is_real_mode():
            # real-mode executor manages real Bitget positions
            try:
                from . import executor, hedge_manager
                result = executor.manage_real_positions(conn)
                # Catastrophe hedge manager — runs every cycle in real mode
                # only. Order matters: manage_active first (close hedge if
                # storm passed), then check_and_open (open new hedge if
                # storm just started). Both are no-ops when conditions
                # aren't met or HEDGE_ENABLED=0.
                hedge_closed  = hedge_manager.manage_active_hedge(conn)
                hedge_opened  = hedge_manager.check_and_open_hedge(conn)
                if hedge_closed or hedge_opened:
                    if isinstance(result, dict):
                        result["hedge_action"] = (
                            "closed" if hedge_closed else "opened")
                return result
            except ImportError:
                return {"skipped": "executor not yet built"}
        else:
            # paper mode — needs a mark-price lookup
            return paper.manage_paper_positions(conn, _mark_price_lookup)


def _mark_price_lookup(symbol: str) -> float:
    """Use the read-only journal client by default (no API key cost on
    market data endpoints). Falls back to the trader client if the
    journal one is unreachable."""
    try:
        import bitget_client
        mp = bitget_client.get_mark_prices([symbol]) or {}
        v = mp.get(symbol) or mp.get(symbol.upper())
        if v:
            return float(v)
    except Exception:
        pass
    try:
        from . import bitget_trader
        return float(bitget_trader.get_mark_price(symbol) or 0)
    except Exception:
        return 0.0


# ── Real-mode dispatcher (used when mode=real) ──────────────────────────────

def _open_real(conn, signal: dict, sizing: dict) -> bool:
    """Dispatch to the real-mode executor. Returns True if order placed."""
    try:
        from . import executor
        return bool(executor.open_real_trade(conn, signal, sizing))
    except ImportError:
        _log(conn, "real_open_blocked", signal,
             "executor.py not built yet — real mode dispatch unavailable")
        return False
    except Exception as e:
        _log(conn, "real_open_error", signal, str(e)[:200])
        return False


def _close_all(conn, reason: str) -> dict:
    """Force-close everything (paper + real). Used by Pause Now."""
    paper_closed = paper.force_close_all(conn, _mark_price_lookup,
                                          reason=reason)
    real_closed = 0
    if fa_config.is_real_mode():
        try:
            from . import executor
            real_closed = executor.force_close_all(conn)
        except ImportError:
            pass
    return {"paper_closed": paper_closed, "real_closed": real_closed,
            "reason": reason}


# ── Internal logger ─────────────────────────────────────────────────────────

def _log(conn, event: str, setup_or_signal: dict, reason: str) -> None:
    """Persist a futures_ai_log row. Embeds a setup snapshot (entry/SL/TP +
    archetype + scores) into the payload so downstream hindsight tooling can
    replay rejected_killswitch / rejected_sizing / consensus_skipped events
    without joining against the analyzed_calls dedup table."""
    try:
        s = setup_or_signal or {}
        entry = (s.get("entry_zone") or {}).get("low") or s.get("entry_price")
        payload = {
            "reason":        reason,
            "entry":         entry,
            "sl":            s.get("sl_price"),
            "tp1":           s.get("tp1_price"),
            "tp2":           s.get("tp2_price"),
            "rr":            s.get("rr_ratio"),
            "scanner_score": s.get("setup_score"),
            "consensus_score": s.get("consensus_score"),
            "archetype":     s.get("trade_type"),
            "confluence":    s.get("confluence"),
            "bear_phase":    s.get("_bear_phase"),
            "timeframe":     s.get("timeframe"),
        }
        conn.execute("""
            INSERT INTO futures_ai_log
              (ts, event, symbol, direction, score, payload_json)
            VALUES (datetime('now'), ?, ?, ?, ?, ?)
        """, (
            event,
            s.get("symbol") or "",
            s.get("direction") or "",
            int(s.get("setup_score") or s.get("consensus_score") or 0),
            json.dumps(payload)[:1500],
        ))
        conn.commit()
    except Exception:
        pass
