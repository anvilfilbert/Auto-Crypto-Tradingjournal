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

                # 1. kill switch
                can_trade, reason = kill_switch.can_open_new_trade(conn)
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
                )
                if not sizing:
                    summary["rejected_sizing"] += 1
                    _log(conn, "rejected_sizing", setup,
                         "risk_budget returned None — SL too tight or score below floor")
                    continue

                # 4. dispatch — paper or real
                signal = {
                    "symbol":          setup.get("symbol"),
                    "direction":       setup.get("direction"),
                    "consensus_score": verdict["consensus_score"],
                    "entry_price":     setup.get("entry_zone", {}).get("low") or setup.get("entry_price"),
                    "sl_price":        setup.get("sl_price"),
                    "tp1_price":       setup.get("tp1_price"),
                    "tp2_price":       setup.get("tp2_price"),
                    "scanner":         verdict["scanner"],
                    "ai":              verdict["ai"],
                }
                if fa_config.is_real_mode():
                    _open_real(conn, signal, sizing)
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
                from . import executor
                return executor.manage_real_positions(conn)
            except ImportError:
                # executor not implemented yet — defensive no-op
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

def _open_real(conn, signal: dict, sizing: dict) -> None:
    """Dispatch to the real-mode executor. Built once paper is validated."""
    try:
        from . import executor
        executor.open_real_trade(conn, signal, sizing)
    except ImportError:
        _log(conn, "real_open_blocked", signal,
             "executor.py not built yet — real mode dispatch unavailable")


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
    try:
        conn.execute("""
            INSERT INTO futures_ai_log
              (ts, event, symbol, direction, score, payload_json)
            VALUES (datetime('now'), ?, ?, ?, ?, ?)
        """, (
            event,
            setup_or_signal.get("symbol") or "",
            setup_or_signal.get("direction") or "",
            int(setup_or_signal.get("setup_score") or
                setup_or_signal.get("consensus_score") or 0),
            json.dumps({"reason": reason})[:500],
        ))
        conn.commit()
    except Exception:
        pass
