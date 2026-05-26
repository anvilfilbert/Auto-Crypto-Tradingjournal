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

    # 1. F&G-led primary classification — but BTC price action must confirm.
    # F&G is a sentiment lag indicator; we require BTC ≥ ±1% 24h to confirm
    # any directional phase. Without confirmation, return 'unknown' so no
    # directional bias is applied (per 2026-05-25 audit — flat BTC with
    # fearful F&G is "sentiment lag", not active decline).
    if fng_v is not None:
        if fng_v < 20:
            # Extreme fear — capitulation is sentiment-driven and price often
            # lags; we accept this without BTC confirmation.
            phase = "capitulation"
            label = f"capitulation (F&G {fng_v} extreme fear)"
        elif fng_v < 40:
            # Fear zone — REQUIRE BTC confirmation to call decline/recovery
            if btc_v is not None and btc_v <= -1:
                phase = "decline"
                label = f"decline (F&G {fng_v} fear + BTC {btc_v:+.1f}% 24h)"
            elif btc_v is not None and btc_v >= 2:
                phase = "recovery"
                label = f"recovery (F&G {fng_v} fear + BTC bouncing {btc_v:+.1f}%)"
            else:
                phase = "unknown"
                btc_str = f"BTC {btc_v:+.1f}%" if btc_v is not None else "BTC unknown"
                label = f"sentiment-lag (F&G {fng_v} fear but {btc_str} — no confirming move)"
        elif fng_v > 75:
            # Extreme greed — distribution is sentiment-driven; accept.
            phase = "distribution"
            label = f"distribution (F&G {fng_v} greed/euphoria)"
        elif fng_v >= 55:
            # Mild greed — REQUIRE BTC confirmation
            if btc_v is not None and btc_v >= 1:
                phase = "recovery"
                label = f"recovery (F&G {fng_v} optimistic + BTC {btc_v:+.1f}%)"
            elif btc_v is not None and btc_v <= -3:
                phase = "distribution"
                label = f"distribution (F&G {fng_v} but BTC {btc_v:+.1f}% — topping)"
            else:
                phase = "unknown"
                btc_str = f"BTC {btc_v:+.1f}%" if btc_v is not None else "BTC unknown"
                label = f"sentiment-lag (F&G {fng_v} optimistic but {btc_str} — no confirming move)"
        else:
            # Neutral F&G 40-55 — use BTC action (unchanged: BTC already required)
            if btc_v is not None and btc_v <= -2:
                phase = "decline"
                label = f"decline (neutral F&G {fng_v}, BTC {btc_v:+.1f}%)"
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

      Aligned → +0.15 (e.g. Short setup in Distribution phase)
      Against → -0.15 (e.g. Long setup in Decline phase)
      Neutral → 0 (unknown phase or no bias)

    Magnitude lowered from ±0.3 to ±0.15 on 2026-05-26 — F&G is a sentiment
    indicator and is acknowledged as marginal vs structural macro
    (BTC trend, VIX, BTC dominance). It should nudge scoring, not dominate
    the decision. The HMM gate sits at ±0.2 — bear_phase is now slightly
    softer than HMM, which matches their relative signal quality.
    """
    bias = PHASE_BIAS.get(phase)
    if not bias or not setup_direction:
        return 0.0, ""
    sd = setup_direction.strip().lower()
    bd = bias.strip().lower()
    if sd == bd:
        return +0.15, f"bear-phase {phase} favors {bias} → setup aligned (+0.15)"
    return -0.15, f"bear-phase {phase} favors {bias} → setup counter-trend (-0.15)"
