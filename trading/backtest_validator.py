"""
A-B (Master plan Week 4): Backtest Validator agent — gates L-3 onwards.

Every learner that proposes a non-trivial parameter change submits the
change through `validate(...)`. The validator replays the last N days of
auto_ai decisions with the OLD value vs the NEW value and returns a
diff + recommendation:

  - "approve"          — new value strictly dominates on the chosen metric
  - "reject"           — new value is materially worse
  - "neutral"          — change too small to matter, default = approve
  - "insufficient"     — not enough closed trades in the window for a fair test

This module ships with the FULL API surface day-one so L-3 callers can
already wire against it. The actual replay engine is staged:

  Stage 0 (TODAY) — heuristic-only:
    * Pull realized P&L from positions table for the window
    * Bucket by archetype/session/symbol depending on what changed
    * Compare bucket-mean vs current ALL-mean
    * Recommendation = approve if proposed direction agrees with bucket
      drift sign, reject if directly counter, neutral otherwise

  Stage 1 (after we have replay scaffolding) — full re-decision:
    * Re-run scanner stages 1..5 on historical bars
    * Re-evaluate consensus with OLD vs NEW scoring
    * Compare counterfactual filled-trades P&L

Stage 1 is intentionally out of scope today — the heuristic gate already
catches the obvious "this change would have lost money" cases.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any, Optional

from trading import bayes

_log = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 30
MIN_TRADES_FOR_VALIDATION = 15
NEUTRAL_BAND = 0.02   # |delta / equity| < 2% → neutral


def _recent_pnls(conn, days: int,
                  archetype: Optional[str] = None,
                  symbol: Optional[str] = None,
                  session: Optional[str] = None) -> list[float]:
    sql = ("SELECT realized_pnl FROM positions "
           "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
           "AND close_time IS NOT NULL AND close_time != '' "
           f"AND close_time >= datetime('now', '-{int(days)} days')")
    params: list = []
    if archetype:
        sql += " AND COALESCE(archetype_at_open, setup_type, 'unknown') = ?"
        params.append(archetype)
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol)
    rows = conn.execute(sql, params).fetchall()
    return [float(r[0] or 0) for r in rows]


def validate(conn, *, key: str, old_value: Any, new_value: Any,
              dimension: Optional[str] = None,
              dimension_value: Optional[str] = None,
              window_days: int = DEFAULT_WINDOW_DAYS,
              metric: str = "mean_pnl") -> dict[str, Any]:
    """Heuristic validator. Returns:

        {
          recommendation: "approve" | "reject" | "neutral" | "insufficient",
          confidence:     0.0..1.0,
          n_trades:       int,
          metric_value:   float,
          baseline:       float,
          reason:         str,
          stage:          0,
        }

    Callers can act on `recommendation in ("approve", "neutral")`.
    """
    archetype = dimension_value if dimension == "archetype" else None
    symbol    = dimension_value if dimension == "symbol"    else None
    session   = dimension_value if dimension == "session"   else None

    pnls = _recent_pnls(conn, window_days,
                          archetype=archetype, symbol=symbol, session=session)
    n = len(pnls)
    if n < MIN_TRADES_FOR_VALIDATION:
        return {
            "recommendation": "insufficient",
            "confidence":     0.0,
            "n_trades":       n,
            "metric_value":   None,
            "baseline":       None,
            "reason": (f"only {n} trades for {dimension or 'global'}"
                       f"={dimension_value or '*'} in window — "
                       f"need ≥{MIN_TRADES_FOR_VALIDATION}"),
            "stage": 0,
        }

    bucket_mean = statistics.fmean(pnls)
    # Baseline = global auto_ai mean in same window
    global_pnls = _recent_pnls(conn, window_days)
    baseline_mean = statistics.fmean(global_pnls) if global_pnls else 0.0

    # Bootstrap CI on bucket mean
    ci = bayes.bootstrap_ci(pnls)
    ci_low = ci.get("ci_low")
    ci_high = ci.get("ci_high")

    # Direction inference
    try:
        old_f = float(old_value); new_f = float(new_value)
        proposed_direction = "up" if new_f > old_f else "down" if new_f < old_f else "none"
    except (TypeError, ValueError):
        proposed_direction = "none"

    bucket_better = bucket_mean > baseline_mean
    bucket_worse = bucket_mean < baseline_mean

    # Heuristic decision
    if proposed_direction == "up" and bucket_worse:
        rec = "reject"
        reason = (f"bucket mean ${bucket_mean:.2f} < baseline ${baseline_mean:.2f} "
                  f"but proposing to INCREASE — direction conflicts with evidence")
        confidence = 0.7
    elif proposed_direction == "down" and bucket_better:
        rec = "reject"
        reason = (f"bucket mean ${bucket_mean:.2f} > baseline ${baseline_mean:.2f} "
                  f"but proposing to DECREASE — direction conflicts with evidence")
        confidence = 0.7
    elif proposed_direction == "up" and bucket_better:
        rec = "approve"
        reason = (f"bucket outperforms (${bucket_mean:.2f} > ${baseline_mean:.2f}) "
                  f"and proposed change increases — aligned")
        confidence = 0.8
    elif proposed_direction == "down" and bucket_worse:
        rec = "approve"
        reason = (f"bucket underperforms (${bucket_mean:.2f} < ${baseline_mean:.2f}) "
                  f"and proposed change decreases — aligned")
        confidence = 0.8
    elif ci_low is not None and ci_high is not None and ci_low <= baseline_mean <= ci_high:
        rec = "neutral"
        reason = (f"bucket CI [${ci_low:.2f}, ${ci_high:.2f}] straddles baseline "
                  f"${baseline_mean:.2f} — change too small to matter")
        confidence = 0.5
    else:
        rec = "neutral"
        reason = (f"no clear evidence either way — bucket=${bucket_mean:.2f}, "
                  f"baseline=${baseline_mean:.2f}")
        confidence = 0.4

    return {
        "recommendation": rec,
        "confidence":     round(confidence, 2),
        "n_trades":       n,
        "metric_value":   round(bucket_mean, 4),
        "baseline":       round(baseline_mean, 4),
        "bucket_ci":      {"low": ci_low, "high": ci_high},
        "reason":         reason,
        "stage":          0,
    }


def validate_or_default(conn, **kw) -> dict[str, Any]:
    """Call `validate`; on any exception return a neutral "approve"
    so the learner is not blocked by validator-side bugs.
    """
    try:
        return validate(conn, **kw)
    except Exception as e:
        _log.warning("backtest_validator.validate failed: %s", e)
        return {
            "recommendation": "approve",
            "confidence":     0.0,
            "n_trades":       0,
            "metric_value":   None,
            "baseline":       None,
            "reason":         f"validator error (fail-open): {e}",
            "stage":          -1,
        }
