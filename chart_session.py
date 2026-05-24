"""
chart_session.py — Intraday session levels for crypto futures.

Initial Balance (IB) is the high/low established during the first hour of
the trading session. For NYSE-aligned crypto futures (where institutional
flow tends to follow US-equity open), the canonical session boundary is
14:30 UTC (NYSE open). After 60 minutes (15:30 UTC), the IB is fixed for
the day:

  - Price above IB high   = bullish breakout bias
  - Price below IB low    = bearish breakout bias
  - Price inside IB range = equilibrium / no bias

Reference: Ochoa, F. (2010), "Secrets of a Pivot Boss". The concept comes
originally from market profile (J. Peter Steidlmayer, CBOT) but is
broadly used in intraday futures strategies.

Config (env-tunable in trading/config.py):
  IB_SESSION_OPEN_UTC_HOUR   = 14   (NYSE open)
  IB_SESSION_OPEN_UTC_MINUTE = 30
  IB_DURATION_MINUTES        = 60
"""

import os
import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)

IB_SESSION_OPEN_UTC_HOUR   = int(os.environ.get("IB_SESSION_OPEN_UTC_HOUR",   "14"))
IB_SESSION_OPEN_UTC_MINUTE = int(os.environ.get("IB_SESSION_OPEN_UTC_MINUTE", "30"))
IB_DURATION_MINUTES        = int(os.environ.get("IB_DURATION_MINUTES",        "60"))

# Modifier magnitude — small (±0.2) because IB is one of many structural
# levels; it amplifies but doesn't dominate.
_IB_WEIGHT_MAGNITUDE = 0.2


def _session_open_for_now(now_utc: Optional[datetime.datetime] = None
                           ) -> datetime.datetime:
    """Compute today's session open in UTC (or yesterday's if before today's open)."""
    if now_utc is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
    today_open = now_utc.replace(
        hour=IB_SESSION_OPEN_UTC_HOUR,
        minute=IB_SESSION_OPEN_UTC_MINUTE,
        second=0, microsecond=0,
    )
    # If "now" is before today's open, use yesterday's session
    if now_utc < today_open:
        today_open -= datetime.timedelta(days=1)
    return today_open


def compute_initial_balance(df_intraday,
                              now_utc: Optional[datetime.datetime] = None
                              ) -> dict:
    """
    Compute today's (or current session's) Initial Balance from 15m or 1H candles.

    Args:
      df_intraday: pandas DataFrame with 'high', 'low', 'open_time' (or similar
                    timestamp index) — must be ≤ 1H granularity to capture IB.
      now_utc: current UTC time (default: actual now). For deterministic tests.

    Returns:
      {
        "high":     float,
        "low":      float,
        "range":    float (high - low),
        "session_open": ISO timestamp string,
        "is_complete": bool (True after IB_DURATION_MINUTES elapsed),
        "label":    str (human-readable),
      }
      Returns empty dict if data insufficient or session not yet started.
    """
    if df_intraday is None or len(df_intraday) == 0:
        return {}

    session_open = _session_open_for_now(now_utc)
    if now_utc is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)

    session_open_ms = int(session_open.timestamp() * 1000)
    ib_end_ms       = session_open_ms + IB_DURATION_MINUTES * 60 * 1000

    # Filter bars within the IB window. Try common timestamp column names.
    ts_col = None
    for cand in ("open_time", "timestamp", "ts", "time"):
        if cand in df_intraday.columns:
            ts_col = cand
            break
    if ts_col is None:
        # Fall back to assuming the index is the timestamp
        try:
            ts_series = df_intraday.index
            # Coerce to ms ints
            ts_ms = [int(getattr(t, "timestamp", lambda: 0)() * 1000) for t in ts_series]
        except Exception:
            return {}
    else:
        try:
            ts_series = df_intraday[ts_col]
            ts_ms = []
            for t in ts_series:
                if isinstance(t, (int, float)):
                    ts_ms.append(int(t) if t > 1e12 else int(t * 1000))
                else:
                    ts_ms.append(int(t.timestamp() * 1000))
        except Exception:
            return {}

    in_ib_mask = [(t >= session_open_ms and t < ib_end_ms) for t in ts_ms]
    in_ib = [i for i, m in enumerate(in_ib_mask) if m]
    if not in_ib:
        return {}

    try:
        highs = [float(df_intraday["high"].iloc[i]) for i in in_ib]
        lows  = [float(df_intraday["low"].iloc[i])  for i in in_ib]
    except (KeyError, IndexError, TypeError, ValueError):
        return {}

    ib_high = max(highs)
    ib_low  = min(lows)
    rng     = ib_high - ib_low

    # IB is complete if current time is past ib_end
    is_complete = int(now_utc.timestamp() * 1000) >= ib_end_ms

    return {
        "high":         round(ib_high, 6),
        "low":          round(ib_low, 6),
        "range":        round(rng, 6),
        "session_open": session_open.isoformat(),
        "is_complete":  is_complete,
        "label":        (f"IB {ib_low:.6g}-{ib_high:.6g} ({IB_DURATION_MINUTES}min, "
                          f"{'complete' if is_complete else 'forming'})"),
    }


def ib_alignment_weight(ib: dict, current_price: float, direction: str
                         ) -> tuple[float, str]:
    """
    Score modifier based on price position relative to today's Initial Balance.

    Logic:
      - price > IB high + Long  = +0.2 (bullish breakout confirmed by IB)
      - price > IB high + Short = -0.2 (shorting into bullish breakout)
      - price < IB low + Long   = -0.2 (longing into bearish breakout)
      - price < IB low + Short  = +0.2 (bearish breakout confirmed)
      - inside IB range         =  0   (no edge, equilibrium)

    Returns (weight, reason). Only fires if IB is_complete AND direction is set.
    """
    if not ib or not ib.get("is_complete") or not direction or not current_price:
        return 0.0, ""

    dir_lc = direction.strip().lower()
    if dir_lc not in ("long", "short"):
        return 0.0, ""
    is_long = (dir_lc == "long")

    ib_high = ib.get("high")
    ib_low  = ib.get("low")
    if ib_high is None or ib_low is None:
        return 0.0, ""

    if current_price > ib_high:
        w = _IB_WEIGHT_MAGNITUDE if is_long else -_IB_WEIGHT_MAGNITUDE
        return w, f"IB: price>IB_high + {direction} → {w:+.2f}"
    if current_price < ib_low:
        w = -_IB_WEIGHT_MAGNITUDE if is_long else _IB_WEIGHT_MAGNITUDE
        return w, f"IB: price<IB_low + {direction} → {w:+.2f}"
    # Inside IB — no signal
    return 0.0, f"IB: price inside [{ib_low:.6g}, {ib_high:.6g}] — neutral"
