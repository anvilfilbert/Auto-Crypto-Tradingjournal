"""
ai_score_comparison.py — Side-by-side comparison of the three scoring systems.

Compares closed positions across:
  - scanner_score: pure technicals (RSI/MACD/EMA/ADX + PO3/bear_phase/HMM modifiers)
  - opus_score:    live consensus (Opus re-grade after Sonnet)
  - hindsight_score: retroactive blind Haiku grader

Returns per-trade rows + per-system aggregates (WR/expectancy/signal accuracy)
by score bucket + disagreement cases. Lets the operator judge empirically
which grader is "better" by outcome rather than by argument.

Cache: result is persisted to settings['score_comparison_cache_json'] on
compute. GET endpoint returns cache; POST endpoint forces recompute.
"""
import json
import datetime
from typing import Optional


# Buckets match the trader_rulebook calibration scheme + hindsight's own scheme
BUCKET_DEFS = [
    ("<5",   lambda s: s is not None and s <  5),
    ("5-6",  lambda s: s is not None and 5 <= s <= 6),
    ("7-8",  lambda s: s is not None and 7 <= s <= 8),
    ("9-10", lambda s: s is not None and s >= 9),
]
MIN_SAMPLE_N    = 5    # below this, a system is flagged "insufficient_data"
MIN_OVERLAP_N   = 10   # below this, pair comparisons flagged "insufficient_overlap"
ENTER_THRESHOLD = 7    # matches hindsight + auto-trader CONSENSUS_MIN_SCORE


def compute_comparison(conn) -> dict:
    """Read all closed positions + their scores from all three systems.
    Build per-trade rows, per-system aggregates, and disagreement set."""

    # Pull all closed positions joined to any hindsight row
    rows = conn.execute("""
        SELECT
            p.id            AS position_id,
            p.symbol        AS symbol,
            p.direction     AS direction,
            p.open_time     AS open_time,
            p.close_time    AS close_time,
            p.realized_pnl  AS realized_pnl,
            p.close_reason  AS close_reason,
            p.chain         AS chain,
            p.setup_score   AS scanner_score,
            p.ai_score_at_open AS opus_score,
            h.setup_score   AS hindsight_score,
            h.would_enter   AS hindsight_would_enter,
            h.verdict       AS hindsight_verdict
        FROM positions p
        LEFT JOIN trade_hindsight h ON h.position_id = p.id
        WHERE p.close_time IS NOT NULL AND p.close_time != ''
        ORDER BY p.close_time DESC
    """).fetchall()

    per_trade = []
    for r in rows:
        d = dict(r)
        d["scanner_score"]   = _coerce_score(d["scanner_score"])
        d["opus_score"]      = _coerce_score(d["opus_score"])
        d["hindsight_score"] = _coerce_score(d["hindsight_score"])
        d["realized_pnl"]    = round(float(d["realized_pnl"] or 0), 4)
        per_trade.append(d)

    # Per-system aggregates
    aggregates = {
        "scanner":   _aggregate_for("scanner_score",   per_trade),
        "opus":      _aggregate_for("opus_score",      per_trade),
        "hindsight": _aggregate_for("hindsight_score", per_trade),
    }

    # Pair disagreements (any 2 of 3 differ by ≥2 points)
    disagreements = _find_disagreements(per_trade)

    # Coverage counts
    meta = {
        "n_total":         len(per_trade),
        "n_with_scanner":   sum(1 for t in per_trade if t["scanner_score"]   is not None),
        "n_with_opus":      sum(1 for t in per_trade if t["opus_score"]      is not None),
        "n_with_hindsight": sum(1 for t in per_trade if t["hindsight_score"] is not None),
        "n_all_three":      sum(1 for t in per_trade
                                if t["scanner_score"]   is not None
                                and t["opus_score"]     is not None
                                and t["hindsight_score"] is not None),
        "computed_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "min_sample_n":  MIN_SAMPLE_N,
        "min_overlap_n": MIN_OVERLAP_N,
    }

    return {
        "per_trade":     per_trade,
        "aggregates":    aggregates,
        "disagreements": disagreements,
        "meta":          meta,
    }


def _coerce_score(v) -> Optional[float]:
    """Score → float or None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _aggregate_for(score_field: str, per_trade: list) -> dict:
    """Bucket trades by score and compute WR / expectancy / signal accuracy per bucket."""
    rows_with_score = [t for t in per_trade if t[score_field] is not None]
    n = len(rows_with_score)

    if n < MIN_SAMPLE_N:
        return {
            "n":          n,
            "by_bucket":  [],
            "overall":    None,
            "insufficient_data": True,
        }

    buckets_out = []
    for label, predicate in BUCKET_DEFS:
        bucket_rows = [t for t in rows_with_score if predicate(t[score_field])]
        if not bucket_rows:
            buckets_out.append({"bucket": label, "n": 0})
            continue
        buckets_out.append(_bucket_stats(label, bucket_rows, score_field))

    overall = _bucket_stats("overall", rows_with_score, score_field)
    return {
        "n":         n,
        "by_bucket": buckets_out,
        "overall":   overall,
        "insufficient_data": False,
    }


def _bucket_stats(label: str, rows: list, score_field: str) -> dict:
    """Compute n, wr, expectancy, signal accuracy (TP/FP/TN/FN) for one bucket."""
    n = len(rows)
    if n == 0:
        return {"bucket": label, "n": 0}

    wins   = [t for t in rows if t["realized_pnl"] >  0.01]
    losses = [t for t in rows if t["realized_pnl"] < -0.01]
    total_pnl = sum(t["realized_pnl"] for t in rows)
    win_rate  = round(100.0 * len(wins) / n, 1)

    # Signal accuracy: would_enter (score >= 7) × actual_outcome
    tp = fp = tn = fn = 0
    for t in rows:
        score = t[score_field]
        would_enter = (score is not None and score >= ENTER_THRESHOLD)
        won = t["realized_pnl"] > 0.01
        if would_enter and won:     tp += 1
        elif would_enter and not won: fp += 1
        elif not would_enter and not won: tn += 1
        elif not would_enter and won: fn += 1

    sig_total = tp + fp + tn + fn
    sig_acc   = round(100.0 * (tp + tn) / sig_total, 1) if sig_total else None

    return {
        "bucket":         label,
        "n":              n,
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       win_rate,
        "total_pnl":      round(total_pnl, 2),
        "expectancy":     round(total_pnl / n, 4),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "signal_accuracy": sig_acc,
    }


def _find_disagreements(per_trade: list) -> list:
    """
    Trades where at least one pair of scoring systems diverges by ≥2 points.
    Sorted by |delta| × |realized_pnl| (most consequential disagreements first).
    """
    out = []
    for t in per_trade:
        scores = {
            "scanner":   t["scanner_score"],
            "opus":      t["opus_score"],
            "hindsight": t["hindsight_score"],
        }
        present = {k: v for k, v in scores.items() if v is not None}
        if len(present) < 2:
            continue

        # Find max delta across all pairs that exist
        max_delta = 0.0
        max_pair  = None
        keys = list(present.keys())
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                delta = abs(present[a] - present[b])
                if delta > max_delta:
                    max_delta = delta
                    max_pair  = (a, b, present[a], present[b])

        if max_delta >= 2 and max_pair is not None:
            a, b, va, vb = max_pair
            out.append({
                "position_id":  t["position_id"],
                "symbol":       t["symbol"],
                "direction":    t["direction"],
                "close_time":   t["close_time"],
                "realized_pnl": t["realized_pnl"],
                "close_reason": t["close_reason"],
                "pair":         f"{a} vs {b}",
                "score_a":      va,
                "score_b":      vb,
                "delta":        round(max_delta, 1),
                "scanner_score":   t["scanner_score"],
                "opus_score":      t["opus_score"],
                "hindsight_score": t["hindsight_score"],
                "magnitude":    round(max_delta * abs(t["realized_pnl"]), 4),
            })
    out.sort(key=lambda x: -x["magnitude"])
    return out


# ── Cache ─────────────────────────────────────────────────────────────────────

CACHE_KEY = "score_comparison_cache_json"


def get_cached(conn) -> Optional[dict]:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (CACHE_KEY,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def save_cache(conn, data: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (CACHE_KEY, json.dumps(data)),
    )
    conn.commit()


def recompute_and_save(conn) -> dict:
    data = compute_comparison(conn)
    save_cache(conn, data)
    return data
