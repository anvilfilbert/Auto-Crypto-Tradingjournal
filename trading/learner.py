"""
trading.learner — post-trade reflection + lesson capture.

For every closed paper or real trade, build a structured reflection
prompt with the full decision chain (scanner score, AI consensus, sizing,
SL/TP path, MFE/MAE trajectory) and ask Sonnet:

  "What worked? What didn't? What should the system change?"

Output is persisted to futures_ai_lessons. The aggregator runs every N
closes to fold lessons into rulebook hints + score-cap adjustments.

Per the 'paper-trade isolation' memory rule: lessons from paper trades
are gathered but DO NOT feed the rulebook miner until paper sample
reaches 50 closed trades. Real trades feed in immediately.
"""
from __future__ import annotations

import json
from typing import Optional


PAPER_SAMPLE_FEEDBACK_THRESHOLD = 50


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS futures_ai_lessons (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT    DEFAULT (datetime('now')),
            source          TEXT    NOT NULL,   -- 'paper' | 'real'
            position_id     INTEGER,            -- paper_positions.id OR positions.id
            symbol          TEXT,
            direction       TEXT,
            archetype       TEXT,
            consensus_score INTEGER,
            outcome         TEXT,               -- 'tp2'|'tp1_only'|'sl'|'mae'|'invalid'
            realized_pnl    REAL,
            mfe_pct         REAL,
            mae_pct         REAL,
            duration_min    INTEGER,
            lesson_json     TEXT,               -- Sonnet structured output
            applied_to_rulebook INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lessons_ts ON futures_ai_lessons(ts DESC)"
    )
    conn.commit()


# ── Public entry points ─────────────────────────────────────────────────────

def reflect_on_paper_close(conn, paper_position_id: int) -> Optional[dict]:
    """Called by paper.py after closing a position. Build the reflection
    prompt + record the lesson."""
    _ensure_table(conn)
    row = conn.execute(
        "SELECT * FROM paper_positions WHERE id=?", (paper_position_id,)
    ).fetchone()
    if not row:
        return None
    rec = dict(row)
    if rec.get("status") != "closed":
        return None

    return _reflect(conn, source="paper", rec=rec, position_id=paper_position_id)


def reflect_on_real_close(conn, position_id: int) -> Optional[dict]:
    """Called when a real-mode Bitget position closes (via executor's
    post-close hook)."""
    _ensure_table(conn)
    row = conn.execute(
        "SELECT * FROM positions WHERE id=?", (position_id,)
    ).fetchone()
    if not row:
        return None
    rec = dict(row)
    return _reflect(conn, source="real", rec=rec, position_id=position_id)


# ── Internals ────────────────────────────────────────────────────────────────

_LESSON_PROMPT = """You are reviewing a SINGLE closed crypto futures trade
executed by an auto-trader. The system needs to learn from each trade —
what worked, what didn't, and what (if anything) should change for
similar setups in the future.

TRADE FACTS:
  Source:         {source}
  Symbol:         {symbol}
  Direction:      {direction}
  Archetype:      {archetype}
  Consensus score: {score}/10
  Notional:       ${notional}
  Outcome:        {outcome}   (realized P&L: ${pnl})
  Duration:       {duration} min
  Entry:          {entry}
  Final SL:       {final_sl}
  TP1 / TP2:      {tp1} / {tp2}
  MFE / MAE:      {mfe}% / {mae}%
  TP1 hit?        {tp1_hit}
  SL hit?         {sl_hit}
  MAE breach?     {mae_breach}

YOUR TASK:
Return ONLY valid JSON with these fields:
{{
  "verdict":      "win" | "loss" | "scratch",
  "primary_cause": "one sentence — what made the trade do what it did",
  "what_worked":   "one sentence",
  "what_failed":   "one sentence (empty string if nothing material)",
  "lesson":        "one sentence — what the SYSTEM should remember for similar setups",
  "rulebook_hint": "one short rule the system could derive from this single
                    trade (or empty string if a single trade isn't enough
                    evidence)",
  "confidence":    "low" | "medium" | "high"
}}"""


def _reflect(conn, source: str, rec: dict, position_id: int
              ) -> Optional[dict]:
    """Build prompt, call Sonnet, persist the lesson."""
    try:
        from ai_client import send as ai_send
        from constants import MODEL
        from helpers import strip_fence

        outcome = rec.get("close_reason") or (
            "tp2" if rec.get("tp2_hit") else
            "sl"  if rec.get("sl_hit")  else
            "mae" if rec.get("mae_breach") else
            "other"
        )

        # Compute duration when present
        dur_min = None
        try:
            import datetime as _dt
            if rec.get("opened_at") and rec.get("closed_at"):
                dt_o = _dt.datetime.fromisoformat(rec["opened_at"][:19])
                dt_c = _dt.datetime.fromisoformat(rec["closed_at"][:19])
                dur_min = int((dt_c - dt_o).total_seconds() / 60)
        except Exception:
            dur_min = rec.get("duration_minutes")

        prompt = _LESSON_PROMPT.format(
            source     = source,
            symbol     = rec.get("symbol"),
            direction  = rec.get("direction"),
            archetype  = rec.get("archetype") or rec.get("setup_type") or "—",
            score      = rec.get("score_consensus") or rec.get("setup_score") or "?",
            notional   = rec.get("notional_usdt") or rec.get("size_usdt") or "?",
            outcome    = outcome,
            pnl        = rec.get("realized_pnl") or 0,
            duration   = dur_min or "?",
            entry      = rec.get("entry_price"),
            final_sl   = rec.get("current_sl") or rec.get("sl_price"),
            tp1        = rec.get("tp1_price") or "?",
            tp2        = rec.get("tp2_price") or "?",
            mfe        = rec.get("mfe_pct") or "?",
            mae        = rec.get("mae_pct") or "?",
            tp1_hit    = "yes" if rec.get("tp1_hit") else "no",
            sl_hit     = "yes" if rec.get("sl_hit") else "no",
            mae_breach = "yes" if rec.get("mae_breach") else "no",
        )

        raw, _cached = ai_send("futures_ai_learner", MODEL,
                                [{"role": "user", "content": prompt}],
                                max_tokens=512)
        data = json.loads(strip_fence(raw.strip()))

        conn.execute("""
            INSERT INTO futures_ai_lessons
              (source, position_id, symbol, direction, archetype,
               consensus_score, outcome, realized_pnl, mfe_pct, mae_pct,
               duration_min, lesson_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            source, position_id, rec.get("symbol"), rec.get("direction"),
            rec.get("archetype") or rec.get("setup_type"),
            rec.get("score_consensus") or rec.get("setup_score"),
            outcome, rec.get("realized_pnl"),
            rec.get("mfe_pct"), rec.get("mae_pct"),
            dur_min, json.dumps(data),
        ))
        conn.commit()

        # Log a short summary to the main audit trail so the operator sees
        # the lesson in the Futures-AI page without opening the lessons table
        try:
            conn.execute("""
                INSERT INTO futures_ai_log
                  (ts, event, symbol, direction, score, payload_json)
                VALUES (datetime('now'), 'lesson', ?, ?, ?, ?)
            """, (
                rec.get("symbol") or "", rec.get("direction") or "",
                int(rec.get("score_consensus") or rec.get("setup_score") or 0),
                json.dumps({
                    "verdict": data.get("verdict"),
                    "lesson":  data.get("lesson"),
                    "rulebook_hint": data.get("rulebook_hint"),
                })[:500],
            ))
            conn.commit()
        except Exception:
            pass

        return data
    except Exception as e:
        # Reflection failure shouldn't break the close path
        try:
            conn.execute("""
                INSERT INTO futures_ai_log
                  (ts, event, symbol, direction, score, payload_json)
                VALUES (datetime('now'), 'lesson_error', ?, ?, ?, ?)
            """, (
                rec.get("symbol") or "", rec.get("direction") or "",
                int(rec.get("score_consensus") or rec.get("setup_score") or 0),
                json.dumps({"error": str(e)[:200]}),
            ))
            conn.commit()
        except Exception:
            pass
        return None


def can_feed_rulebook(conn) -> tuple[bool, str]:
    """Returns (allowed, reason). Paper lessons only feed the rulebook
    after the threshold; real lessons always feed."""
    try:
        n_paper_closed = conn.execute(
            "SELECT COUNT(*) FROM futures_ai_lessons WHERE source='paper'"
        ).fetchone()[0]
        n_real_closed = conn.execute(
            "SELECT COUNT(*) FROM futures_ai_lessons WHERE source='real'"
        ).fetchone()[0]
    except Exception:
        return False, "lessons table unavailable"

    if n_real_closed > 0:
        return True, f"{n_real_closed} real lessons available"
    if n_paper_closed >= PAPER_SAMPLE_FEEDBACK_THRESHOLD:
        return True, (
            f"{n_paper_closed} paper lessons reached threshold "
            f"({PAPER_SAMPLE_FEEDBACK_THRESHOLD})"
        )
    return False, (
        f"paper lessons {n_paper_closed}/{PAPER_SAMPLE_FEEDBACK_THRESHOLD} "
        f"and no real trades yet — rulebook still trains on operator's "
        f"manual trades only"
    )
