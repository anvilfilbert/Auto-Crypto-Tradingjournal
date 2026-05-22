"""
setup_classifier.py — Two complementary setup-archetype classifiers.

(A) classify_ai     — Claude-based, sends the full 4H + 1H technical
                      picture to the call analyzer model and asks for a
                      JSON classification + reasoning. Expensive (one
                      Sonnet call per trade) but defensible — gives us
                      a ground-truth label for cross-validation.

(B+C+D) classify_rules — Improved rule-based classifier. Three changes vs
                          scanner_prompts._detect_archetype:
  - B (multi-signal voting): each archetype gets a 0-N score from several
    indicators rather than a single strict gate. Highest wins, with a
    minimum confidence floor.
  - C (WT lookback): inspects the last 3 4H bars for a WaveTrend cross,
    not just the open candle. Fixes the "0 reversals because WT signal
    only fires on exact crossover candle" blindness.
  - D (taxonomy): 5 archetypes (breakout, reversal, continuation,
    range_bound, low_conviction) so 'continuation' stops being a
    dump-bucket for everything-else.

Both functions return:
  {archetype: str, confidence: float (0..1), reasoning: str, scores: dict}

Designed so the live scanner can call classify_rules() for free on
every cycle, while batch backfills can call classify_ai() once and we
can periodically compare them to keep the rule-based version honest.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

import chart_candles
import chart_indicators


# 4-archetype taxonomy after the 22-05-2026 calibration. range_bound was
# dropped because Haiku ground-truth used it 0/111 — every range-bound
# trade collapsed into low_conviction in practice. Confidence floor raised
# 3 → 4 because the AI ground truth showed 11/27 rule-continuations
# (~40%) were really low_conviction setups that the rule classifier was
# letting through with a 3-point score.
ARCHETYPES = ("breakout", "reversal", "continuation", "low_conviction")
_MIN_CONFIDENCE_FLOOR = 4   # winner must reach this score, else low_conviction


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_ms(iso: str) -> Optional[int]:
    try:
        s = (iso or "").strip()
        if not s:
            return None
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00")[:19])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _recent_wt_cross(df, direction: str, lookback: int = 3) -> Optional[str]:
    """
    Look at the last `lookback` 4H bars for any WaveTrend crossover that
    would imply a reversal in the given direction. Returns a label
    ('gold_buy', 'buy', 'sell') or None.

    The original classifier checked only the last bar — most entries
    don't land exactly on a cross candle, so reversal was effectively
    unreachable.
    """
    try:
        wt = chart_indicators.compute_wavetrend(df)
        if wt is None or wt.empty:
            return None
        tail = wt.tail(lookback)
        is_long = (direction or "").lower() == "long"

        if is_long:
            # Bullish reversal — buy/gold_buy in the last N bars
            for sig in tail["signal"]:
                if sig in ("gold_buy", "buy"):
                    return sig
        else:
            for sig in tail["signal"]:
                if sig == "sell":
                    return sig
        return None
    except Exception:
        return None


# ── (B+C+D) Improved rule-based classifier ────────────────────────────────────

def classify_rules(symbol: str, direction: str, open_time: str,
                    candles_df=None) -> dict:
    """
    Multi-signal voting classifier with WT lookback. Free to run.

    `candles_df` optional — if supplied (e.g. by the live scanner that
    already has the data), the classifier reuses it. Otherwise fetches
    fresh 4H candles ending at open_time.
    """
    ms = _to_ms(open_time)
    if ms is None and candles_df is None:
        return _result("low_conviction", 0.0, "no open_time and no candles supplied", {})

    if candles_df is None:
        candles_df = chart_candles.get_candles_at_time(symbol, "4H", ms, limit=200)
    if candles_df is None or candles_df.empty:
        return _result("low_conviction", 0.0, "no 4H candles available", {})

    inds = chart_indicators.compute_all_indicators(candles_df)
    rsi = ((inds.get("rsi")     or {}).get("value", 50)) or 50
    adx = ((inds.get("adx")     or {}).get("value", 20)) or 20
    vol = ((inds.get("volume")  or {}).get("ratio", 1.0)) or 1.0
    ema_align = (inds.get("ema") or {}).get("alignment", "") or ""
    wt_inds   = inds.get("wavetrend") or {}
    wt_zone   = wt_inds.get("zone")
    is_long   = (direction or "").lower() == "long"

    # WaveTrend lookback over recent bars instead of the single open bar
    wt_recent = _recent_wt_cross(candles_df, direction, lookback=3)

    # Score each archetype 0..N+
    scores = {a: 0 for a in ARCHETYPES}

    # --- BREAKOUT ---
    if vol > 1.5:                                       scores["breakout"] += 1
    if vol > 2.5:                                       scores["breakout"] += 1   # extra weight on big spike
    if adx > 20:                                        scores["breakout"] += 1
    if "fully bullish"  in ema_align and is_long:       scores["breakout"] += 1
    if "fully bearish"  in ema_align and not is_long:   scores["breakout"] += 1
    if is_long     and 55 <= rsi <= 78:                 scores["breakout"] += 1
    if not is_long and 22 <= rsi <= 45:                 scores["breakout"] += 1

    # --- REVERSAL ---
    # RSI gates widened from <=35/>=65 to <=40/>=60 — the strict thresholds
    # missed 10 of 26 real reversals per AI ground truth (they ended up in
    # low_conviction because nothing else hit floor either).
    if is_long     and rsi <= 40:                       scores["reversal"] += 2
    if not is_long and rsi >= 60:                       scores["reversal"] += 2
    if wt_recent is not None:                           scores["reversal"] += 2
    if wt_zone == ("oversold" if is_long else "overbought"):
                                                        scores["reversal"] += 1
    # Counter-trend EMA stack adds confluence to reversal thesis
    if is_long     and "fully bearish" in ema_align:    scores["reversal"] += 1
    if not is_long and "fully bullish" in ema_align:    scores["reversal"] += 1

    # --- CONTINUATION (trend pullback) ---
    if is_long     and "fully bullish" in ema_align:    scores["continuation"] += 2
    if not is_long and "fully bearish" in ema_align:    scores["continuation"] += 2
    if is_long     and 40 <= rsi <= 60:                 scores["continuation"] += 1
    if not is_long and 40 <= rsi <= 60:                 scores["continuation"] += 1
    if 15 <= adx <= 35:                                 scores["continuation"] += 1
    if 0.7 <= vol <= 1.5:                               scores["continuation"] += 1
    # range_bound dropped — AI never picked it (0/111). Trades with low ADX
    # and mixed EMAs now correctly fall to low_conviction via the floor.

    # Pick the winner
    best = max(scores, key=scores.get)
    best_score = scores[best]
    runner_up = sorted(scores.values(), reverse=True)[1]
    margin = best_score - runner_up
    # Below threshold → low_conviction
    if best_score < _MIN_CONFIDENCE_FLOOR or margin == 0:
        return _result(
            "low_conviction",
            best_score / 6.0,   # confidence still meaningful as raw evidence
            f"top score {best_score}/{best!r} below floor {_MIN_CONFIDENCE_FLOOR} or tied",
            scores,
        )
    confidence = min(1.0, best_score / 5.0)
    reasoning = (f"RSI={rsi:.0f} ADX={adx:.0f} vol={vol:.2f} EMA={ema_align or 'n/a'} "
                 f"WT_recent={wt_recent or 'none'} → {best} ({best_score}/{sum(scores.values())})")
    return _result(best, confidence, reasoning, scores)


def _result(archetype: str, confidence: float, reasoning: str, scores: dict) -> dict:
    return {
        "archetype":  archetype,
        "confidence": round(confidence, 2),
        "reasoning":  reasoning,
        "scores":     scores,
    }


# ── (A) AI-based classifier ───────────────────────────────────────────────────

_AI_PROMPT_TEMPLATE = """You are classifying the archetype of a single crypto futures setup so that the trader can analyse historical performance by category.

CLASSIFY INTO EXACTLY ONE OF THESE ARCHETYPES:
- breakout       : price escaping a consolidation/range with volume + momentum
- reversal       : counter-trend bounce/fade at an exhaustion zone (RSI extreme, structural level, divergence)
- continuation   : established trend, looking for a pullback or trend-follow entry
- low_conviction : mixed signals, sideways market, or no clear pattern — would normally skip

SETUP:
Symbol:    {symbol}
Direction: {direction}
Entry time (UTC): {open_time}

TECHNICAL PICTURE AT ENTRY (reconstructed from candles):
{prompt_text_4h}
{prompt_text_1h}

Respond with ONLY a JSON object (no markdown, no code fences):
{{"archetype":"breakout|reversal|continuation|low_conviction",
  "confidence":0.0-1.0,
  "reasoning":"one sentence citing the specific technicals that drove the call"}}"""


def classify_ai(symbol: str, direction: str, open_time: str,
                 prompt_text_4h: str = "", prompt_text_1h: str = "") -> dict:
    """
    Ask Claude to classify this setup. Used for historical backfill and
    cross-validation against the rule-based classifier. Returns the same
    shape as classify_rules() with scores={} (AI doesn't expose per-class
    score components — just a confidence).
    """
    # Imported lazily so the rule-based path stays free of AI deps.
    from ai_client import send as ai_send
    from constants import FAST_MODEL
    from helpers import strip_fence

    prompt = _AI_PROMPT_TEMPLATE.format(
        symbol=symbol,
        direction=direction,
        open_time=open_time,
        prompt_text_4h=prompt_text_4h or "(no 4H prompt text)",
        prompt_text_1h=prompt_text_1h or "(no 1H prompt text)",
    )
    try:
        raw, _cached = ai_send(
            "setup_classifier", FAST_MODEL,    # Haiku is enough for a 5-way pick
            [{"role": "user", "content": prompt}],
            max_tokens=256,
        )
        data = json.loads(strip_fence(raw.strip()))
        arch = (data.get("archetype") or "").strip().lower()
        if arch not in ARCHETYPES:
            arch = "low_conviction"
        conf = float(data.get("confidence") or 0)
        return _result(arch, min(1.0, max(0.0, conf)),
                       data.get("reasoning") or "", {})
    except Exception as e:
        return _result("low_conviction", 0.0,
                       f"AI classification failed: {str(e)[:80]}", {})
