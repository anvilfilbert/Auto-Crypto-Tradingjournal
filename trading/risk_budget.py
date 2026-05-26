"""
trading.risk_budget — per-trade sizing.

Given a setup (score, entry, SL) + the trader's current equity, compute
the notional size + leverage that bounds loss to the configured risk %.

Formula:
  risk_dollars = equity × RISK_PER_TRADE_PCT × score_mult × streak_mult
  sl_distance_pct = |entry - SL| / entry
  notional = risk_dollars / sl_distance_pct
  notional = min(notional, max(MAX_NOTIONAL_USDT, equity × MAX_NOTIONAL_PCT))
  leverage = min(MAX_LEVERAGE, ceil(notional / margin_available))

Profit Compounding Strategy (2026-05-23) additions:
  - score_mult — unchanged: 1.0×/1.5×/2.0× by setup score
  - streak_mult — NEW: 1× base, multiplied by N consecutive wins since
    last loss / breaker reset (capped at MAX_STREAK_MULTIPLIER)
  - dynamic notional cap = max(fixed_floor, equity × MAX_NOTIONAL_PCT)
    so position size grows naturally as the book compounds

Returns None when the setup can't be sized (SL too tight, score too low,
math degenerate, etc.) — caller treats None as "skip this signal".
"""
from __future__ import annotations

import math
from typing import Optional

from . import config


def _consecutive_wins(conn=None) -> int:
    """
    Count of consecutive auto-trader WINNERS up to the most recent close.
    Mirrors kill_switch._consecutive_losses but inverted. Honors the
    operator-initiated breaker_reset_at stamp — only counts trades closed
    AFTER the reset, so the streak builds fresh from each operator
    override.

    Used by the Profit Compounding Strategy progression: each consecutive
    win since the last loss/reset multiplies the per-trade risk budget.
    Returns 0 if no closes, no DB access, or the most recent close was a
    loss (which is what resets the progression to base risk).
    """
    if conn is None:
        from database import db_conn
        with db_conn() as c:
            return _consecutive_wins(c)
    reset_at = config.breaker_reset_at(conn)
    try:
        if config.is_real_mode():
            if reset_at:
                rows = conn.execute(
                    "SELECT realized_pnl FROM positions "
                    "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0)"
                    "AND close_time IS NOT NULL AND close_time != '' "
                    "AND close_time > ? "
                    "ORDER BY close_time DESC LIMIT 10",
                    (reset_at,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT realized_pnl FROM positions "
                    "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) AND close_time IS NOT NULL AND close_time != '' "
                    "ORDER BY close_time DESC LIMIT 10"
                ).fetchall()
        else:
            if reset_at:
                rows = conn.execute(
                    "SELECT realized_pnl FROM paper_positions "
                    "WHERE status='closed' AND closed_at > ? "
                    "ORDER BY closed_at DESC LIMIT 10",
                    (reset_at,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT realized_pnl FROM paper_positions "
                    "WHERE status='closed' "
                    "ORDER BY closed_at DESC LIMIT 10"
                ).fetchall()
        n = 0
        for (pnl,) in rows:
            if (pnl or 0) > 0:    # strict — breakeven (0) does NOT extend streak
                n += 1
            else:
                break
        return n
    except Exception:
        return 0


def _streak_multiplier(wins: int) -> float:
    """Streak-based risk multiplier — two opposing modes, operator picks via
    config.STREAK_MODE (env or runtime UI):

      "compound" (default):  risk GROWS with wins (Profit Compounding)
        streak 0/1 → 1.0×, streak 2 → 2.0×, …, capped at MAX_STREAK_MULTIPLIER

      "euphoria_dampener" (Feature 10, Douglas):  risk SHRINKS after 3+ wins
        streak 0-2 → 1.0× (normal), streak 3+ → EUPHORIA_SIZE_MULT (0.75×)

      "off": always 1.0× (operator wants neither compounding nor dampening)

    A loss resets the streak → multiplier back to 1.0×.
    """
    mode = (config.streak_mode() or "compound").lower()
    if mode == "off" or wins < 2:
        return 1.0
    if mode == "euphoria_dampener":
        if wins >= config.EUPHORIA_CAP_WINS:
            return float(config.EUPHORIA_SIZE_MULT)
        return 1.0
    # default: compound
    if not config.COMPOUND_STREAK_ENABLED:
        return 1.0
    return float(min(int(wins), config.MAX_STREAK_MULTIPLIER))


def _effective_notional_cap(equity_usdt: float) -> float:
    """Dynamic notional cap — grows with equity per Profit Compounding.
    Floor of MAX_NOTIONAL_USDT so small accounts still get a tradeable
    minimum size."""
    return max(config.MAX_NOTIONAL_USDT,
                equity_usdt * config.MAX_NOTIONAL_PCT)


# ── Volatility-aware sizing dampener ─────────────────────────────────────────
# Per-asset ATR(14) on 4H as the volatility proxy. Reference ATR is the
# median across the major-symbol watchlist (sampled occasionally).
# When current asset's ATR% > reference × VOL_OUTLIER_RATIO, the position
# shrinks by VOL_DAMPENER_FLOOR so a single high-vol asset doesn't
# dominate basket risk. Cached 5 min per symbol to keep size_trade fast.
import time as _time_mod
import os as _os

VOL_DAMPENER_ENABLED   = bool(int(_os.environ.get("FUTURES_AI_VOL_DAMPENER_ENABLED", "1")))
VOL_REFERENCE_ATR_PCT  = float(_os.environ.get("FUTURES_AI_VOL_REFERENCE_ATR_PCT",  "3.0"))  # BTC 4H ATR% baseline
VOL_OUTLIER_RATIO      = float(_os.environ.get("FUTURES_AI_VOL_OUTLIER_RATIO",       "1.5"))
VOL_DAMPENER_FLOOR     = float(_os.environ.get("FUTURES_AI_VOL_DAMPENER_FLOOR",      "0.5"))  # don't shrink past 50%
_VOL_CACHE: dict = {}      # symbol → (timestamp, atr_pct)
_VOL_CACHE_TTL = 300       # 5 minutes


def _get_asset_atr_pct(symbol: str) -> Optional[float]:
    """Fetch 4H ATR% for a symbol, cached 5min. Returns None on error."""
    if not symbol:
        return None
    now = _time_mod.time()
    cached = _VOL_CACHE.get(symbol)
    if cached and (now - cached[0]) < _VOL_CACHE_TTL:
        return cached[1]
    try:
        # Import lazily to avoid loading chart stack at module-import time
        from chart_context import get_chart_context
        ctx = get_chart_context(symbol, ["4H"])
        atr = ((ctx.get("4H") or {}).get("indicators") or {}).get("atr") or {}
        atr_pct = atr.get("pct")
        if atr_pct is None:
            return None
        atr_pct = float(atr_pct)
        _VOL_CACHE[symbol] = (now, atr_pct)
        return atr_pct
    except Exception:
        return None


def _vol_dampener(symbol: str) -> tuple[float, str]:
    """
    Per-asset volatility-aware sizing multiplier.

    When asset's 4H ATR% is significantly above the reference baseline,
    shrink position size proportionally. Caps at VOL_DAMPENER_FLOOR.

    Returns (multiplier, reason). On error or disabled: (1.0, "").

    Examples (VOL_REFERENCE_ATR_PCT=3.0, VOL_OUTLIER_RATIO=1.5):
      asset ATR% = 3.0 → ratio=1.0 → no dampening (mult 1.0)
      asset ATR% = 4.5 → ratio=1.5 → just at threshold (mult ≈ 1.0)
      asset ATR% = 6.0 → ratio=2.0 → mult = 1.5/2.0 = 0.75
      asset ATR% = 12  → ratio=4.0 → would be 0.375, floored to 0.5
    """
    if not VOL_DAMPENER_ENABLED:
        return 1.0, ""
    atr_pct = _get_asset_atr_pct(symbol)
    if atr_pct is None or atr_pct <= 0:
        return 1.0, "vol_dampener: ATR unavailable, no adjustment"
    ratio = atr_pct / VOL_REFERENCE_ATR_PCT
    if ratio <= VOL_OUTLIER_RATIO:
        return 1.0, ""
    # Outlier — scale down. mult = VOL_OUTLIER_RATIO / ratio, floored
    mult = max(VOL_DAMPENER_FLOOR, VOL_OUTLIER_RATIO / ratio)
    return round(mult, 3), (
        f"vol_dampener: ATR% {atr_pct:.2f} vs ref {VOL_REFERENCE_ATR_PCT:.1f} "
        f"(ratio {ratio:.2f}×) → size ×{mult:.2f}"
    )


def _drawdown_dampener(equity_usdt: float) -> tuple[float, str]:
    """
    Graduated drawdown response (Bear Market Strategy Ch 8).

    Returns (risk_multiplier, reason). Scales DOWN risk as total
    drawdown grows, BEFORE the binary breakers trip. Smooths the
    transition between "trade normally" and "force-stop":

      0   to -5%   total DD → 1.00× (normal)
      -5  to -10%  total DD → 0.75× (caution: review trades)
      -10 to -15%  total DD → 0.50× (warning: pause aggressive setups)
      below -15%               → kill_switch breaker handles it

    The breaker (-15% TOTAL_DD_BREAKER_PCT) still trips as a hard stop.
    This function only graduates the path TO the breaker, giving more
    runway to recover before being force-flat.
    """
    start_eq = config.starting_equity()
    if start_eq <= 0:
        return 1.0, ""
    dd_pct = (equity_usdt - start_eq) / start_eq    # negative when below start
    if dd_pct >= -0.05:
        return 1.0, ""
    if dd_pct >= -0.10:
        return 0.75, f"DD {dd_pct*100:.1f}% in 5-10% zone — risk ×0.75 (caution)"
    if dd_pct >= -0.15:
        return 0.50, f"DD {dd_pct*100:.1f}% in 10-15% zone — risk ×0.50 (warning)"
    # below -15% — breaker should already have tripped, but if not, lock to minimum
    return 0.25, f"DD {dd_pct*100:.1f}% past 15% danger zone — risk ×0.25 (crisis)"


def size_trade(score: int, entry: float, sl: float,
               equity_usdt: Optional[float] = None,
               conn=None,
               symbol: Optional[str] = None,
               opus_score: Optional[int] = None) -> Optional[dict]:
    """
    Returns sizing dict or None if not sizeable.
      {
        notional_usdt: float, leverage: int, risk_usdt: float,
        sl_distance_pct: float, score_multiplier: float,
        streak_multiplier: float, win_streak: int,
        effective_cap_usdt: float, capped: bool,
        sizing_tier: "full"|"half"
      }

    2026-05-26 — tiered Opus sizing (Phase 1):
      opus_score >= 6 → tier="full" — normal 2% risk
      opus_score == 5 → tier="half" — 1% risk (half notional)
    Phase 2 (deferred): DCA averaging at -0.5% from entry — needs state-
    machine tracking, separate design session.
    """
    if score < min(config.RISK_SCORE_MULTIPLIERS):
        return None
    try:
        entry = float(entry); sl = float(sl)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or sl <= 0 or entry == sl:
        return None

    eq = equity_usdt if equity_usdt is not None else config.starting_equity()
    if eq <= 0:
        return None

    score = max(min(int(score), 10), 0)
    score_mult = config.RISK_SCORE_MULTIPLIERS.get(score,
            max(config.RISK_SCORE_MULTIPLIERS.values()) if score > 10 else 0)
    if score_mult <= 0:
        return None

    # Profit Compounding Strategy — streak progression
    wins = _consecutive_wins(conn)
    streak_mult = _streak_multiplier(wins)

    # Bear Market Strategy — graduated drawdown dampener. Scales risk
    # DOWN as equity bleeds toward the breaker. Runs BEFORE the breaker
    # check so we glide into safety rather than slam into it.
    dd_mult, dd_reason = _drawdown_dampener(eq)

    # Per-asset volatility-aware sizing — shrink notional on high-vol assets
    # so one outlier doesn't blow basket risk. Cached 5min per symbol.
    vol_mult, vol_reason = _vol_dampener(symbol or "")

    risk_dollars = (eq * config.RISK_PER_TRADE_PCT * score_mult
                    * streak_mult * dd_mult * vol_mult)
    sl_dist_pct = abs(entry - sl) / entry
    if sl_dist_pct < 0.002:   # SL closer than 0.2% — would be unreasonable lev
        return None

    notional_raw = risk_dollars / sl_dist_pct
    cap = _effective_notional_cap(eq)
    notional = min(notional_raw, cap)

    # ── Tiered Opus sizing (2026-05-26 Phase 1) ─────────────────────────────
    # When Opus consensus grade is marginal (=5), halve the position so the
    # SL-hit loss is ~1% of equity instead of ~2%. Opus 6+ trades stay at
    # full size. Phase 2 will add DCA averaging at -0.5% from entry — that
    # mechanic is deferred (state-machine + monitoring complexity).
    sizing_tier = "full"
    if opus_score is not None and opus_score <= 5:
        notional = notional * 0.5
        risk_dollars = risk_dollars * 0.5
        sizing_tier = "half"

    # Leverage policy (2026-05-26): operator preference is to always use
    # MAX_LEVERAGE (currently 10×) regardless of notional/equity ratio.
    # Rationale: identical risk per trade (risk = notional × SL distance)
    # but smaller margin lock-up. Downside: liquidation distance tighter
    # but the pre-placed SL fires well before liquidation.
    lev = config.MAX_LEVERAGE

    return {
        "notional_usdt":      round(notional, 2),
        "leverage":           lev,
        "risk_usdt":          round(min(risk_dollars, notional * sl_dist_pct), 2),
        "sl_distance_pct":    round(sl_dist_pct * 100, 2),
        "score_multiplier":   score_mult,
        "streak_multiplier":  streak_mult,
        "win_streak":         wins,
        "dd_dampener":        round(dd_mult, 2),
        "dd_dampener_reason": dd_reason,
        "vol_dampener":       round(vol_mult, 3),
        "vol_dampener_reason": vol_reason,
        "effective_cap_usdt": round(cap, 2),
        "capped":             notional_raw > cap,
        "sizing_tier":        sizing_tier,
        "opus_score":         opus_score,
    }
