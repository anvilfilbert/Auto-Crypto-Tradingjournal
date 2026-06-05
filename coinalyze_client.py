"""
coinalyze_client.py — Coinalyze derivatives data (Phase 2: multi-exchange).

OI, liquidations, and 24h liquidation trend now sum across all major perp
venues (`_MAJOR_PERP_EXCHANGES`) that list this base/USDT pair, discovered
once per hour from `/future-markets`. funding-by-exchange spread now spans
the same ~11 venues instead of the original 3 hardcoded ones.

Coinalyze symbol codes confirmed 2026-05-29:
  A=Binance, 6=Bybit, 3=OKX, 4=Huobi, 0=BitMEX, F=Bitfinex,
  Y=Gate.io, W=WOO X, S=Aster, H=Hyperliquid, 8=dYdX.

`get_funding_rate` and `get_long_short_ratio` stay Binance-only —
only Binance + Bybit report L/S; the headline funding rate is the
single-venue Binance number used for the sentiment label.

API docs: https://api.coinalyze.net/v1/doc/
Rate limit: 40 requests/minute (free tier)

Confirmed API response shapes (tested 2026-05-15, units re-verified 2026-05-29):
  open-interest:
    [{"symbol": "BTCUSDT_PERP.A", "value": 102168.237, "update": 1778869122575}]
    → "value" is OI in BASE_ASSET units (coins), per future-markets metadata
      `oi_lq_vol_denominated_in: 'BASE_ASSET'`. Verified empirically against
      Binance's `openInterest` field — identical numbers for BTC/ETH/ZEC/SOL.

  funding-rate:
    [{"symbol": "...", "value": <rate_float>, "update": <ms_timestamp>}]
    → "value" is the funding rate (e.g. 0.0001 = 0.01%)

  long-short-ratio:
    [{"symbol": "...", "value": <ratio_float>, "update": <ms_timestamp>}]
    → "value" is long/short ratio (>1 means more longs)

  liquidation-history:
    [{"symbol": "...", "t": <ms>, "l": <long_liq_usd>, "s": <short_liq_usd>}, ...]
    → OHLCV-style array; "l" = long liquidations USD, "s" = short liquidations USD
    → Requires "from" and "to" ms timestamps + valid interval
    → Valid intervals: 1min, 3min, 5min, 15min, 30min, 1hour, 2hour, 4hour, 6hour, 12hour, daily, weekly
"""
import os
import time
import urllib.request
import json
import logging
import threading
from typing import Optional

_log = logging.getLogger(__name__)

_BASE = "https://api.coinalyze.net/v1"
_API_KEY = os.environ.get("COINALYZE_API_KEY", "")
_TIMEOUT = 10

# Curated list of major perpetual venues for multi-exchange aggregation
# (Phase 2, 2026-05-29). Codes per Coinalyze /v1/exchanges; mapped 2026-05-29.
_MAJOR_PERP_EXCHANGES = {
    "A": "Binance",
    "6": "Bybit",
    "3": "OKX",
    "4": "Huobi",
    "0": "BitMEX",
    "F": "Bitfinex",
    "Y": "Gate.io",
    "W": "WOO X",
    "S": "Aster",
    "H": "Hyperliquid",
    "8": "dYdX",
}

# /future-markets cache for symbol discovery across exchanges.
# Structure: {base_asset: {exchange_code: full_coinalyze_symbol}}
_markets_index: dict = {}
_markets_index_ts: float = 0.0
_markets_index_lock = threading.Lock()
_MARKETS_TTL = 3600  # 1 hour


def is_configured() -> bool:
    """Return True if the Coinalyze API key is set."""
    return bool(_API_KEY)


def _symbol(trading_pair: str) -> str:
    """Convert 'BTCUSDT' → 'BTCUSDT_PERP.A' (Binance perpetual on Coinalyze)."""
    pair = trading_pair.upper()
    if pair.endswith("_PERP.A"):
        return pair
    if not pair.endswith("USDT"):
        pair = pair + "USDT"
    return pair + "_PERP.A"


def _get(path: str, params: dict) -> dict | list | None:
    """Single authenticated GET. Returns parsed JSON or None on error."""
    if not _API_KEY:
        _log.warning("COINALYZE_API_KEY not set")
        return None
    params["api_key"] = _API_KEY
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_BASE}/{path}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TradingJournal/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception as e:
        _log.debug("Coinalyze %s failed: %s", path, e)
        return None


def _refresh_market_index() -> None:
    """Populate _markets_index from /future-markets. USDT-quoted perps only."""
    global _markets_index, _markets_index_ts
    data = _get("future-markets", {})
    if not isinstance(data, list):
        return
    idx: dict = {}
    for m in data:
        if not isinstance(m, dict):
            continue
        if not m.get("is_perpetual"):
            continue
        if (m.get("quote_asset") or "").upper() != "USDT":
            continue
        base = (m.get("base_asset") or "").upper()
        ex_code = m.get("exchange") or ""
        sym = m.get("symbol") or ""
        if not base or not ex_code or not sym:
            continue
        if ex_code not in _MAJOR_PERP_EXCHANGES:
            continue
        idx.setdefault(base, {})[ex_code] = sym
    _markets_index = idx
    _markets_index_ts = time.time()


def _symbols_for_base(base_asset: str) -> dict[str, str]:
    """Return {exchange_code: full_symbol} for major perps trading `base_asset`/USDT."""
    base = (base_asset or "").upper().replace("USDT", "").replace("_PERP.A", "")
    if not base:
        return {}
    with _markets_index_lock:
        if not _markets_index or (time.time() - _markets_index_ts) > _MARKETS_TTL:
            _refresh_market_index()
        return dict(_markets_index.get(base, {}))


def get_open_interest(symbol: str) -> dict:
    """
    Aggregated open interest across major perpetual venues.

    Sums OI in BASE_ASSET coins for every major exchange (per
    `_MAJOR_PERP_EXCHANGES`) where a USDT perp exists for this base.
    All amounts are in coins — Coinalyze's `oi_lq_vol_denominated_in:
    'BASE_ASSET'` flag — so cross-exchange summation is unit-safe.

    Returns {
      "oi_coins":         float,    # total across all reporting venues
      "oi_by_exchange":   {name: coins},
      "exchange_count":   int,
      "oi_symbol":        str,       # representative symbol (Binance _PERP.A)
    } or {}.
    """
    syms = _symbols_for_base(symbol)
    if not syms:
        # Fall back to Binance-only if discovery failed
        sym = _symbol(symbol)
        data = _get("open-interest", {"symbols": sym})
        if not data:
            return {}
        rec = data[0] if isinstance(data, list) and data else {}
        v = float(rec.get("value") or 0)
        if v == 0:
            return {}
        return {"oi_coins": round(v, 3), "oi_by_exchange": {"Binance": round(v, 3)},
                "exchange_count": 1, "oi_symbol": sym}

    data = _get("open-interest", {"symbols": ",".join(syms.values())})
    try:
        if not data:
            return {}
        by_ex: dict[str, float] = {}
        for rec in (data if isinstance(data, list) else [data]):
            sym = rec.get("symbol", "")
            code = sym.rsplit(".", 1)[-1] if "." in sym else ""
            ex_name = _MAJOR_PERP_EXCHANGES.get(code)
            if not ex_name:
                continue
            v = float(rec.get("value") or 0)
            if v > 0:
                by_ex[ex_name] = round(v, 3)
        if not by_ex:
            return {}
        total = sum(by_ex.values())
        return {
            "oi_coins":       round(total, 3),
            "oi_by_exchange": by_ex,
            "exchange_count": len(by_ex),
            "oi_symbol":      syms.get("A", _symbol(symbol)),
        }
    except Exception:
        return {}


def get_open_interest_history(symbol: str, hours: int = 4) -> dict:
    """
    Aggregated OI change over the last `hours` across major perp venues.

    For each per-exchange series, takes first_open and last_close, then sums
    across exchanges. The % change is computed on the aggregated totals so a
    flat Binance + rising Bybit shows the true cross-venue flow.

    Returns {"oi_change_pct": float, "oi_then": float, "oi_now": float,
             "exchange_count": int} or {}.
    """
    if hours < 1:
        return {}
    # Coinalyze *-history endpoints all use unix SECONDS. The earlier comment
    # claiming liquidation-history was ms-based was wrong — re-verified
    # 2026-05-29 empirically: ms timestamps return [] silently, seconds work.
    now_s  = int(time.time())
    from_s = now_s - (hours * 3600)
    syms = _symbols_for_base(symbol) or {"A": _symbol(symbol)}
    data = _get("open-interest-history", {
        "symbols":  ",".join(syms.values()),
        "interval": "1hour",
        "from":     from_s,
        "to":       now_s,
    })
    try:
        if not data:
            return {}
        records = data if isinstance(data, list) else [data]
        sum_open = 0.0
        sum_close = 0.0
        ex_count = 0
        for rec in records:
            if not isinstance(rec, dict):
                continue
            bars = rec.get("history") or []
            if not bars:
                continue
            bars = sorted(bars, key=lambda b: b.get("t", 0))
            o = float(bars[0].get("o") or bars[0].get("open") or 0)
            c = float(bars[-1].get("c") or bars[-1].get("close") or 0)
            if o <= 0 or c <= 0:
                continue
            sum_open += o
            sum_close += c
            ex_count += 1
        if sum_open <= 0 or sum_close <= 0:
            return {}
        change_pct = (sum_close - sum_open) / sum_open * 100.0
        return {
            "oi_change_pct":  round(change_pct, 3),
            "oi_then":        round(sum_open, 3),
            "oi_now":         round(sum_close, 3),
            "exchange_count": ex_count,
        }
    except Exception:
        return {}


def get_liquidations(symbol: str, lookback_hours: int = 1) -> dict:
    """
    Aggregated liquidation volume over the last `lookback_hours` across major
    perp venues. Values in BASE_ASSET coins.

    Returns {"liq_long_coins": float, "liq_short_coins": float,
             "liq_total_coins": float, "exchange_count": int} or {}.
    """
    now_s = int(time.time())
    from_s = now_s - (lookback_hours * 3600)
    syms = _symbols_for_base(symbol) or {"A": _symbol(symbol)}
    data = _get("liquidation-history", {
        "symbols":  ",".join(syms.values()),
        "interval": "1hour",
        "from":     from_s,
        "to":       now_s,
    })
    try:
        if not data:
            return {}
        records = data if isinstance(data, list) else [data]
        long_liq = 0.0
        short_liq = 0.0
        ex_count = 0
        for rec in records:
            if not isinstance(rec, dict):
                continue
            bars = rec.get("history") or []
            if not bars:
                continue
            sub_long = sum(float(b.get("l") or 0) for b in bars)
            sub_short = sum(float(b.get("s") or 0) for b in bars)
            if sub_long > 0 or sub_short > 0:
                ex_count += 1
            long_liq += sub_long
            short_liq += sub_short
        if long_liq == 0 and short_liq == 0:
            return {}
        return {
            "liq_long_coins":  round(long_liq, 4),
            "liq_short_coins": round(short_liq, 4),
            "liq_total_coins": round(long_liq + short_liq, 4),
            "exchange_count": ex_count,
        }
    except Exception:
        return {}


def get_funding_rate(symbol: str) -> dict:
    """
    Current aggregated funding rate across all exchanges.

    Response shape: [{"symbol": "...", "value": <rate>, "update": <ms>}]
    "value" is the funding rate float (e.g. 0.0001 = 0.01% per 8h)

    Returns {"rate": float, "annualized_pct": float, "sentiment": str} or {}.
    """
    data = _get("funding-rate", {"symbols": _symbol(symbol)})
    try:
        if not data:
            return {}
        record = data[0] if isinstance(data, list) else data
        # Confirmed field: "value" = funding rate float
        # Keep None as None — 0.0 means balanced longs/shorts, None means no data
        raw = record.get("value") if record.get("value") is not None else record.get("fundingRate") if record.get("fundingRate") is not None else record.get("r")
        if raw is None:
            return {}
        rate = float(raw)
        ann = round(rate * 3 * 365 * 100, 2)  # 8h payments × 3/day × 365 days
        if rate > 0.0005:
            sentiment = "longs_paying_heavily"
        elif rate > 0.0001:
            sentiment = "longs_paying"
        elif rate < -0.0001:
            sentiment = "shorts_paying"
        else:
            sentiment = "neutral"
        return {"rate": rate, "annualized_pct": ann, "sentiment": sentiment}
    except Exception:
        return {}


def get_long_short_ratio(symbol: str) -> dict:
    """
    Long/short account ratio on Binance perp (latest 1h bar).

    Coinalyze response shape (verified 2026-05-29):
      [{"symbol": "...", "history": [{"t": <s>, "r": <ratio>,
                                      "l": <long_pct>, "s": <short_pct>}, ...]}]
    "r" > 1 means more long accounts than short. Plain `/long-short-ratio`
    endpoint (without `-history`) returns 404 — was broken from inception
    until 2026-05-29.

    Returns {"ratio": float, "longs_pct": float, "shorts_pct": float} or {}.
    """
    now_s  = int(time.time())
    from_s = now_s - 3600
    data = _get("long-short-ratio-history", {
        "symbols":  _symbol(symbol),
        "interval": "1hour",
        "from":     from_s,
        "to":       now_s,
    })
    try:
        if not data:
            return {}
        records = data if isinstance(data, list) else [data]
        bars = (records[0].get("history")
                if records and isinstance(records[0], dict) and "history" in records[0]
                else records)
        if not bars:
            return {}
        latest = sorted(bars, key=lambda b: b.get("t", 0))[-1]
        ratio = float(latest.get("r") or 0)
        if ratio <= 0:
            return {}
        # Prefer API-supplied long%/short% when present, else derive from ratio.
        longs_pct  = float(latest.get("l") or 0) or round(ratio / (1 + ratio) * 100, 1)
        shorts_pct = float(latest.get("s") or 0) or round(100 - longs_pct, 1)
        return {
            "ratio":      round(ratio, 3),
            "longs_pct":  round(longs_pct, 1),
            "shorts_pct": round(shorts_pct, 1),
        }
    except Exception:
        return {}


def get_funding_by_exchange(symbol: str) -> dict:
    """
    Per-exchange funding rates across all major perpetual venues that list
    this USDT pair (per `_MAJOR_PERP_EXCHANGES`).

    Returns {<exchange_name_lower>: float, ..., "spread_pct": float}
    Spread = (max - min) × 100 → in PERCENT.
    Pre-Phase-2 only queried Binance/Bybit/OKX hardcoded; now uses the
    market-discovery layer so any major venue that lists the pair is included.
    """
    syms = _symbols_for_base(symbol)
    if not syms:
        return {}
    data = _get("funding-rate", {"symbols": ",".join(syms.values())})
    try:
        if not data:
            return {}
        rates: dict = {}
        for record in (data if isinstance(data, list) else [data]):
            sym = record.get("symbol", "")
            code = sym.rsplit(".", 1)[-1] if "." in sym else ""
            ex_name = _MAJOR_PERP_EXCHANGES.get(code)
            if not ex_name:
                continue
            rate = float(record.get("value") or record.get("r") or 0)
            rates[ex_name.lower()] = rate
        if len(rates) >= 2:
            vals = list(rates.values())
            rates["spread_pct"] = round((max(vals) - min(vals)) * 100, 5)
        return rates
    except Exception:
        return {}


def get_liquidation_trend(symbol: str) -> dict:
    """
    Aggregated 24h liquidation trend across major perp venues.
    Bars per hour are summed across exchanges, then the hourly aggregate is
    used to detect accelerate/decelerate and dominant side.

    Returns {"total_24h_coins": float, "recent_6h_coins": float,
             "trend": ..., "dominant_side": ..., "exchange_count": int} or {}.
    """
    now_s = int(time.time())
    from_s = now_s - 24 * 3600
    syms = _symbols_for_base(symbol) or {"A": _symbol(symbol)}
    data = _get("liquidation-history", {
        "symbols":  ",".join(syms.values()),
        "interval": "1hour",
        "from":     from_s,
        "to":       now_s,
    })
    try:
        if not data or not isinstance(data, list):
            return {}
        # Aggregate hourly: hour_ts → (long_coins_sum, short_coins_sum)
        per_hour: dict[int, list[float]] = {}
        ex_count = 0
        for rec in data:
            if not isinstance(rec, dict):
                continue
            bars = rec.get("history") or []
            if not bars:
                continue
            ex_count += 1
            for b in bars:
                t = int(b.get("t") or 0)
                if t == 0:
                    continue
                cur = per_hour.setdefault(t, [0.0, 0.0])
                cur[0] += float(b.get("l") or 0)
                cur[1] += float(b.get("s") or 0)
        if len(per_hour) < 6:
            return {}
        ordered = sorted(per_hour.items(), key=lambda x: x[0])
        total_long = total_short = 0.0
        recent_long = recent_short = 0.0
        for i, (_t, (l, s)) in enumerate(ordered):
            total_long += l
            total_short += s
            if i >= len(ordered) - 6:
                recent_long += l
                recent_short += s

        total_24h = total_long + total_short
        recent_6h = recent_long + recent_short
        older_18h = total_24h - recent_6h
        avg_6h_rate = recent_6h / 6
        avg_18h_rate = older_18h / 18 if older_18h > 0 else 0

        trend = (
            "accelerating" if avg_6h_rate > avg_18h_rate * 1.5
            else "decelerating" if avg_6h_rate < avg_18h_rate * 0.67
            else "stable"
        )
        dominant = (
            "longs" if total_long > total_short * 1.2
            else "shorts" if total_short > total_long * 1.2
            else "equal"
        )
        return {
            "total_24h_coins": round(total_24h, 4),
            "recent_6h_coins": round(recent_6h, 4),
            "trend":           trend,
            "dominant_side":   dominant,
            "exchange_count":  ex_count,
        }
    except Exception:
        return {}


def get_all(symbol: str) -> dict:
    """
    Fetch OI, liquidations, funding rate, L/S ratio, per-exchange funding,
    and 24h liquidation trend in parallel.

    All sources degrade gracefully — returns {} sub-dicts on individual failures.
    Use this in the agent pipeline for a single symbol.

    Returns:
        {
            "oi":                  {"oi_coins": float (aggregated),
                                    "oi_by_exchange": {name: coins},
                                    "exchange_count": int,
                                    "oi_symbol": str} or {},
            "liquidations":        {"liq_long_coins": float, "liq_short_coins": float,
                                    "liq_total_coins": float, "exchange_count": int} or {},
            "funding":             {"rate": float, "annualized_pct": float, "sentiment": str} or {},
            "long_short":          {"ratio": float, "longs_pct": float, "shorts_pct": float} or {},
            "funding_by_exchange": {<name_lower>: float, ..., "spread_pct": float} or {},
            "liquidation_trend":   {"total_24h_coins": float, "recent_6h_coins": float,
                                    "trend": str, "dominant_side": str,
                                    "exchange_count": int} or {},
        }
    """
    results: dict = {}

    def _fetch(name, fn, *args):
        try:
            results[name] = fn(*args)
        except Exception:
            results[name] = {}

    threads = [
        threading.Thread(target=_fetch, args=("oi",                  get_open_interest,      symbol)),
        threading.Thread(target=_fetch, args=("liquidations",         get_liquidations,       symbol)),
        threading.Thread(target=_fetch, args=("funding",              get_funding_rate,       symbol)),
        threading.Thread(target=_fetch, args=("long_short",           get_long_short_ratio,   symbol)),
        threading.Thread(target=_fetch, args=("funding_by_exchange",  get_funding_by_exchange, symbol)),
        threading.Thread(target=_fetch, args=("liquidation_trend",    get_liquidation_trend,  symbol)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=12)

    return {
        "oi":                  results.get("oi", {}),
        "liquidations":        results.get("liquidations", {}),
        "funding":             results.get("funding", {}),
        "long_short":          results.get("long_short", {}),
        "funding_by_exchange": results.get("funding_by_exchange", {}),
        "liquidation_trend":   results.get("liquidation_trend", {}),
    }
