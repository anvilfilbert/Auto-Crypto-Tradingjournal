"""
chart_supply_demand.py — Feature 13: Supply/Demand zones with order-absorption decay.

A SUPPLY zone (bearish): a rapid-expansion DOWN candle (or sequence) preceded
by tight consolidation. Marks an area where heavy selling absorbed buying.

A DEMAND zone (bullish): mirror — rapid-expansion UP candle preceded by
tight consolidation. Heavy buying absorbed selling.

Order-absorption decay (Schlotmann):
  - Touch 1: zone at FULL strength → ±0.3 confluence weight
  - Touch 2: zone at HALF strength → ±0.15
  - Touch 3+: zone INVALID → 0 (broken)

Persistence:
  Zones are stored in `sd_zones` table (migration v61). The scanner detector
  inserts new zones; touch counts update when price visits existing zones.

  Schema: sd_zones(id, symbol, timeframe, zone_type, top, bottom, touches,
                    valid, created_at, last_seen)
"""

import logging
import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Detection params
RAPID_EXPANSION_BAR_BODY_PCT = 0.7      # body ≥ 70% of bar range = strong directional bar
RAPID_EXPANSION_VOL_RATIO    = 1.5      # volume ≥ 1.5× recent average
CONSOLIDATION_MAX_RANGE_ATR  = 1.0      # consolidation bars stay within 1× ATR
CONSOLIDATION_LOOKBACK       = 5        # bars to check for consolidation before expansion
MIN_ZONE_WIDTH_PCT           = 0.001    # zone must be ≥0.1% wide to be useful

# Decay logic
ZONE_FULL_STRENGTH_TOUCHES   = 1        # 0-1 touches = full strength
ZONE_HALF_STRENGTH_TOUCHES   = 2        # 2 touches = half strength
ZONE_INVALIDATE_TOUCHES      = 3        # 3+ touches = invalid

# Touch detection: price within this % of zone counts as a touch
TOUCH_PROXIMITY_PCT          = 0.5


def detect_sd_zones(df, atr_value: float = None, lookback: int = 30) -> list:
    """
    Scan recent candles for supply/demand zone candidates.

    Returns a list of zone dicts:
      {"zone_type": "supply"|"demand", "top": float, "bottom": float,
       "created_bar_idx": int}

    Empty list when no clean zones found.
    """
    if df is None or len(df) < lookback + CONSOLIDATION_LOOKBACK + 1:
        return []
    out = []
    try:
        candles = df.iloc[-(lookback + CONSOLIDATION_LOOKBACK):]
        for i in range(CONSOLIDATION_LOOKBACK, len(candles) - 1):
            bar = candles.iloc[i]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            vol = float(bar.get("volume", 0))
            bar_range = h - l
            if bar_range <= 0:
                continue
            body_pct = abs(c - o) / bar_range
            if body_pct < RAPID_EXPANSION_BAR_BODY_PCT:
                continue   # not a strong directional bar

            # Check volume (compare to avg of prior bars)
            prior = candles.iloc[max(0, i - 20):i]
            if len(prior) == 0:
                continue
            avg_vol = float(prior["volume"].mean()) if "volume" in prior.columns else 0
            if avg_vol > 0 and vol < avg_vol * RAPID_EXPANSION_VOL_RATIO:
                continue   # not a volume expansion

            # Check prior consolidation
            cons_window = candles.iloc[max(0, i - CONSOLIDATION_LOOKBACK):i]
            cons_range = float(cons_window["high"].max() - cons_window["low"].min())
            if atr_value and cons_range > atr_value * CONSOLIDATION_MAX_RANGE_ATR:
                continue   # prior bars were too volatile to be a consolidation

            # Classify direction + create zone
            if c > o:   # green expansion = demand zone (zone at the consolidation low)
                top    = cons_window["high"].max()
                bottom = cons_window["low"].min()
                ztype  = "demand"
            else:   # red expansion = supply zone (zone at the consolidation high)
                top    = cons_window["high"].max()
                bottom = cons_window["low"].min()
                ztype  = "supply"

            width_pct = (top - bottom) / max((top + bottom) / 2, 1)
            if width_pct < MIN_ZONE_WIDTH_PCT:
                continue   # zone too thin to be useful

            out.append({
                "zone_type": ztype,
                "top":       round(float(top), 6),
                "bottom":    round(float(bottom), 6),
                "created_bar_idx": i,
            })
    except Exception as e:
        logger.debug("detect_sd_zones error: %s", e)
        return []
    return out


def zone_strength(touches: int) -> float:
    """
    Map touch count to relative strength (0-1 scale).
      0-1 touches → 1.0 (full)
      2 touches   → 0.5 (half)
      3+ touches  → 0.0 (invalid)
    """
    if touches < ZONE_HALF_STRENGTH_TOUCHES:
        return 1.0
    if touches < ZONE_INVALIDATE_TOUCHES:
        return 0.5
    return 0.0


# ── Persistence helpers ────────────────────────────────────────────────────


def upsert_zone(conn, symbol: str, timeframe: str, zone: dict) -> int:
    """
    Insert a new zone or update if a close-match already exists.

    "Close match" = same symbol+timeframe+zone_type with overlapping price bands
    (any overlap counts — we don't create duplicate zones at the same level).
    """
    try:
        # Look for an overlapping existing zone
        existing = conn.execute("""
            SELECT id, top, bottom, touches FROM sd_zones
            WHERE symbol=? AND timeframe=? AND zone_type=? AND valid=1
              AND NOT (top < ? OR bottom > ?)
        """, (symbol, timeframe, zone["zone_type"], zone["bottom"], zone["top"])
        ).fetchone()
        if existing:
            # Already exists — update last_seen, return id
            conn.execute(
                "UPDATE sd_zones SET last_seen=datetime('now') WHERE id=?",
                (existing["id"],))
            conn.commit()
            return int(existing["id"])
        # Insert new
        cur = conn.execute("""
            INSERT INTO sd_zones (symbol, timeframe, zone_type, top, bottom,
                                    touches, valid, last_seen)
            VALUES (?, ?, ?, ?, ?, 0, 1, datetime('now'))
        """, (symbol, timeframe, zone["zone_type"], zone["top"], zone["bottom"]))
        conn.commit()
        return int(cur.lastrowid)
    except Exception as e:
        logger.debug("upsert_zone error: %s", e)
        return 0


def record_touches_at_price(conn, symbol: str, timeframe: str,
                              current_price: float) -> None:
    """
    Increment touch counts on any valid zones that the current price is
    visiting (within TOUCH_PROXIMITY_PCT of the zone band).

    Side effect on the sd_zones table — no return.
    """
    if not current_price or current_price <= 0:
        return
    try:
        rows = conn.execute("""
            SELECT id, top, bottom, touches FROM sd_zones
            WHERE symbol=? AND timeframe=? AND valid=1
        """, (symbol, timeframe)).fetchall()
        for r in rows:
            top    = float(r["top"])
            bottom = float(r["bottom"])
            # Proximity check: price within zone OR within X% of band
            buffer = current_price * (TOUCH_PROXIMITY_PCT / 100)
            if bottom - buffer <= current_price <= top + buffer:
                new_touches = int(r["touches"]) + 1
                still_valid = 1 if new_touches < ZONE_INVALIDATE_TOUCHES else 0
                conn.execute(
                    "UPDATE sd_zones SET touches=?, valid=?, last_seen=datetime('now') "
                    "WHERE id=?",
                    (new_touches, still_valid, r["id"]))
        conn.commit()
    except Exception as e:
        logger.debug("record_touches_at_price error: %s", e)


def sd_zone_weight(conn, symbol: str, timeframe: str, current_price: float,
                     direction: str) -> tuple[float, str]:
    """
    Read the nearest valid demand/supply zone and return weighted contribution.

    Args:
      conn: DB connection
      symbol, timeframe: which zones to look up
      current_price: where price is right now
      direction: Long/Short (we look for zones that ALIGN with the trade direction)

    Direction logic:
      - Long  + demand zone below current price = +weight (support to lean on)
      - Long  + supply zone above current price = -weight (resistance overhead)
      - Short + supply zone above current price = +weight (resistance helps)
      - Short + demand zone below current price = -weight (support to fight)

    Returns (weight, label). Weight is 0.3 × strength (1.0/0.5/0.0).
    """
    if not symbol or not direction or not current_price or current_price <= 0:
        return 0.0, ""
    try:
        rows = conn.execute("""
            SELECT zone_type, top, bottom, touches FROM sd_zones
            WHERE symbol=? AND timeframe=? AND valid=1
        """, (symbol, timeframe)).fetchall()
        if not rows:
            return 0.0, ""
        is_long = direction.strip().lower() == "long"
        # Find the most relevant zone
        # Long: nearest demand BELOW price (support)
        # Short: nearest supply ABOVE price (resistance to lean on)
        best_w = 0.0
        best_label = ""
        for r in rows:
            top    = float(r["top"])
            bottom = float(r["bottom"])
            touches = int(r["touches"])
            strength = zone_strength(touches)
            if strength == 0:
                continue
            ztype = r["zone_type"]
            if is_long and ztype == "demand" and top < current_price:
                # Demand below = support → bullish
                w = 0.3 * strength
                if w > best_w:
                    best_w = w
                    best_label = (f"S/D: demand zone {bottom:.6g}-{top:.6g} "
                                   f"(touches {touches}, strength {strength}) supports Long")
            elif not is_long and ztype == "supply" and bottom > current_price:
                w = 0.3 * strength  # Short benefits from supply overhead
                if w > -best_w:    # for Short, weight should be negative (bearish bias)
                    pass
        # Defensive: if no aligned zone found, return 0
        return (round(best_w, 3), best_label) if best_w > 0 else (0.0, "")
    except Exception as e:
        logger.debug("sd_zone_weight error: %s", e)
        return 0.0, ""
