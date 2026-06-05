"""
A-E (Master plan Week 11): Cascade Predictor. Combines three independent
microstructure signals into one cascade-risk score in [0, 1]:

  VPIN (N-4)        — order-flow toxicity. Higher = more informed flow.
  Funding spread    — disagreement across venues = positioning stress.
  OI delta vs price — divergent OI build-up against price = late longs/
                       shorts likely to capitulate.

The fused score is:

  risk = 0.50 × min(1, VPIN/0.7)
       + 0.30 × min(1, funding_spread_abs / 0.005)     # 0.5%/day = 1.0
       + 0.20 × min(1, oi_divergence_signal)

Public API:

  evaluate(conn, symbol, direction)
     → {risk: float, veto: bool, reasons: [...], parts: {...}}

Used as a veto in signal_consensus (after the VPIN gate, before order
placement) when risk > FUTURES_AI_CASCADE_VETO_THRESHOLD (default 0.75)
AND direction sides with the cascade pressure.

Asymmetry rule:
  Cascade risk usually hurts the SAME side that the divergence is
  building against. Example: OI rising while price falls = late shorts
  piling in → upside squeeze risk → veto NEW SHORTS, not new longs.
  Symmetric for longs.
"""
from __future__ import annotations

import logging
import os
from typing import Any

_log = logging.getLogger(__name__)

VETO_THRESHOLD = float(os.environ.get("FUTURES_AI_CASCADE_VETO_THRESHOLD", "0.75"))


def _vpin_component(conn, symbol: str) -> tuple[float, dict[str, Any]]:
    try:
        from trading import vpin as _vpin
        snap = _vpin.latest_for_symbol(conn, symbol, max_age_minutes=30)
    except Exception:
        snap = None
    if not snap or snap.get("vpin") is None:
        return (0.0, {"available": False})
    v = float(snap["vpin"])
    score = min(1.0, v / 0.70)
    return (score, {"available": True, "vpin": v, "score": score})


def _funding_component(symbol: str) -> tuple[float, dict[str, Any]]:
    """Funding-rate spread across venues. Higher absolute spread = more
    positioning stress = higher cascade risk."""
    try:
        from coinalyze_client import get_funding_rates
        fr = get_funding_rates(symbol)
    except Exception:
        fr = None
    if not fr or not isinstance(fr, dict):
        return (0.0, {"available": False})
    spread = fr.get("spread_pct")
    if spread is None:
        rates = [v for v in fr.get("by_venue", {}).values()
                  if isinstance(v, (int, float))]
        if len(rates) >= 2:
            spread = max(rates) - min(rates)
        else:
            return (0.0, {"available": False, "reason": "single-venue"})
    spread_abs = abs(float(spread))
    score = min(1.0, spread_abs / 0.005)   # 0.5% across-venue spread = 1.0
    return (score, {"available": True, "spread_pct": spread_abs, "score": score})


def _oi_divergence_component(symbol: str, direction: str) -> tuple[float, dict[str, Any]]:
    """OI rising while price falls (or vice versa) — late-comers piling
    in against the move. Higher divergence = higher squeeze risk on the
    opposing side."""
    try:
        from coinalyze_client import get_oi_history
        from market_context import get_price_change_24h
    except Exception:
        return (0.0, {"available": False})
    try:
        oi_now, oi_24h_ago = get_oi_history(symbol)
        if not oi_now or not oi_24h_ago:
            return (0.0, {"available": False})
        oi_delta = (oi_now - oi_24h_ago) / oi_24h_ago
        price_change = get_price_change_24h(symbol)
        if price_change is None:
            return (0.0, {"available": False})
    except Exception:
        return (0.0, {"available": False})

    # Divergence = OI growth in OPPOSITE direction of price.
    if (oi_delta > 0.05 and price_change < -0.02):
        # OI up, price down → late shorts → squeeze risk for SHORTS
        side_at_risk = "short"
    elif (oi_delta > 0.05 and price_change > 0.02):
        # OI up, price up → late longs piling in → squeeze risk for LONGS
        side_at_risk = "long"
    else:
        return (0.0, {"available": True, "oi_delta": oi_delta,
                      "price_change": price_change, "score": 0.0})

    magnitude = min(1.0, abs(oi_delta) / 0.20)   # 20% OI delta = full risk
    relevant = ((side_at_risk == "long"  and (direction or "").lower().startswith("l")) or
                (side_at_risk == "short" and (direction or "").lower().startswith("s")))
    score = magnitude if relevant else 0.0
    return (score, {"available": True, "oi_delta": round(oi_delta, 3),
                    "price_change": round(price_change, 4),
                    "side_at_risk": side_at_risk, "score": score})


def evaluate(conn, symbol: str, direction: str = "Long") -> dict[str, Any]:
    vpin_score, vpin_info = _vpin_component(conn, symbol)
    funding_score, funding_info = _funding_component(symbol)
    oi_score, oi_info = _oi_divergence_component(symbol, direction)

    risk = 0.50 * vpin_score + 0.30 * funding_score + 0.20 * oi_score
    veto = risk >= VETO_THRESHOLD

    reasons = []
    if vpin_score > 0.5: reasons.append(f"VPIN toxicity {vpin_info.get('vpin', 0):.2f}")
    if funding_score > 0.5: reasons.append(f"funding spread {funding_info.get('spread_pct', 0)*100:.2f}%")
    if oi_score > 0.5: reasons.append(f"OI divergence ({oi_info.get('side_at_risk', '?')}-side at risk)")
    return {
        "symbol":    symbol,
        "direction": direction,
        "risk":      round(risk, 3),
        "veto":      veto,
        "reasons":   reasons,
        "parts": {
            "vpin":    {"weight": 0.50, "score": round(vpin_score, 3),    **vpin_info},
            "funding": {"weight": 0.30, "score": round(funding_score, 3), **funding_info},
            "oi":      {"weight": 0.20, "score": round(oi_score, 3),      **oi_info},
        },
        "threshold": VETO_THRESHOLD,
    }


def veto_check(conn, symbol: str, direction: str) -> tuple[bool, str, dict]:
    """Convenience for signal_consensus / executor."""
    result = evaluate(conn, symbol, direction)
    if result["veto"]:
        return (True, f"cascade risk {result['risk']:.2f} ≥ "
                       f"{VETO_THRESHOLD:.2f} — {', '.join(result['reasons'])}",
                result)
    return (False, f"cascade risk {result['risk']:.2f} below veto", result)
