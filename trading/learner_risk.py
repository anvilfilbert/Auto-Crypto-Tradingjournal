"""
L-5 (Master plan Week 10): risk-parameter learners. Three params:

  risk_per_trade_pct       — Kelly-fraction-style adjustment based on
                              recent expectancy + win rate. Floored at
                              0.5% (capital protection) and ceilinged at
                              4% (max Kelly-quarter).
  max_notional_usdt        — scales with growing equity, but the learner
                              can also pull it BACK on drawdown clusters.
  time_stop_hours          — if average-time-to-win is materially shorter
                              than average-time-to-loss, set a time stop
                              at percentile(time-to-win, 75%).

All three pass through A-B validator. All three also gate on a "DD-pause"
rule: if total drawdown is below -8% in the lookback window, the learner
proposes ONLY conservative changes (lower risk, lower notional, shorter
time stop) and refuses any proposal that would loosen risk.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime
from typing import Any

from trading import backtest_validator, bayes, learned

_log = logging.getLogger(__name__)

LEARNER_NAME = "risk_params_v1"
LOOKBACK_DAYS = 30
MIN_TRADES = 20

RISK_PCT_FLOOR = 0.005
RISK_PCT_CEILING = 0.04
KELLY_FRACTION = 0.25

NOTIONAL_FLOOR = 25.0
NOTIONAL_CEILING_FRACTION = 0.10   # ≤10% of equity

DD_PAUSE_THRESHOLD = -0.08

MAX_DELTA_RISK = 0.005             # ±0.5pp per cycle
MAX_DELTA_NOTIONAL = 10.0          # ±$10 per cycle
MAX_DELTA_TIMESTOP_HOURS = 4


def _equity_now(conn) -> float:
    try:
        from trading import kill_switch
        return float(kill_switch._equity_now(conn))
    except Exception:
        return 100.0


def _pnls(conn, days: int) -> list[tuple[float, str, str]]:
    """Returns [(pnl, open_time, close_time), ...] for closed auto_ai trades."""
    rows = conn.execute(
        "SELECT realized_pnl, open_time, close_time FROM positions "
        "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
        "AND close_time IS NOT NULL AND close_time != '' "
        f"AND close_time >= datetime('now', '-{int(days)} days')"
    ).fetchall()
    return [(float(r[0] or 0), r[1] or "", r[2] or "") for r in rows]


def _hours_between(iso_open: str, iso_close: str) -> float | None:
    try:
        a = datetime.fromisoformat(iso_open[:19])
        b = datetime.fromisoformat(iso_close[:19])
        return (b - a).total_seconds() / 3600.0
    except Exception:
        return None


def _kelly_risk_pct(wr: float, mean_win: float, mean_loss: float) -> float:
    """Kelly fraction × KELLY_FRACTION (quarter-Kelly is the prudent default)."""
    if mean_loss >= 0 or mean_win <= 0:
        return RISK_PCT_FLOOR
    payoff = mean_win / abs(mean_loss)
    if payoff <= 0:
        return RISK_PCT_FLOOR
    kelly = wr - (1 - wr) / payoff
    return max(RISK_PCT_FLOOR,
               min(RISK_PCT_CEILING, max(0.0, kelly) * KELLY_FRACTION))


def _clamp(cur: float, proposed: float, max_delta: float,
           floor: float, ceiling: float) -> float:
    delta = proposed - cur
    if delta > max_delta:
        proposed = cur + max_delta
    elif delta < -max_delta:
        proposed = cur - max_delta
    return max(floor, min(ceiling, proposed))


def _is_dd_pause(pnls: list[float], equity: float) -> bool:
    if not pnls or equity <= 0:
        return False
    return (sum(pnls) / equity) <= DD_PAUSE_THRESHOLD


def evaluate_and_update(conn) -> dict[str, Any]:
    trades = _pnls(conn, LOOKBACK_DAYS)
    summary: dict[str, Any] = {"applied": [], "skipped": [], "dd_pause": False}
    if len(trades) < MIN_TRADES:
        summary["skipped"].append({"key": "all", "reason": f"only {len(trades)} trades, need ≥{MIN_TRADES}"})
        return summary

    equity = _equity_now(conn)
    pnls = [t[0] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    wr = len(wins) / len(pnls)
    mean_win = statistics.fmean(wins) if wins else 0.0
    mean_loss = statistics.fmean(losses) if losses else 0.0

    dd_pause = _is_dd_pause(pnls, equity)
    summary["dd_pause"] = dd_pause

    # ── 1. risk_per_trade_pct ──
    cur_risk = float(learned.get(conn, "risk_per_trade_pct", default=0.02))
    proposed_risk = _kelly_risk_pct(wr, mean_win, mean_loss)
    if dd_pause and proposed_risk > cur_risk:
        proposed_risk = cur_risk   # don't loosen during DD pause
    new_risk = _clamp(cur_risk, proposed_risk, MAX_DELTA_RISK,
                       RISK_PCT_FLOOR, RISK_PCT_CEILING)
    if abs(new_risk - cur_risk) >= 0.001:
        verdict = backtest_validator.validate_or_default(
            conn, key="risk_per_trade_pct",
            old_value=cur_risk, new_value=new_risk,
        )
        if verdict["recommendation"] != "reject":
            result = learned.set(
                conn, "risk_per_trade_pct", round(new_risk, 4),
                learner_name=LEARNER_NAME,
                gate_reason=(f"WR={wr:.2f}, mean_win={mean_win:.2f}, "
                              f"mean_loss={mean_loss:.2f}, Kelly×0.25={proposed_risk:.4f}"
                              f"{' (DD-pause held)' if dd_pause else ''}"),
                sample_size=len(pnls),
                default_value=0.02,
                payload={"validator": verdict, "wr": wr,
                          "kelly_raw": proposed_risk, "dd_pause": dd_pause},
            )
            if result.get("action") == "applied":
                summary["applied"].append({"key": "risk_per_trade_pct",
                                            "old": cur_risk, "new": round(new_risk, 4)})

    # ── 2. max_notional_usdt ──
    cur_notional = float(learned.get(conn, "max_notional_usdt", default=25.0))
    proposed_notional = max(NOTIONAL_FLOOR, equity * NOTIONAL_CEILING_FRACTION)
    if dd_pause and proposed_notional > cur_notional:
        proposed_notional = cur_notional
    new_notional = _clamp(cur_notional, proposed_notional, MAX_DELTA_NOTIONAL,
                           NOTIONAL_FLOOR, equity * NOTIONAL_CEILING_FRACTION)
    if abs(new_notional - cur_notional) >= 1.0:
        verdict = backtest_validator.validate_or_default(
            conn, key="max_notional_usdt",
            old_value=cur_notional, new_value=new_notional,
        )
        if verdict["recommendation"] != "reject":
            result = learned.set(
                conn, "max_notional_usdt", round(new_notional, 2),
                learner_name=LEARNER_NAME,
                gate_reason=f"equity ${equity:.2f} × 10% = ${proposed_notional:.2f}",
                sample_size=len(pnls),
                default_value=25.0,
                payload={"validator": verdict, "equity": equity, "dd_pause": dd_pause},
            )
            if result.get("action") == "applied":
                summary["applied"].append({"key": "max_notional_usdt",
                                            "old": cur_notional, "new": round(new_notional, 2)})

    # ── 3. time_stop_hours ──
    win_durations = []
    loss_durations = []
    for pnl, ot, ct in trades:
        h = _hours_between(ot, ct)
        if h is None: continue
        (win_durations if pnl > 0 else loss_durations).append(h)
    if len(win_durations) >= 8 and len(loss_durations) >= 5:
        win_p75 = sorted(win_durations)[int(len(win_durations) * 0.75)]
        loss_med = statistics.median(loss_durations)
        # Only set a time stop if losses linger noticeably longer than winners
        if loss_med > win_p75 * 1.4:
            cur_ts = float(learned.get(conn, "time_stop_hours", default=24.0))
            proposed_ts = max(2.0, round(win_p75 * 1.2, 1))
            if dd_pause and proposed_ts > cur_ts:
                proposed_ts = cur_ts
            new_ts = _clamp(cur_ts, proposed_ts, MAX_DELTA_TIMESTOP_HOURS, 2.0, 72.0)
            if abs(new_ts - cur_ts) >= 1.0:
                verdict = backtest_validator.validate_or_default(
                    conn, key="time_stop_hours",
                    old_value=cur_ts, new_value=new_ts,
                )
                if verdict["recommendation"] != "reject":
                    result = learned.set(
                        conn, "time_stop_hours", round(new_ts, 1),
                        learner_name=LEARNER_NAME,
                        gate_reason=(f"win_p75={win_p75:.1f}h, loss_med={loss_med:.1f}h → "
                                      f"time stop at {new_ts:.1f}h"),
                        sample_size=len(trades),
                        default_value=24.0,
                        payload={"validator": verdict, "win_p75": win_p75,
                                  "loss_med": loss_med},
                    )
                    if result.get("action") == "applied":
                        summary["applied"].append({"key": "time_stop_hours",
                                                    "old": cur_ts, "new": round(new_ts, 1)})

    return summary
