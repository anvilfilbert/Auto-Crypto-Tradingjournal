"""
ai_rulebook.py — Personalised trader rulebook derived from trade history.

Self-learning loop:
  1. Collect stats from DB (patterns, calibration, symbol performance)
  2. Claude synthesises 5-10 actionable rules grounded in real numbers
  3. Rules stored in trader_rulebook table
  4. Injected into every AI prompt — Claude knows your edge leaks before it speaks

Three helpers for prompt injection:
  get_rulebook_for_prompt(conn)     → rules block for live trade + call analyzer
  get_calibration_for_prompt(conn)  → score accuracy block for call analyzer
  get_similar_trades_for_prompt(symbol, setup_type, direction, conn) → past trades block

Auto-update: triggered by POST /api/rulebook/update or weekly on sync.
Minimum 15 trades required before generating a rulebook.
"""

import json
import os
import re
import traceback
from datetime import datetime, timezone

from constants import MODEL
from ai_client import send as ai_send
from database import get_conn

# Escape hatch only — by default the rulebook is purely data-driven. This env
# var lets the operator drop synthesised rules whose title/body matches any
# comma-separated pattern (case-insensitive). Use sparingly — suppression
# adds personal bias on top of the data and should be reserved for cases
# where a model failure (not a market truth) produces misleading rules.
_SUPPRESS_RAW = os.environ.get("RULEBOOK_SUPPRESS_PATTERNS", "").strip()
_RULEBOOK_SUPPRESS = [p.strip().lower() for p in _SUPPRESS_RAW.split(",") if p.strip()]


def _should_suppress(rule: dict) -> bool:
    if not _RULEBOOK_SUPPRESS:
        return False
    blob = f"{rule.get('title','')} {rule.get('rule','')}".lower()
    return any(pat in blob for pat in _RULEBOOK_SUPPRESS)
from helpers  import strip_fence

MIN_TRADES   = 15
MAX_SIMILAR  = 8


# ── Calibration ────────────────────────────────────────────────────────────────

def get_calibration_data(conn) -> list:
    """How accurate have setup scores been? Groups analyzed_calls by score tier."""
    rows = conn.execute("""
        SELECT
            CASE
                WHEN setup_score >= 8 THEN 'high (8-10)'
                WHEN setup_score >= 6 THEN 'good (6-7)'
                WHEN setup_score >= 4 THEN 'moderate (4-5)'
                ELSE 'weak (1-3)'
            END AS tier,
            MIN(setup_score) AS min_score,
            COUNT(*) AS n,
            ROUND(100.0 * SUM(CASE WHEN hit_tp1=1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS tp1_rate,
            ROUND(100.0 * SUM(CASE WHEN hit_sl=1  THEN 1 ELSE 0 END) / COUNT(*), 1) AS sl_rate,
            ROUND(AVG(outcome_pnl), 2) AS avg_pnl
        FROM analyzed_calls
        WHERE outcome IS NOT NULL AND setup_score IS NOT NULL
        GROUP BY tier
        ORDER BY min_score DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_calibration_for_prompt(conn=None, exchange: str = None) -> str:
    """
    Enhanced calibration block: includes entry rate per tier + actionable verdict.
    exchange: if provided, filters to calls matched to that exchange only.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    exch_clause = ""
    exch_params = []
    if exchange in ('bitget', 'blofin'):
        exch_clause = "AND COALESCE(exchange, 'bitget') = ?"
        exch_params = [exchange]
    try:
        rows = conn.execute(f"""
            SELECT
                CASE
                    WHEN setup_score >= 8 THEN 'high (8-10)'
                    WHEN setup_score >= 6 THEN 'good (6-7)'
                    WHEN setup_score >= 4 THEN 'moderate (4-5)'
                    ELSE 'weak (1-3)'
                END AS tier,
                MIN(setup_score) AS min_score,
                COUNT(*) AS n,
                SUM(CASE WHEN status IN ('matched','closed') THEN 1 ELSE 0 END) AS entered,
                ROUND(100.0 * SUM(CASE WHEN hit_tp1=1 AND outcome IS NOT NULL THEN 1 ELSE 0 END)
                    / NULLIF(SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END), 0), 1) AS tp1_rate,
                ROUND(100.0 * SUM(CASE WHEN hit_sl=1 AND outcome IS NOT NULL THEN 1 ELSE 0 END)
                    / NULLIF(SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END), 0), 1) AS sl_rate,
                ROUND(AVG(CASE WHEN outcome IS NOT NULL THEN outcome_pnl END), 2) AS avg_pnl
            FROM analyzed_calls
            WHERE setup_score IS NOT NULL {exch_clause}
            GROUP BY tier
            ORDER BY min_score DESC
        """, exch_params).fetchall()
        if not rows:
            return ""
        # Only emit tiers where at least one trade has an actual outcome recorded
        rows = [r for r in rows if r["tp1_rate"] is not None or r["sl_rate"] is not None]
        if not rows:
            return ""
        lines = ["YOUR SETUP SCORE CALIBRATION (entry rate + actual outcomes):"]
        for r in rows:
            r = dict(r)
            entry_rate = round(r["entered"] / r["n"] * 100, 0) if r["n"] else 0
            verdict = ""
            if r["tp1_rate"] is not None:
                if r["tp1_rate"] >= 60:
                    verdict = " → ENTER"
                elif r["tp1_rate"] <= 30:
                    verdict = " → SKIP"
            lines.append(
                f"  Score {r['tier']}: {r['n']} analyzed, {entry_rate:.0f}% entered — "
                f"TP1 {r['tp1_rate']}%, SL {r['sl_rate']}%, avg P&L {r['avg_pnl']} USDT{verdict}"
            )

        # Append hindsight signal accuracy if data exists
        try:
            h_rows = conn.execute("""
                SELECT
                    CASE
                        WHEN setup_score >= 8 THEN 'high (8-10)'
                        WHEN setup_score >= 6 THEN 'good (6-7)'
                        WHEN setup_score >= 4 THEN 'moderate (4-5)'
                        ELSE 'weak (1-3)'
                    END AS tier,
                    MIN(setup_score) AS min_score,
                    COUNT(*) AS n,
                    SUM(CASE WHEN verdict='TP' THEN 1 ELSE 0 END) AS tp,
                    SUM(CASE WHEN verdict='FP' THEN 1 ELSE 0 END) AS fp,
                    SUM(CASE WHEN verdict='TN' THEN 1 ELSE 0 END) AS tn,
                    SUM(CASE WHEN verdict='FN' THEN 1 ELSE 0 END) AS fn
                FROM trade_hindsight
                WHERE verdict IS NOT NULL AND verdict != 'NEUTRAL'
                GROUP BY tier
                ORDER BY min_score DESC
            """).fetchall()
            if h_rows:
                lines.append("\nHINDSIGHT SIGNAL ACCURACY (retroactive blind scoring of past trades):")
                for hr in h_rows:
                    hr = dict(hr)
                    sig_total = hr["tp"] + hr["fp"] + hr["tn"] + hr["fn"]
                    accuracy  = round((hr["tp"] + hr["tn"]) / sig_total * 100, 1) if sig_total else None
                    acc_txt   = f"{accuracy}%" if accuracy is not None else "n/a"
                    lines.append(
                        f"  Score {hr['tier']}: {hr['n']} trades — "
                        f"TP {hr['tp']} / FP {hr['fp']} / TN {hr['tn']} / FN {hr['fn']} — "
                        f"accuracy {acc_txt}"
                    )
        except Exception:
            pass  # trade_hindsight table may not exist on older installs

        return "\n".join(lines)
    finally:
        if own_conn:
            conn.close()


# ── Similar trades ─────────────────────────────────────────────────────────────

def get_similar_trades(symbol: str, setup_type: str, direction: str,
                       conn, limit: int = MAX_SIMILAR,
                       exchange: str = None) -> list:
    """
    Fetch recent closed trades for same symbol + setup type + direction.
    Falls back to symbol-only if fewer than 3 matching trades found.
    exchange: if set, restrict to trades from that exchange.
    """
    exch_clause = " AND COALESCE(exchange,'bitget')=?" if exchange in ('bitget','blofin') else ""
    ep          = [exchange] if exch_clause else []
    rows = conn.execute(
        f"SELECT realized_pnl, direction, setup_type, duration_minutes, "
        f"entry_price, close_price, open_time FROM positions "
        f"WHERE symbol=? AND direction=? AND (setup_type=? OR ? IS NULL OR ?='')"
        f"{exch_clause} ORDER BY close_time DESC LIMIT ?",
        [symbol, direction, setup_type, setup_type, setup_type] + ep + [limit]
    ).fetchall()

    if len(rows) < 3:
        rows = conn.execute(
            f"SELECT realized_pnl, direction, setup_type, duration_minutes, "
            f"entry_price, close_price, open_time FROM positions "
            f"WHERE symbol=?{exch_clause} ORDER BY close_time DESC LIMIT ?",
            [symbol] + ep + [limit]
        ).fetchall()

    return [dict(r) for r in rows]


def get_similar_trades_for_prompt(symbol: str, setup_type: str, direction: str,
                                   conn=None) -> str:
    """Formatted similar-trades block for call analyzer prompt."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        trades = get_similar_trades(symbol, setup_type, direction, conn)
        if not trades:
            return ""

        label = f"{direction} {setup_type}" if setup_type else direction
        lines = [f"YOUR SIMILAR PAST TRADES — {symbol} ({label}, last {len(trades)}):"]
        wins = [t for t in trades if (t["realized_pnl"] or 0) > 0]
        losses = [t for t in trades if (t["realized_pnl"] or 0) < 0]

        for t in trades:
            pnl   = t["realized_pnl"] or 0
            dur   = t["duration_minutes"] or 0
            dur_s = f"{dur//60}h{dur%60:02d}m" if dur >= 60 else f"{dur}m"
            result = "WIN" if pnl > 0 else "LOSS"
            setup  = f" [{t['setup_type']}]" if t.get("setup_type") else ""
            date   = (t["open_time"] or "")[:10]
            lines.append(
                f"  {date}{setup}: entry {t['entry_price']} → close {t['close_price']}, "
                f"{'+'if pnl>=0 else ''}{pnl:.2f} USDT, {dur_s} ({result})"
            )

        avg_win  = round(sum(t["realized_pnl"] for t in wins)  / len(wins),  2) if wins  else 0
        avg_loss = round(sum(t["realized_pnl"] for t in losses) / len(losses), 2) if losses else 0
        lines.append(
            f"  → {len(wins)}W / {len(losses)}L | "
            f"avg win +{avg_win} USDT | avg loss {avg_loss} USDT"
        )
        return "\n".join(lines)
    finally:
        if own_conn:
            conn.close()


# ── Stats collection ───────────────────────────────────────────────────────────

def _collect_stats(conn) -> dict:
    def rows(sql):
        return [dict(r) for r in conn.execute(sql).fetchall()]

    # Recent-30d slices alongside lifetime — exposes regime shifts. A rule
    # that held over 6 months may be irrelevant in the last month if the
    # market regime flipped (the 'last 20 deteriorating' Q4 finding showed
    # exactly this). The prompt below instructs the AI to weight recent
    # more when the two windows diverge.
    by_weekday_30d = rows("""
        SELECT CASE strftime('%w',close_time)
                 WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday'
                 WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday' WHEN '5' THEN 'Friday'
                 ELSE 'Saturday' END AS day,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions
        WHERE close_time >= datetime('now','-30 days')
        GROUP BY day HAVING n >= 3 ORDER BY total_pnl DESC
    """)
    by_setup_30d = rows("""
        SELECT setup_type, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions
        WHERE close_time >= datetime('now','-30 days')
          AND setup_type IS NOT NULL AND setup_type != ''
        GROUP BY setup_type HAVING n >= 3 ORDER BY total_pnl DESC
    """)

    by_setup = rows("""
        SELECT setup_type, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions WHERE setup_type IS NOT NULL AND setup_type != ''
        GROUP BY setup_type HAVING n >= 5 ORDER BY n DESC
    """)
    # Sort by total_pnl (expectancy proxy) instead of win_rate — a day with 69%
    # WR but -$105 P&L is worse than one with 75% WR and +$166. The miner used
    # to pick rules off WR alone, which produced misleading 'avoid Monday'
    # rules where Monday's WR was actually fine; the real signal is expectancy.
    by_weekday = rows("""
        SELECT CASE strftime('%w',close_time)
                 WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday'
                 WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday' WHEN '5' THEN 'Friday'
                 ELSE 'Saturday' END AS day,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions GROUP BY day HAVING n >= 5 ORDER BY total_pnl DESC
    """)
    by_session = rows("""
        SELECT CASE
                 WHEN CAST(strftime('%H',open_time) AS INT) BETWEEN 0  AND 7  THEN 'Asia (00-08)'
                 WHEN CAST(strftime('%H',open_time) AS INT) BETWEEN 8  AND 12 THEN 'London (08-13)'
                 WHEN CAST(strftime('%H',open_time) AS INT) BETWEEN 13 AND 20 THEN 'NY/Overlap (13-21)'
                 ELSE 'Late/Off-hours (21-24)'
               END AS session,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions GROUP BY session HAVING n >= 5 ORDER BY total_pnl DESC
    """)
    by_direction = rows("""
        SELECT direction, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions GROUP BY direction
    """)
    by_duration = rows("""
        SELECT CASE
                 WHEN duration_minutes < 60    THEN '< 1h'
                 WHEN duration_minutes < 240   THEN '1-4h'
                 WHEN duration_minutes < 1440  THEN '4-24h'
                 WHEN duration_minutes < 10080 THEN '1-7 days'
                 ELSE '> 7 days'
               END AS bucket,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions WHERE duration_minutes IS NOT NULL
        GROUP BY bucket HAVING n >= 5 ORDER BY total_pnl DESC
    """)
    by_grade = rows("""
        SELECT execution_grade AS grade, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl
        FROM positions WHERE execution_grade IS NOT NULL
        GROUP BY grade HAVING n >= 3 ORDER BY grade
    """)
    worst = rows("""
        SELECT symbol, COUNT(*) AS n,
               ROUND(SUM(realized_pnl),2) AS total_pnl,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate
        FROM positions GROUP BY symbol HAVING n >= 5
        ORDER BY total_pnl ASC LIMIT 5
    """)
    best = rows("""
        SELECT symbol, COUNT(*) AS n,
               ROUND(SUM(realized_pnl),2) AS total_pnl,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate
        FROM positions GROUP BY symbol HAVING n >= 5
        ORDER BY total_pnl DESC LIMIT 5
    """)
    overall = dict(conn.execute("""
        SELECT COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(SUM(realized_pnl),2) AS total_pnl,
               ROUND(AVG(CASE WHEN realized_pnl>0 THEN realized_pnl END),2) AS avg_win,
               ROUND(AVG(CASE WHEN realized_pnl<0 THEN realized_pnl END),2) AS avg_loss
        FROM positions
    """).fetchone())
    recent = dict(conn.execute("""
        SELECT COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM (SELECT realized_pnl FROM positions ORDER BY close_time DESC LIMIT 20)
    """).fetchone())

    # ── Skill-provenance slices (added 2026-05-24) ──────────────────────────
    # Six dimensions populated at trade-open by the auto-trader chain.
    # ONLY auto_ai positions carry these — manual trades have NULLs.
    # The rulebook was previously blind to these dimensions; AI couldn't
    # cite "low_conviction archetype = 0% WR" or "opus_overrides = +EV"
    # because the stats dict didn't include them.
    by_consensus_model = rows("""
        SELECT consensus_model_used AS consensus_model, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),4) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions
        WHERE chain='auto_ai' AND consensus_model_used IS NOT NULL
        GROUP BY consensus_model_used HAVING n >= 3 ORDER BY total_pnl DESC
    """)
    by_bear_phase = rows("""
        SELECT bear_phase_at_open AS bear_phase, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),4) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions
        WHERE chain='auto_ai' AND bear_phase_at_open IS NOT NULL
        GROUP BY bear_phase_at_open HAVING n >= 3 ORDER BY total_pnl DESC
    """)
    by_archetype = rows("""
        SELECT archetype_at_open AS archetype, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),4) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions
        WHERE chain='auto_ai' AND archetype_at_open IS NOT NULL
        GROUP BY archetype_at_open HAVING n >= 3 ORDER BY total_pnl DESC
    """)
    by_po3_bucket = rows("""
        SELECT CASE
                 WHEN po3_total IS NULL THEN 'unknown'
                 WHEN po3_total < 0     THEN 'negative (fighting)'
                 WHEN po3_total = 0     THEN 'neutral (no PO3)'
                 WHEN po3_total <= 0.3  THEN 'one modifier (+small)'
                 WHEN po3_total <= 0.6  THEN 'two modifiers'
                 ELSE                        'three+ modifiers (max stack)'
               END AS po3_bucket,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),4) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions
        WHERE chain='auto_ai'
        GROUP BY po3_bucket HAVING n >= 3 ORDER BY total_pnl DESC
    """)
    by_opus_overrides = rows("""
        SELECT CASE WHEN opus_had_overrides=1 THEN 'with_overrides' ELSE 'no_overrides' END AS overrides,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),4) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions
        WHERE chain='auto_ai' AND consensus_model_used IS NOT NULL
        GROUP BY overrides HAVING n >= 3 ORDER BY total_pnl DESC
    """)
    by_tp_count = rows("""
        SELECT COALESCE(tp_levels_count, 0) AS tp_count, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),4) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions
        WHERE chain='auto_ai'
        GROUP BY tp_count HAVING n >= 3 ORDER BY tp_count ASC
    """)
    # Close-reason mix (mostly auto_ai but include manual for completeness)
    by_close_reason = rows("""
        SELECT close_reason, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions
        WHERE close_reason IS NOT NULL AND close_reason != ''
        GROUP BY close_reason HAVING n >= 2 ORDER BY total_pnl DESC
    """)

    return {
        "overall": overall, "recent_20": recent,
        "by_setup": by_setup, "by_weekday": by_weekday, "by_session": by_session,
        "by_direction": by_direction, "by_duration": by_duration, "by_grade": by_grade,
        "by_weekday_30d": by_weekday_30d, "by_setup_30d": by_setup_30d,
        "worst_symbols": worst, "best_symbols": best,
        "score_calibration": get_calibration_data(conn),
        # Skill-provenance dimensions (auto_ai chain only — fields are NULL
        # on manual trades by design). Lets the AI reason about WHICH
        # SKILLS are working not just which symbols/hours.
        "by_consensus_model": by_consensus_model,
        "by_bear_phase":      by_bear_phase,
        "by_archetype":       by_archetype,
        "by_po3_bucket":      by_po3_bucket,
        "by_opus_overrides":  by_opus_overrides,
        "by_tp_count":        by_tp_count,
        "by_close_reason":    by_close_reason,
    }


# ── Claude synthesis ───────────────────────────────────────────────────────────

def _ask_claude(stats: dict, total: int) -> list:
    sections = []
    ov = stats["overall"]
    sections.append(
        f"OVERALL: {total} trades, {ov.get('win_rate')}% WR, "
        f"total P&L {ov.get('total_pnl')} USDT, "
        f"avg win {ov.get('avg_win')} / avg loss {ov.get('avg_loss')} USDT"
    )
    r20 = stats["recent_20"]
    sections.append(f"RECENT 20: {r20.get('win_rate')}% WR, {r20.get('total_pnl')} USDT P&L")

    for label, key in [
        ("BY SETUP TYPE (lifetime)", "by_setup"),
        ("BY SETUP TYPE (last 30d) — recent regime",  "by_setup_30d"),
        ("BY DAY (lifetime)",         "by_weekday"),
        ("BY DAY (last 30d) — recent regime", "by_weekday_30d"),
        ("BY SESSION", "by_session"),
        ("BY DURATION", "by_duration"),
        ("BY EXECUTION GRADE", "by_grade"),
        ("WORST SYMBOLS", "worst_symbols"),
        ("BEST SYMBOLS", "best_symbols"),
        # ── Auto-trader skill cohorts (chain='auto_ai' only) ────────────
        ("BY CONSENSUS MODEL — auto_ai chain (Sonnet vs Opus)", "by_consensus_model"),
        ("BY BEAR-PHASE AT ENTRY — auto_ai chain (distribution/decline/capitulation/recovery)", "by_bear_phase"),
        ("BY ARCHETYPE — auto_ai chain (breakout/reversal/continuation/range/low_conviction)", "by_archetype"),
        ("BY PO3 STACKING — auto_ai chain (range × FVG × session modifiers)", "by_po3_bucket"),
        ("BY OPUS OVERRIDES — auto_ai chain (did Opus override scanner targets?)", "by_opus_overrides"),
        ("BY TP COUNT — auto_ai chain (multi-TP ladder depth)", "by_tp_count"),
        ("BY CLOSE REASON — all chains (TP / SL / BE_stop / MAE_cut / trail / manual)", "by_close_reason"),
    ]:
        rows = stats.get(key, [])
        if rows:
            sections.append(label + ":\n" + "\n".join(
                "  " + " | ".join(f"{k}: {v}" for k, v in r.items() if k != "min_score")
                for r in rows
            ))

    sections.append("BY DIRECTION:\n" + "\n".join(
        f"  {r['direction']}: {r['n']} trades, {r['win_rate']}% WR, total {r['total_pnl']} USDT"
        for r in stats["by_direction"]
    ))

    cal = stats.get("score_calibration", [])
    if cal:
        sections.append("SCORE CALIBRATION:\n" + "\n".join(
            f"  Score {r['tier']}: {r['n']} calls — TP1 {r['tp1_rate']}%, SL {r['sl_rate']}%, avg {r['avg_pnl']} USDT"
            for r in cal
        ))

    prompt = (
        f"Analyse this crypto futures trader's record purely from the data ({total} closed trades).\n\n"
        + "\n\n".join(sections) + "\n\n"
        "Your job: tell the trader what they're doing *well* and what they're doing *badly*, "
        "strictly based on the numbers above. You are an unbiased coach reporting evidence, "
        "not a strategist with opinions about which directions or symbols 'should' work.\n\n"
        "Rule types:\n"
        "  warning     — a behaviour or condition where the data shows the trader loses money\n"
        "  strength    — a behaviour or condition where the data shows the trader makes money\n"
        "  habit       — execution-discipline observation (timing, sizing, hold duration, etc.)\n"
        "  calibration — note about how accurate their setup scores have been\n"
        "  skill       — observation about which auto-trader skill cohort is working / failing\n"
        "                (consensus_model, bear_phase, archetype, po3_bucket, opus_overrides,\n"
        "                tp_count, or close_reason). Use this when the BY * — auto_ai chain\n"
        "                slices show a meaningful gap.\n\n"
        "STRICT EVIDENCE RULES (non-negotiable):\n"
        "  0. PREFER RECENT DATA WHEN IT DIVERGES FROM LIFETIME. When the\n"
        "     '(last 30d)' slice disagrees with the lifetime slice for the\n"
        "     same dimension (e.g. Tuesday was +EV lifetime but -EV last\n"
        "     30d), the rule should describe the recent state and flag the\n"
        "     shift. Markets change regime; rules that describe a dead\n"
        "     regime mislead the trader. Mention both numbers in the rule.\n"
        "  1. Optimise on EXPECTANCY (total_pnl, avg_pnl × n) — NOT on win_rate alone.\n"
        "     A 70% WR with negative total_pnl is a loser. A 50% WR with strongly positive "
        "total_pnl is a winner. Win_rate without P&L context is misleading.\n"
        "  2. NO PRIOR BIAS. Do not assume long is safer than short, that a specific session "
        "is bad, or that any market regime is 'risky'. The data decides. If shorts show "
        "positive expectancy, label them a strength. If Friday Asia-session trades make money, "
        "say so. Never write a rule that contradicts the numbers in front of you.\n"
        "  3. Every rule must cite specific numbers from the data above. No generic advice.\n"
        "  4. Cover both technicals (setup, day, session, duration, score-bucket) AND behaviour "
        "(execution_grade, hold time vs outcome, MFE captured vs left on table when present).\n"
        "  5. Min 5 trades per pattern. Skip categories with insufficient data — do not invent.\n\n"
        "Return ONLY valid JSON. No markdown. No prose outside the JSON array.\n"
        '[{"type":"warning|strength|habit|calibration|skill","title":"max 7 words",'
        '"rule":"1-2 sentences with specific numbers","confidence":"high|medium|low","data_points":0}]'
    )

    raw_text, _cached = ai_send(
        "rulebook", MODEL,
        [{"role": "user", "content": prompt}],
        max_tokens=4096,
    )
    raw = strip_fence(raw_text.strip())
    return json.loads(raw)


# ── Public API ─────────────────────────────────────────────────────────────────

MIN_NEW_TRADES = 5   # minimum new closed trades required to regenerate the rulebook


def update_rulebook(conn=None, force: bool = False) -> dict:
    """Generate fresh rules and persist to trader_rulebook. Returns result dict.
    Pass force=True to bypass the new-trade guard."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        if total < MIN_TRADES:
            return {
                "rules": [], "trade_count": total, "insufficient_data": True,
                "message": f"Need at least {MIN_TRADES} trades — you have {total}.",
            }

        if not force:
            stored = conn.execute(
                "SELECT value FROM settings WHERE key='rulebook_trade_count'"
            ).fetchone()
            prev_count = int(stored[0]) if stored else 0
            delta = total - prev_count
            if delta < MIN_NEW_TRADES:
                return {
                    **get_rulebook(conn), "skipped": True,
                    "message": f"Only {delta} new trade(s) since last update — need {MIN_NEW_TRADES}+. Use force=true to override.",
                }

        stats = _collect_stats(conn)
        rules = _ask_claude(stats, total)

        # Archive current rules before wiping (keep last 3 versions)
        current_rules = [dict(r) for r in conn.execute(
            "SELECT rule_type, title, rule, confidence, data_points FROM trader_rulebook"
        ).fetchall()]
        if current_rules:
            last_ver = (conn.execute(
                "SELECT MAX(version) FROM trader_rulebook_history"
            ).fetchone()[0] or 0)
            conn.execute(
                "INSERT INTO trader_rulebook_history (version, rules_json, trade_count) VALUES (?,?,?)",
                (last_ver + 1, json.dumps(current_rules), total)
            )
            conn.execute(
                "DELETE FROM trader_rulebook_history WHERE version <= ?",
                (last_ver + 1 - 3,)
            )

        conn.execute("DELETE FROM trader_rulebook")
        suppressed = 0
        for r in rules:
            if _should_suppress(r):
                suppressed += 1
                continue
            conn.execute(
                "INSERT INTO trader_rulebook (rule_type, title, rule, confidence, data_points) "
                "VALUES (?,?,?,?,?)",
                (r.get("type","insight"), r.get("title",""),
                 r.get("rule",""), r.get("confidence","medium"), r.get("data_points",0))
            )
        if suppressed:
            print(f"[rulebook] suppressed {suppressed} rule(s) matching RULEBOOK_SUPPRESS_PATTERNS")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        conn.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('rulebook_updated_at',?)", (now,)
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('rulebook_trade_count',?)", (str(total),)
        )
        conn.commit()
        return get_rulebook(conn)

    except Exception as e:
        traceback.print_exc()
        return {"error": f"Rulebook update failed: {type(e).__name__}: {e}"}
    finally:
        if own_conn:
            conn.close()


def get_rulebook(conn=None) -> dict:
    """Return rulebook rules + metadata for API response."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        rules = [dict(r) for r in conn.execute("""
            SELECT id, rule_type, title, rule, confidence, data_points, generated_at
            FROM trader_rulebook
            ORDER BY CASE rule_type
                WHEN 'warning' THEN 1 WHEN 'calibration' THEN 2
                WHEN 'habit' THEN 3 WHEN 'strength' THEN 4 ELSE 5 END
        """).fetchall()]
        updated = conn.execute(
            "SELECT value FROM settings WHERE key='rulebook_updated_at'"
        ).fetchone()
        return {"rules": rules, "updated_at": updated[0] if updated else None, "count": len(rules)}
    finally:
        if own_conn:
            conn.close()


def get_rulebook_for_prompt(conn=None) -> str:
    """
    Concise text block for Claude prompt injection.
    Rules older than 30 days get a [stale] annotation so Claude can down-weight them.
    Warnings and calibration first — most safety-critical.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT rule_type, title, rule, confidence,
                   CAST(julianday('now') - julianday(generated_at) AS INTEGER) AS age_days
            FROM trader_rulebook
            ORDER BY CASE rule_type
                WHEN 'warning' THEN 1 WHEN 'calibration' THEN 2
                WHEN 'habit' THEN 3 WHEN 'strength' THEN 4 ELSE 5 END
        """).fetchall()
        if not rows:
            return ""
        updated = conn.execute(
            "SELECT value FROM settings WHERE key='rulebook_updated_at'"
        ).fetchone()
        ts = updated[0] if updated else "unknown"
        icon = {"warning": "⚠", "strength": "✓", "habit": "→", "calibration": "~"}
        lines = [f"TRADER RULEBOOK (personalised from trade history, updated {ts}):"]
        for r in rows:
            age = r[4] or 0
            stale = " [stale — may not reflect recent behaviour]" if age > 30 else ""
            lines.append(f"  {icon.get(r[0],'•')} [{r[0].upper()}] {r[1]}: {r[2]}{stale}")
        return "\n".join(lines)
    finally:
        if own_conn:
            conn.close()
