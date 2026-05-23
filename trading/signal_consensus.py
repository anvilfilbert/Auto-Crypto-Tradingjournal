"""
trading.signal_consensus — gate scanner setups through AI consensus.

Pipeline:
  1. Scanner produces setups with score 1-10. Anything ≥ SCANNER_MIN_SCORE
     (currently 7) is a candidate.
  2. For each candidate, run a SECOND opinion through call_analyzer
     (Sonnet, the more thorough model with the full data pipeline +
     rulebook + calibration). If Sonnet's verdict disagrees (score < 7
     OR opposite direction OR explicit warning), the signal is REJECTED.
  3. Passing signals get returned with both scores + a consensus_score
     (the floor of the two — conservative).

Token cost: scanner already spent 1 Haiku call to score the setup;
consensus adds 1 Sonnet call per candidate. With SCANNER_MIN_SCORE=7
and 30 finalists per cycle producing ~5 score-7+ candidates, that's
~5 Sonnet calls/cycle * 48 cycles/day = 240 Sonnet calls/day ~ $1/day.
Well-cached prefix keeps it lower.

Rejection reasons are persisted to futures_ai_log so the operator can
audit why the chain didn't act on a 7+ setup.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from constants import MODEL
from helpers import strip_fence


# ── Public API ───────────────────────────────────────────────────────────────

def evaluate(scanner_setup: dict, conn) -> dict:
    """
    Given a scanner setup dict (the shape from ai_scanner._stage3 output),
    return a consensus verdict:
      {
        approved:    bool,
        consensus_score: int (min of scanner + AI),
        reason:      str (one-line explanation),
        scanner:     {score, direction, archetype},
        ai:          {score, direction, would_enter, summary},
      }
    """
    sym       = scanner_setup.get("symbol") or scanner_setup.get("_symbol")
    direction = scanner_setup.get("direction")
    sc_score  = int(scanner_setup.get("setup_score") or 0)
    archetype = scanner_setup.get("trade_type") or "—"

    base = {
        "approved":        False,
        "consensus_score": sc_score,
        "reason":          "",
        "scanner":         {"score": sc_score, "direction": direction,
                            "archetype": archetype},
        "ai":              None,
    }

    # Quick veto: scanner score must clear threshold first
    from constants import SCANNER_MIN_SCORE
    if sc_score < SCANNER_MIN_SCORE:
        base["reason"] = f"scanner score {sc_score} < threshold {SCANNER_MIN_SCORE}"
        return base

    # Run AI second-opinion via the call_analyzer pipeline
    try:
        from ai_call import analyze_call as _analyze
        # Synthesize a call text from the scanner setup so the analyzer
        # gets a normal-looking input. Lifts the price targets it already
        # computed so Sonnet doesn't have to re-derive them.
        call_text = _build_call_text(scanner_setup)
        result = _analyze(
            call_text=call_text,
            account_equity=100.0,
            market_regime=None,
            open_positions=[],
        )
    except Exception as e:
        base["reason"] = f"AI consensus call failed: {str(e)[:120]}"
        _log(conn, "consensus_error", sym, direction, sc_score, base["reason"])
        return base

    ai_score = int(result.get("setup_score") or 0)
    ai_dir   = (result.get("direction") or "").strip()
    ai_warns = result.get("_reviewer_warnings") or []
    ai_summary = (result.get("summary") or "")[:200]

    base["ai"] = {
        "score":        ai_score,
        "direction":    ai_dir,
        "warnings":     ai_warns,
        "summary":      ai_summary,
    }

    # Disagreement rules. All rejection payloads include the AI's
    # rationale (summary + warnings) so the operator can see WHY Sonnet
    # disagreed — not just that it did. Without this the decision log
    # tells you a setup was killed but gives no learnable signal.
    if ai_score < SCANNER_MIN_SCORE:
        base["reason"] = f"AI scored {ai_score} (below {SCANNER_MIN_SCORE} threshold)"
        _log(conn, "consensus_rejected", sym, direction, sc_score,
             json.dumps({
                 "ai_score":  ai_score,
                 "reason":    base["reason"],
                 "ai_summary": ai_summary,
                 "ai_warnings": ai_warns[:3],
             }))
        return base

    if ai_dir and direction and ai_dir.lower() != direction.lower():
        base["reason"] = f"direction mismatch (scanner={direction}, AI={ai_dir})"
        _log(conn, "consensus_rejected", sym, direction, sc_score,
             json.dumps({
                 "reason":     base["reason"],
                 "ai_score":   ai_score,
                 "ai_summary": ai_summary,
             }))
        return base

    if any("critical" in str(w).lower() or "high risk" in str(w).lower()
           for w in ai_warns):
        base["reason"] = f"AI flagged critical warning: {ai_warns[:1]}"
        _log(conn, "consensus_rejected", sym, direction, sc_score,
             json.dumps({
                 "warnings":   ai_warns,
                 "ai_score":   ai_score,
                 "ai_summary": ai_summary,
             }))
        return base

    base["approved"]        = True
    base["consensus_score"] = min(sc_score, ai_score)
    base["reason"]          = "ok"
    _log(conn, "consensus_approved", sym, direction, base["consensus_score"],
         json.dumps({
             "scanner_score": sc_score, "ai_score": ai_score,
             "archetype":     archetype,
         }))
    return base


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_call_text(setup: dict) -> str:
    """Synthesize a scanner-setup-as-call message that ai_call.analyze_call
    can parse. Keeps the AI's prompt-space familiar."""
    sym  = setup.get("symbol")
    dir_ = setup.get("direction")
    entry = setup.get("entry_zone", {}).get("low") or setup.get("entry_price")
    sl    = setup.get("sl_price")
    tp1   = setup.get("tp1_price")
    tp2   = setup.get("tp2_price")
    rr    = setup.get("rr_ratio") or "?"
    arch  = setup.get("trade_type") or "—"
    reason = setup.get("_rationale") or setup.get("summary") or ""

    return (
        f"Auto-trader consensus check — score {setup.get('setup_score','?')}/10\n"
        f"Symbol: {sym}\n"
        f"Direction: {dir_}\n"
        f"Archetype: {arch}\n"
        f"Proposed entry: {entry}\n"
        f"Proposed SL: {sl}\n"
        f"Proposed TP1: {tp1}\n"
        f"Proposed TP2: {tp2}\n"
        f"R:R: {rr}\n\n"
        f"Scanner rationale:\n{reason}\n\n"
        "Please independently evaluate this setup. If you agree, return a "
        "score ≥ 7 with the same direction. If you disagree, return your "
        "honest score and direction with reasoning. The trader will only "
        "act on this signal if both you and the scanner agree."
    )


def _log(conn, event: str, sym: str, direction: str, score: int,
         payload: str) -> None:
    try:
        conn.execute("""
            INSERT INTO futures_ai_log(ts, event, symbol, direction, score, payload_json)
            VALUES (datetime('now'), ?, ?, ?, ?, ?)
        """, (event, sym or "", direction or "", int(score or 0), payload))
        conn.commit()
    except Exception:
        pass
