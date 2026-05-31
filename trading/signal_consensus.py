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
import os
from typing import Optional

from constants import MODEL
from helpers import strip_fence


def _consensus_model() -> str:
    """Pick the TradePrep model for the consensus gate.

    Reads FUTURES_AI_CONSENSUS_MODEL env var. Accepts either a full Anthropic
    model ID (`claude-opus-4-7`, `claude-sonnet-4-6`, etc.) or a family alias
    (`opus`, `sonnet`, `haiku`). Falls back to the project default Sonnet
    (constants.MODEL) when unset or empty.

    Rationale: empirically Opus calibrated 5/5 on the 2026-05-23 score-6
    Sonnet rejections — every one of those setups hit TP in hindsight.
    The +$40/mo cost over Sonnet is small vs the missed-trade value.
    """
    raw = (os.environ.get("FUTURES_AI_CONSENSUS_MODEL") or "").strip()
    if not raw:
        return MODEL
    aliases = {
        "opus":   "claude-opus-4-7",
        "sonnet": "claude-sonnet-4-6",
        "haiku":  "claude-haiku-4-5-20251001",
    }
    return aliases.get(raw.lower(), raw)


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

    # Pre-consensus dedup — if this symbol already has an open auto_ai position
    # the executor would reject as real_dedup anyway. Skip the Opus call to
    # save ~$0.05/dup. Fails open: any error reading positions = still call
    # consensus so a transient Bitget hiccup never blocks a real entry.
    try:
        from trading import bitget_trader as _bt
        open_syms = {p.get("symbol") for p in (_bt.get_open_positions() or [])
                     if p.get("symbol")}
        if sym in open_syms:
            base["reason"] = f"dedup: {sym} already has open auto_ai position"
            _log(conn, "consensus_skipped_dedup", sym, direction, sc_score,
                 json.dumps({"sym": sym, "open_syms_count": len(open_syms)}))
            return base
    except Exception as _dedup_err:
        # Never block on dedup-check errors
        _log_err = str(_dedup_err)[:120]

    # Run AI second-opinion via the call_analyzer pipeline
    try:
        from ai_call import analyze_call as _analyze
        # Synthesize a call text from the scanner setup so the analyzer
        # gets a normal-looking input. Lifts the price targets it already
        # computed so the model doesn't have to re-derive them.
        call_text = _build_call_text(scanner_setup)
        consensus_model = _consensus_model()
        result = _analyze(
            call_text=call_text,
            account_equity=100.0,
            market_regime=None,
            open_positions=[],
            trade_prep_model=consensus_model,
        )
    except Exception as e:
        base["reason"] = f"AI consensus call failed: {str(e)[:120]}"
        _log(conn, "consensus_error", sym, direction, sc_score,
             json.dumps({**_setup_snapshot(scanner_setup, sc_score, 0, "", "", []),
                         "error": base["reason"]}))
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

    # Build the shadow-log snapshot — every consensus log carries the FULL setup
    # (entry/SL/TP, scanner+AI scores, archetype, regime) so a later hindsight
    # pass can simulate the trade without joining against analyzed_calls
    # (which dedups setups for the same symbol and loses their per-rejection
    # snapshot).
    snap = _setup_snapshot(scanner_setup, sc_score, ai_score, ai_dir, ai_summary, ai_warns)

    # Disagreement rules. All rejection payloads include the AI's
    # rationale (summary + warnings) so the operator can see WHY Sonnet
    # disagreed — not just that it did. Without this the decision log
    # tells you a setup was killed but gives no learnable signal.
    #
    # BUG-006 fix (2026-05-26): AI approval threshold uses CONSENSUS_MIN_SCORE
    # (env-driven, default 5) instead of SCANNER_MIN_SCORE (6). The half-tier
    # sizing path in risk_budget.size_trade triggers at opus_score==5 — but it
    # was unreachable while consensus rejected at AI<6, making the half-tier
    # dead code. CONSENSUS_MIN_SCORE is the threshold the half-tier was
    # designed to work with; downstream Path 3 R:R viability + pre-flight
    # drift check still apply as safety nets.
    from trading import config as fa_config
    if ai_score < fa_config.CONSENSUS_MIN_SCORE:
        base["reason"] = f"AI scored {ai_score} (below {fa_config.CONSENSUS_MIN_SCORE} threshold)"
        _log(conn, "consensus_rejected", sym, direction, sc_score,
             json.dumps({**snap, "reject_kind": "low_score", "reason": base["reason"]}))
        return base

    # N-1 (Master plan Noise §2.6) — consensus variance gate. Two voters
    # (Sonnet scanner + Opus consensus) shouldn't disagree by more than
    # 2.5 score points; if they do, the setup is ambiguous and the noise-
    # free decision is to skip. Tunable via env FUTURES_AI_MAX_SCORE_GAP.
    try:
        import os as _os
        max_gap = float(_os.environ.get("FUTURES_AI_MAX_SCORE_GAP", "2.5"))
    except Exception:
        max_gap = 2.5
    score_gap = abs(int(sc_score) - int(ai_score))
    if score_gap > max_gap:
        base["reason"] = (f"voters disagree: scanner={sc_score}, AI={ai_score}, "
                          f"gap {score_gap} > max_gap {max_gap}")
        _log(conn, "rejected_consensus_variance", sym, direction, sc_score,
             json.dumps({**snap, "reject_kind": "consensus_variance",
                         "reason": base["reason"], "gap": score_gap,
                         "max_gap": max_gap}))
        return base

    # N-4 (Master plan Noise §2.9) — VPIN toxicity gate. When VPIN >= 0.7
    # the microstructure is informed-trader dominated → high cascade risk in
    # the next 30-60min. Veto new entries until the toxicity normalises.
    # Reads from the latest vpin_snapshot row (scheduler polls every 5min).
    try:
        from trading import vpin as _vpin
        veto, vpin_reason, vpin_val = _vpin.vpin_veto(conn, sym)
        if veto:
            base["reason"] = f"VPIN veto: {vpin_reason}"
            _log(conn, "rejected_vpin_toxicity", sym, direction, sc_score,
                 json.dumps({**snap, "reject_kind": "vpin_toxicity",
                             "vpin": vpin_val, "reason": vpin_reason}))
            return base
    except Exception:
        pass  # never block on VPIN module errors

    # A-E (Master plan Week 11) — Cascade Predictor. Fuses VPIN +
    # funding spread + OI divergence into a single risk score and vetoes
    # the side that's at risk of being squeezed. Direction-aware: only
    # blocks the trade if the cascade pressure builds against this side.
    try:
        from trading import cascade_predictor
        cascade_veto, cascade_reason, cascade_result = cascade_predictor.veto_check(
            conn, sym, direction)
        if cascade_veto:
            base["reason"] = f"Cascade veto: {cascade_reason}"
            _log(conn, "rejected_cascade_risk", sym, direction, sc_score,
                 json.dumps({**snap, "reject_kind": "cascade_risk",
                             "result": cascade_result, "reason": cascade_reason}))
            return base
    except Exception:
        pass  # never block on cascade module errors

    if ai_dir and direction and ai_dir.lower() != direction.lower():
        base["reason"] = f"direction mismatch (scanner={direction}, AI={ai_dir})"
        _log(conn, "consensus_rejected", sym, direction, sc_score,
             json.dumps({**snap, "reject_kind": "direction_mismatch", "reason": base["reason"]}))
        return base

    if any("critical" in str(w).lower() or "high risk" in str(w).lower()
           for w in ai_warns):
        base["reason"] = f"AI flagged critical warning: {ai_warns[:1]}"
        _log(conn, "consensus_rejected", sym, direction, sc_score,
             json.dumps({**snap, "reject_kind": "critical_warning", "reason": base["reason"]}))
        return base

    base["approved"]        = True
    base["consensus_score"] = min(sc_score, ai_score)
    base["reason"]          = "ok"

    # A-A (Master plan): Red-Team agent — adversarial second opinion.
    # Soft mode (default): logs penalty for audit, does not block. Hard
    # mode (after operator review at +14d): veto=True blocks the trade.
    try:
        from trading import red_team_agent as _rt
        # Canonical scanner emits entry as entry_zone.low (a band); use the
        # same fallback pattern as the Opus-override block below so the
        # red-team agent doesn't falsely flag every setup as "missing entry".
        _rt_entry = ((scanner_setup.get("entry_zone") or {}).get("low")
                     or scanner_setup.get("entry_price"))
        verdict = _rt.evaluate_setup({
            "symbol":          sym,
            "direction":       direction,
            "scanner_score":   sc_score,
            "ai_score":        ai_score,
            "consensus_score": base["consensus_score"],
            "ai_summary":      ai_summary,
            "entry_price":     _rt_entry,
            "sl_price":        scanner_setup.get("sl_price"),
            "tp1_price":       scanner_setup.get("tp1_price"),
            "tp2_price":       scanner_setup.get("tp2_price"),
        })
        base["red_team"] = verdict
        if verdict.get("veto") and verdict.get("mode") == "hard":
            base["approved"] = False
            base["reason"]   = f"red_team_veto: {verdict.get('summary', '')[:120]}"
            _log(conn, "red_team_veto_hard", sym, direction, base["consensus_score"],
                 json.dumps({**snap, "verdict": verdict}))
            return base
        if verdict.get("score_penalty", 0) > 0 or verdict.get("veto_raw"):
            event = "red_team_penalty" if verdict.get("score_penalty", 0) > 0 else "red_team_passthrough"
            _log(conn, event, sym, direction, base["consensus_score"],
                 json.dumps({**snap, "verdict": verdict}))
    except Exception as _rt_err:
        # Never block trading because the red-team agent had an issue
        base["red_team"] = {"error": str(_rt_err), "mode": "skipped"}

    # Opus override path: when the consensus model produces its own
    # entry/SL/TP ladder, merge them into the scanner setup so the executor
    # uses them. Safety rule: SL can only move TIGHTER (closer to entry) —
    # the consensus model is allowed to reduce risk but never silently
    # increase it. Entry can shift freely (the order isn't placed yet).
    overrides = _build_overrides(scanner_setup, result)
    if overrides:
        base["overrides"] = overrides
        for k, v in overrides.items():
            # Apply onto a copy of the scanner setup so the downstream
            # executor uses the merged values. Don't mutate caller's dict
            # in place — orchestrator iterates over the original list.
            scanner_setup[f"_override_{k}"] = v
        snap["overrides"] = overrides

    _log(conn, "consensus_approved", sym, direction, base["consensus_score"],
         json.dumps({**snap, "reject_kind": None}))
    return base


# ── Override helpers ─────────────────────────────────────────────────────────

def _build_overrides(scanner_setup: dict, ai_result: dict) -> dict:
    """Compare Opus's emitted entry/SL/TP ladder against the scanner's
    proposed prices. Returns dict of overrides that pass safety checks.
    Empty dict = use scanner targets unchanged."""
    out: dict = {}
    direction = (scanner_setup.get("direction") or "Long").lower()
    sc_entry = (scanner_setup.get("entry_zone") or {}).get("low") or scanner_setup.get("entry_price")
    sc_sl    = scanner_setup.get("sl_price")
    sc_tp1   = scanner_setup.get("tp1_price")

    ai_entry = ai_result.get("entry_price") or 0
    ai_sl    = ai_result.get("sl_price") or 0
    # BUG-007 fix (2026-05-26): filter None out of ai_tps before comparing.
    # Opus occasionally emits a sparse ladder (e.g. [1.0, None, 2.0]) which
    # crashed the monotonicity check with "'>' not supported between float
    # and NoneType" — observed on VIRTUALUSDT during a quick_score_only call.
    ai_tps_raw = ai_result.get("tp_prices") or []
    ai_tps = [float(t) for t in ai_tps_raw if t is not None]
    # Backfill ai_tps from tp1/tp2 if Opus didn't emit a ladder
    if not ai_tps:
        for v in (ai_result.get("tp1_price"), ai_result.get("tp2_price")):
            if v:
                ai_tps.append(float(v))

    # Entry override — accept if Opus moved it AND the move is small (<2% drift)
    if ai_entry and sc_entry and abs(ai_entry - sc_entry) / sc_entry > 0.001:
        drift_pct = abs(ai_entry - sc_entry) / sc_entry * 100
        if drift_pct < 2.0:
            out["entry"] = float(ai_entry)
        # else: too far — likely a hallucination, ignore

    # BUG-007 fix (2026-05-26): when scanner setup is quick_score_only it has
    # no entry/sl prices. Use Opus's values directly — there's nothing to
    # override against, but the trade still needs an entry/sl to fire.
    if not sc_entry and ai_entry:
        out["entry"] = float(ai_entry)
    if not sc_sl and ai_sl:
        out["sl"] = float(ai_sl)

    # SL override — ONLY accept tighter (closer to entry). Loosening is unsafe.
    entry_for_sl = ai_entry or sc_entry
    if ai_sl and sc_sl and entry_for_sl:
        if direction == "long":
            tighter = ai_sl > sc_sl  # higher SL on a Long = tighter
        else:
            tighter = ai_sl < sc_sl  # lower SL on a Short = tighter
        # Also keep SL on the safe side of entry
        on_safe_side = (ai_sl < entry_for_sl) if direction == "long" else (ai_sl > entry_for_sl)
        if tighter and on_safe_side:
            out["sl"] = float(ai_sl)

    # TP ladder override — accept if Opus emitted ≥3 levels with structure
    if len(ai_tps) >= 3:
        # Validate monotonicity matches direction
        if direction == "long":
            ok = all(ai_tps[i+1] > ai_tps[i] for i in range(len(ai_tps)-1))
        else:
            ok = all(ai_tps[i+1] < ai_tps[i] for i in range(len(ai_tps)-1))
        # And first TP on the right side of entry. BUG-007 fix (2026-05-26):
        # guard against None entry_ref — quick_score_only setups have no
        # scanner entry, and if Opus didn't supply one either we can't
        # validate the TP ladder side.
        entry_ref = out.get("entry") or sc_entry
        if entry_ref:
            first_tp_ok = (ai_tps[0] > entry_ref) if direction == "long" \
                           else (ai_tps[0] < entry_ref)
            if ok and first_tp_ok:
                out["tp_prices"] = ai_tps[:7]

    return out


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

    # Phase 1-4 modifier context — explicit visibility into each modifier
    # the scanner applied. Each field is a human-readable label with the
    # signed magnitude embedded (e.g. "CPR: higher_value → +0.3").
    mod_lines = []
    for f in ("_bear_phase", "_hmm_regime", "_cpr", "_ib",
              "_po3_range", "_po3_fvg", "_po3_session"):
        v = setup.get(f)
        if v:
            mod_lines.append(f"  · {v}")
    mod_block = ""
    if mod_lines:
        mod_block = (
            "\n\nPhase 1-4 modifier context (already applied to scanner score "
            "— provided for transparency, not for re-application):\n"
            + "\n".join(mod_lines)
        )

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
        f"Scanner rationale:\n{reason}"
        f"{mod_block}\n\n"
        "Please independently evaluate this setup. If you agree, return a "
        "score ≥ 7 with the same direction. If you disagree, return your "
        "honest score and direction with reasoning. The trader will only "
        "act on this signal if both you and the scanner agree."
    )


def _setup_snapshot(setup: dict, sc_score: int, ai_score: int,
                    ai_dir: str, ai_summary: str, ai_warns: list) -> dict:
    """Compact dict embedded in every consensus log entry. Self-contained so
    hindsight + Opus re-review tooling never needs to join against the
    analyzed_calls dedup table."""
    entry = setup.get("entry_zone", {}).get("low") or setup.get("entry_price")
    return {
        # the trade structure — what would have been placed
        "entry":         entry,
        "sl":            setup.get("sl_price"),
        "tp1":           setup.get("tp1_price"),
        "tp2":           setup.get("tp2_price"),
        "rr":            setup.get("rr_ratio"),
        # scores
        "scanner_score": sc_score,
        "ai_score":      ai_score,
        "ai_direction":  ai_dir,
        "ai_summary":    ai_summary,
        "ai_warnings":   list(ai_warns)[:3],
        # context (helpful for hindsight grouping)
        "archetype":     setup.get("trade_type") or "—",
        "confluence":    setup.get("confluence"),
        "bear_phase":    setup.get("_bear_phase"),
        "po3_range":     setup.get("_po3_range"),
        "po3_fvg":       setup.get("_po3_fvg"),
        "po3_session":   setup.get("_po3_session"),
        "hmm_regime":    setup.get("_hmm_regime"),
        "cpr":           setup.get("_cpr"),
        "ib":            setup.get("_ib"),
        "regime":        setup.get("regime_label"),
        "timeframe":     setup.get("timeframe"),
        "rationale":     (setup.get("_rationale") or "")[:200],
    }


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
