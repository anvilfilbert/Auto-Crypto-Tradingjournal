"""
L-3 (Master plan Week 5): score & threshold learners. Gated by A-B
backtest_validator before any write — this is the dependency that made
A-B a Week-4 prerequisite.

What this learner adjusts:

  consensus_min_score:
    Looks at the Opus-calibration table (ai_score_at_open vs realized P&L).
    If buckets below the current min_score are consistently profitable →
    propose lowering. If buckets at/above current min_score are
    consistently unprofitable → propose raising. The change passes through
    A-B's heuristic before being committed.

Guards:
  - Bayesian gate (R-5) on per-bucket P&L direction
  - Minimum 25 trades per bucket
  - A-B validator must return "approve" or "neutral"
  - Cap on per-cycle delta: ±1 score point
  - Hard bounds: 5 ≤ consensus_min_score ≤ 10
"""
from __future__ import annotations

import logging
import statistics
from typing import Any

from trading import backtest_validator, bayes, learned

_log = logging.getLogger(__name__)

LEARNER_NAME = "consensus_threshold_v1"
MIN_TRADES_PER_BUCKET = 25
LOOKBACK_DAYS = 30
MAX_DELTA_PER_CYCLE = 1
MIN_SCORE_FLOOR = 5
MAX_SCORE_CEILING = 10
DEFAULT_MIN_SCORE = 8


def _bucketed_pnls(conn, lookback_days: int) -> dict[int, list[float]]:
    """Bucket closed auto_ai trades by floor(ai_score_at_open).
    Returns {score_bucket: [pnl, ...]}.
    """
    rows = conn.execute(
        "SELECT ai_score_at_open, realized_pnl FROM positions "
        "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
        "AND ai_score_at_open IS NOT NULL "
        "AND close_time IS NOT NULL AND close_time != '' "
        f"AND close_time >= datetime('now', '-{int(lookback_days)} days')"
    ).fetchall()
    out: dict[int, list[float]] = {}
    for r in rows:
        try:
            bucket = int(float(r[0] or 0))
        except (TypeError, ValueError):
            continue
        if bucket < 1 or bucket > 10:
            continue
        out.setdefault(bucket, []).append(float(r[1] or 0))
    return out


def _current_min_score(conn) -> int:
    from trading import config as _cfg
    try:
        return int(_cfg.get_consensus_min_score())
    except Exception:
        return DEFAULT_MIN_SCORE


def evaluate_and_update(conn) -> dict[str, Any]:
    """Propose + validate + apply a new consensus_min_score.
    Returns {"action": ..., "old": ..., "new": ..., "reason": ..., "validator": ...}.
    """
    buckets = _bucketed_pnls(conn, LOOKBACK_DAYS)
    current = _current_min_score(conn)

    # Step 1: classify each bucket as positive / negative / inconclusive
    classified: dict[int, str] = {}
    expectancies: dict[int, float] = {}
    for score_bucket, pnls in sorted(buckets.items()):
        n = len(pnls)
        if n < MIN_TRADES_PER_BUCKET:
            classified[score_bucket] = "insufficient"
            continue
        mean_pnl = statistics.fmean(pnls)
        expectancies[score_bucket] = mean_pnl
        post = bayes.posterior_expectancy(pnls)
        p_above_0 = post.get("p_above_0")
        if p_above_0 is None:
            classified[score_bucket] = "no_posterior"
        elif p_above_0 > 0.80:
            classified[score_bucket] = "positive"
        elif p_above_0 < 0.20:
            classified[score_bucket] = "negative"
        else:
            classified[score_bucket] = "inconclusive"

    # Step 2: propose direction
    below_current = [b for b in classified if b < current]
    at_and_above  = [b for b in classified if b >= current]
    below_positive = [b for b in below_current if classified[b] == "positive"]
    above_negative = [b for b in at_and_above if classified[b] == "negative"]

    proposed = current
    direction = "hold"
    reason = ""

    if below_positive and not above_negative:
        # Buckets below the threshold are profitable → lower the threshold
        new = max(below_positive)
        if new < current:
            proposed = max(MIN_SCORE_FLOOR, new)
            direction = "lower"
            reason = (f"buckets {below_positive} below current {current} are "
                      f"profitably positive (R-5 posterior p>0 > 0.80) — "
                      f"lower threshold to {proposed}")
    elif above_negative and not below_positive:
        new = max(above_negative) + 1
        if new > current:
            proposed = min(MAX_SCORE_CEILING, new)
            direction = "raise"
            reason = (f"buckets {above_negative} at/above current {current} are "
                      f"loss-making (R-5 posterior p>0 < 0.20) — "
                      f"raise threshold to {proposed}")
    else:
        reason = "no clear-cut evidence in either direction"

    # Clamp delta per cycle
    if abs(proposed - current) > MAX_DELTA_PER_CYCLE:
        proposed = current + (MAX_DELTA_PER_CYCLE if proposed > current else -MAX_DELTA_PER_CYCLE)

    if proposed == current:
        learned.log_skip(conn, "consensus_min_score", LEARNER_NAME,
                          f"no change proposed ({reason})",
                          sample_size=sum(len(v) for v in buckets.values()))
        return {"action": "hold", "old": current, "new": current,
                "reason": reason, "classified": classified}

    # Step 3: A-B validator gate
    verdict = backtest_validator.validate_or_default(
        conn,
        key="consensus_min_score",
        old_value=current,
        new_value=proposed,
        dimension=None,
        dimension_value=None,
    )
    if verdict["recommendation"] == "reject":
        learned.log_skip(conn, "consensus_min_score", LEARNER_NAME,
                          f"A-B rejected: {verdict['reason']}",
                          sample_size=verdict.get("n_trades", 0),
                          payload={"validator": verdict, "proposed": proposed})
        return {"action": "rejected_by_validator", "old": current,
                "new": current, "reason": verdict["reason"],
                "validator": verdict, "classified": classified}

    # Step 4: write
    result = learned.set(
        conn, "consensus_min_score", int(proposed),
        learner_name=LEARNER_NAME,
        gate_reason=reason,
        sample_size=sum(len(v) for v in buckets.values()),
        default_value=DEFAULT_MIN_SCORE,
        payload={"validator": verdict, "expectancies": expectancies,
                  "classified": classified, "direction": direction},
    )
    return {
        "action":     result.get("action"),
        "old":        current,
        "new":        int(proposed),
        "reason":     reason,
        "direction":  direction,
        "validator":  verdict,
        "classified": classified,
        "expectancies": expectancies,
    }
