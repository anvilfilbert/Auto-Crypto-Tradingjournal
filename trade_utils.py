"""
trade_utils.py — Shared trading utilities for AI analysis modules.

Centralises sector definitions and ATR-based SL quality check,
which were previously duplicated in ai_call.py and ai_limit.py.
"""

import logging
from typing import Optional
import chart_context

logger = logging.getLogger(__name__)

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
        logger.warning(
            "enforce_tp_floor skipped: entry=%s atr_4h=%s — wrong-side TPs cannot be repaired",
            entry, atr_4h,
        )
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
        logger.warning(
            "enforce_sl_floor skipped: entry=%s atr_4h=%s — wrong-side SL cannot be repaired",
            entry, atr_4h,
        )
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


# Feature 20 — SafeZone SL (Elder's "stay away from the crowd" stop placement).
# Stop-hunt zones cluster around round numbers and obvious swing extremes.
# Moving SL 0.5× ATR beyond these "obvious" levels often avoids the wick
# that would trigger a premature exit.
SAFEZONE_BUFFER_ATR_MULT     = 0.5    # how far to push SL past a round/swing zone
SAFEZONE_ROUND_PROXIMITY_PCT = 0.5    # SL within this % of a round number = stop-hunt zone


def _round_number_for(price: float) -> Optional[float]:
    """
    Find the nearest "round number" for a given price magnitude.

    Examples:
      $65,000 BTC → nearest $1,000 step    (65000)
      $3,500 ETH  → nearest $100 step      (3500)
      $0.003 alt  → nearest $0.0001 step
    Scales by floor(log10(price)) to handle any asset.
    """
    if price is None or price <= 0:
        return None
    try:
        import math as _math
        magnitude = _math.floor(_math.log10(price))
        step = 10 ** (magnitude - 1)  # one order below the asset magnitude
        # Snap to multiples of step
        return round(price / step) * step
    except (ValueError, TypeError):
        return None


def safezone_sl(entry: float, direction: str, sl: float,
                  atr_4h: float) -> tuple[float, list[str]]:
    """
    Push SL beyond round-number stop-hunt zones (Elder, Trading for a Living).

    If the proposed SL is within SAFEZONE_ROUND_PROXIMITY_PCT% of the nearest
    round number relative to entry, move the SL further by SAFEZONE_BUFFER_ATR_MULT× ATR.

    Direction-aware: for Long, "further" means lower; for Short, higher.
    Joins the existing enforce_*_floor pipeline in Stage 3 (after enforce_sl_floor).

    Returns (sl_adjusted, notes). Returns (sl, []) on missing data or no adjustment.
    """
    notes: list[str] = []
    try:
        entry  = float(entry or 0)
        sl     = float(sl or 0)
        atr_4h = float(atr_4h or 0)
    except (TypeError, ValueError):
        return sl, notes
    if entry <= 0 or sl <= 0 or atr_4h <= 0:
        return sl, notes

    round_num = _round_number_for(sl)
    if round_num is None:
        return sl, notes
    proximity_pct = abs(sl - round_num) / entry * 100.0
    if proximity_pct > SAFEZONE_ROUND_PROXIMITY_PCT:
        return sl, notes   # SL not near a round zone

    is_long = (direction or "").strip().lower() == "long"
    buffer = atr_4h * SAFEZONE_BUFFER_ATR_MULT
    new_sl = (sl - buffer) if is_long else (sl + buffer)
    notes.append(
        f"SafeZone SL: original {sl:.6g} within {proximity_pct:.2f}% of round "
        f"{round_num:.6g} — pushed to {new_sl:.6g} ({SAFEZONE_BUFFER_ATR_MULT}× ATR buffer)"
    )
    return round(new_sl, 6), notes


# Feature 9 — Trade Grade (Elder A-trade channel normalization).
TRADE_GRADE_CHANNEL_ATR_MULT = 4.0  # 4× ATR_4H as proxy for "expected channel"


def compute_trade_grade(symbol: str, entry: float, close_price: float,
                          direction: str) -> Optional[float]:
    """
    Normalize trade P&L by expected channel height (≈ 4× ATR_4H).

    A grade ≥ 0.30 = A-trade (captured ≥30% of expected channel).
    A grade ≥ 0.15 = B-trade.
    A grade < 0    = loser (price went wrong direction).

    Direction-aware: for Long, positive grade = profit; for Short, mirror.

    Returns float or None on missing/invalid data. ATR is fetched fresh
    (close-time channel, not entry-time — easier; accuracy trade-off
    documented in the architect's note).
    """
    try:
        entry = float(entry or 0)
        close_price = float(close_price or 0)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or close_price <= 0:
        return None
    try:
        ctx = chart_context.get_chart_context(symbol, ["4H"])
        atr = ((ctx.get("4H") or {}).get("indicators") or {}).get("atr") or {}
        atr_v = atr.get("value")
        if atr_v is None or atr_v <= 0:
            return None
        atr_v = float(atr_v)
    except Exception:
        return None
    channel = atr_v * TRADE_GRADE_CHANNEL_ATR_MULT
    if channel <= 0:
        return None
    is_long = (direction or "").strip().lower() == "long"
    sign = 1 if is_long else -1
    # Signed distance in direction of trade
    move = (close_price - entry) * sign
    return move / channel


def validate_direction_vs_levels(direction: str, entry: float, sl: float,
                                  tp1: float, tp2: float = 0) -> tuple[bool, str]:
    """
    Returns (ok, reason). Confirms levels sit on the correct side of entry
    given the direction. Last-line defence against direction/levels drift
    between Stage 1 (technicals) and Stage 3 (LLM-emitted ladder) — the
    enforce_* functions can be skipped when ATR is missing, so a final
    geometric check is needed before a setup reaches consensus.

    Rules:
      Long  → sl < entry  AND  tp1 > entry  (and tp2 > entry when present)
      Short → sl > entry  AND  tp1 < entry  (and tp2 < entry when present)
    """
    try:
        e   = float(entry or 0)
        s   = float(sl    or 0)
        t1  = float(tp1   or 0)
        t2  = float(tp2   or 0)
    except (TypeError, ValueError):
        return False, "non-numeric level"

    if e <= 0:
        return False, "entry <= 0"
    if s <= 0 or t1 <= 0:
        return False, "sl or tp1 missing/<=0"

    is_long = (direction or "").strip().lower() == "long"
    if is_long:
        if s >= e:
            return False, f"Long: sl {s} >= entry {e}"
        if t1 <= e:
            return False, f"Long: tp1 {t1} <= entry {e}"
        if t2 > 0 and t2 <= e:
            return False, f"Long: tp2 {t2} <= entry {e}"
        return True, ""
    # Short
    if s <= e:
        return False, f"Short: sl {s} <= entry {e}"
    if t1 >= e:
        return False, f"Short: tp1 {t1} >= entry {e}"
    if t2 > 0 and t2 >= e:
        return False, f"Short: tp2 {t2} >= entry {e}"
    return True, ""


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
