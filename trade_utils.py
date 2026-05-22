"""
trade_utils.py — Shared trading utilities for AI analysis modules.

Centralises sector definitions and ATR-based SL quality check,
which were previously duplicated in ai_call.py and ai_limit.py.
"""

import chart_context

# Sector → USDT symbol list (synced with JS SECTORS in 08-live.js)
SECTORS = {
    "BTC":      ["BTCUSDT", "WBTCUSDT"],
    "ETH/L2":   ["ETHUSDT", "ARBUSDT", "OPUSDT", "MATICUSDT", "STRKUSDT", "ZKUSDT", "SCROLLUSDT"],
    "SOL/L1":   ["SOLUSDT", "AVAXUSDT", "SUIUSDT", "APTUSDT", "NEARUSDT", "SEIUSDT", "INJUSDT"],
    "Meme":     ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "BOMEUSDT", "WIFUSDT", "BONKUSDT",
                 "FLOKIUSDT", "MOGUSDT", "POPCATUSDT"],
    "DeFi":     ["UNIUSDT", "AAVEUSDT", "CRVUSDT", "MKRUSDT", "SNXUSDT", "COMPUSDT", "DYDXUSDT"],
    "AI/Infra": ["FETUSDT", "RENDERUSDT", "WLDUSDT", "TAOUSDT", "AGIXUSDT", "GRTUSDT"],
}


def atr_sl_warning(symbol: str, entry: float, sl: float) -> str:
    """Return a warning string if SL distance is within 1H ATR noise range."""
    try:
        ctx     = chart_context.get_chart_context(symbol, ["1H"])
        inds    = ctx.get("1H", {}).get("indicators", {})
        atr     = inds.get("atr", {})
        if not atr or not atr.get("value"):
            return ""
        atr_val = atr["value"]
        sl_dist = abs(entry - sl)
        if sl_dist < atr_val * 0.5:
            return (f"SL distance {sl_dist:.4f} < 0.5× 1H ATR ({atr_val:.4f}) — "
                    "stop is inside noise, very high chance of premature trigger")
        if sl_dist < atr_val:
            return (f"SL distance {sl_dist:.4f} < 1× 1H ATR ({atr_val:.4f}) — "
                    "tight stop, moderate noise risk")
    except Exception:
        pass
    return ""


# TP minimum distance enforcement. Hindsight (60d, n=225 scanner setups)
# showed a 96% TP1 hit rate but avg_win $10 vs avg_loss $21 — TPs were
# printing on noise alone. 1× ATR_4H floor stops the AI from setting TP1
# inside the bar's expected range.
TP1_MIN_ATR_MULTIPLE = 1.0
TP2_MIN_ATR_MULTIPLE = 2.0


def enforce_tp_floor(entry: float, direction: str, tp1: float, tp2: float,
                      atr_4h: float) -> tuple[float, float, list[str]]:
    """
    Returns (tp1_adjusted, tp2_adjusted, notes). Bumps TP1/TP2 to the
    minimum ATR-based distance from entry when the AI returned values
    inside the noise floor. Each adjustment is recorded in notes so the
    caller can surface why the TP changed.

    Direction-aware: TP must be on the correct side of entry (above for
    Long, below for Short) before the floor applies.
    """
    notes: list[str] = []
    try:
        entry   = float(entry or 0)
        tp1     = float(tp1 or 0)
        tp2     = float(tp2 or 0)
        atr_4h  = float(atr_4h or 0)
    except (TypeError, ValueError):
        return tp1, tp2, notes
    if entry <= 0 or atr_4h <= 0:
        return tp1, tp2, notes

    is_long = (direction or "").strip().lower() == "long"
    min_tp1_dist = atr_4h * TP1_MIN_ATR_MULTIPLE
    min_tp2_dist = atr_4h * TP2_MIN_ATR_MULTIPLE

    def _floor(tp: float, min_dist: float, label: str) -> float:
        if tp <= 0:
            return tp
        dist = (tp - entry) if is_long else (entry - tp)
        if dist >= min_dist:
            return tp
        # AI placed TP too tight (or wrong side). Push to minimum.
        new_tp = (entry + min_dist) if is_long else (entry - min_dist)
        notes.append(
            f"{label} bumped from {tp:.6g} to {new_tp:.6g} — was within "
            f"{min_dist/atr_4h:.1f}× ATR_4H ({atr_4h:.4f}) of entry"
        )
        return round(new_tp, 6)

    tp1_new = _floor(tp1, min_tp1_dist, "TP1")
    tp2_new = _floor(tp2, min_tp2_dist, "TP2")

    # If TP1 got bumped, make sure TP2 stays beyond TP1 (preserve laddering)
    if tp2_new > 0 and ((is_long and tp2_new <= tp1_new) or
                        (not is_long and tp2_new >= tp1_new)):
        tp2_floor_for_ladder = (tp1_new + atr_4h) if is_long else (tp1_new - atr_4h)
        if tp2_new != tp2_floor_for_ladder:
            notes.append(f"TP2 pushed to {tp2_floor_for_ladder:.6g} to preserve TP1<TP2 ladder")
            tp2_new = round(tp2_floor_for_ladder, 6)

    return tp1_new, tp2_new, notes


# SL sanity envelope. SL must be on the correct side of entry, not inside
# 0.5× ATR_4H noise, and not absurdly wide (> 8× ATR_4H — that's a 16×
# adverse range, basically a no-stop). Mirrors the envelope used by
# scripts/fix_all_unsane_tpsl.py so scanner-side and exec-side agree.
SL_MIN_ATR_MULTIPLE = 0.5
SL_MAX_ATR_MULTIPLE = 8.0
SL_DEFAULT_ATR_MULTIPLE = 1.0


def enforce_sl_floor(entry: float, direction: str, sl: float,
                      atr_4h: float) -> tuple[float, list[str]]:
    """
    Returns (sl_adjusted, notes). Repairs SL when AI placed it on the
    wrong side of entry, inside ATR noise, or absurdly wide.

    Direction-aware: for Long, SL must be BELOW entry; for Short, ABOVE.
    Repairs use 1× ATR_4H from entry on the correct side — same default
    the live-order placement uses, so the journal and Bitget agree.
    """
    notes: list[str] = []
    try:
        entry  = float(entry or 0)
        sl     = float(sl or 0)
        atr_4h = float(atr_4h or 0)
    except (TypeError, ValueError):
        return sl, notes
    if entry <= 0 or atr_4h <= 0:
        return sl, notes

    is_long = (direction or "").strip().lower() == "long"
    default_sl = (entry - atr_4h * SL_DEFAULT_ATR_MULTIPLE) if is_long \
                 else (entry + atr_4h * SL_DEFAULT_ATR_MULTIPLE)

    if sl <= 0:
        notes.append(f"SL was missing — set to {default_sl:.6g} (entry ∓1× ATR_4H)")
        return round(default_sl, 6), notes

    # Wrong side of entry — repair to default
    if is_long and sl >= entry:
        notes.append(f"SL {sl:.6g} was at/above entry for Long — repaired to "
                     f"{default_sl:.6g}")
        return round(default_sl, 6), notes
    if not is_long and sl <= entry:
        notes.append(f"SL {sl:.6g} was at/below entry for Short — repaired to "
                     f"{default_sl:.6g}")
        return round(default_sl, 6), notes

    sl_dist = abs(entry - sl)
    min_dist = atr_4h * SL_MIN_ATR_MULTIPLE
    max_dist = atr_4h * SL_MAX_ATR_MULTIPLE

    if sl_dist < min_dist:
        new_sl = (entry - min_dist) if is_long else (entry + min_dist)
        notes.append(f"SL bumped from {sl:.6g} to {new_sl:.6g} — was "
                     f"{sl_dist/atr_4h:.2f}× ATR_4H (need ≥{SL_MIN_ATR_MULTIPLE}×)")
        return round(new_sl, 6), notes
    if sl_dist > max_dist:
        new_sl = (entry - max_dist) if is_long else (entry + max_dist)
        notes.append(f"SL pulled in from {sl:.6g} to {new_sl:.6g} — was "
                     f"{sl_dist/atr_4h:.2f}× ATR_4H (cap {SL_MAX_ATR_MULTIPLE}×)")
        return round(new_sl, 6), notes

    return sl, notes


def normalize_symbol(s: str) -> str:
    """BTC/USDT, btc-usdt → BTCUSDT.""",
    return (s or '').upper().replace('/', '').replace('-', '').replace('_', '').strip()


def normalize_direction(s: str) -> str:
    """long/buy/open_long → Long; short/sell → Short.""",
    d = (s or '').strip().lower()
    if d in ('long', 'buy', 'open_long'):  return 'Long'
    if d in ('short', 'sell', 'open_short'): return 'Short'
    return s


# Default tolerance for linking a call to a position. 20% covers a few candles
# of natural drift between call creation and position entry without admitting
# the analyst-feed parser misreads we saw on XPLUSDT (3.2x off scale).
PRICE_SCALE_MATCH_THRESHOLD = 0.20


def price_scale_matches(call_entry, pos_entry,
                         threshold: float = PRICE_SCALE_MATCH_THRESHOLD) -> bool:
    """
    True when a call's reference price is within ±threshold of the position's
    actual entry. Guards against linking a call whose analyst-feed parser
    captured a wrong number (e.g. 0.287 attached to an XPLUSDT position at
    0.0901). Returns True when either side is missing — calling sites should
    decide separately whether to require a price to be present.
    """
    try:
        c = float(call_entry or 0)
        p = float(pos_entry or 0)
    except (TypeError, ValueError):
        return True
    if c <= 0 or p <= 0:
        return True
    return abs(c - p) / p <= threshold
