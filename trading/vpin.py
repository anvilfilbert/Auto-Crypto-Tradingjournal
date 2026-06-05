"""
N-4 (Master plan Week 8): VPIN — Volume-synchronized Probability of
Informed trading (Easley, Lopez de Prado, O'Hara 2012).

VPIN is a microstructure toxicity gauge. When informed traders dominate
the flow, sustained imbalance between buyer-initiated and seller-initiated
volume builds up. A VPIN > 0.7 historically precedes flash crashes
(May 2010 NYSE) and crypto liquidation cascades. We use it as a hard
gate: when VPIN exceeds the threshold, new entries are vetoed because
the next 30-60 minutes carry abnormal cascade risk.

Implementation note (today's ship):

  This module ships the REST-poll variant of VPIN. A proper VPIN
  subscriber consumes Binance @aggTrade WebSocket frames and accumulates
  micro-buckets in real time. Today we sample the last 60min of aggTrades
  via REST every poll cycle. That's strictly worse than WebSocket
  (snapshot lag, missing buckets between polls) but it ships in one file
  and unblocks A-E (Cascade Predictor) which depends on a VPIN signal.

  Migration path: same module, swap `fetch_recent_trades(...)` for a
  callback that drains a WS queue. Schema and `vpin_for_symbol` API
  stay the same.

Algorithm (Easley et al.):

  1) Tag each trade as buy-initiated (price ≥ prior mid) or
     sell-initiated. Use the lee-Ready tick rule as fallback.
  2) Slice the trade stream into volume buckets of size V (V chosen so
     ~50 buckets/day). For BTC V≈1500 BTC; for alts we scale by 24h vol.
  3) Per bucket: |Buy_vol - Sell_vol| / V  → "order-flow imbalance".
  4) VPIN = mean of imbalances over the last N=50 buckets.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

_log = logging.getLogger(__name__)

_BINANCE_AGG_URL = "https://fapi.binance.com/fapi/v1/aggTrades"
_DEFAULT_BUCKETS = int(os.environ.get("FUTURES_AI_VPIN_BUCKETS", "50"))
_VPIN_VETO_THRESHOLD = float(os.environ.get("FUTURES_AI_VPIN_VETO", "0.70"))


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vpin_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            vpin REAL NOT NULL,
            n_buckets INTEGER NOT NULL,
            bucket_volume REAL NOT NULL,
            sample_n_trades INTEGER NOT NULL,
            window_minutes INTEGER NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vpin_symbol_ts ON vpin_snapshot(symbol, ts DESC)")
    conn.commit()


def _fetch_aggtrades(symbol: str, lookback_minutes: int = 60,
                     limit: int = 1000) -> list[dict]:
    """Pull last N minutes of aggTrades. Binance caps to 1h history via
    startTime+endTime; for longer windows we'd need ID-based paging. 1h
    is the right window for VPIN anyway.
    """
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(lookback_minutes) * 60 * 1000
    try:
        r = requests.get(_BINANCE_AGG_URL, params={
            "symbol": symbol.replace("USDT", "USDT"),
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": min(int(limit), 1000),
        }, timeout=10)
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        _log.warning("VPIN fetch %s failed: %s", symbol, e)
        return []


def _classify_trade_side(prev_price: float | None, trade: dict) -> str:
    """Lee-Ready tick rule: trade.maker_is_buyer=True → seller-initiated."""
    is_maker_buyer = trade.get("m", False)
    return "sell" if is_maker_buyer else "buy"


def _bucket_volume(symbol: str, lookback_minutes: int) -> float:
    """Pick bucket size V so we get ~50 buckets in the window.
    Total_vol / 50. Fall back to a sensible per-symbol default.
    """
    trades = _fetch_aggtrades(symbol, lookback_minutes, limit=1000)
    if not trades:
        return 0.0
    total_vol = sum(float(t["q"]) for t in trades)
    return max(total_vol / _DEFAULT_BUCKETS, 0.001)


def _vpin_from_trades(trades: list[dict], bucket_v: float) -> tuple[float, int]:
    """Walk trades chronologically, accumulate into volume buckets, sum
    |buy - sell| per bucket / bucket_v, average the latest N buckets.
    """
    if not trades or bucket_v <= 0:
        return 0.0, 0
    imbalances = []
    cur_buy = cur_sell = 0.0
    prev_price = None
    for t in trades:
        try:
            price = float(t["p"]); qty = float(t["q"])
        except (KeyError, ValueError):
            continue
        side = _classify_trade_side(prev_price, t)
        if side == "buy":
            cur_buy += qty
        else:
            cur_sell += qty
        prev_price = price
        if cur_buy + cur_sell >= bucket_v:
            # Clip at 1.0 — VPIN is bounded [0,1] by definition (proportion
            # of imbalance). A single oversized trade can push cur_buy+cur_sell
            # past bucket_v before the flush check, which would make the raw
            # ratio exceed 1.0 and inflate the average above its theoretical
            # max. Clipping preserves the math intent without re-bucketing.
            imbalance = abs(cur_buy - cur_sell) / bucket_v
            imbalances.append(min(imbalance, 1.0))
            cur_buy = cur_sell = 0.0
    if not imbalances:
        return 0.0, 0
    # Average over last N=min(50, len) buckets
    sample = imbalances[-min(len(imbalances), _DEFAULT_BUCKETS):]
    return sum(sample) / len(sample), len(sample)


def compute_vpin(symbol: str, lookback_minutes: int = 60) -> dict[str, Any]:
    """Public entry-point. Returns:
       {symbol, vpin, n_buckets, bucket_volume, sample_n_trades, window_minutes, ok}
    """
    trades = _fetch_aggtrades(symbol, lookback_minutes, limit=1000)
    if not trades:
        return {"symbol": symbol, "ok": False, "reason": "no trades",
                "vpin": None, "n_buckets": 0,
                "bucket_volume": 0.0, "sample_n_trades": 0,
                "window_minutes": lookback_minutes}
    bucket_v = max(sum(float(t["q"]) for t in trades) / _DEFAULT_BUCKETS, 0.001)
    vpin, n_buckets = _vpin_from_trades(trades, bucket_v)
    return {
        "symbol":          symbol,
        "ok":              True,
        "vpin":            round(vpin, 4),
        "n_buckets":       n_buckets,
        "bucket_volume":   round(bucket_v, 6),
        "sample_n_trades": len(trades),
        "window_minutes":  lookback_minutes,
        "ts":              time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def snapshot(conn, symbols: list[str]) -> list[dict]:
    """Compute VPIN for each symbol and persist to vpin_snapshot."""
    _ensure_table(conn)
    out = []
    for sym in symbols:
        result = compute_vpin(sym)
        if not result.get("ok"):
            continue
        try:
            conn.execute(
                "INSERT INTO vpin_snapshot "
                "(ts, symbol, vpin, n_buckets, bucket_volume, sample_n_trades, window_minutes) "
                "VALUES (?,?,?,?,?,?,?)",
                (result["ts"], sym, result["vpin"], result["n_buckets"],
                 result["bucket_volume"], result["sample_n_trades"],
                 result["window_minutes"])
            )
        except Exception as e:
            _log.warning("VPIN persist %s failed: %s", sym, e)
        out.append(result)
    conn.commit()
    return out


def latest_for_symbol(conn, symbol: str, max_age_minutes: int = 30) -> dict | None:
    """Most recent vpin reading for symbol (if fresh enough)."""
    _ensure_table(conn)
    row = conn.execute(
        "SELECT ts, vpin, n_buckets, bucket_volume, sample_n_trades, window_minutes "
        "FROM vpin_snapshot WHERE symbol=? "
        f"AND ts >= datetime('now', '-{int(max_age_minutes)} minutes') "
        "ORDER BY ts DESC LIMIT 1",
        (symbol,)
    ).fetchone()
    if not row:
        return None
    return {"ts": row[0], "vpin": row[1], "n_buckets": row[2],
            "bucket_volume": row[3], "sample_n_trades": row[4],
            "window_minutes": row[5]}


def vpin_veto(conn, symbol: str) -> tuple[bool, str, float | None]:
    """Should we veto a new entry for this symbol on VPIN grounds?

    Returns (veto, reason, vpin_value).
    """
    snap = latest_for_symbol(conn, symbol, max_age_minutes=30)
    if not snap:
        return (False, "no recent VPIN snapshot", None)
    v = snap.get("vpin")
    if v is None:
        return (False, "VPIN n/a", None)
    if v >= _VPIN_VETO_THRESHOLD:
        return (True, f"VPIN {v:.2f} ≥ veto threshold {_VPIN_VETO_THRESHOLD:.2f} "
                       f"(n_buckets={snap.get('n_buckets')})", v)
    return (False, f"VPIN {v:.2f} below veto threshold", v)
