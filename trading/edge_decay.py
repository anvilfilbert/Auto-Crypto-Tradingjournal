"""
R-4 (Master plan Week 1 Day 4): per-archetype edge-decay monitors.

Two complementary statistical change-point detectors that operate on the
sequence of closed auto_ai trades, grouped by archetype. Both alert
WEEKS earlier than the rulebook regeneration cycle — which is the whole
point: catch the decay before it shows up in 30-day averages.

CUSUM (Cumulative Sum, Page 1954):
  Maintains running sum S_t of (x_t - μ_ref - k), reset to 0 if negative.
  Alerts when S_t > h.  Robust for known-mean shifts.
  μ_ref = baseline mean (we use overall in-window mean)
  k     = noise filter (default 0.5σ)
  h     = alarm threshold (default 4σ)

Page-Hinkley (1954):
  Tracks T_t = M_t - min(M_t' : t' ≤ t) where M_t accumulates centered
  observations minus a small delta. Alerts when T_t > λ.  Faster to
  react than CUSUM for unknown shift magnitudes.

Both report a sign (negative = adverse mean shift — edge decay; positive
= favorable shift — edge improvement). For our purposes, only negative
shifts trigger alerts (we don't want the system to react to "we got
lucky").

Public API:
  evaluate(conn, window_days=30) -> {archetype: {...metrics...}}
  alerts_only(conn, window_days=30) -> [archetype, reason] for any active alert
"""
from __future__ import annotations

import logging
import statistics
from typing import Any

_log = logging.getLogger(__name__)


def _cusum(observations: list[float], ref_mean: float,
            k: float, h: float) -> tuple[float, bool, int]:
    """Sequential CUSUM. Returns (final_value, alert_fired, alert_index_or_-1)."""
    s = 0.0
    final_s = 0.0
    alert_fired = False
    alert_idx = -1
    for i, x in enumerate(observations):
        # Negative-shift detector: accumulate when x is BELOW expected
        s = max(0.0, s + (ref_mean - x - k))
        final_s = s
        if s > h and not alert_fired:
            alert_fired = True
            alert_idx = i
            s = 0.0  # reset (single-alert behavior; subsequent alerts logged separately)
    return final_s, alert_fired, alert_idx


def _page_hinkley(observations: list[float],
                   delta: float, lambda_: float) -> tuple[float, bool, int]:
    """Page-Hinkley test. Returns (final_T, alert_fired, alert_index_or_-1)."""
    if not observations:
        return 0.0, False, -1
    # Online running mean
    running_sum = 0.0
    M = 0.0
    M_min = 0.0
    final_T = 0.0
    alert_fired = False
    alert_idx = -1
    for i, x in enumerate(observations):
        running_sum += x
        mu = running_sum / (i + 1)
        # Negative-shift detector — accumulate when x < mu - delta
        M += (mu - x) - delta
        if M < M_min:
            M_min = M
        T = M - M_min
        final_T = T
        if T > lambda_ and not alert_fired:
            alert_fired = True
            alert_idx = i
    return final_T, alert_fired, alert_idx


def _pnls_by_archetype(conn, window_days: int) -> dict[str, list[float]]:
    """Pull closed auto_ai realized_pnl values, grouped by archetype label.

    Uses `archetype_at_open` (snapshot taken at trade open by the
    orchestrator) when present, else falls back to `setup_type` (the
    AI-classifier label, written at backfill time). Hedges excluded.
    Returns dict {archetype: [pnl, pnl, ...]} ordered by close_time ASC.
    """
    sql = (
        "SELECT COALESCE(NULLIF(TRIM(archetype_at_open),''), "
        "       NULLIF(TRIM(setup_type),''), 'unknown') AS arch, "
        "       realized_pnl "
        "FROM positions "
        "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
        "AND close_time IS NOT NULL AND close_time != '' "
        f"AND close_time >= datetime('now', '-{int(window_days)} days') "
        "ORDER BY close_time ASC"
    )
    rows = conn.execute(sql).fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        arch = r[0] or "unknown"
        pnl = float(r[1] or 0)
        out.setdefault(arch, []).append(pnl)
    return out


def evaluate(conn, window_days: int = 30,
              k_sigma: float = 0.5, h_sigma: float = 4.0,
              ph_delta_sigma: float = 0.005, ph_lambda_sigma: float = 5.0,
              min_samples: int = 8) -> dict[str, Any]:
    """Run CUSUM + Page-Hinkley per archetype.

    Returns:
      {
        archetype: {
          n: int,
          recent_mean: float,
          recent_sigma: float,
          cusum_value: float,
          cusum_alert: bool,
          cusum_alert_index: int,
          ph_value: float,
          ph_alert: bool,
          ph_alert_index: int,
          severity: "ok" | "watch" | "alert",
        }
      }

    Severity rule:
      - "ok"    : neither alert
      - "watch" : one alert
      - "alert" : both alerts
      - "ns"    : not enough samples (n < min_samples)
    """
    buckets = _pnls_by_archetype(conn, window_days)
    out: dict[str, Any] = {}

    for arch, pnls in buckets.items():
        n = len(pnls)
        if n < min_samples:
            out[arch] = {
                "n": n, "severity": "ns",
                "reason": f"only {n} samples, need ≥{min_samples}",
            }
            continue

        mean = statistics.fmean(pnls)
        sigma = statistics.pstdev(pnls) if n > 1 else 1.0
        if sigma <= 0:
            sigma = 1.0

        cu_val, cu_alert, cu_idx = _cusum(pnls, ref_mean=mean,
                                            k=k_sigma * sigma,
                                            h=h_sigma * sigma)
        ph_val, ph_alert, ph_idx = _page_hinkley(pnls,
                                                   delta=ph_delta_sigma * sigma,
                                                   lambda_=ph_lambda_sigma * sigma)

        if cu_alert and ph_alert:
            severity = "alert"
        elif cu_alert or ph_alert:
            severity = "watch"
        else:
            severity = "ok"

        out[arch] = {
            "n": n,
            "recent_mean": round(mean, 4),
            "recent_sigma": round(sigma, 4),
            "cusum_value": round(cu_val, 4),
            "cusum_alert": cu_alert,
            "cusum_alert_index": cu_idx,
            "ph_value": round(ph_val, 4),
            "ph_alert": ph_alert,
            "ph_alert_index": ph_idx,
            "severity": severity,
        }
    return out


def alerts_only(conn, window_days: int = 30) -> list[dict]:
    """Convenience for the daily Telegram report — returns only archetypes
    currently in 'watch' or 'alert' state."""
    result = evaluate(conn, window_days=window_days)
    out = []
    for arch, d in result.items():
        if d.get("severity") in ("watch", "alert"):
            out.append({"archetype": arch, **d})
    return out
