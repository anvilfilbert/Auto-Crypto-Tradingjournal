"""
L-4 (Master plan Week 9): TP/SL distance learners — gated by R-5 + A-B.

The closed-trade MFE/MAE distribution per archetype tells us empirically
where TP1 should land and how much SL buffer is needed. Two writers:

  archetype.tp1_atr_target:
    Median MFE-in-ATR for winning trades minus a small buffer.
    Default fall-through = 1.0 (scanner's current floor).

  archetype.sl_atr_buffer:
    The 90th-percentile of MAE-in-ATR among ULTIMATELY-WINNING trades.
    (We don't average MAE of LOSSES — those by definition got stopped.)
    Default fall-through = 1.0.

Why this layout:
  TP1 from MFE-of-winners answers "where do winners actually peak before
  retracing?" — directly trade-able. SL buffer from MAE-of-winners answers
  "how much heat do winners take before paying off?" — minimum buffer
  needed to avoid stopping out trades that would have worked.

Both write through learned_params; both gated by A-B validator and
clamped to a sensible range (0.5 ≤ ATR multiple ≤ 4.0).

Reads expectations:
  - positions.mfe_atr_4h  (max favorable excursion, in 4H-ATR units)
  - positions.mae_atr_4h  (max adverse excursion, in 4H-ATR units)
  These columns are added idempotently via _ensure_columns() — older
  positions stay NULL and are skipped by the learner.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any

from trading import backtest_validator, learned

_log = logging.getLogger(__name__)

LEARNER_NAME = "tpsl_distance_v1"
LOOKBACK_DAYS = 60
MIN_TRADES_PER_ARCHETYPE = 15
ATR_MULT_FLOOR = 0.5
ATR_MULT_CEILING = 4.0
TP_BUFFER_FRACTION = 0.15   # take median MFE × 0.85 to leave room
MAX_DELTA_PER_CYCLE = 0.3


def _ensure_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "mfe_atr_4h" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN mfe_atr_4h REAL")
    if "mae_atr_4h" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN mae_atr_4h REAL")
    conn.commit()


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def _pull_by_archetype(conn) -> dict[str, list[dict]]:
    """Fetch closed auto_ai trades with mfe/mae populated, group by archetype."""
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(archetype_at_open),''), "
        "       NULLIF(TRIM(setup_type),''), 'unknown') AS arch, "
        "       realized_pnl, mfe_atr_4h, mae_atr_4h "
        "FROM positions "
        "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
        "AND close_time IS NOT NULL AND close_time != '' "
        "AND mfe_atr_4h IS NOT NULL "
        f"AND close_time >= datetime('now', '-{int(LOOKBACK_DAYS)} days')"
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r[0], []).append({
            "pnl": float(r[1] or 0),
            "mfe": float(r[2] or 0),
            "mae": float(r[3] or 0),
        })
    return out


def _clamp_delta_and_bounds(current: float, proposed: float) -> float:
    delta = proposed - current
    if delta > MAX_DELTA_PER_CYCLE:
        proposed = current + MAX_DELTA_PER_CYCLE
    elif delta < -MAX_DELTA_PER_CYCLE:
        proposed = current - MAX_DELTA_PER_CYCLE
    return max(ATR_MULT_FLOOR, min(ATR_MULT_CEILING, proposed))


def evaluate_and_update(conn) -> dict[str, Any]:
    _ensure_columns(conn)
    by_arch = _pull_by_archetype(conn)
    summary: dict[str, Any] = {"applied": [], "skipped": []}

    for arch, records in by_arch.items():
        n = len(records)
        if n < MIN_TRADES_PER_ARCHETYPE:
            summary["skipped"].append({"archetype": arch, "reason": "insufficient", "n": n})
            continue

        winners = [r for r in records if r["pnl"] > 0]
        if len(winners) < 5:
            summary["skipped"].append({"archetype": arch, "reason": "too_few_winners",
                                       "n": n, "winners": len(winners)})
            continue

        mfe_winners = sorted([r["mfe"] for r in winners])
        mae_winners = sorted([abs(r["mae"]) for r in winners])
        median_mfe = statistics.median(mfe_winners)
        p90_mae    = _percentile(mae_winners, 0.90)

        proposed_tp = median_mfe * (1 - TP_BUFFER_FRACTION)
        proposed_sl = max(p90_mae * 1.05, 0.5)  # 5% headroom above observed worst-MAE-of-winner

        # ── TP1 distance learner ──
        tp_key = f"archetype.{arch}.tp1_atr_target"
        cur_tp = learned.get(conn, tp_key, default=1.0)
        try: cur_tp = float(cur_tp)
        except Exception: cur_tp = 1.0

        new_tp = _clamp_delta_and_bounds(cur_tp, proposed_tp)
        if abs(new_tp - cur_tp) >= 0.05:
            verdict_tp = backtest_validator.validate_or_default(
                conn, key=tp_key,
                old_value=cur_tp, new_value=new_tp,
                dimension="archetype", dimension_value=arch,
            )
            if verdict_tp["recommendation"] != "reject":
                result = learned.set(
                    conn, tp_key, round(new_tp, 3),
                    learner_name=LEARNER_NAME,
                    gate_reason=f"median_winner_MFE={median_mfe:.2f}×ATR → "
                                f"TP1={new_tp:.2f}×ATR",
                    sample_size=len(winners),
                    default_value=1.0,
                    payload={"validator": verdict_tp,
                             "median_mfe": round(median_mfe, 3),
                             "p90_mae": round(p90_mae, 3),
                             "n_winners": len(winners)},
                )
                if result.get("action") == "applied":
                    summary["applied"].append({"archetype": arch, "key": tp_key,
                                                "old": cur_tp, "new": round(new_tp, 3)})

        # ── SL buffer learner ──
        sl_key = f"archetype.{arch}.sl_atr_buffer"
        cur_sl = learned.get(conn, sl_key, default=1.0)
        try: cur_sl = float(cur_sl)
        except Exception: cur_sl = 1.0

        new_sl = _clamp_delta_and_bounds(cur_sl, proposed_sl)
        if abs(new_sl - cur_sl) >= 0.05:
            verdict_sl = backtest_validator.validate_or_default(
                conn, key=sl_key,
                old_value=cur_sl, new_value=new_sl,
                dimension="archetype", dimension_value=arch,
            )
            if verdict_sl["recommendation"] != "reject":
                result = learned.set(
                    conn, sl_key, round(new_sl, 3),
                    learner_name=LEARNER_NAME,
                    gate_reason=f"p90_winner_MAE={p90_mae:.2f}×ATR → "
                                f"SL buffer={new_sl:.2f}×ATR",
                    sample_size=len(winners),
                    default_value=1.0,
                    payload={"validator": verdict_sl,
                             "p90_mae": round(p90_mae, 3),
                             "median_mfe": round(median_mfe, 3),
                             "n_winners": len(winners)},
                )
                if result.get("action") == "applied":
                    summary["applied"].append({"archetype": arch, "key": sl_key,
                                                "old": cur_sl, "new": round(new_sl, 3)})

    return summary
