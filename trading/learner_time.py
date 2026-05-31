"""
L-2 (Master plan Week 4): per-session and per-DoW modifier learners.

Like learner_symbol.py but bucketing by:
  - session (Asia / London / NY-AM / NY-Overlap / NY-PM / Off-hours)
  - day-of-week (Sun..Sat)
  - hour (UTC 0-23)

Gated by R-5 Bayesian posterior on WR. Min samples per bucket: 20
(slightly looser than per-symbol's 10, since these buckets aggregate
faster).

Operator can pin any bucket via the learned_params.pinned column.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from trading import bayes, learned

_log = logging.getLogger(__name__)

LEARNER_NAME = "time_modifiers_v1"
MIN_TRADES_PER_BUCKET = 20
LOOKBACK_DAYS = 30
EQUITY_PCT_THRESHOLD = 0.02   # 2% of equity = "meaningful"
MAX_DELTA_PER_CYCLE = 0.15
PENALTY_CAP = -1.0
BOOST_CAP = 0.5

P_WR_BELOW_50_FOR_PENALTY = 0.85
P_WR_ABOVE_60_FOR_BOOST   = 0.85

_SESSION_BUCKETS = [
    ("Asia",      0,  8),
    ("London",    8, 13),
    ("NY-AM",    13, 16),
    ("NY-Overlap", 16, 18),
    ("NY-PM",    18, 22),
    ("Off-hours", 22, 24),
]
_DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _session_of(hour: int) -> str:
    for lbl, lo, hi in _SESSION_BUCKETS:
        if lo <= hour < hi:
            return lbl
    return "Off-hours"


def _bucket_by_dim(conn, dim: str) -> dict[str, list[tuple[float, int]]]:
    """Pull closed auto_ai trades and bucket by dim ('session' | 'dow' | 'hour').
    Returns {bucket_label: [(pnl, is_win), ...]}.
    """
    rows = conn.execute(
        "SELECT realized_pnl, close_time FROM positions "
        "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
        "AND close_time IS NOT NULL AND close_time != '' "
        f"AND close_time >= datetime('now', '-{int(LOOKBACK_DAYS)} days')"
    ).fetchall()
    out: dict[str, list[tuple[float, int]]] = {}
    for r in rows:
        pnl = float(r[0] or 0)
        ct = r[1]
        try:
            dt = datetime.fromisoformat(ct[:19])
        except Exception:
            continue
        if dim == "session":
            label = _session_of(dt.hour)
        elif dim == "dow":
            label = _DOW_LABELS[(dt.weekday() + 1) % 7]
        elif dim == "hour":
            label = f"h{dt.hour:02d}"
        else:
            continue
        is_win = 1 if pnl > 0 else 0
        out.setdefault(label, []).append((pnl, is_win))
    return out


def _equity_now(conn) -> float:
    try:
        from trading import kill_switch
        return kill_switch._equity_now(conn)
    except Exception:
        return 100.0


def _clamp_delta(current: float, proposed: float) -> float:
    delta = proposed - current
    if delta > MAX_DELTA_PER_CYCLE:
        proposed = current + MAX_DELTA_PER_CYCLE
    elif delta < -MAX_DELTA_PER_CYCLE:
        proposed = current - MAX_DELTA_PER_CYCLE
    return max(PENALTY_CAP, min(BOOST_CAP, proposed))


def _evaluate_dim(conn, dim: str, key_base: str) -> dict[str, Any]:
    """Run the learner for one dimension. Returns summary dict."""
    summary: dict[str, Any] = {"dim": dim, "checked": 0, "applied": [], "skipped": []}
    buckets = _bucket_by_dim(conn, dim)
    equity = _equity_now(conn)
    abs_threshold = equity * EQUITY_PCT_THRESHOLD

    for bucket_label, recs in buckets.items():
        summary["checked"] += 1
        n = len(recs)
        key = f"{key_base}.{bucket_label}"
        current = learned.get(conn, key, default=0.0)
        try: current = float(current)
        except Exception: current = 0.0

        if n < MIN_TRADES_PER_BUCKET:
            learned.log_skip(conn, key, LEARNER_NAME,
                              f"only {n} trades, need ≥{MIN_TRADES_PER_BUCKET}",
                              sample_size=n)
            summary["skipped"].append({"bucket": bucket_label, "reason": "insufficient", "n": n})
            continue

        pnls = [r[0] for r in recs]
        wins = sum(r[1] for r in recs)
        losses = n - wins
        total_pnl = sum(pnls)

        post = bayes.posterior_win_rate(wins, losses)
        p_below_50 = 1 - post["p_above_50pct"]
        p_above_60 = post["p_above_60pct"]

        proposed: float | None = None
        reason: str | None = None
        if p_below_50 > P_WR_BELOW_50_FOR_PENALTY and total_pnl < -abs_threshold:
            proposed = current - 0.3
            reason = f"P(WR<50)={p_below_50:.2f}, total_pnl=${total_pnl:.2f} → penalty -0.3"
        elif p_above_60 > P_WR_ABOVE_60_FOR_BOOST and total_pnl > abs_threshold:
            proposed = current + 0.2
            reason = f"P(WR>60)={p_above_60:.2f}, total_pnl=${total_pnl:.2f} → boost +0.2"
        else:
            learned.log_skip(conn, key, LEARNER_NAME,
                              f"gate not met: P(WR<50)={p_below_50:.2f}, "
                              f"P(WR>60)={p_above_60:.2f}, total=${total_pnl:.2f}",
                              sample_size=n)
            summary["skipped"].append({"bucket": bucket_label, "reason": "gate_not_met",
                                       "n": n, "wr_mean": post["mean"]})
            continue

        new_value = _clamp_delta(current, proposed)
        if abs(new_value - current) < 0.01:
            learned.log_skip(conn, key, LEARNER_NAME,
                              f"no change after clamp",
                              sample_size=n)
            continue

        result = learned.set(conn, key, round(new_value, 3),
                               learner_name=LEARNER_NAME,
                               gate_reason=reason,
                               sample_size=n,
                               ci_low=post["ci_low"],
                               ci_high=post["ci_high"],
                               default_value=0.0,
                               payload={"wr_posterior": post,
                                        "total_pnl": round(total_pnl, 2)})
        if result.get("action") == "applied":
            summary["applied"].append({"bucket": bucket_label, "old": current,
                                       "new": round(new_value, 3), "reason": reason})

    return summary


def run_all(conn) -> dict[str, Any]:
    """Run session, DoW, and hour learners. Returns merged summary."""
    return {
        "session": _evaluate_dim(conn, "session", "session_modifier"),
        "dow":     _evaluate_dim(conn, "dow", "dow_modifier"),
        "hour":    _evaluate_dim(conn, "hour", "hour_modifier"),
    }
