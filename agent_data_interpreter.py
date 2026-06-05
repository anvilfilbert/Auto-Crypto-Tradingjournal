"""
agent_data_interpreter.py — DataInterpreter agent.

Pure function — no AI, no DB, no network. Transforms raw candles from
CollectorResult into structured technical signals for downstream agents.
"""
import chart_indicators
import chart_sr
import chart_context as cc

from agent_types import InterpreterInput, InterpreterResult

_ANALYST_INSTRUCTIONS = """You are a senior technical analyst specialising in crypto futures (USDT-M perpetuals, 10x leverage).

You receive pre-computed indicators. Do NOT restate raw numbers — synthesise them into trading insight.

## MANDATORY OUTPUT (exactly these 6 sections, no additions):

**TREND** (1 sentence): EMA stack + ADX direction and strength.
**MOMENTUM** (1 sentence): RSI + MACD + WaveTrend confluence verdict.
**STRUCTURE** (1 sentence): Nearest key S/R level and its significance to the setup.
**SIGNAL COUNT** (format: X/12 aligned): Count signals agreeing with the primary bias.
**BIAS** (one of: STRONG LONG | LONG | NEUTRAL | SHORT | STRONG SHORT)
**CONFIDENCE** (one of: HIGH | MED | LOW)

## CONFIDENCE RULES:
- HIGH: ≥8/12 signals aligned, ADX > 20, EMA stack clean, within kill zone
- MED: 6–7/12 aligned OR ADX 15–20 OR outside kill zone
- LOW: <6/12 aligned OR ADX < 15 OR VIX > 30 flagged in context OR HMM=ranging with low conviction

## BIAS RULES:
- STRONG: ≥8 aligned, ADX > 25, clear EMA stack
- LONG/SHORT: 6–7 aligned
- NEUTRAL: <6 aligned or signals conflicting
"""

# Public alias for use as system= parameter in downstream AI calls
ANALYST_INSTRUCTIONS = _ANALYST_INSTRUCTIONS


def run(inp: InterpreterInput) -> InterpreterResult:
    collected = inp["collected"]
    symbol    = collected["symbol"]
    candles   = collected["candles"]

    by_tf = {}
    for tf, df in candles.items():
        if df is None or df.empty:
            by_tf[tf] = {}
            continue
        try:
            by_tf[tf] = chart_indicators.compute_all_indicators(df)
        except Exception:
            by_tf[tf] = {}

    # S/R from primary timeframe (prefer 4H)
    _4h = candles.get("4H")
    if _4h is not None and not _4h.empty:
        primary_df = _4h
    else:
        primary_df = next(
            (df for df in candles.values() if df is not None and not df.empty), None
        )
    sr_levels = []
    if primary_df is not None and not primary_df.empty:
        try:
            sr_levels = chart_sr.detect_support_resistance(primary_df)
        except Exception:
            pass

    # confluence_score expects ctx in format {tf: {"indicators": {...}, "ok": True}}
    # Only compute confluence when at least one timeframe has real indicator data.
    conf_ctx = {tf: {"indicators": data, "ok": bool(data)} for tf, data in by_tf.items()}
    conf = {}
    if any(v["ok"] for v in conf_ctx.values()):
        try:
            conf = cc.confluence_score(symbol, list(candles.keys()), ctx=conf_ctx)
        except Exception:
            pass

    return InterpreterResult(
        symbol           = symbol,
        by_timeframe     = by_tf,
        sr_levels        = sr_levels,
        confluence_score = conf,
        trend_direction  = _trend(by_tf),
        momentum_bias    = _momentum(conf),
        prompt_text      = _prompt_text(symbol, by_tf, conf, sr_levels),
    )


def _trend(by_tf: dict) -> str:
    bullish = bearish = 0
    for data in by_tf.values():
        ema = data.get("ema", {})
        bias = str(ema.get("bias", "") or ema.get("trend", "") or ema.get("alignment", "")).lower()
        if "bullish" in bias:
            bullish += 1
        elif "bearish" in bias:
            bearish += 1
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "neutral"


def _momentum(conf: dict) -> str:
    label = conf.get("label", "").lower()
    if "strong" in label:
        return "strong"
    if label in ("bullish", "bearish"):
        return "moderate"
    if "neutral" in label:
        return "weak"
    return "conflicted"


def _prompt_text(symbol: str, by_tf: dict, conf: dict, sr: list) -> str:
    parts = [f"[{symbol}]"]
    # Discover current price for context + S/R proximity filtering. Without
    # this Sonnet was being fed bare support levels (e.g. "support@4.902")
    # with no anchor to know they were 15% below live — it anchored entries
    # to stale supports and Path 3 rejected every fill (2026-05-27 audit).
    current_price = None
    for tf, data in by_tf.items():
        if data:
            cp = (data.get("ema") or {}).get("current_price")
            if cp:
                current_price = float(cp); break

    for tf, data in by_tf.items():
        if not data:
            continue
        rsi_v  = data.get("rsi",  {}).get("value", "?")
        ema_b  = (data.get("ema",  {}).get("bias")
                  or data.get("ema", {}).get("trend")
                  or data.get("ema", {}).get("alignment", "?"))
        adx_v  = data.get("adx",  {}).get("value", "?")
        macd_s = data.get("macd", {}).get("signal", "?")
        parts.append(f"{tf}: RSI {rsi_v} | EMA {ema_b} | ADX {adx_v} | MACD {macd_s}")
    if conf:
        parts.append(f"Confluence {conf.get('label','?')} ({conf.get('score',0):.1f}/{conf.get('max',0):.1f})")

    # Always surface current price so downstream can sanity-check S/R levels.
    if current_price:
        parts.append(f"PRICE: {current_price:.6g}")

    if sr:
        if current_price:
            # Filter S/R to within ±15% of current — anything further is a
            # stale chart level the market has left behind and shouldn't be
            # used as an entry anchor. Then pick balanced near-supports +
            # near-resistances so Sonnet has both entry and TP candidates.
            close = [s for s in sr
                     if s.get("price") and
                     abs(float(s["price"]) - current_price) / current_price <= 0.15]
            sups = sorted(
                [s for s in close if s.get("type") == "support"
                 and float(s["price"]) < current_price],
                key=lambda s: current_price - float(s["price"]))[:2]
            ress = sorted(
                [s for s in close if s.get("type") == "resistance"
                 and float(s["price"]) > current_price],
                key=lambda s: float(s["price"]) - current_price)[:2]
            near = sups + ress
            if not near:
                # No tradeable S/R inside ±15% — tell Sonnet explicitly so
                # the entry-validity rule in trade_prep triggers a wait/skip.
                parts.append("S/R: none within ±15% of current — wait for retrace required")
            else:
                sr_str = " ".join(f"{s.get('type','?')}@{s.get('price','?')}" for s in near)
                parts.append(f"S/R: {sr_str}")
        else:
            near = sr[:3]
            sr_str = " ".join(f"{s.get('type','?')}@{s.get('price','?')}" for s in near)
            parts.append(f"S/R: {sr_str}")
    return " | ".join(parts)[:500]
