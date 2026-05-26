"""
scanner_criteria.py — Criteria DSL and kill-zone helpers for the setup scanner.

Provides:
- CRITERIA_DEFAULTS: the default on/off map for each scoring criterion.
- _disabled_criteria_block(): builds the prompt fragment listing disabled checks.
- _is_in_kill_zone() / _annotate_kill_zone(): ICT kill-zone time helpers.
"""

import datetime

# ── Criteria defaults ──────────────────────────────────────────────────────────
# Each key maps to a scoring check. When False the stage-2 gate skips the hard
# filter AND the prompt tells Claude to ignore that criterion.

CRITERIA_DEFAULTS: dict = {
    "rsi":        True,   # Reject overextended RSI (>78 long / <22 short)
    "macd":       True,   # MACD alignment counts as a 4H signal
    "ema_stack":  True,   # EMA stack alignment counts as a 4H signal
    "adx":        True,   # Reject ADX < 15 (flat/choppy)
    "sr_anchor":  True,   # Require ≥2 S/R levels + entry within 4×ATR
    "wavetrend":  True,   # VMC Cipher / WaveTrend signal in scoring
    "volume":     True,   # Volume confirmation in scoring
    "funding":    True,   # Funding rate penalty (-1/-2 score points)
    "fear_greed": True,   # Fear & Greed ±0.5 adjustment
    "atr_sl":     True,   # Cap score ≤ 6 when SL < 1×ATR from entry
    "rr_minimum": True,   # Cap score ≤ 6 when R:R < 2:1
}

_CRITERIA_DISABLED_LABELS: dict = {
    "rsi":        "RSI overbought/oversold — do NOT penalise or filter on RSI extremes",
    "macd":       "MACD alignment — ignore MACD direction entirely",
    "ema_stack":  "EMA stack — ignore EMA alignment entirely",
    "adx":        "ADX trend strength — do NOT require or factor ADX",
    "sr_anchor":  "S/R anchor — entry does NOT need to be near a named level; score purely on momentum/pattern",
    "wavetrend":  "WaveTrend/VMC Cipher — ignore WT signal entirely",
    "volume":     "Volume confirmation — do NOT require or reward volume",
    "funding":    "Funding rate — do NOT apply any funding rate penalties",
    "fear_greed": "Fear & Greed — do NOT apply F&G score adjustments",
    "atr_sl":     "ATR SL floor — do NOT cap score if SL is tight (inside 1×ATR)",
    "rr_minimum": "R:R minimum — do NOT cap score for low R:R; score the setup quality regardless",
}


def _disabled_criteria_block(criteria: dict) -> str:
    """Return a prompt section listing which checks Claude must skip."""
    disabled = [
        f"  - {_CRITERIA_DISABLED_LABELS[k]}"
        for k in _CRITERIA_DISABLED_LABELS
        if not criteria.get(k, True)
    ]
    if not disabled:
        return ""
    return (
        "DISABLED SCORING CRITERIA (user has turned these OFF — do NOT apply them, "
        "do NOT mention them in your rationale):\n" + "\n".join(disabled)
    )


# ── Kill zone helpers ──────────────────────────────────────────────────────────

def _is_in_kill_zone(utc_hour: int = None) -> bool:
    """
    Return True if the given UTC hour falls within an institutional kill zone.
    London: 07:00–09:59 UTC  |  NY AM: 12:00–14:59 UTC
    Pass utc_hour explicitly for testing; defaults to current UTC time.
    """
    h = utc_hour if utc_hour is not None else datetime.datetime.utcnow().hour
    return (7 <= h < 10) or (12 <= h < 15)


# Extended PO3 kill-zone map (Power-of-3 / ICT framework). All windows in
# UTC. Roughly matches the NY-session institutional rhythm:
#   London     07:00–10:00 UTC (= 02:00–05:00 NY)
#   NY AM      12:00–16:00 UTC (= 07:00–11:00 NY)
#   Silver Bullet 13:30–14:30 UTC (= 08:30–09:30 NY, peak NY AM)
#   NY PM      18:30–21:00 UTC (= 13:30–16:00 NY)
#   Dead hour  16:30–17:30 UTC (= 11:30–12:30 NY, lunch chop)
# Score modifier applied per zone — institutional volume / volatility is
# empirically higher inside these windows, so setups appearing here have
# slightly better follow-through statistics in crypto since ETF launch.
KILL_ZONE_MULTIPLIERS = {
    "silver_bullet":  +0.3,   # peak NY AM, highest probability window
    "ny_am":          +0.2,
    "london":         +0.2,
    "ny_pm":          +0.15,
    "dead_hour":      -0.2,   # NY lunch chop — avoid
}


def _classify_session(utc_hour: int = None, utc_minute: int = None) -> str:
    """
    Returns the current session bucket name. Used by the kill-zone modifier.
    Silver Bullet window is checked at minute-precision; others at hour.
    """
    now = datetime.datetime.utcnow()
    h = utc_hour if utc_hour is not None else now.hour
    m = utc_minute if utc_minute is not None else now.minute
    # Silver Bullet first (most specific): 13:30–14:30 UTC
    if (h == 13 and m >= 30) or (h == 14 and m < 30):
        return "silver_bullet"
    if 7 <= h < 10:
        return "london"
    if 12 <= h < 16:
        return "ny_am"
    if (h == 16 and m >= 30) or (h == 17 and m < 30):
        return "dead_hour"
    if (h == 18 and m >= 30) or (19 <= h < 21):
        return "ny_pm"
    return "off_hours"


def _apply_kill_zone_modifier(score: float, utc_hour: int = None,
                                utc_minute: int = None
                                ) -> tuple[float, list[str]]:
    """
    Apply the PO3 kill-zone score modifier. Boosts setups appearing during
    institutional windows; lightly penalises NY-lunch chop. Returns
    (adjusted_score, warnings). Does NOT interact with the personal bad-hour
    cap — that runs separately and takes precedence (the cap clamps the
    final value, so this modifier can boost into a hard ceiling but never
    past one). Applied in scanner Stage 3 before the personal bad-hour cap.
    """
    warnings: list[str] = []
    session = _classify_session(utc_hour, utc_minute)
    mult = KILL_ZONE_MULTIPLIERS.get(session, 0.0)
    if mult == 0.0:
        return score, warnings
    new_score = max(0.0, min(10.0, score + mult))
    warnings.append(
        f"PO3 session '{session}' → score {mult:+.1f} "
        f"({score:.2f} → {new_score:.2f})"
    )
    return new_score, warnings


# Hours where THIS trader's own data shows net losses despite decent WR.
# Derived from 90d analytics: UTC 13 (-$52), 15 (-$145), 19 (-$143), 20 (-$26).
# Combined = -$365 (60% of NY-session total loss). Entries opened in these
# hours get score-capped so the AI can't surface fresh setups during the
# trader's historically toxic windows.
# Operator-behavior caps (personal bad-hour + reversal archetype) were
# removed from scanner scoring 2026-05-25 — they were operator-history
# priors, not market facts. The constants and helper functions were kept
# for one week as dead refs in case a legacy report imported them; none
# did. Fully removed 2026-05-26.
#
# For audit: the prior rules were
#   PERSONAL_BAD_HOURS_UTC = {13, 15, 19, 20}  → cap score to 5.5
#   REVERSAL_CAP = 5.5 unless ≥3 same-side confluence signals (archetype=reversal)


def _annotate_kill_zone(result: dict, utc_hour: int = None) -> dict:
    """
    Append warnings to the urgency field:
      - '⚠ Outside kill zone' when outside institutional London/NY-AM windows

    Personal bad-hour annotation removed 2026-05-25 — operator-behavior
    priors are no longer applied to scanner output.
    """
    notes: list[str] = []
    if not _is_in_kill_zone(utc_hour):
        notes.append("⚠ Outside kill zone")
    if not notes:
        return result
    warning = " ".join(notes)
    existing = result.get("urgency") or ""
    result["urgency"] = (existing + " " + warning).strip() if existing else warning
    return result
