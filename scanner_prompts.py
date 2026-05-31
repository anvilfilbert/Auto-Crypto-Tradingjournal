"""
scanner_prompts.py — Prompt builders for the setup scanner.

Provides:
- _build_prompt(): full per-symbol prompt for Sonnet scoring.
- _build_shared_prefix(): legacy combined prefix (stable + market context).
- _build_scanner_stable(): cacheable stable prefix (rulebook + scoring scale).
- _build_batch_prompt(): single prompt for all top-N finalists.
- _quick_score(): Haiku pass-1 cheap pre-filter.
"""

import hashlib
import json
import logging
import time as _time

from prompt_fragments import SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES, DRAW_ON_LIQUIDITY_RULES
from constants import MODEL, FAST_MODEL, SCANNER_MIN_SCORE, PROMPT_CACHE_MIN_CHARS
from ai_client import send as ai_send
from helpers import strip_fence, build_cached_messages
from scanner_criteria import CRITERIA_DEFAULTS, _disabled_criteria_block

logger = logging.getLogger(__name__)

# ── scanner_quick result cache (#1 cost optimisation) ───────────────────────
# Scanner runs every 30 min but 4H candles only close every 4h → 87% of
# consecutive scans have IDENTICAL OHLCV data for each symbol, so the Haiku
# quick-score returns the same answer. Hash the deterministic inputs (candle
# prompt_text + macro context + symbol/direction/threshold), cache the parsed
# return value with a TTL just under the scanner cadence. Only successful
# Haiku returns are cached — exceptions skip the cache so the next call
# retries. Negative results (score < threshold) ARE cached: those are the
# majority of the volume and the biggest single source of waste.
_QUICK_SCORE_CACHE: dict[str, tuple[float, dict | None]] = {}
_QUICK_SCORE_CACHE_TTL = 28 * 60       # 28 min — fires just before next scan
_QUICK_SCORE_CACHE_MAX = 6000          # LRU evict at 6k entries (~12h watchlist × 4 dirs)
_QUICK_SCORE_STATS = {"hits": 0, "misses": 0, "writes": 0, "evictions": 0}


def _quick_score_cache_key(symbol: str, direction: str, mkt_str: str,
                            pt_1d: str, pt_4h: str, pt_1h: str,
                            min_score: int) -> str:
    """Deterministic SHA-256 of the inputs that determine the Haiku output."""
    h = hashlib.sha256()
    for part in (symbol, direction, str(min_score), mkt_str or "",
                  pt_1d or "", pt_4h or "", pt_1h or ""):
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"|")
    return h.hexdigest()


def _quick_score_cache_get(key: str):
    """Returns (value, hit). value may legitimately be None when a previous
    call returned None (score below threshold) — that's still a cache hit.
    """
    ent = _QUICK_SCORE_CACHE.get(key)
    if not ent:
        _QUICK_SCORE_STATS["misses"] += 1
        return None, False
    ts, value = ent
    if _time.time() - ts > _QUICK_SCORE_CACHE_TTL:
        _QUICK_SCORE_CACHE.pop(key, None)
        _QUICK_SCORE_STATS["misses"] += 1
        return None, False
    _QUICK_SCORE_STATS["hits"] += 1
    return value, True


def _quick_score_cache_set(key: str, value):
    if len(_QUICK_SCORE_CACHE) >= _QUICK_SCORE_CACHE_MAX:
        # Cheap-and-correct LRU: drop the oldest entry by timestamp.
        oldest = min(_QUICK_SCORE_CACHE, key=lambda k: _QUICK_SCORE_CACHE[k][0])
        _QUICK_SCORE_CACHE.pop(oldest, None)
        _QUICK_SCORE_STATS["evictions"] += 1
    _QUICK_SCORE_CACHE[key] = (_time.time(), value)
    _QUICK_SCORE_STATS["writes"] += 1


def quick_score_cache_stats() -> dict:
    """Snapshot for monitoring / UI surfaces."""
    total = _QUICK_SCORE_STATS["hits"] + _QUICK_SCORE_STATS["misses"]
    hit_rate = (_QUICK_SCORE_STATS["hits"] / total) if total else 0.0
    return {
        "hits":      _QUICK_SCORE_STATS["hits"],
        "misses":    _QUICK_SCORE_STATS["misses"],
        "writes":    _QUICK_SCORE_STATS["writes"],
        "evictions": _QUICK_SCORE_STATS["evictions"],
        "size":      len(_QUICK_SCORE_CACHE),
        "ttl_min":   _QUICK_SCORE_CACHE_TTL // 60,
        "hit_rate":  round(hit_rate, 3),
    }


def _detect_archetype(ctx: dict, direction: str, symbol: str = "") -> str:
    """
    Detect the most likely setup archetype from 4H indicators.
    Returns one of: "breakout" | "reversal" | "continuation" |
                    "range_bound" | "low_conviction"

    Delegates to setup_classifier.classify_rules which uses:
      - multi-signal voting (each archetype gets a 0..N score)
      - WT-cross lookback over last 3 bars (fixes the "0 reversals"
        blind spot where WT only fires on the exact crossover candle)
      - 5-archetype taxonomy so 'continuation' stops being the
        dump-bucket for everything else

    Falls back to 'continuation' on classifier failure to preserve
    behaviour for downstream rubric selection.
    """
    try:
        from setup_classifier import classify_rules
        import chart_candles as _cc
        # Try to re-use the candles the ctx fetcher already has. The ctx
        # dict shape doesn't expose raw candles, so refetch — chart_candles
        # has its own LRU cache so this is cheap.
        # Symbol is required for the fetch; callers that don't have one
        # can pass it explicitly via the new keyword arg.
        if symbol:
            df = _cc.get_candles(symbol, "4H", limit=200)
            return classify_rules(symbol, direction, "", candles_df=df)["archetype"]
    except Exception:
        pass
    return "continuation"


def _archetype_rubric(archetype: str, direction: str, min_score: int) -> str:
    """Return a scoring rubric tailored to the detected setup archetype."""
    is_long = direction.lower() == "long"

    if archetype == "reversal":
        rsi_zone = "< 32" if is_long else "> 68"
        return (
            f"SETUP ARCHETYPE — REVERSAL / MEAN REVERSION\n"
            f"Primary trigger: WaveTrend crossover signal is REQUIRED (gold_buy/buy for Long, gold_sell/sell for Short).\n"
            f"RSI extremes required for 8+: RSI {rsi_zone} — exhaustion must be clear.\n"
            f"S/R anchor: Entry MUST be at or near a named structural level (support for Long, resistance for Short).\n"
            f"ADX: LOW is favourable (< 28) — ranging/choppy markets are ripe for reversals.\n"
            f"Score 9-10: WT gold/crossover signal + RSI extreme + S/R anchor + MFI divergence + low ADX.\n"
            f"Score 7-8: WT signal + RSI extreme + S/R anchor.\n"
            f"Score 6: WT signal + RSI extreme only (S/R less clear).\n"
            f"PENALISE: No WT crossover signal present. Strong EMA alignment opposing the reversal (trend too strong). ADX > 35."
        )

    if archetype == "breakout":
        rsi_zone = "55–78" if is_long else "22–45"
        return (
            f"SETUP ARCHETYPE — BREAKOUT / MOMENTUM EXPANSION\n"
            f"Non-negotiable: Volume ≥ 1.5× average — conviction breakouts require participation.\n"
            f"RSI acceptable: {rsi_zone} — momentum present, not yet exhausted.\n"
            f"Price must be breaking or have broken a key S/R level with candle body close beyond it.\n"
            f"MACD: Bullish/bearish cross with growing histogram adds significant conviction.\n"
            f"ADX: Ideally rising from below 25 — breakout launching from consolidation.\n"
            f"Score 9-10: Volume > 2× + clean S/R break + MACD growing + ADX rising + RSI in range.\n"
            f"Score 7-8: Volume > 1.5× + S/R break + at least one momentum signal confirming.\n"
            f"Score 6: Volume marginally above avg + S/R break (weaker conviction).\n"
            f"PENALISE: Volume below average (false breakout risk). RSI > 80 / < 20 (already exhausted). No S/R level being broken."
        )

    # continuation (default)
    rsi_zone = "45–68" if is_long else "32–55"
    return (
        f"SETUP ARCHETYPE — CONTINUATION / TREND FOLLOWING\n"
        f"Primary requirement: EMA stack aligned AND ADX ≥ 18 (trend must exist).\n"
        f"RSI sweet spot: {rsi_zone} — trend momentum WITHOUT exhaustion. RSI outside this range is a warning.\n"
        f"MACD: Aligned and ideally growing histogram = additional conviction.\n"
        f"Volume: Above average confirms institutional participation.\n"
        f"Score 9-10: EMA stack + ADX ≥ 25 + MACD growing + RSI in sweet spot + volume above avg + clean pullback to S/R.\n"
        f"Score 7-8: EMA stack + MACD aligned + RSI in range + clear entry level.\n"
        f"Score 6: At least 2 of 3 primary signals (EMA stack/ADX/MACD) + valid entry and SL.\n"
        f"PENALISE: RSI > 74 or < 26 (overextended — near exhaustion). ADX < 15 (no trend). EMA stack opposing direction."
    )


def _build_macro_header(macro_ctx: dict) -> str:
    """Short macro context header prepended to every scanner setup prompt."""
    if not macro_ctx:
        return ""
    parts = []

    vix = macro_ctx.get("vix")
    fg  = macro_ctx.get("fear_greed")
    btc_dom = macro_ctx.get("btc_dominance")
    usdt_dom = macro_ctx.get("usdt_dominance")
    es_chg   = macro_ctx.get("es_change_pct")
    stable_dom = macro_ctx.get("stable_dominance_pct")
    meme_cap   = macro_ctx.get("meme_cap_usd")

    if vix:
        regime = macro_ctx.get("regime", "").replace("_", " ").upper()
        parts.append(f"VIX {vix:.0f} ({regime})")
    if es_chg is not None:
        arrow = "▲" if es_chg >= 0 else "▼"
        parts.append(f"ES1! {arrow}{abs(es_chg):.1f}%")
    if fg is not None:
        fg_label = "Extreme Fear" if fg < 25 else "Fear" if fg < 45 else "Neutral" if fg < 55 else "Greed" if fg < 75 else "Extreme Greed"
        parts.append(f"F&G {fg}/100 ({fg_label})")
    if btc_dom:
        parts.append(f"BTC.D {btc_dom:.1f}%")
    if usdt_dom:
        parts.append(f"USDT.D {usdt_dom:.1f}%")
    if stable_dom:
        parts.append(f"STABLE.D {stable_dom:.1f}%")
    if meme_cap:
        parts.append(f"MEME ${meme_cap/1e9:.1f}B")
    if macro_ctx.get("macro_risk"):
        hrs = macro_ctx.get("hours_until")
        evt = macro_ctx.get("next_event", "macro event")
        hrs_str = f" in {hrs:.0f}h" if hrs else ""
        parts.append(f"⚠️ {evt}{hrs_str}")
    return "MACRO: " + " | ".join(parts) + "\n\n" if parts else ""


def _build_prompt(symbol, ctx, conf, direction, mkt_str, history, rulebook_str, min_score=SCANNER_MIN_SCORE,
                  macro_ctx: dict = None, archetype: str = ""):
    inds_4h = ctx.get("4H", {}).get("indicators", {})
    inds_1h = ctx.get("1H", {}).get("indicators", {}) or {}
    ema     = inds_4h.get("ema", {}) or {}
    price   = ema.get("current_price", 0)
    atr_1h  = (inds_1h.get("atr", {}) or {}).get("value", 0)
    atr_4h  = (inds_4h.get("atr", {}) or {}).get("value", 0)

    sr_4h = inds_4h.get("support_resistance", [])
    sr_text_4h = "\n".join(
        f"  {l['type'].upper():10s} {l['price']:.6g}  "
        f"(strength {l.get('strength', 1)}, {l.get('touches', 1)} touches)"
        for l in sorted(sr_4h, key=lambda x: -x.get("touches", 1))[:6]
    ) or "  None detected"

    sr_1h = inds_1h.get("support_resistance", [])
    sr_text_1h = "\n".join(
        f"  {l['type'].upper():10s} {l['price']:.6g}  "
        f"({l.get('touches', 1)} touches)"
        for l in sorted(sr_1h, key=lambda x: -x.get("touches", 1))[:5]
    ) or "  None detected"

    tl_4h = inds_4h.get("trendlines", [])
    tl_text = "\n".join(
        f"  {l.get('direction','?').upper()} trendline — {l.get('timeframe','4H')}, "
        f"{l.get('touches',1)} touches"
        for l in tl_4h[:3]
    ) or "  None"

    pt_4h = ctx.get("4H", {}).get("prompt_text", "No 4H data")
    pt_1h = ctx.get("1H", {}).get("prompt_text", "")
    pt_1d = ctx.get("1D", {}).get("prompt_text", "No 1D data")

    conf_line = (
        f"{conf['label']} ({conf['score']:+.2f}/{conf['max']} — "
        f"{conf['bullish']} bullish / {conf['bearish']} bearish signals)"
    )

    atr_note = ""
    if atr_1h:
        atr_note = f"1H ATR: ~{atr_1h:.4g}  |  4H ATR: ~{atr_4h:.4g}"
    elif atr_4h:
        atr_note = f"4H ATR: ~{atr_4h:.4g}"
    price_note = f"Current price: {price:.6g}  |  {atr_note}" if price else ""

    pt_1h_block = f"\n{pt_1h}" if pt_1h else ""
    hist_text  = json.dumps(history) if history.get("trades") else "No closed trades on this symbol yet"
    mkt_block  = f"\nMARKET CONTEXT:\n{mkt_str}\n" if mkt_str else ""
    rb_block   = f"\nTRADER RULEBOOK (known patterns — respect these):\n{rulebook_str}\n" if rulebook_str else ""
    macro_header = _build_macro_header(macro_ctx or {})

    archetype = archetype or _detect_archetype(ctx, direction, symbol=symbol)
    rubric_block = _archetype_rubric(archetype, direction, min_score)

    return f"""{macro_header}You are a professional crypto futures analyst. Score the current {direction.upper()} setup for {symbol} on a 1-10 scale and provide specific trade parameters.

MULTI-TIMEFRAME BREAKDOWN (HTF → LTF):
  1D  → directional bias and major structural levels
  4H  → setup confirmation and trend structure
  1H  → entry zone and stop loss placement (fresher, tighter precision)
  4H/1D → take profit targets at higher structural resistance/support

TECHNICAL DATA:
{pt_1d}
{pt_4h}{pt_1h_block}

CONFLUENCE: {conf_line}
{price_note}

KEY S/R LEVELS — 4H (TP targets, macro structure):
{sr_text_4h}

KEY S/R LEVELS — 1H (entry zone and SL anchors — USE THESE for entry/SL):
{sr_text_1h}

ACTIVE TRENDLINES:
{tl_text}
{mkt_block}
TRADER HISTORY ON {symbol}:
{hist_text}
{rb_block}
─────────────────────────────────────────
{MARKET_CONTEXT_RULES}

{SCORING_SCALE}

{LEVEL_PROXIMITY_RULES}

{rubric_block}

REQUIREMENTS for any score ≥ {min_score}:
- Entry zone: anchored to a 1H structural level (S/R, EMA, trendline) with exact prices — use 1H data for precision
- Stop loss: beyond the nearest 1H structural level and ≥ 1× 1H ATR from entry — state the level and ATR distance
- Take profits: at 4H or 1D resistance/support levels — name the structural zone for each TP
- Detailed rationale for EVERY level: name the S/R zone, reference the indicator value, explain WHY

RATIONALE DEPTH REQUIRED:
  entry_rationale: "Price pulling back to 1H support at $X (4 touches on 1H, aligns with 4H EMA50 at $Y). 1H RSI cooled to 44 from 68 — momentum reset without breaking structure."
  sl_rationale: "Below 1H swing low at $Z and 1H support cluster at $W. Distance $D = 1.5× 1H ATR ($A) — outside 1H noise, below 4H demand."
  tp1_rationale: "4H resistance at $R1, high-volume rejection on [date]. R:R 1:2.3 from midpoint entry."
  tp2_rationale: "1D resistance cluster and 1.618 Fibonacci extension from last major swing. R:R 1:4.1."

If the setup scores below {min_score} (no valid entry level, SL inside ATR noise, or no logical TP):
{{"setup_score": 0, "reason": "one sentence why this doesn't qualify"}}

Otherwise respond with this exact structure:
{{
  "setup_score": 8, "setup_label": "Strong",
  "direction": "{direction}",
  "why_this_score": "2-3 sentences explaining specifically what earns this score and what would need to be different for a 9 or a 7",
  "entry_zone": {{"low": 0.0, "high": 0.0, "rationale": "Name the 1H level, give the price, explain WHY this is the entry zone"}},
  "sl_price": 0.0,
  "sl_rationale": "Name the 1H structural level, state 1H ATR distance (e.g. 1.5× 1H ATR), explain the invalidation logic",
  "tp1_price": 0.0,
  "tp1_rationale": "Name the 4H resistance/target, explain why price is likely to stall or reverse there",
  "tp2_price": 0.0,
  "tp2_rationale": "Name the 4H/1D higher target, explain the structural or Fibonacci significance",
  "rr_ratio": "1:X.X",
  "chart_pattern": "Specific pattern name — or null if none",
  "key_conditions": ["Specific signal with values, e.g. 1H RSI 44 reset from 68", "4H MACD bull crossover", "1D EMA stack 20>50>200 bullish"],
  "risks": ["Specific risk with context", "Second risk"],
  "urgency": "Now|1-4h|Today|1-3 days",
  "timeframe": "Multi-TF (1D/4H/1H)",
  "confluence_summary": "One sentence: the 2-3 most important aligned signals that create conviction",
  "summary": "2-3 sentence overall assessment referencing actual price numbers from the technical picture"
}}

Respond with ONLY valid JSON — no markdown, no code fences."""


def _build_shared_prefix(mkt_str: str, rulebook_str: str,
                          min_score: int = SCANNER_MIN_SCORE,
                          criteria: dict = None) -> str:
    """
    Full shared prefix (legacy path — used when stable_prefix is not passed separately).
    For caching-aware callers use _build_scanner_stable() + mkt_block separately.
    """
    stable   = _build_scanner_stable(rulebook_str, min_score, criteria)
    mkt_part = f"MARKET CONTEXT:\n{mkt_str}\n\n" if mkt_str else ""
    return mkt_part + stable


def _build_scanner_stable(rulebook_str: str, min_score: int = SCANNER_MIN_SCORE,
                           criteria: dict = None) -> str:
    """
    Cacheable scanner prefix: rulebook + scoring scale + rule fragments + playbook.
    Stable across scan runs (changes only when rulebook updates).
    Pass as stable_prefix to build_cached_messages() so Anthropic caches it.

    Size target: ≥ 1024 tokens (~4096 chars) — Haiku 4.5's cache_control minimum.
    The playbook block is what gets us past the threshold AND improves Haiku's
    scoring quality at the same time (it makes scoring decisions less arbitrary).
    """
    cr       = criteria or CRITERIA_DEFAULTS
    rb_block = f"TRADER RULEBOOK:\n{rulebook_str}\n\n" if rulebook_str else ""
    dis_block = _disabled_criteria_block(cr)
    dis_part  = f"\n{dis_block}\n" if dis_block else ""
    caps = []
    if cr.get("sr_anchor", True): caps.append("no structural entry")
    if cr.get("atr_sl",    True): caps.append("SL inside ATR noise")
    if cr.get("rr_minimum",True): caps.append("R:R below 2:1")
    cap_str = " or ".join(caps) if caps else "no valid setup"
    return (
        f"{rb_block}"
        + SCORING_SCALE + "\n"
        + "5=Mod(borderline), 6=Accept(R:R≥2), 7=Good(R:R≥2.5), "
        + "8=Strong(R:R≥3), 9=Excellent(multi-TF,R:R≥3.5), 10=Perfect(R:R≥4)\n"
        + f"Score <{min_score} if: {cap_str}.{dis_part}\n\n"
        + LEVEL_PROXIMITY_RULES + "\n\n"
        + MARKET_CONTEXT_RULES + "\n\n"
        + DRAW_ON_LIQUIDITY_RULES + "\n\n"
        + SCANNER_PLAYBOOK
    )


# Inlined into stable_prefix so it counts toward the cache_control 1024-token
# minimum. Doubles as a scoring rubric the AI can lean on instead of inventing
# its own each call. ~1700 chars; brings the stable block to ~5000 chars total.
SCANNER_PLAYBOOK = """SETUP ARCHETYPES (use the one that matches the technical picture):

REVERSAL — WaveTrend crossover at RSI extreme (oversold-bounce or overbought-fade).
  Best when: 4H RSI <30 (long) or >70 (short), volume spike at the turn, S/R confluence.
  Score boost: +1 if 1H confirms with same-direction WaveTrend cross.
  Score cap: 7 unless higher TF (1D) is also at extreme — pure 4H reversals tend to fail.

BREAKOUT — Range escape with volume + momentum.
  Best when: volume ratio >1.8x, ADX rising past 18-20, price closing above prior swing high (long) or below low (short).
  Score boost: +1 if 1D context confirms (1D above EMA20 for long breakouts, below EMA20 for short breakouts).
  Score cap: 7 if entry is mid-range — wait for retest or confirmed close beyond level.

CONTINUATION — Trend pullback to structural level.
  Best when: 4H + 1D EMA stack agrees with direction, 1H pullback into 4H S/R, RSI reset to 40-55 (long) or 45-60 (short).
  Score boost: +1 if pullback bounces with WaveTrend reset (rising from low for long, falling from high for short).
  Score cap: 8 — this is the highest-conviction archetype when alignment is clean.

COMMON DOWNGRADES (apply these against your initial score):
- Counter-trend on 1D: -2 (e.g. long when 1D is below EMA50 falling)
- ADX < 15 on 4H: -1 (no trend, momentum signals less reliable)
- Volume ratio < 0.8x: -1 (low participation = poor follow-through)
- RSI > 75 (long) or < 25 (short): -1 (already extended, late entry)
- 4H + 1H disagree on direction: -1 (TF conflict)
- Price within ATR×0.5 of major resistance (long) or support (short): -1 (no room to TP)

ENTRY VALIDITY — HARD RULE (added 2026-05-26):
The entry price you emit MUST be reachable from the current chart, not a memory
of where support used to be. Specifically:
- Entry must be within ±5% of the current price OR within the last 24h range.
- If the only valid structural entry is further than that, the setup is NOT
  currently tradeable — score it ≤ 5 and put "wait for retrace to <level>" in
  the rationale. Do NOT emit an entry the market hasn't recently visited.
- If you're emitting a Long entry below the current price by more than 5%,
  OR a Short entry above the current price by more than 5%, you are picking
  a structural level the market has already left behind. Either find a level
  closer to current price (e.g. nearest recent S/R within ±5%) or admit no
  current entry is available.

QUALITY OVER QUANTITY: a 5 with one strong factor beats a 7 you can't justify. If you can't name 3 concrete factors driving the score in one sentence, score it ≤5.
"""


def _quick_score(symbol: str, ctx: dict, conf: dict, direction: str,
                 stable_prefix: str, mkt_str: str,
                 min_score: int = SCANNER_MIN_SCORE) -> dict | None:
    """
    Pass 1 — cheap Haiku call returning only a score (0-10) and confirmed direction.
    Returns None if score < min_score or on any error.

    stable_prefix: rulebook + scoring scale + rules (cached across calls within
        a scan cycle, as long as it clears the Anthropic minimum cache size).
    mkt_str: market context — same across all symbols of one scan cycle but
        varies between cycles; NOT cached (would bust the cache key every cycle).
    """
    pt_4h = ctx.get("4H", {}).get("prompt_text", "")
    pt_1h = ctx.get("1H", {}).get("prompt_text", "")
    pt_1d = ctx.get("1D", {}).get("prompt_text", "")

    # Candle-hash cache check (#1 cost optimisation). pt_1d/4h/1h are
    # deterministic functions of OHLCV — same candles → same string → same
    # Haiku output. mkt_str adds the macro context layer so we don't reuse
    # a score made under a different VIX/F&G snapshot.
    _cache_key = _quick_score_cache_key(
        symbol, direction, mkt_str, pt_1d, pt_4h, pt_1h, min_score)
    _cached_value, _cache_hit = _quick_score_cache_get(_cache_key)
    if _cache_hit:
        return _cached_value

    conf_line = f"{conf['label']} ({conf['bullish']}↑/{conf['bearish']}↓)"

    inds_4h = ctx.get("4H", {}).get("indicators", {})
    inds_1h = (ctx.get("1H", {}).get("indicators", {}) or {})
    sr_4h = inds_4h.get("support_resistance", [])
    sr_1h = inds_1h.get("support_resistance", [])
    sr_compact = "  ".join(
        f"{'S' if l['type']=='support' else 'R'}:{l['price']:.6g}({l.get('touches',1)}t)"
        for l in sorted(sr_4h, key=lambda x: -x.get("touches", 1))[:4]
    ) or "none"
    sr_1h_compact = "  ".join(
        f"{'S' if l['type']=='support' else 'R'}:{l['price']:.6g}({l.get('touches',1)}t)"
        for l in sorted(sr_1h, key=lambda x: -x.get("touches", 1))[:3]
    ) or "none"
    pt_1h_block = f"\n{pt_1h}" if pt_1h else ""

    # BUG-005 fix (2026-05-26): example reason is now direction-aware. The
    # previous bullish-only example ("4H EMA stack bullish, RSI reset to 52")
    # anchored Haiku toward bullish criteria, dropping 100% of Shorts that
    # survived Stage 2 (observed funnel: 19 Shorts → 0 final on 2026-05-26).
    is_long = direction.lower() == "long"
    example_reason = (
        "4H EMA stack bullish, RSI reset to 52, clean S/R entry zone"
        if is_long else
        "4H EMA stack bearish, RSI rejected at 62, clean S/R rejection zone"
    )
    variable = (
        f"Score this {direction.upper()} setup for {symbol} — return score 0-10 "
        f"and one short sentence explaining the key factor behind the score.\n\n"
        f"{pt_1d}\n{pt_4h}{pt_1h_block}\n"
        f"Confluence: {conf_line}\n4H S/R: {sr_compact}\n1H S/R: {sr_1h_compact}\n\n"
        f'If score < {min_score}: {{"score":0}}\n'
        f'If score >= {min_score}: {{"score":7,"direction":"{direction}",'
        f'"reason":"one sentence — main factor (e.g. \'{example_reason}\')"}}\n'
        "Respond with ONLY valid JSON — no extras."
    )

    try:
        # build_cached_messages tags stable_prefix with cache_control=ephemeral
        # when it clears PROMPT_CACHE_MIN_CHARS, so within-cycle reads cost ~10%
        # of the input price instead of full price.
        mkt_block = f"MARKET CONTEXT:\n{mkt_str}\n" if mkt_str else ""
        messages  = build_cached_messages(
            context        = mkt_block,
            prompt         = variable,
            stable_prefix  = stable_prefix,
        )
        # Bumped max_tokens 120→200 — was causing truncated JSON for setups
        # whose reason string was a few words long. Same prompt, more headroom.
        msg_text, _cached = ai_send(
            "scanner_quick", FAST_MODEL,
            messages,
            max_tokens=200,
        )
        # Robust JSON extraction — handles models that prepend text or emit
        # multiple JSON objects (~27% of Stage-2 scores were dropped by this).
        from helpers import extract_json_object
        raw = msg_text.strip()
        obj_str = extract_json_object(raw) or strip_fence(raw)
        try:
            r = json.loads(obj_str)
        except Exception:
            # Final fallback: tighten quotes + trim trailing commas
            cleaned = obj_str.replace("“", '"').replace("”", '"').replace(",}", "}").replace(",]", "]")
            r = json.loads(cleaned)
        # Both branches below get cached — score-below-threshold is the
        # dominant case and the biggest cost saving from this cache.
        if r.get("score", 0) < min_score:
            _quick_score_cache_set(_cache_key, None)
            return None
        _result = {
            "score":     r["score"],
            "direction": r.get("direction", direction),
            "reason":    r.get("reason", ""),
        }
        _quick_score_cache_set(_cache_key, _result)
        return _result
    except Exception as e:
        # Log first 160 chars of raw response so we can see what the model
        # actually returned. Truncation/extra-data are the dominant modes.
        # NB: do NOT cache exceptions — next call must retry.
        snippet = (msg_text[:160] if 'msg_text' in dir() else '<no response>').replace("\n", " ")
        logger.warning("quick-score failed for %s: %s | raw: %s", symbol, e, snippet)
        return None


def _build_batch_prompt(finalists, histories, min_score=SCANNER_MIN_SCORE, criteria=None,
                        nansen_signals=None):
    """Build a single prompt for all top-N symbols. Returns user_prompt string."""
    parts = []
    for i, (symbol, ctx, conf, direction, score, _reason) in enumerate(finalists, 1):
        inds_4h = ctx.get("4H", {}).get("indicators", {})
        inds_1h = ctx.get("1H", {}).get("indicators", {}) or {}
        ema     = inds_4h.get("ema", {}) or {}
        price   = ema.get("current_price", 0)
        atr_4h  = (inds_4h.get("atr", {}) or {}).get("value", 0)
        atr_1h  = (inds_1h.get("atr", {}) or {}).get("value", 0)
        pt_4h   = ctx.get("4H", {}).get("prompt_text", "No 4H data")
        pt_1h   = ctx.get("1H", {}).get("prompt_text", "")
        pt_1d   = ctx.get("1D", {}).get("prompt_text", "No 1D data")

        # 4H S/R for TP targets; 1H S/R for entry/SL anchors
        sr_4h = inds_4h.get("support_resistance", [])
        sr_4h_compact = "  ".join(
            f"{'S' if l['type']=='support' else 'R'}:{l['price']:.6g}({l.get('touches',1)}t)"
            for l in sorted(sr_4h, key=lambda x: -x.get("touches", 1))[:4]
        ) or "none"
        sr_1h = inds_1h.get("support_resistance", [])
        sr_1h_compact = "  ".join(
            f"{'S' if l['type']=='support' else 'R'}:{l['price']:.6g}({l.get('touches',1)}t)"
            for l in sorted(sr_1h, key=lambda x: -x.get("touches", 1))[:4]
        ) or "none"

        hist = histories.get(symbol, {"trades": 0})
        conf_line = f"{conf['label']} ({conf['bullish']}↑/{conf['bearish']}↓)"
        ns      = (nansen_signals or {}).get(symbol, {})
        ns_line = f"\n{ns['prompt_line']}" if ns.get("ok") else ""
        archetype = _detect_archetype(ctx, direction, symbol=symbol)
        atr_str = f"1H ATR:{atr_1h:.4g}  4H ATR:{atr_4h:.4g}" if atr_1h else f"4H ATR:{atr_4h:.4g}"
        pt_1h_block = f"\n{pt_1h}" if pt_1h else ""

        parts.append(
            f"--- SETUP {i}: {symbol} ({direction.upper()}) | Archetype: {archetype.upper()} ---\n"
            f"{pt_1d}\n{pt_4h}{pt_1h_block}\n"
            f"Confluence: {conf_line}  |  Price: {price:.6g}  |  {atr_str}\n"
            f"4H S/R (TP targets): {sr_4h_compact}\n"
            f"1H S/R (entry/SL — USE THESE): {sr_1h_compact}\n"
            f"History: {json.dumps(hist)}{ns_line}"
        )

    cr = criteria or CRITERIA_DEFAULTS
    dis_block = _disabled_criteria_block(cr)
    dis_part  = f"\n{dis_block}\n" if dis_block else ""

    level_rules = []
    if cr.get("sr_anchor", True): level_rules.append("Entry >1×ATR from level → max 6")
    if cr.get("atr_sl",    True): level_rules.append("SL <1×1H ATR from entry → max 6")
    if cr.get("rr_minimum",True): level_rules.append("R:R<2:1 → max 6")
    level_str = ". ".join(level_rules) + "." if level_rules else ""

    setups_text = "\n\n".join(parts)
    scoring_hint = SCORING_SCALE[:60] + " ... 9=Excellent(R:R≥3.5), 10=Perfect(R:R≥4)"
    archetype_hints = (
        "ARCHETYPES: REVERSAL=WaveTrend+RSI extreme at S/R required, "
        "penalise strong opposing EMA trend. "
        "BREAKOUT=Volume>1.5x required, RSI in momentum zone, price breaking S/R. "
        "CONTINUATION=EMA stack+ADX≥18 required, RSI in sweet spot 45-68(L)/32-55(S)."
    )
    htf_ltf_rule = (
        "HTF→LTF BREAKDOWN: Use 1D for directional bias. Use 4H to confirm trend structure. "
        "Use 1H S/R levels to anchor the entry zone and stop loss (fresher, tighter precision). "
        "Use 4H/1D resistance or support for take profit targets."
    )
    user_prompt = (
        f"Analyze these {len(finalists)} crypto futures setups. "
        f"Return a JSON ARRAY of exactly {len(finalists)} objects — one per setup, in the same order.\n\n"
        f"{setups_text}\n\n"
        f"{htf_ltf_rule}\n"
        f"{archetype_hints}\n"
        f"{scoring_hint}\n"
        f"{level_str}{dis_part}\n"
        f"For setups scoring >= {min_score}, use this structure:\n"
        '{"symbol":"X","direction":"Long","setup_score":7,"setup_label":"Good",'
        '"why_this_score":"2-3 sentences","entry_zone":{"low":0,"high":0,"rationale":"1H level name + price"},'
        '"sl_price":0,"sl_rationale":"1H structural level + 1H ATR distance","tp1_price":0,"tp1_rationale":"4H target",'
        '"tp2_price":0,"tp2_rationale":"4H/1D target","rr_ratio":"1:X","chart_pattern":null,'
        '"key_conditions":["..."],"risks":["..."],"urgency":"Now|1-4h|Today|1-3 days",'
        '"timeframe":"Multi-TF (1D/4H/1H)","confluence_summary":"...","summary":"..."}\n'
        f'For setups scoring below {min_score}: {{"symbol":"X","setup_score":0,"reason":"why"}}\n\n'
        "Respond with ONLY a valid JSON array — no markdown, no code fences."
    )
    return user_prompt
