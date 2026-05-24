"""
ai_calibration.py — Opus threshold calibration analysis.

Buckets closed auto_ai positions by the AI score they entered with
(`ai_score_at_open`, populated by executor._insert_open_position).
For each bucket, computes:
  - n (sample size)
  - win_rate (% of positions with realized_pnl > 0)
  - tp1_hit_rate (% closed by close_reason='TP')
  - sl_hit_rate (% closed by close_reason='SL')
  - avg_pnl, total_pnl
  - expectancy_per_trade (total_pnl / n)

Observation-only — does NOT auto-adjust the consensus threshold. The
operator reads the report and decides whether to retune CONSENSUS_MIN_SCORE.

Minimum sample size per bucket: 5 (below that, "insufficient_data" returned).
Reliable recalibration: n >= 15 per bucket.
"""
from typing import Optional


MIN_BUCKET_N    = 5    # below this: report bucket but flag as low-sample
RELIABLE_N      = 15   # reliable recalibration threshold


def compute_calibration(conn) -> dict:
    """
    Returns calibration data for auto_ai chain, bucketed by ai_score_at_open.

    Shape:
        {
          "n_total": int,                      # total closed auto_ai
          "n_with_score": int,                 # those with ai_score_at_open populated
          "buckets": [
            {
              "score": float,
              "n": int,
              "win_rate": float,               # percent
              "tp1_hit_rate": float,
              "sl_hit_rate": float,
              "avg_pnl": float,
              "total_pnl": float,
              "expectancy": float,             # total_pnl / n
              "reliable": bool,                # n >= RELIABLE_N
            }, ...
          ],
          "current_threshold": float,          # config CONSENSUS_MIN_SCORE
          "verdict": str,                      # human-readable summary
        }
    """
    rows = conn.execute("""
        SELECT
            ai_score_at_open AS score,
            COUNT(*) AS n,
            ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0.01 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
            ROUND(100.0 * SUM(CASE WHEN UPPER(close_reason) = 'TP' THEN 1 ELSE 0 END) / COUNT(*), 1) AS tp1_hit_rate,
            ROUND(100.0 * SUM(CASE WHEN UPPER(close_reason) = 'SL' THEN 1 ELSE 0 END) / COUNT(*), 1) AS sl_hit_rate,
            ROUND(AVG(realized_pnl), 4) AS avg_pnl,
            ROUND(SUM(realized_pnl), 2) AS total_pnl
        FROM positions
        WHERE chain = 'auto_ai'
          AND close_time IS NOT NULL AND close_time != ''
          AND ai_score_at_open IS NOT NULL
        GROUP BY ai_score_at_open
        ORDER BY ai_score_at_open ASC
    """).fetchall()

    n_total = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE chain='auto_ai' "
        "AND close_time IS NOT NULL AND close_time != ''"
    ).fetchone()[0]
    n_with_score = sum(r["n"] for r in rows)

    buckets = []
    for r in rows:
        d = dict(r)
        d["expectancy"] = round((d["total_pnl"] or 0) / d["n"], 4) if d["n"] else 0
        d["reliable"]   = d["n"] >= RELIABLE_N
        buckets.append(d)

    # Resolve current threshold from config (best-effort import)
    current_threshold = None
    try:
        from trading import config as fa_config
        current_threshold = fa_config.CONSENSUS_MIN_SCORE
    except Exception:
        pass

    # Build a human-readable verdict
    verdict = _build_verdict(buckets, current_threshold, n_with_score)

    return {
        "n_total":           n_total,
        "n_with_score":      n_with_score,
        "buckets":           buckets,
        "current_threshold": current_threshold,
        "verdict":           verdict,
        "reliable_n":        RELIABLE_N,
        "min_bucket_n":      MIN_BUCKET_N,
    }


def _build_verdict(buckets: list, threshold: Optional[float], n_with_score: int) -> str:
    """Plain-English summary of what the data is saying about the threshold."""
    if not buckets or n_with_score < MIN_BUCKET_N:
        return (f"Insufficient data — only {n_with_score} closed auto_ai positions "
                f"with ai_score_at_open populated. Need at least {RELIABLE_N} per "
                f"score bucket for reliable recalibration. Keep collecting.")

    reliable_buckets = [b for b in buckets if b["reliable"]]
    if not reliable_buckets:
        observed_n = sum(b["n"] for b in buckets)
        return (f"Observation only: {observed_n} positions across {len(buckets)} score "
                f"buckets, but no bucket has the {RELIABLE_N}+ samples needed for "
                f"recalibration. Buckets shown below for reference.")

    # Find best-expectancy and worst-expectancy reliable buckets
    by_exp = sorted(reliable_buckets, key=lambda b: b["expectancy"], reverse=True)
    best  = by_exp[0]
    worst = by_exp[-1]

    msg = (f"Reliable buckets: {len(reliable_buckets)} of {len(buckets)}. "
           f"Best: score {best['score']} (n={best['n']}, expectancy "
           f"{best['expectancy']:+.3f} USDT/trade). ")
    if best is not worst:
        msg += (f"Worst: score {worst['score']} (n={worst['n']}, expectancy "
                f"{worst['expectancy']:+.3f} USDT/trade). ")

    if threshold is not None:
        # Is the current threshold capturing the +EV buckets?
        above = [b for b in reliable_buckets if b["score"] >= threshold]
        below = [b for b in reliable_buckets if b["score"] < threshold]
        if above and below:
            avg_above = sum(b["expectancy"] for b in above) / len(above)
            avg_below = sum(b["expectancy"] for b in below) / len(below)
            if avg_above > avg_below:
                msg += (f"Current threshold {threshold} captures the +EV side "
                        f"(avg expectancy {avg_above:+.3f} vs {avg_below:+.3f}).")
            else:
                msg += (f"⚠ Current threshold {threshold} may be miscalibrated — "
                        f"buckets below {threshold} have higher expectancy "
                        f"({avg_below:+.3f}) than above ({avg_above:+.3f}). "
                        f"Consider lowering threshold to widen entry.")
    return msg
