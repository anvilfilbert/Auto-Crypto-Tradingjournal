# market_regime.py
"""
3-state GaussianHMM regime classifier for BTC.
States labeled by mean log-return: trending_up / ranging / trending_down.
Retrains every 4 h; model saved via joblib for inspection.
"""
import logging
import os
import time
import numpy as np

_log        = logging.getLogger(__name__)
_TTL        = 14400   # 4 h
_CACHE: dict[str, tuple[float, dict]] = {}
_MODEL_PATH = os.path.join(os.path.dirname(__file__), ".hmm_regime_model.joblib")


def _fetch_ohlcv(limit: int = 540):
    import ccxt
    import pandas as pd
    ex  = ccxt.binance({"options": {"defaultType": "future"}})
    raw = ex.fetch_ohlcv("BTCUSDT", "4h", limit=limit)
    df  = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["close"] = df["close"].astype(float)
    return df


def _build_features(df) -> np.ndarray:
    log_ret  = np.log(df["close"] / df["close"].shift(1)).fillna(0).values
    rvol     = (df["close"].pct_change()
                .rolling(5, min_periods=2).std()
                .bfill().values)
    vol_norm = (df["volume"] /
                df["volume"].rolling(20, min_periods=5).mean()
                ).fillna(1.0).values
    X = np.column_stack([log_ret, rvol, vol_norm])
    return X[~np.isnan(X).any(axis=1)]


def _assign_labels(model, X: np.ndarray) -> dict[int, str]:
    states = model.predict(X)
    means  = {s: float(X[states == s, 0].mean()) if (states == s).any() else 0.0
              for s in range(model.n_components)}
    ordered = sorted(means, key=means.get)
    return {ordered[0]: "trending_down",
            ordered[1]: "ranging",
            ordered[2]: "trending_up"}


def _fit_and_predict() -> dict:
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError:
        return {"ok": False, "reason": "hmmlearn not installed — pip install hmmlearn"}
    try:
        df = _fetch_ohlcv(540)
        if len(df) < 50:
            return {"ok": False, "reason": f"only {len(df)} bars available"}
        X = _build_features(df)
        if len(X) < 30:
            return {"ok": False, "reason": "insufficient clean features"}

        model = GaussianHMM(n_components=3, covariance_type="diag",
                            n_iter=100, random_state=42)
        model.fit(X)

        label_map     = _assign_labels(model, X)
        current_state = int(model.predict(X)[-1])
        label         = label_map.get(current_state, "ranging")

        _, posteriors = model.score_samples(X)
        confidence    = float(posteriors[-1, current_state])

        try:
            import joblib
            joblib.dump({"model": model, "label_map": label_map}, _MODEL_PATH)
        except Exception:
            pass

        return {"ok": True, "label": label, "state_idx": current_state,
                "confidence": round(confidence, 3), "label_map": label_map}
    except Exception as exc:
        _log.warning("market_regime: %s", exc)
        return {"ok": False, "reason": str(exc)}


def detect_regime() -> dict:
    """Return current BTC market regime, TTL-cached."""
    now = time.time()
    if "btc" in _CACHE:
        ts, data = _CACHE["btc"]
        if now - ts < _TTL:
            return data
    result        = _fit_and_predict()
    _CACHE["btc"] = (now, result)
    return result


# HMM as a direct score gate (added 2026-05-24). Distinct from bear_phase which
# uses HMM only as a tie-breaker. This modifier applies a smaller magnitude
# (±0.2 vs bear_phase's ±0.3) gated on confidence > 0.6 — boundary regimes
# don't get a strong vote. Magnitudes intentionally lower than bear_phase
# because the two signals can stack in the aligned-direction case.
_HMM_CONFIDENCE_THRESHOLD = 0.60
_HMM_ALIGNMENT_WEIGHT     = 0.20


def hmm_alignment_weight(regime: dict, direction: str) -> tuple[float, str]:
    """
    Standalone HMM regime modifier for setup scoring.

    Args:
      regime:    output of detect_regime() — must have 'ok', 'label', 'confidence'
      direction: "Long" or "Short" (the setup's direction)

    Returns:
      (weight, reason) — weight is ±0.2 or 0; reason is a short label suitable
                          for logging and the summary line passed to Sonnet.

    Rules:
      trending_up + Long   → +0.2   "HMM: trending_up + Long → +0.2"
      trending_up + Short  → -0.2   (fighting macro trend)
      trending_down + Long → -0.2
      trending_down + Short → +0.2
      ranging              →  0.0  (no directional bias)
      confidence < 0.6     →  0.0  (boundary regime — don't bet on it)
      ok != True           →  0.0  (model didn't run; no signal)
    """
    if not regime or not regime.get("ok"):
        return 0.0, ""

    label = str(regime.get("label", "")).lower()
    conf  = float(regime.get("confidence") or 0)
    if conf < _HMM_CONFIDENCE_THRESHOLD:
        return 0.0, f"HMM: {label} (conf {conf:.2f} below threshold — skip)"

    dir_lc = str(direction or "").strip().lower()
    is_long = dir_lc == "long"
    is_short = dir_lc == "short"
    if not (is_long or is_short):
        return 0.0, ""

    if "trending_up" in label:
        w = _HMM_ALIGNMENT_WEIGHT if is_long else -_HMM_ALIGNMENT_WEIGHT
    elif "trending_down" in label:
        w = -_HMM_ALIGNMENT_WEIGHT if is_long else _HMM_ALIGNMENT_WEIGHT
    else:
        return 0.0, f"HMM: {label} (no directional bias)"

    return w, f"HMM: {label} + {direction} → {w:+.1f}"
