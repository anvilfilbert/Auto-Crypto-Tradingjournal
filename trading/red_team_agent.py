"""
A-A (Master plan Specialized Agents §2.1): Red-Team / Devil's Advocate.

Given a setup that passed consensus + score threshold + variance gate,
this agent argues ONLY against taking it. Returns a structured veto
verdict: {veto: bool, severity: high/med/low, reasons: [str], score_penalty: float}.

Two modes (env-driven):
  soft (default for first 14 days): adds score penalty; trade may still
    execute if remaining score clears threshold
  hard: veto=True blocks the trade outright

Cost: ~5 calls/day × Haiku rates ≈ $0.20/day.

Wired into orchestrator after signal_consensus.evaluate() and before
bitget_trader.place_market_order().
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

_log = logging.getLogger(__name__)

# Constants — tunable via env (will move to learned_params in L-1 refactor)
DEFAULT_MODE = "soft"
DEFAULT_PENALTY_PER_HIGH = 0.5     # score penalty per high-severity reason
DEFAULT_PENALTY_PER_MED = 0.25
SOFT_DAYS = 14  # soft → hard review countdown


def _model_id() -> str:
    return os.environ.get("RED_TEAM_MODEL", "claude-haiku-4-5-20251001")


def _mode() -> str:
    return os.environ.get("FUTURES_AI_RED_TEAM_MODE", DEFAULT_MODE).strip().lower()


_SYSTEM_PROMPT = (
    "You are a red-team analyst for a crypto-futures auto-trader. Your job "
    "is to argue ONLY against taking the setup that's about to be executed. "
    "Don't validate it, don't acknowledge its merits — only describe what "
    "would have to be true for it to lose money, and how likely each scenario "
    "is. Be specific, concrete, and brief.\n\n"
    "Output strict JSON:\n"
    "{\n"
    "  \"veto\":      true|false,                # only true if ≥1 high-severity reason\n"
    "  \"severity\":  \"high\"|\"med\"|\"low\",   # worst single-reason severity\n"
    "  \"reasons\":   [{\"text\": \"...\", \"severity\": \"high|med|low\", \"prob\": 0.0-1.0}],\n"
    "  \"summary\":   \"one-line takeaway\"\n"
    "}\n"
    "A high-severity reason is one that, if true, would result in ≥1R loss with >50% probability. "
    "A med is 0.5R-1R loss. A low is <0.5R or merely 'not optimal'. Don't pad — fewer better-justified "
    "reasons beat more weak ones."
)


def evaluate_setup(consensus_payload: dict, rulebook_summary: Optional[str] = None) -> dict:
    """Run the red-team on a consensus-approved setup. Returns a verdict dict.

    consensus_payload should be the output of signal_consensus.evaluate()
    plus any context (symbol, direction, entry, sl, tp, scanner reasoning).

    Returns:
      {
        veto: bool,
        severity: "high"|"med"|"low",
        score_penalty: float (0 if no veto in soft mode),
        reasons: [...],
        summary: str,
        mode: "soft"|"hard",
        cost_usd: float,
        latency_ms: int,
      }
    """
    mode = _mode()

    # Compact prompt — only what matters
    sym = consensus_payload.get("symbol") or consensus_payload.get("sym") or "?"
    direction = consensus_payload.get("direction") or "?"
    sc_score = consensus_payload.get("scanner_score") or consensus_payload.get("sc_score")
    ai_score = consensus_payload.get("ai_score")
    consensus_score = consensus_payload.get("consensus_score")
    ai_summary = consensus_payload.get("ai_summary") or ""
    entry = consensus_payload.get("entry_price")
    sl = consensus_payload.get("sl_price")
    tp1 = consensus_payload.get("tp1_price")

    user_msg = (
        f"Setup just approved by consensus:\n"
        f"  Symbol:    {sym}\n"
        f"  Direction: {direction}\n"
        f"  Scanner score: {sc_score}/10\n"
        f"  AI (Opus) score: {ai_score}/10\n"
        f"  Consensus score: {consensus_score}/10\n"
        f"  Entry: {entry}  SL: {sl}  TP1: {tp1}\n"
        f"  AI summary: {ai_summary[:500]}\n"
    )
    if rulebook_summary:
        user_msg += f"\nRecent rulebook (auto_ai chain) — relevant rules:\n{rulebook_summary[:1500]}\n"
    user_msg += "\nArgue ONLY against this setup. Output JSON as specified."

    try:
        import ai_client
        import time as _t
        t0 = _t.time()
        # ai_client.send returns (response_text, cached_tokens) tuple
        text, _cached_tokens = ai_client.send(
            module="red_team_agent",
            model=_model_id(),
            messages=[
                {"role": "user", "content": user_msg},
            ],
            max_tokens=512,
            system=_SYSTEM_PROMPT,
        )
        latency_ms = int((_t.time() - t0) * 1000)
        cost = 0.0  # Cost tracked via log_token_usage inside ai_client.send
    except Exception as e:
        _log.warning("red_team: ai_client.send failed: %s", e)
        return _empty_verdict(mode, error=str(e))

    # Parse JSON
    verdict_data = _parse_json(text)
    if not verdict_data:
        return _empty_verdict(mode, error="non-JSON response", latency_ms=latency_ms,
                               cost_usd=cost)

    # Compute score penalty from reasons
    score_penalty = 0.0
    for r in verdict_data.get("reasons", []):
        sev = (r.get("severity") or "").lower()
        if sev == "high":
            score_penalty += DEFAULT_PENALTY_PER_HIGH
        elif sev == "med":
            score_penalty += DEFAULT_PENALTY_PER_MED

    veto = bool(verdict_data.get("veto", False))
    if mode == "soft":
        # Soft mode never blocks — only penalizes
        veto_effective = False
    else:
        veto_effective = veto

    return {
        "veto":          veto_effective,
        "veto_raw":      veto,
        "severity":      verdict_data.get("severity", "low"),
        "score_penalty": round(score_penalty, 2),
        "reasons":       verdict_data.get("reasons", []),
        "summary":       verdict_data.get("summary", ""),
        "mode":          mode,
        "cost_usd":      cost,
        "latency_ms":    latency_ms,
    }


def _parse_json(text: str) -> Optional[dict]:
    """Robust JSON extraction — handles markdown fences and trailing text."""
    if not text:
        return None
    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            if p.strip().startswith("{") or "veto" in p[:50]:
                text = p
                break
    text = text.strip()
    # Find first { and last } to handle prefix/suffix
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def _empty_verdict(mode: str, error: str = "", latency_ms: int = 0,
                    cost_usd: float = 0.0) -> dict:
    """Fail-safe when the agent can't be reached or response is unparseable."""
    return {
        "veto":          False,
        "veto_raw":      False,
        "severity":      "low",
        "score_penalty": 0.0,
        "reasons":       [],
        "summary":       f"agent unavailable: {error}" if error else "",
        "mode":          mode,
        "cost_usd":      cost_usd,
        "latency_ms":    latency_ms,
        "error":         error,
    }
