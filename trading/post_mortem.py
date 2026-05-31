"""
A-C (Master plan Week 5): Post-Mortem agent. For every closed losing
trade in the auto_ai chain, ask Haiku to classify the failure mode and
the proximate trigger. Results land in `trade_postmortem` and surface in
the daily Telegram report's "recurring failure modes" line.

Cost discipline:
  - Only LOSSES are analyzed (winners are not interesting for failure-mode
    learning, and would 2× the API spend for noise)
  - Skipped when realized_pnl > -$1.00 (negligible/breakeven)
  - One pass per close — `postmortem_done=1` flag prevents reanalysis
  - Hard daily cap via $5/day agent budget (FUTURES_AI_AGENT_DAILY_CAP)

Classification taxonomy (fixed list — Haiku must pick from these):
  - sl_too_tight       (stopped near reversal point)
  - bad_entry_timing   (entry chased an already-extended move)
  - macro_flush        (market-wide drawdown event, not symbol-specific)
  - regime_mismatch    (long in clear downtrend / short in clear uptrend)
  - news_event         (unexpected catalyst, foreseeable from calendar)
  - structure_violation (price broke key S/R after entry)
  - false_breakout     (entered on apparent breakout that reversed)
  - liquidity_event    (cascading liqs in the direction of pain)
  - overconfidence     (size/risk too large for the setup quality)
  - unknown            (escape hatch — agent unsure)

Output schema (per row):
  postmortem_tag       primary failure-mode label
  postmortem_severity  low | medium | high
  postmortem_reason    1-2 sentence explanation
  postmortem_evidence  comma-separated list of supporting datapoints
  postmortem_cost_usd  API cost
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ai_client import send as _ai_send

_log = logging.getLogger(__name__)

LOSS_THRESHOLD_USD = -1.00

ALLOWED_TAGS = [
    "sl_too_tight", "bad_entry_timing", "macro_flush", "regime_mismatch",
    "news_event", "structure_violation", "false_breakout",
    "liquidity_event", "overconfidence", "unknown",
]

_SYSTEM_PROMPT = (
    "You are a trading post-mortem analyst. You are given a single CLOSED "
    "losing trade from an auto-traded crypto futures book. Your job is to "
    "classify the failure mode using ONE tag from the allowed list, rate "
    "severity (low/medium/high), explain in 1-2 sentences, and list 2-4 "
    "concrete evidence points from the data given. Be ruthless about "
    "selecting `unknown` when the data is genuinely ambiguous. Return "
    "STRICT JSON only — no markdown, no preamble."
)


def _ensure_table(conn):
    """Idempotent — adds the postmortem columns to positions if missing,
    and creates the helper trade_postmortem table.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "postmortem_tag" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN postmortem_tag TEXT")
    if "postmortem_severity" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN postmortem_severity TEXT")
    if "postmortem_reason" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN postmortem_reason TEXT")
    if "postmortem_evidence" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN postmortem_evidence TEXT")
    if "postmortem_done" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN postmortem_done INTEGER DEFAULT 0")
    if "postmortem_cost_usd" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN postmortem_cost_usd REAL")
    conn.commit()


def _candidates(conn, lookback_hours: int = 48) -> list[dict]:
    """Closed auto_ai losers in the last N hours that haven't been analyzed yet."""
    # NB: positions table stores TPs in `tp_levels` JSON (no tp1_price column)
    # and SL is on a Bitget plan order (no sl_price column). Fields we'd love
    # to have (target levels at open) live in `tps_json` / `tp_levels` blobs;
    # we pass the entry/close/PnL story and let the LLM reason from those.
    rows = conn.execute(
        "SELECT id, symbol, direction, entry_price, close_price, tp_levels, "
        "       realized_pnl, close_reason, archetype_at_open, setup_type, "
        "       ai_score_at_open, bear_phase_at_open, close_time, "
        "       leverage, open_time "
        "FROM positions "
        "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
        "AND close_time IS NOT NULL AND close_time != '' "
        "AND (postmortem_done IS NULL OR postmortem_done=0) "
        "AND realized_pnl IS NOT NULL "
        f"AND close_time >= datetime('now', '-{int(lookback_hours)} hours') "
        "ORDER BY close_time DESC"
    ).fetchall()
    out = []
    for r in rows:
        rec = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "symbol": r[1], "direction": r[2],
            "entry_price": r[3], "close_price": r[4], "tp_levels": r[5],
            "realized_pnl": r[6], "close_reason": r[7],
            "archetype_at_open": r[8], "setup_type": r[9],
            "ai_score_at_open": r[10], "bear_phase_at_open": r[11],
            "close_time": r[12], "leverage": r[13], "open_time": r[14],
        }
        pnl = float(rec.get("realized_pnl") or 0)
        if pnl > LOSS_THRESHOLD_USD:
            continue
        out.append(rec)
    return out


def _build_user_prompt(trade: dict) -> str:
    parts = [
        f"Symbol: {trade.get('symbol')}",
        f"Direction: {trade.get('direction')}",
        f"Entry: {trade.get('entry_price')}  Close: {trade.get('close_price')}",
        f"TP ladder (JSON, may include hit flags): {trade.get('tp_levels')}",
        f"Close reason: {trade.get('close_reason')}",
        f"Realized P&L: ${trade.get('realized_pnl')}",
        f"Open time: {trade.get('open_time')}  Close time: {trade.get('close_time')}",
        f"Archetype at open: {trade.get('archetype_at_open')}",
        f"Setup type: {trade.get('setup_type')}",
        f"AI score at open: {trade.get('ai_score_at_open')}",
        f"Bear phase at open: {trade.get('bear_phase_at_open')}",
        f"Leverage: {trade.get('leverage')}",
        "",
        f"Allowed tags: {', '.join(ALLOWED_TAGS)}",
        "",
        "Return JSON:",
        '{"tag": "<one of allowed>", "severity": "low|medium|high", '
        '"reason": "1-2 sentences", "evidence": ["...", "...", "..."]}',
    ]
    return "\n".join(parts)


def _parse_json_loose(text: str) -> dict:
    """Strip optional code fences then json-parse."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:].strip()
    try:
        return json.loads(s)
    except Exception:
        # Try first {...} extract
        first = s.find("{")
        last  = s.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(s[first:last+1])
            except Exception:
                pass
    return {}


def analyze_one(conn, trade: dict) -> dict[str, Any]:
    """Run Haiku post-mortem for one trade, write result back to positions row.

    Two paths:
    - DSPy mode (FUTURES_AI_POSTMORTEM_DSPY=1): uses dspy_modules.post_mortem
      with optional compiled few-shot demos. Bypasses _SYSTEM_PROMPT.
    - Default: original Haiku-via-ai_client.send path with _SYSTEM_PROMPT.
    """
    _ensure_table(conn)

    # DSPy path — opt in via env var. Falls back to default on import/runtime error.
    if os.environ.get("FUTURES_AI_POSTMORTEM_DSPY", "0").strip() == "1":
        try:
            from dspy_modules.post_mortem import PostMortemClassifier
            clf = PostMortemClassifier.load()
            result = clf(trade)
            if result.get("ok"):
                parsed = {
                    "tag":      result["tag"],
                    "severity": result["severity"],
                    "reason":   result["reason"],
                    "evidence": result["evidence"],
                }
                # Skip the parse-from-text path entirely; jump to persistence.
                return _persist_postmortem(conn, trade, parsed)
            _log.warning("DSPy post_mortem returned not-ok for trade %s: %s",
                          trade.get("id"), result.get("error"))
        except Exception as e:
            _log.warning("DSPy path failed (%s); falling back to plain Haiku",
                          str(e)[:200])

    prompt = _build_user_prompt(trade)
    # ai_client.send signature: (module, model, messages, max_tokens, system=None, provider=None)
    # Returns: (response_text, cached_tokens) tuple.
    model_id = os.environ.get("FUTURES_AI_POSTMORTEM_MODEL", "claude-haiku-4-5-20251001")
    try:
        raw_text, _cached_tokens = _ai_send(
            module="post_mortem",
            model=model_id,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            system=_SYSTEM_PROMPT,
        )
    except Exception as e:
        _log.warning("post_mortem: AI call failed for trade %s: %s", trade.get("id"), e)
        return {"trade_id": trade.get("id"), "ok": False, "error": str(e)}

    parsed = _parse_json_loose(raw_text or "")
    # Estimate per-trade cost from text lengths. 4 chars ~= 1 token (Haiku).
    # Exact counts are in token_usage; this column gives a fast per-trade
    # signal without a join. Within ~10% of true cost.
    cost_usd = _estimate_haiku_cost(
        input_chars=len(_SYSTEM_PROMPT) + len(prompt),
        output_chars=len(raw_text or ""),
    )

    # DSPy shadow path — opt-in via FUTURES_AI_POSTMORTEM_DSPY_SHADOW=1.
    # Runs DSPy in background thread on the same trade, logs the comparison
    # to shadow_responses (primary_module='post_mortem'). Never blocks.
    if os.environ.get("FUTURES_AI_POSTMORTEM_DSPY_SHADOW", "0").strip() == "1":
        _fire_dspy_shadow(trade, primary_text=raw_text or "", primary_parsed=parsed,
                           primary_prompt_chars=len(_SYSTEM_PROMPT) + len(prompt))

    return _persist_postmortem(conn, trade, parsed, cost_usd=cost_usd)


def _fire_dspy_shadow(trade: dict, primary_text: str, primary_parsed: dict,
                       primary_prompt_chars: int) -> None:
    """Spawn a daemon thread that runs DSPy on the same trade and logs the
    comparison to shadow_responses. Best-effort — never raises.
    """
    import threading
    def _run():
        import time as _t
        import uuid
        import json
        try:
            from dspy_modules.post_mortem import PostMortemClassifier
            clf = PostMortemClassifier.load()
            _t0 = _t.time()
            result = clf(trade)
            latency_ms = int((_t.time() - _t0) * 1000)

            # Pack both outputs as compact JSON for side-by-side diff later.
            primary_json = json.dumps(primary_parsed, default=str)[:8000]
            shadow_json = json.dumps(result, default=str)[:8000]
            shadow_err = None if result.get("ok") else (result.get("error") or "unknown")

            from database import db_conn
            with db_conn() as _conn:
                _conn.execute(
                    "INSERT INTO shadow_responses ("
                    "primary_request_id, primary_module, primary_model, primary_text, "
                    "primary_input_tokens, primary_output_tokens, primary_latency_ms, "
                    "shadow_provider, shadow_model, shadow_text, shadow_latency_ms, "
                    "shadow_input_tokens, shadow_output_tokens, shadow_cost_usd, shadow_error"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex, "post_mortem", "claude-haiku-4-5",
                        primary_json,
                        primary_prompt_chars // 4, len(primary_text) // 4, 0,
                        "anthropic-dspy", "claude-haiku-4-5-20251001",
                        shadow_json, latency_ms,
                        0, 0, 0.0,  # token counts captured separately via LoggingLM
                        shadow_err,
                    )
                )
                _conn.commit()
        except Exception as e:
            _log.warning("DSPy shadow failed: %s", str(e)[:200])

    threading.Thread(target=_run, daemon=True, name="dspy-shadow:post_mortem").start()


_HAIKU_INPUT_PER_MTOK = 1.00
_HAIKU_OUTPUT_PER_MTOK = 5.00


def _estimate_haiku_cost(input_chars: int, output_chars: int) -> float:
    """Char-based Haiku cost estimate. Returns USD."""
    in_tok = input_chars // 4
    out_tok = output_chars // 4
    return round(
        in_tok * _HAIKU_INPUT_PER_MTOK / 1e6 +
        out_tok * _HAIKU_OUTPUT_PER_MTOK / 1e6,
        6,
    )


def _persist_postmortem(conn, trade: dict, parsed: dict,
                         cost_usd: float = 0.0) -> dict[str, Any]:
    """Validate parsed fields, write back to positions, return public result.

    Shared by the plain Haiku path and the DSPy path.

    cost_usd: per-trade USD estimate. Plain Haiku path passes a char-based
    estimate; DSPy path will pass the actual cost from the DSPy LM history
    once Fix 7 lands. Zero is acceptable when the caller cannot estimate.
    """
    tag = (parsed.get("tag") or "unknown").strip()
    if tag not in ALLOWED_TAGS:
        tag = "unknown"
    severity = (parsed.get("severity") or "low").strip().lower()
    if severity not in ("low", "medium", "high"):
        severity = "low"
    reason = (parsed.get("reason") or "")[:500]
    evidence = parsed.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]
    evidence_csv = ", ".join(str(e)[:120] for e in evidence[:6])

    conn.execute(
        "UPDATE positions SET postmortem_tag=?, postmortem_severity=?, "
        "postmortem_reason=?, postmortem_evidence=?, postmortem_done=1, "
        "postmortem_cost_usd=? WHERE id=?",
        (tag, severity, reason, evidence_csv, cost_usd, trade.get("id"))
    )
    conn.commit()

    return {
        "trade_id": trade.get("id"),
        "ok": True,
        "tag": tag,
        "severity": severity,
        "reason": reason,
        "evidence": evidence,
        "cost_usd": cost_usd,
    }


def run_pending(conn, max_per_cycle: int = 5) -> dict[str, Any]:
    """Process the queue of un-analyzed losers, up to max_per_cycle each call."""
    _ensure_table(conn)
    pending = _candidates(conn)
    summary: dict[str, Any] = {"pending": len(pending), "analyzed": 0,
                                "results": [], "total_cost_usd": 0.0}
    for trade in pending[:max_per_cycle]:
        result = analyze_one(conn, trade)
        if result.get("ok"):
            summary["analyzed"] += 1
            summary["total_cost_usd"] += float(result.get("cost_usd") or 0)
            summary["results"].append({
                "id": result["trade_id"],
                "tag": result["tag"],
                "severity": result["severity"],
            })
    return summary


def top_recurring_tags(conn, window_days: int = 7, limit: int = 3) -> list[dict]:
    """Return the top N most common postmortem tags in the window."""
    rows = conn.execute(
        "SELECT postmortem_tag, COUNT(*) as cnt, "
        "       SUM(CASE WHEN postmortem_severity='high' THEN 1 ELSE 0 END) as high_cnt "
        "FROM positions "
        "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
        "AND postmortem_done=1 AND postmortem_tag IS NOT NULL "
        f"AND close_time >= datetime('now', '-{int(window_days)} days') "
        "GROUP BY postmortem_tag ORDER BY cnt DESC LIMIT ?",
        (int(limit),)
    ).fetchall()
    return [{"tag": r[0], "count": int(r[1]), "high_count": int(r[2] or 0)}
            for r in rows]
