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


# Hours where THIS trader's own data shows net losses despite decent WR.
# Derived from 90d analytics: UTC 13 (-$52), 15 (-$145), 19 (-$143), 20 (-$26).
# Combined = -$365 (60% of NY-session total loss). Entries opened in these
# hours get score-capped so the AI can't surface fresh setups during the
# trader's historically toxic windows.
PERSONAL_BAD_HOURS_UTC: set[int] = {13, 15, 19, 20}
PERSONAL_BAD_HOUR_CAP  = 5.5   # below SCANNER_MIN_SCORE=7 → effectively blocked


def _is_in_personal_bad_hour(utc_hour: int = None) -> bool:
    """
    Per-trader rule: hours 13, 15, 19, 20 UTC have negative expectancy
    in this trader's history. Not the same as institutional kill zones —
    those flag *all* trades; this flags the trader's specific dead spots.
    """
    h = utc_hour if utc_hour is not None else datetime.datetime.utcnow().hour
    return h in PERSONAL_BAD_HOURS_UTC


# Reversal-archetype trades produced -$375 across 26 trades (54% WR,
# avg MFE only 1.76% before reversing). Cap scores when the AI sees a
# reversal setup unless there's strong confluence to justify it.
REVERSAL_CAP                  = 5.5
REVERSAL_CONFLUENCE_BYPASS    = 3   # >=3 confluence signals lifts the cap


def _apply_reversal_cap(score: float, archetype: str,
                         bullish_signals: int = 0, bearish_signals: int = 0
                         ) -> tuple[float, list[str]]:
    """
    Cap reversal-archetype scores at 5.5 unless the confluence count is
    strong (>=3 same-side signals). Reversals in this trader's history
    only paid off when multiple signals lined up — pure RSI-extreme or
    WT-cross plays bled money. Returns (capped_score, warnings).
    """
    warnings: list[str] = []
    if archetype != "reversal" or score <= REVERSAL_CAP:
        return score, warnings
    same_side_signals = max(bullish_signals or 0, bearish_signals or 0)
    if same_side_signals >= REVERSAL_CONFLUENCE_BYPASS:
        return score, warnings
    warnings.append(
        f"Reversal archetype with only {same_side_signals} confluence signals "
        f"(needs {REVERSAL_CONFLUENCE_BYPASS}+) — score capped at {REVERSAL_CAP}. "
        f"Historical reversals 26 trades 54% WR -$375 total."
    )
    return min(score, REVERSAL_CAP), warnings


def _apply_personal_bad_hour_cap(score: float, utc_hour: int = None
                                  ) -> tuple[float, list[str]]:
    """Cap score during the trader's known bad hours so the scanner can
    not surface a fresh entry inside a -$285 leak window. Returns
    (capped_score, warnings)."""
    warnings: list[str] = []
    if _is_in_personal_bad_hour(utc_hour) and score > PERSONAL_BAD_HOUR_CAP:
        h = utc_hour if utc_hour is not None else datetime.datetime.utcnow().hour
        warnings.append(
            f"UTC hour {h:02d} is in personal bad-hour set "
            f"{sorted(PERSONAL_BAD_HOURS_UTC)} (90d P&L -$365) — score capped at {PERSONAL_BAD_HOUR_CAP}"
        )
        score = min(score, PERSONAL_BAD_HOUR_CAP)
    return score, warnings


def _annotate_kill_zone(result: dict, utc_hour: int = None) -> dict:
    """
    Append warnings to the urgency field:
      - '⚠ Outside kill zone' when outside institutional London/NY-AM windows
      - '⚠ Personal bad-hour window' when in the trader's known dead spots
    """
    notes: list[str] = []
    if not _is_in_kill_zone(utc_hour):
        notes.append("⚠ Outside kill zone")
    if _is_in_personal_bad_hour(utc_hour):
        notes.append("⛔ Personal bad-hour window")
    if not notes:
        return result
    warning = " ".join(notes)
    existing = result.get("urgency") or ""
    result["urgency"] = (existing + " " + warning).strip() if existing else warning
    return result
