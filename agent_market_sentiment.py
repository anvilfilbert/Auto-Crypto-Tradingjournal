"""
agent_market_sentiment.py — MarketSentimentAnalyzer agent.

Pure function — reads only from CollectorResult. No AI, no DB, no network.
Interprets funding, L/S ratio, Fear & Greed, and Grok into a structured
sentiment verdict with a contra_signal flag.
"""
import os

from agent_types import SentimentInput, SentimentResult


def run(inp: SentimentInput) -> SentimentResult:
    direction = inp["direction"]
    c         = inp["collected"]

    fg     = c.get("fear_greed",   {})
    fr     = c.get("funding_rate", {})
    ls     = c.get("long_short",   {})
    grok   = c.get("grok",         {})

    score       = 5.0
    key_factors = []

    # ── Fear & Greed ────────────────────────────────────────────────────────
    # Two fixes shipped 2026-06-01:
    # 1. Logic inversion: the Greed branch previously penalised Shorts (line
    #    was `score -= 0.5` for Short in Greed zone). Contrarian-bearish at
    #    Greed should BOOST Shorts, not penalise them.
    # 2. Magnitude asymmetry: Long and Short used different magnitudes (Long
    #    ±1.5/±0.5, Short ±1.0/±0.3). Now mirror-symmetric using a signed delta.
    # F&G pause switch: FUTURES_AI_FNG_PAUSED=1 (default ON) bypasses the F&G
    # block entirely. Per operator decision 2026-06-01: F&G is no longer
    # trusted as scoring input; market structure replaces it. Set to 0 to
    # re-enable.
    fg_val = fg.get("value")
    fg_cls = fg.get("classification", "")
    fng_paused = os.environ.get("FUTURES_AI_FNG_PAUSED", "1").strip() == "1"
    if fg_val is not None and not fng_paused:
        key_factors.append(f"F&G {fg_val} — {fg_cls}")
        # Signed delta: positive for Long in fear zones, negative for Long in
        # greed zones; Short is the mirror image.
        if fg_val <= 25:        # Extreme Fear — contrarian bullish
            delta = 1.5 if direction == "Long" else -1.5
        elif fg_val <= 45:      # Fear
            delta = 0.5 if direction == "Long" else -0.5
        elif fg_val >= 75:      # Extreme Greed — contrarian bearish
            delta = -1.5 if direction == "Long" else 1.5
        elif fg_val >= 55:      # Greed
            delta = -0.5 if direction == "Long" else 0.5
        else:                    # Neutral 45-55
            delta = 0
        score += delta
    elif fg_val is not None and fng_paused:
        # Still surface F&G in key_factors for telemetry/UI, but as info only.
        key_factors.append(f"F&G {fg_val} — {fg_cls} (paused: not scored)")

    # ── Funding rate ────────────────────────────────────────────────────────
    fr_rate = fr.get("rate")
    funding_bias = "neutral"
    if fr_rate is not None:
        fr_pct = fr.get("rate_pct", fr_rate * 100)
        if fr_rate > 0:
            funding_bias = "longs_paying"
            key_factors.append(f"Funding +{fr_pct:.4f}% — longs paying")
            if direction == "Long":
                score -= 0.5 if not fr.get("high") else 1.5
        elif fr_rate < 0:
            funding_bias = "shorts_paying"
            key_factors.append(f"Funding {fr_pct:.4f}% — shorts paying")
            if direction == "Short":
                score -= 0.5 if not fr.get("high") else 1.5

    # ── Long/Short ratio ────────────────────────────────────────────────────
    long_pct      = ls.get("long_pct", 50)
    crowd_pos     = "balanced"
    contra_signal = False

    if ls.get("ok"):
        if long_pct > 65:
            crowd_pos = "majority_long"
            key_factors.append(f"L/S ratio: {long_pct}% long — crowded")
            if direction == "Long":
                contra_signal = True
                score -= 1.0
            else:
                score += 0.5   # contrarian short
        elif long_pct < 35:
            crowd_pos = "majority_short"
            key_factors.append(f"L/S ratio: {long_pct}% long — crowded short")
            if direction == "Short":
                contra_signal = True
                score -= 1.0
            else:
                score += 0.5   # contrarian long

    # ── Grok social ─────────────────────────────────────────────────────────
    grok_text = grok.get("text", "")
    if grok_text:
        weight = grok.get("weight", 0.4)
        lower  = grok_text.lower()
        if any(w in lower for w in ("bearish", "dump", "fud", "sell", "⚠")):
            score -= 1.0 * weight if direction == "Long" else -0.5 * weight
        elif any(w in lower for w in ("bullish", "pump", "buy", "accumul")):
            score += 0.5 * weight if direction == "Long" else -0.5 * weight

    score = round(max(0.0, min(10.0, score)), 1)
    bias  = "bullish" if score > 6 else "bearish" if score < 4 else "neutral"

    lines = [f"Sentiment {score}/10 ({bias})"]
    if key_factors:
        lines.append("Factors: " + " | ".join(key_factors))
    if grok_text:
        lines.append(f"Social: {grok_text[:120]}")
    prompt_text = "\n".join(lines)

    return SentimentResult(
        macro_bias      = bias,
        sentiment_score = score,
        funding_bias    = funding_bias,
        crowd_position  = crowd_pos,
        contra_signal   = contra_signal,
        key_factors     = key_factors,
        grok_summary    = grok_text,
        prompt_text     = prompt_text,
    )
