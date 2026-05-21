"""
ai_self_review.py — Per-trade alpha-leak retrospective.

When a closed trade's outcome materially disagrees with the AI's prediction
(score-high → lost, score-low → won), this module asks the AI:

    "You scored this setup X/10 and cited Y as your top reasons.
     The actual outcome was Z.
     If you could only see ONE additional signal that would have flipped your
     call, what would it be? Be specific (signal name, threshold, timeframe).
     Reply in 2 sentences."

The answer is stored in `ai_self_review` table. Over time, the recurring
suggestions become the journal's "AI wishlist" — signals worth adding to the
confluence stack.

Cost: ~$0.02 per review with caching, since the prompt is short and the
context (ANALYST_INSTRUCTIONS) is in the cached prefix.

Trigger paths:
  - run_review(call_id, conn) — explicit, used by API endpoint
  - run_pending_reviews(conn, limit) — batched, picks the N most-egregious
    alpha-leak trades that haven't been reviewed yet
"""
import json
from datetime import datetime, timezone

from constants import MODEL
from ai_client import send as ai_send
from helpers import strip_fence


# A trade is "alpha-leak worthy" when the predicted-vs-actual disagrees enough
# to be informative. We keep the threshold simple: high-score losers and
# low-score winners.
SCORE_HIGH = 7
SCORE_LOW  = 5


def _candidate_trades(conn, limit: int = 5) -> list[dict]:
    """Return up to `limit` closed trades where the AI was meaningfully wrong
    and we haven't already self-reviewed them."""
    cur = conn.execute("""
        SELECT a.id, a.symbol, a.direction, a.setup_score, a.outcome,
               a.outcome_pnl, a.cot_reasoning, a.analysis_json
        FROM analyzed_calls a
        LEFT JOIN ai_self_review r ON r.call_id = a.id
        WHERE r.id IS NULL
          AND a.outcome IN ('won','lost')
          AND a.setup_score IS NOT NULL
          AND (
                (a.setup_score >= ? AND a.outcome = 'lost')   -- false positive
             OR (a.setup_score <= ? AND a.outcome = 'won')    -- false negative
          )
        ORDER BY ABS(COALESCE(a.outcome_pnl, 0)) DESC
        LIMIT ?
    """, (SCORE_HIGH, SCORE_LOW, limit))
    return [dict(r) for r in cur.fetchall()]


def _build_prompt(trade: dict) -> str:
    aj = {}
    try:
        aj = json.loads(trade.get("analysis_json") or "{}")
    except Exception:
        pass
    kc = aj.get("key_conditions") or []
    return f"""You are reviewing one of your own past trade calls in light of how it actually played out.

SETUP:  {trade['symbol']} {trade['direction']} — you scored {trade['setup_score']}/10
ACTUAL: outcome={trade['outcome']}, realised P&L={trade.get('outcome_pnl')}

YOUR ORIGINAL REASONING:
{trade.get('cot_reasoning','(none recorded)')}

KEY CONDITIONS YOU CITED:
{chr(10).join(f'- {c}' for c in kc) if kc else '(none recorded)'}

If you could see ONE additional signal that would have flipped your call (entered vs skipped),
what would it be? Be specific: signal name, threshold, timeframe.

Reply with JSON only:
{{"missed_signal":"<short name>","threshold":"<e.g. 4H ADX > 30>","timeframe":"4H|1D|1H","weight":"high|medium|low","why":"<1 sentence>"}}"""


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_self_review (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id       INTEGER NOT NULL UNIQUE,
            missed_signal TEXT,
            threshold     TEXT,
            timeframe     TEXT,
            weight        TEXT,
            why           TEXT,
            raw_response  TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def run_review(call_id: int, conn) -> dict:
    """Run a self-review for one call_id. Caller must have a live conn."""
    _ensure_table(conn)
    # Skip if already reviewed
    existing = conn.execute(
        "SELECT id FROM ai_self_review WHERE call_id=?", (call_id,)
    ).fetchone()
    if existing:
        return {"call_id": call_id, "status": "already_reviewed"}

    row = conn.execute("""
        SELECT id, symbol, direction, setup_score, outcome, outcome_pnl,
               cot_reasoning, analysis_json
        FROM analyzed_calls WHERE id = ?
    """, (call_id,)).fetchone()
    if not row:
        return {"call_id": call_id, "status": "not_found"}

    trade = dict(row)
    prompt = _build_prompt(trade)
    text, _cached = ai_send(
        "self_review", MODEL,
        [{"role": "user", "content": prompt}],
        max_tokens=256,
        system=None,
    )
    parsed = {}
    try:
        parsed = json.loads(strip_fence((text or "").strip()))
    except Exception:
        pass

    conn.execute("""
        INSERT INTO ai_self_review (call_id, missed_signal, threshold,
                                     timeframe, weight, why, raw_response)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        call_id,
        parsed.get("missed_signal", ""),
        parsed.get("threshold", ""),
        parsed.get("timeframe", ""),
        parsed.get("weight", ""),
        parsed.get("why", ""),
        text or "",
    ))
    conn.commit()
    return {"call_id": call_id, "status": "reviewed", **parsed}


def run_pending_reviews(conn, limit: int = 5) -> dict:
    """Process up to `limit` unreviewed alpha-leak trades."""
    _ensure_table(conn)
    candidates = _candidate_trades(conn, limit=limit)
    results = []
    for c in candidates:
        try:
            results.append(run_review(c["id"], conn))
        except Exception as e:
            results.append({"call_id": c["id"], "status": "error", "error": str(e)[:120]})
    return {"reviewed": len(results), "results": results}


def aggregate_wishlist(conn, min_count: int = 2) -> list[dict]:
    """Group the stored reviews by missed_signal to find recurring suggestions."""
    _ensure_table(conn)
    cur = conn.execute("""
        SELECT LOWER(TRIM(missed_signal)) AS sig,
               COUNT(*) AS n,
               GROUP_CONCAT(DISTINCT timeframe) AS tfs,
               GROUP_CONCAT(DISTINCT threshold) AS thresholds,
               GROUP_CONCAT(why, ' | ')         AS reasons
        FROM ai_self_review
        WHERE missed_signal IS NOT NULL AND missed_signal != ''
        GROUP BY LOWER(TRIM(missed_signal))
        HAVING COUNT(*) >= ?
        ORDER BY n DESC
    """, (min_count,))
    return [dict(r) for r in cur.fetchall()]


def format_for_prompt(conn) -> str:
    """Top recurring AI-wishlist signals, condensed for the cached prefix."""
    wishlist = aggregate_wishlist(conn, min_count=2)
    if not wishlist:
        return ""
    lines = [
        f"AI SELF-REVIEW WISHLIST — signals that recurred in retrospect "
        f"on prior losses/missed wins:",
    ]
    for w in wishlist[:5]:
        tfs = (w.get("tfs") or "").split(",")[0]
        lines.append(
            f"  • {w['sig']} — flagged {w['n']}× as the missing signal "
            f"(common TFs: {tfs or '?'})"
        )
    lines.append("Weight these moderately — they're self-suggestions, not "
                 "validated edges yet.")
    return "\n".join(lines)
