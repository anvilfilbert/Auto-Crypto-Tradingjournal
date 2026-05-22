"""
mfe_mae.py — Maximum Favorable / Adverse Excursion calculator.

For a closed position, fetch the price history between open_time and
close_time and compute:
  - mfe_price : best price (highest for Long, lowest for Short)
  - mae_price : worst price (lowest for Long, highest for Short)
  - mfe_pct   : MFE as % move from entry in the favorable direction
  - mae_pct   : MAE as % move from entry in the adverse direction
                (always negative — represents drawdown depth)

Used to measure TP/SL placement quality:
  - If avg MFE is much higher than realized return → TPs too tight
  - If avg MAE is much deeper than realized loss → SLs too tight (got out
    just before reversal) or trades took unnecessary heat before winning

Granularity: 1H candles. For sub-hour trades the function falls back to
returning None — those trades are too short to measure meaningfully.
"""
import datetime as _dt
from typing import Optional, Tuple

from chart_candles import get_candles_at_time


def _to_ms(iso_str: str) -> Optional[int]:
    """Convert ISO datetime (assumed UTC) to Unix ms. None on parse failure."""
    try:
        s = (iso_str or "").strip()
        if not s:
            return None
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00")[:19])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def compute(symbol: str, direction: str, entry_price: float,
            open_time: str, close_time: str
            ) -> Optional[Tuple[float, float, float, float]]:
    """
    Returns (mfe_price, mae_price, mfe_pct, mae_pct) or None if the trade
    is too short or the candle fetch failed.

    mfe_pct is signed positive (favorable). mae_pct is signed negative
    (adverse). For Long, MFE uses candle highs / MAE uses lows; reversed
    for Short.
    """
    if not entry_price or entry_price <= 0:
        return None

    open_ms  = _to_ms(open_time)
    close_ms = _to_ms(close_time)
    if open_ms is None or close_ms is None:
        return None

    duration_min = (close_ms - open_ms) / 60_000
    if duration_min < 60:
        return None   # sub-hour trade — 1H granularity can't resolve it

    # Fetch 1H candles ending at close_time. Limit covers up to ~8 days
    # which is plenty for the longest swing trades we run.
    df = get_candles_at_time(symbol, "1H", close_ms, limit=200)
    if df is None or df.empty:
        return None

    # Keep only candles inside the trade window. Candle timestamp is the
    # *open* of the bar so we include bars whose open is between open_ms
    # (exclusive) and close_ms (inclusive) — gives us only the bars the
    # position was actually open for.
    df = df[(df["timestamp"] >= open_ms) & (df["timestamp"] <= close_ms)]
    if df.empty:
        return None

    is_long = (direction or "").strip().lower() == "long"

    if is_long:
        mfe_price = float(df["high"].max())
        mae_price = float(df["low"].min())
        mfe_pct = (mfe_price - entry_price) / entry_price * 100.0
        mae_pct = (mae_price - entry_price) / entry_price * 100.0
    else:
        mfe_price = float(df["low"].min())
        mae_price = float(df["high"].max())
        mfe_pct = (entry_price - mfe_price) / entry_price * 100.0
        mae_pct = (entry_price - mae_price) / entry_price * 100.0

    return (round(mfe_price, 6), round(mae_price, 6),
            round(mfe_pct, 2),  round(mae_pct, 2))


def update_position(conn, position_id: int) -> bool:
    """
    Compute MFE/MAE for the given position and write all 4 fields.
    Returns True on success. No-op if any field is already populated.
    """
    row = conn.execute(
        "SELECT symbol, direction, entry_price, open_time, close_time, mfe_pct "
        "FROM positions WHERE id=?", (position_id,)
    ).fetchone()
    if not row:
        return False
    if row["mfe_pct"] is not None:
        return False   # already done — idempotent

    result = compute(row["symbol"], row["direction"], row["entry_price"],
                     row["open_time"], row["close_time"])
    if not result:
        return False

    mfe_p, mae_p, mfe_pct, mae_pct = result
    conn.execute(
        "UPDATE positions SET mfe_price=?, mae_price=?, mfe_pct=?, mae_pct=? "
        "WHERE id=?",
        (mfe_p, mae_p, mfe_pct, mae_pct, position_id),
    )
    return True
