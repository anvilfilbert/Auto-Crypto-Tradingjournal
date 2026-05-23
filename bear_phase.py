"""
bear_phase.py — Bear-market phase classifier (bear-market framework Ch 1).

Crypto cycles run through four distinct phases. Each phase favors a
different directional bias for short-term futures setups:

  Phase 1  DISTRIBUTION — smart money exits into strength; retail bids
                          tops; funding elevated; rallies fail.
                          → FAVOR SHORTS, fade longs.

  Phase 2  DECLINE      — lower highs / lower lows confirmed; sentiment
                          rolls over; altcoins bleed harder than BTC.
                          → FAVOR SHORTS strongly; longs counter-trend.

  Phase 3  CAPITULATION — maximum pain; F&G extreme fear; long-term
                          holders accumulate; on-chain signals bottom.
                          → FAVOR LONGS (bounce / mean-reversion);
                            avoid fresh shorts.

  Phase 4  RECOVERY     — higher lows form; structure rebuilds; volume
                          on up days; capital flows back.
                          → FAVOR LONGS; trend continuation.

Public:
- classify_phase(btc_change_24h_pct, fng, btc_dom_pct, vix, regime_label)
  → {phase, bias, label}
- phase_alignment_weight(phase, setup_direction) → ±0.3 score modifier
"""
from __future__ import annotations
from typing import Optional


PHASE_BIAS = {
    "distribution": "Short",
    "decline":      "Short",
    "capitulation": "Long",
    "recovery":     "Long",
    "unknown":      None,
}


def classify_phase(btc_change_24h_pct: Optional[float],
                    fng: Optional[int],
                    btc_dom_pct: Optional[float] = None,
                    vix: Optional[float] = None,
                    hmm_regime: Optional[str] = None) -> dict:
    """
    Rule-based classifier from market context (uses what we already fetch
    in scanner macro_ctx). Returns:
        {phase: str, bias: "Long"|"Short"|None, label: str}

    Logic — same input order of confidence as the framework teaches:
    1. F&G is the loudest sentiment indicator
       - F&G < 20 (extreme fear)            → capitulation
       - F&G < 40 + BTC dropping            → decline
       - F&G > 75 + BTC near highs          → distribution
    2. HMM regime (if available) refines
       - trending_down with mild F&G        → decline
       - trending_up with rising sentiment  → recovery
    3. BTC 24h price action breaks ties

    Returns phase 'unknown' when inputs are missing — caller treats as
    no bias.
    """
    # Defaults
    phase = "unknown"
    label = "insufficient data"

    fng_v = int(fng) if fng is not None else None
    btc_v = float(btc_change_24h_pct) if btc_change_24h_pct is not None else None
    btc_d = float(btc_dom_pct) if btc_dom_pct is not None else None

    # 1. F&G-led primary classification
    if fng_v is not None:
        if fng_v < 20:
            phase = "capitulation"
            label = f"capitulation (F&G {fng_v} extreme fear)"
        elif fng_v < 40:
            # fear zone — could be early decline or late capitulation
            if btc_v is not None and btc_v <= -3:
                phase = "decline"
                label = f"decline (F&G {fng_v} fear + BTC {btc_v:.1f}% 24h)"
            elif btc_v is not None and btc_v >= 2:
                phase = "recovery"
                label = f"recovery (F&G {fng_v} fear + BTC bouncing {btc_v:+.1f}%)"
            else:
                phase = "decline"
                label = f"decline (F&G {fng_v} fear, BTC drifting)"
        elif fng_v > 75:
            phase = "distribution"
            label = f"distribution (F&G {fng_v} greed/euphoria)"
        elif fng_v >= 55:
            # Mild greed
            if btc_v is not None and btc_v >= 3:
                phase = "recovery"
                label = f"recovery (F&G {fng_v} optimistic + BTC {btc_v:+.1f}%)"
            elif btc_v is not None and btc_v <= -3:
                phase = "distribution"
                label = f"distribution (F&G {fng_v} but BTC {btc_v:.1f}% — topping)"
            else:
                phase = "recovery"
                label = f"recovery (F&G {fng_v} optimistic, BTC flat)"
        else:
            # neutral F&G 40-55 — use BTC action
            if btc_v is not None and btc_v <= -2:
                phase = "decline"
                label = f"decline (neutral F&G {fng_v}, BTC {btc_v:.1f}%)"
            elif btc_v is not None and btc_v >= 2:
                phase = "recovery"
                label = f"recovery (neutral F&G {fng_v}, BTC {btc_v:+.1f}%)"
            else:
                phase = "unknown"
                label = f"neutral chop (F&G {fng_v}, no clear direction)"

    # 2. HMM regime refinement — overrides only if it strongly disagrees
    if hmm_regime:
        rl = str(hmm_regime).lower()
        if "trending_down" in rl and phase in ("recovery", "unknown"):
            phase = "decline"
            label += " | HMM: trending_down"
        elif "trending_up" in rl and phase in ("decline", "unknown"):
            phase = "recovery"
            label += " | HMM: trending_up"

    # 3. High BTC dominance (>60%) signals risk-off — slight tilt to decline
    if btc_d is not None and btc_d > 62 and phase == "unknown":
        phase = "decline"
        label = f"decline (BTC.D {btc_d:.1f}% — flight to BTC, alts bleeding)"

    return {
        "phase": phase,
        "bias":  PHASE_BIAS.get(phase),
        "label": label,
    }


def phase_alignment_weight(phase: str, setup_direction: str) -> tuple[float, str]:
    """
    Score modifier when a setup's direction aligns (or fights) the
    current bear-phase bias. Returns (weight, reason).

      Aligned → +0.3 (e.g. Short setup in Distribution phase)
      Against → -0.3 (e.g. Long setup in Decline phase)
      Neutral → 0 (unknown phase or no bias)
    """
    bias = PHASE_BIAS.get(phase)
    if not bias or not setup_direction:
        return 0.0, ""
    sd = setup_direction.strip().lower()
    bd = bias.strip().lower()
    if sd == bd:
        return +0.3, f"bear-phase {phase} favors {bias} → setup aligned (+0.3)"
    return -0.3, f"bear-phase {phase} favors {bias} → setup counter-trend (-0.3)"
