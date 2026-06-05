"""
L-0 (Master plan Week 2): per-symbol modifier learner.

Smallest-proof demonstration of the learning loop end-to-end:
  observe → decide via R-5 gate → write to learned_params → log → safety-circuit
  monitors post-change outcome.

This learner penalizes symbols whose auto_ai performance has demonstrably
poor edge (Bayesian-gate: probability of true WR < 50% > 90%) AND meaningful
dollar loss (total P&L < -3% of current equity).

Conversely, boosts symbols with strong positive edge:
  P(true WR > 60%) > 90% AND total_pnl > +3% of equity → +0.3 score boost.

Max single-step change: ±0.2 score points per learner cycle. Safety bounds:
  symbol_modifier ∈ [-1.5, +0.6]. Hard clamp prevents runaway changes.

Pinned symbols are respected — operator overrides survive the learner.
"""
from __future__ import annotations

import logging
from typing import Any

from trading import bayes, learned

_log = logging.getLogger(__name__)

# ─── Constants (will move to learned_params in L-1 read-path refactor) ──
LEARNER_NAME = "symbol_modifier_v1"
MIN_TRADES_PER_SYMBOL = 10
LOOKBACK_DAYS = 30
EQUITY_PCT_THRESHOLD = 0.03   # 3% of equity = "meaningful" loss/gain
MAX_DELTA_PER_CYCLE = 0.2     # max single-step change
PENALTY_CAP = -1.5            # how far we can push down a bad symbol
BOOST_CAP = 0.6               # how far we can push up a good one
DEFAULT_MODIFIER = 0.0        # baseline (no adjustment)

# Gate thresholds
P_WR_BELOW_50_FOR_PENALTY = 0.90   # need ≥90% credibility on "WR<50%"
P_WR_ABOVE_60_FOR_BOOST   = 0.90   # need ≥90% credibility on "WR>60%"


def _pnls_by_symbol(conn) -> dict[str, list[tuple[float, int]]]:
    """Pull closed auto_ai realized_pnl values per symbol, plus their
    binary outcome (1=win, 0=loss/BE).

    Returns {symbol: [(pnl, is_win), ...]}.
    """
    sql = (
        "SELECT symbol, realized_pnl FROM positions "
        "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
        "AND close_time IS NOT NULL AND close_time != '' "
        f"AND close_time >= datetime('now', '-{int(LOOKBACK_DAYS)} days') "
        "ORDER BY close_time ASC"
    )
    rows = conn.execute(sql).fetchall()
    out: dict[str, list[tuple[float, int]]] = {}
    for r in rows:
        sym = r[0]
        pnl = float(r[1] or 0)
        is_win = 1 if pnl > 0 else 0
        out.setdefault(sym, []).append((pnl, is_win))
    return out


def _equity_now(conn) -> float:
    """Best-effort current equity for the -3% threshold calculation."""
    try:
        from trading import kill_switch
        return kill_switch._equity_now(conn)
    except Exception:
        return 100.0  # safe default


def _clamp_delta(current: float, proposed: float) -> float:
    """Apply MAX_DELTA_PER_CYCLE bound + global caps."""
    delta = proposed - current
    if delta > MAX_DELTA_PER_CYCLE:
        proposed = current + MAX_DELTA_PER_CYCLE
    elif delta < -MAX_DELTA_PER_CYCLE:
        proposed = current - MAX_DELTA_PER_CYCLE
    return max(PENALTY_CAP, min(BOOST_CAP, proposed))


def evaluate_and_update(conn) -> dict[str, Any]:
    """Run the learner. Returns a summary of decisions.

    Output shape:
      {
        'checked': N,
        'applied': [{symbol, old, new, reason}, ...],
        'skipped': [{symbol, reason}, ...],
      }
    """
    summary: dict[str, Any] = {"checked": 0, "applied": [], "skipped": []}

    buckets = _pnls_by_symbol(conn)
    equity = _equity_now(conn)
    abs_pnl_threshold = equity * EQUITY_PCT_THRESHOLD

    for sym, recs in buckets.items():
        summary["checked"] += 1
        n = len(recs)
        key = f"symbol_modifier.{sym}"
        current = learned.get(conn, key, default=DEFAULT_MODIFIER)
        try: current = float(current)
        except Exception: current = DEFAULT_MODIFIER

        if n < MIN_TRADES_PER_SYMBOL:
            learned.log_skip(conn, key, LEARNER_NAME,
                              f"only {n} trades, need ≥{MIN_TRADES_PER_SYMBOL}",
                              sample_size=n, payload={"current_modifier": current})
            summary["skipped"].append({"symbol": sym, "reason": "insufficient samples",
                                      "n": n})
            continue

        pnls = [r[0] for r in recs]
        wins = sum(r[1] for r in recs)
        losses = n - wins
        total_pnl = sum(pnls)

        # R-5 Bayesian posterior on WR
        post = bayes.posterior_win_rate(wins, losses)
        p_below_50 = 1 - post["p_above_50pct"]
        p_above_60 = post["p_above_60pct"]

        proposed: float | None = None
        reason: str | None = None

        # Penalty branch
        if p_below_50 > P_WR_BELOW_50_FOR_PENALTY and total_pnl < -abs_pnl_threshold:
            proposed = current - 0.5
            reason = (f"WR posterior mean {post['mean']:.2f}, P(WR<50%)={p_below_50:.2f}, "
                      f"total_pnl=${total_pnl:.2f} < -${abs_pnl_threshold:.2f} → penalty -0.5")
        # Boost branch
        elif p_above_60 > P_WR_ABOVE_60_FOR_BOOST and total_pnl > abs_pnl_threshold:
            proposed = current + 0.3
            reason = (f"WR posterior mean {post['mean']:.2f}, P(WR>60%)={p_above_60:.2f}, "
                      f"total_pnl=${total_pnl:.2f} > ${abs_pnl_threshold:.2f} → boost +0.3")
        else:
            learned.log_skip(conn, key, LEARNER_NAME,
                              f"gate not met: P(WR<50)={p_below_50:.2f}, P(WR>60)={p_above_60:.2f}, "
                              f"total_pnl=${total_pnl:.2f} vs threshold ±${abs_pnl_threshold:.2f}",
                              sample_size=n,
                              payload={"current_modifier": current,
                                       "wr_posterior": post,
                                       "total_pnl": round(total_pnl, 2)})
            summary["skipped"].append({"symbol": sym, "reason": "gate_not_met",
                                      "n": n, "wr_mean": post["mean"],
                                      "total_pnl": round(total_pnl, 2)})
            continue

        new_value = _clamp_delta(current, proposed)
        if abs(new_value - current) < 0.01:
            learned.log_skip(conn, key, LEARNER_NAME,
                              f"proposed delta within noise (current={current}, "
                              f"proposed={proposed}, clamped={new_value})",
                              sample_size=n)
            summary["skipped"].append({"symbol": sym, "reason": "no_change_after_clamp"})
            continue

        result = learned.set(conn, key, round(new_value, 3),
                               learner_name=LEARNER_NAME,
                               action="applied",
                               gate_reason=reason,
                               sample_size=n,
                               ci_low=post["ci_low"],
                               ci_high=post["ci_high"],
                               default_value=DEFAULT_MODIFIER,
                               payload={"wr_posterior": post,
                                        "total_pnl": round(total_pnl, 2),
                                        "proposed_unclamped": proposed,
                                        "current_before": current})
        if result.get("action") == "applied":
            summary["applied"].append({"symbol": sym, "old": current,
                                       "new": round(new_value, 3),
                                       "reason": reason})
        else:
            summary["skipped"].append({"symbol": sym, "reason": result.get("action")})

    return summary
