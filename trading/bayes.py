"""
R-5 (Master plan Week 1 Day 5): Bayesian credible intervals + frequentist
Wilson scores. Foundation for ALL future learner updates — every
parameter change goes through the posterior gate so we don't act on
12-trade samples.

Three families of intervals:

  Win-rate (binomial):
    posterior_win_rate(wins, losses)  → Beta(α+1, β+1) credible interval
    wilson_score(wins, total)         → frequentist alternative (no prior)
    Both fine for our use; Bayesian preferred because it composes with
    prior knowledge and stays well-defined at n=0.

  Continuous (P&L, expectancy):
    bootstrap_ci(samples, statistic)  → percentile bootstrap on mean / median
                                         / any user statistic
    posterior_expectancy(pnls)        → Gaussian posterior on mean P&L,
                                         conjugate-Normal-with-known-variance

  Decision helper:
    gate(stat_value, ci_low, ci_high, threshold) → bool
    Used by learners to decide whether a proposed change passes
    the "is this real?" test.

Why this module gates learners:
  Without CI gating, a per-archetype WR bucket with 8 trades at 75% WR
  looks like a "discover" signal, but the 95% credible interval is
  roughly [40%, 95%] — easily includes 50%. The Bayesian gate refuses
  to act unless the credible interval EXCLUDES the null hypothesis
  (e.g., WR=50%, expectancy=0).
"""
from __future__ import annotations

import logging
import statistics
from typing import Callable, Optional

try:
    from scipy import stats as _scipy_stats
    _SCIPY_OK = True
except Exception:
    _SCIPY_OK = False

_log = logging.getLogger(__name__)


# ─── Beta-Binomial — win rate posterior ─────────────────────────────────

def posterior_win_rate(wins: int, losses: int,
                        prior_alpha: float = 1.0, prior_beta: float = 1.0,
                        confidence: float = 0.95) -> dict:
    """Beta-Binomial posterior on win rate.

    Prior: Beta(α=1, β=1) by default — uniform (uninformative). Use
    Beta(0.5, 0.5) for Jeffreys prior (slightly more conservative at
    extremes). Posterior: Beta(α + wins, β + losses).

    Returns:
      {
        n:              total observations
        mean:           posterior mean (= α / (α+β))
        ci_low:         lower bound of credible interval
        ci_high:        upper bound of credible interval
        p_above_50pct:  P(true WR > 0.5)
        p_above_55pct:  P(true WR > 0.55)
        p_above_60pct:  P(true WR > 0.60)
        confidence:     the level used (default 0.95)
      }
    """
    if not _SCIPY_OK:
        return {"error": "scipy not available"}
    a = prior_alpha + max(wins, 0)
    b = prior_beta + max(losses, 0)
    if a <= 0 or b <= 0:
        return {"error": "invalid alpha/beta"}
    dist = _scipy_stats.beta(a, b)
    alpha = (1 - confidence) / 2
    return {
        "n":             int(wins + losses),
        "mean":          round(float(a / (a + b)), 4),
        "ci_low":        round(float(dist.ppf(alpha)), 4),
        "ci_high":       round(float(dist.ppf(1 - alpha)), 4),
        "p_above_50pct": round(float(1 - dist.cdf(0.50)), 4),
        "p_above_55pct": round(float(1 - dist.cdf(0.55)), 4),
        "p_above_60pct": round(float(1 - dist.cdf(0.60)), 4),
        "confidence":    confidence,
    }


# ─── Wilson score — frequentist alternative ────────────────────────────

def wilson_score(wins: int, total: int, confidence: float = 0.95) -> dict:
    """Wilson score interval on a binomial proportion. Better than the
    normal approximation for small n; correct at n=0 (returns [0, 1]).

    Returns {n, point: wins/total, ci_low, ci_high, confidence}.
    """
    if total <= 0:
        return {"n": 0, "point": None, "ci_low": 0.0, "ci_high": 1.0, "confidence": confidence}
    if not _SCIPY_OK:
        # Hand-coded Wilson — no scipy needed
        import math
        p = wins / total
        z = 1.96 if confidence == 0.95 else 2.576 if confidence == 0.99 else 1.645
        denom = 1 + z * z / total
        centre = (p + z * z / (2 * total)) / denom
        half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
        return {
            "n": total, "point": round(p, 4),
            "ci_low": round(max(0.0, centre - half), 4),
            "ci_high": round(min(1.0, centre + half), 4),
            "confidence": confidence,
        }
    # Scipy path — same formula, just uses scipy's z
    z = float(_scipy_stats.norm.ppf(1 - (1 - confidence) / 2))
    p = wins / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * (((p * (1 - p) + z * z / (4 * total)) / total) ** 0.5) / denom
    return {
        "n": total, "point": round(p, 4),
        "ci_low": round(max(0.0, centre - half), 4),
        "ci_high": round(min(1.0, centre + half), 4),
        "confidence": confidence,
    }


# ─── Bootstrap CI for continuous statistics ─────────────────────────────

def bootstrap_ci(samples: list[float],
                  statistic: Callable[[list[float]], float] = statistics.fmean,
                  n_resamples: int = 5000,
                  confidence: float = 0.95) -> dict:
    """Percentile bootstrap CI on any sample statistic.

    Default statistic is the mean (i.e., expectancy). Pass
    `statistics.median` for median, or a custom callable.

    Returns {n, point, ci_low, ci_high, confidence}.
    """
    n = len(samples)
    if n < 2:
        return {"n": n, "point": None, "ci_low": None, "ci_high": None,
                "confidence": confidence}
    try:
        import random as _r
        # Lazy import numpy — if unavailable, fall back to pure-Python sampling
        try:
            import numpy as _np
            arr = _np.array(samples, dtype=float)
            rng = _np.random.default_rng()
            resamples = rng.choice(arr, size=(n_resamples, n), replace=True)
            stats_dist = _np.array([statistic(row.tolist()) for row in resamples])
            alpha = (1 - confidence) / 2
            ci_low, ci_high = _np.quantile(stats_dist, [alpha, 1 - alpha])
            return {
                "n": n,
                "point": round(float(statistic(samples)), 4),
                "ci_low": round(float(ci_low), 4),
                "ci_high": round(float(ci_high), 4),
                "confidence": confidence,
            }
        except ImportError:
            stats_dist = []
            for _ in range(n_resamples):
                resample = [samples[_r.randrange(n)] for _ in range(n)]
                stats_dist.append(statistic(resample))
            stats_dist.sort()
            alpha = (1 - confidence) / 2
            lo_idx = int(alpha * n_resamples)
            hi_idx = int((1 - alpha) * n_resamples) - 1
            return {
                "n": n,
                "point": round(statistic(samples), 4),
                "ci_low": round(stats_dist[lo_idx], 4),
                "ci_high": round(stats_dist[hi_idx], 4),
                "confidence": confidence,
            }
    except Exception as e:
        _log.warning("bootstrap_ci failed: %s", e)
        return {"n": n, "point": None, "error": str(e)}


# ─── Posterior expectancy (Gaussian conjugate) ─────────────────────────

def posterior_expectancy(pnls: list[float], prior_mean: float = 0.0,
                          prior_n: float = 0.0,
                          confidence: float = 0.95) -> dict:
    """Posterior on the mean P&L per trade. Uses a conjugate Normal prior
    (prior_mean with prior_n virtual observations); when prior_n=0 this
    reduces to the frequentist Gaussian CI.

    Returns {n, mean, ci_low, ci_high, p_above_0, confidence}.
    """
    n = len(pnls)
    if n < 2:
        return {"n": n, "mean": None, "ci_low": None, "ci_high": None,
                "p_above_0": None, "confidence": confidence}
    s_mean = statistics.fmean(pnls)
    s_sigma = statistics.pstdev(pnls) if n > 1 else 1.0
    # Conjugate Normal-Normal update (known σ approximation; pragmatic)
    total_n = n + prior_n
    if total_n <= 0:
        return {"n": n, "mean": None, "error": "no observations"}
    post_mean = (n * s_mean + prior_n * prior_mean) / total_n
    post_se = s_sigma / max((total_n) ** 0.5, 1e-9)
    if _SCIPY_OK:
        z = float(_scipy_stats.norm.ppf(1 - (1 - confidence) / 2))
    else:
        z = 1.96 if confidence == 0.95 else 2.576
    half = z * post_se
    ci_low, ci_high = post_mean - half, post_mean + half
    # P(true mean > 0) under Normal approximation
    try:
        if _SCIPY_OK:
            p_above_0 = float(1 - _scipy_stats.norm.cdf(0, loc=post_mean, scale=post_se))
        else:
            # Pure-Python normal CDF via erf
            import math
            p_above_0 = 0.5 * (1 - math.erf((0 - post_mean) / (post_se * (2 ** 0.5))))
    except Exception:
        p_above_0 = None
    return {
        "n":         n,
        "mean":      round(post_mean, 4),
        "sigma":     round(s_sigma, 4),
        "se":        round(post_se, 4),
        "ci_low":    round(ci_low, 4),
        "ci_high":   round(ci_high, 4),
        "p_above_0": round(p_above_0, 4) if p_above_0 is not None else None,
        "confidence": confidence,
    }


# ─── Decision gate ─────────────────────────────────────────────────────

def gate(ci_low: Optional[float], ci_high: Optional[float],
          null_value: float = 0.5, direction: str = "above",
          n_observed: int = 0, min_n: int = 20) -> dict:
    """Should a learner act on this observation?

    direction='above'  : require ci_low > null_value  (e.g., WR significantly > 50%)
    direction='below'  : require ci_high < null_value  (e.g., WR significantly < 50%)

    Returns {pass: bool, reason: str}.
    """
    if n_observed < min_n:
        return {"pass": False,
                "reason": f"only {n_observed} samples, need ≥{min_n}"}
    if ci_low is None or ci_high is None:
        return {"pass": False, "reason": "no credible interval available"}
    if direction == "above":
        if ci_low > null_value:
            return {"pass": True,
                    "reason": f"CI [{ci_low}, {ci_high}] excludes null {null_value} (above)"}
        return {"pass": False,
                "reason": f"CI [{ci_low}, {ci_high}] includes null {null_value} (above)"}
    if direction == "below":
        if ci_high < null_value:
            return {"pass": True,
                    "reason": f"CI [{ci_low}, {ci_high}] excludes null {null_value} (below)"}
        return {"pass": False,
                "reason": f"CI [{ci_low}, {ci_high}] includes null {null_value} (below)"}
    return {"pass": False, "reason": f"unknown direction {direction}"}
