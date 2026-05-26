"""
risk_analytics.py — Portfolio risk metrics using free Binance public data.

All functions are pure (no DB access except compute_pnl_attribution,
compute_kelly_by_bucket, compute_alpha_decay which need historical trade data).
OHLCV data from Binance futures public endpoint via ccxt.

Public API:
  compute_portfolio_var(positions, equity) -> dict
  compute_correlation_matrix(positions)    -> dict
  compute_pnl_attribution(conn, days)      -> dict
  compute_kelly_by_bucket(conn)            -> dict
  compute_alpha_decay(conn)                -> dict
"""
import numpy as np
import pandas as pd
import yfinance as yf


def _fetch_ohlcv_df(symbol: str, tf: str = "4H", limit: int = 500) -> pd.DataFrame:
    """
    Fetch OHLCV from Binance futures public API (free, no auth).
    Returns DataFrame with columns: close, volume. Index: datetime.
    Mockable in tests via monkeypatch("risk_analytics._fetch_ohlcv_df", ...).
    """
    try:
        import ccxt as _ccxt
        ex = _ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        ccxt_sym = symbol.replace("USDT", "/USDT:USDT")
        raw = ex.fetch_ohlcv(ccxt_sym, tf, limit=limit)
        if not raw:
            return pd.DataFrame()
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df.index = pd.to_datetime(df["ts"], unit="ms")
        return df[["close", "volume"]].astype(float)
    except Exception:
        return pd.DataFrame()


def _daily_returns(symbol: str, lookback_days: int = 90) -> pd.Series:
    """Return daily return series for a symbol, resampled from 4H OHLCV."""
    limit = lookback_days * 6 + 10
    df = _fetch_ohlcv_df(symbol, tf="4H", limit=limit)
    if df.empty:
        return pd.Series(dtype=float)
    daily = df["close"].resample("D").last().dropna()
    return daily.pct_change().dropna()


def compute_portfolio_var(positions: list, equity: float,
                          lookback_days: int = 90) -> dict:
    """
    Historical simulation VaR on the current open portfolio.
    Fetches 90 days of Binance 4H OHLCV (free, public endpoint).
    Returns var_95_usd, var_99_usd, var_95_pct, var_99_pct, total_notional,
    horizon_days, sample_days, available.
    """
    if not positions:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "total_notional": 0.0, "horizon_days": 1,
                "sample_days": 0, "available": False}

    total_notional = sum(float(p.get("size_usdt") or 0) for p in positions)
    if total_notional <= 0:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "total_notional": 0.0, "horizon_days": 1,
                "sample_days": 0, "available": False}

    returns_dict: dict[str, pd.Series] = {}
    for p in positions:
        sym = p.get("symbol", "")
        if not sym:
            continue
        r = _daily_returns(sym, lookback_days)
        if not r.empty:
            direction = (p.get("direction") or "Long").lower()
            returns_dict[sym] = r if direction == "long" else -r

    if not returns_dict:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "total_notional": round(total_notional, 2),
                "horizon_days": 1, "sample_days": 0, "available": False}

    df = pd.DataFrame(returns_dict).dropna()
    if df.empty or len(df) < 10:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "total_notional": round(total_notional, 2),
                "horizon_days": 1, "sample_days": len(df), "available": False}

    weights = {sym: float(p.get("size_usdt") or 0) / total_notional
               for p in positions
               for sym in [p.get("symbol", "")]
               if sym in returns_dict}

    portfolio_returns = sum(df[sym] * w for sym, w in weights.items() if sym in df.columns)

    pct_95 = float(np.percentile(portfolio_returns, 5))
    pct_99 = float(np.percentile(portfolio_returns, 1))

    return {
        "var_95_usd":     round(abs(pct_95) * total_notional, 2),
        "var_99_usd":     round(abs(pct_99) * total_notional, 2),
        "var_95_pct":     round(abs(pct_95) * 100, 2),
        "var_99_pct":     round(abs(pct_99) * 100, 2),
        "total_notional": round(total_notional, 2),
        "horizon_days":   1,
        "sample_days":    len(portfolio_returns),
        "available":      True,
    }


def compute_correlation_matrix(positions: list, lookback_days: int = 30) -> dict:
    """
    Pairwise Pearson correlation between open positions using 30-day daily returns.
    Flags high-risk pairs (correlation > 0.70, same direction).
    Returns matrix, high_risk_pairs, symbols, lookback_days, sample_days, available.
    """
    if len(positions) < 2:
        return {"matrix": [], "high_risk_pairs": [], "available": False,
                "reason": "Need at least 2 open positions"}

    returns_dict = {}
    for p in positions:
        sym = p.get("symbol", "")
        if not sym or sym in returns_dict:
            continue
        r = _daily_returns(sym, lookback_days)
        if not r.empty:
            returns_dict[sym] = r

    if len(returns_dict) < 2:
        return {"matrix": [], "high_risk_pairs": [], "available": False,
                "reason": "Insufficient price history for correlation"}

    df = pd.DataFrame(returns_dict).dropna()
    if len(df) < 5:
        return {"matrix": [], "high_risk_pairs": [], "available": False,
                "reason": f"Only {len(df)} days of aligned data"}

    corr = df.corr()
    symbols = list(corr.columns)
    matrix = []
    for i, sa in enumerate(symbols):
        for sb in symbols[i+1:]:
            matrix.append({"symbol_a": sa, "symbol_b": sb,
                            "correlation": round(float(corr.loc[sa, sb]), 3)})
    matrix.sort(key=lambda x: abs(x["correlation"]), reverse=True)

    dir_map = {p["symbol"]: (p.get("direction") or "Long").lower() for p in positions}
    high_risk_pairs = [
        m for m in matrix
        if abs(m["correlation"]) > 0.70
        and dir_map.get(m["symbol_a"]) == dir_map.get(m["symbol_b"])
    ]

    return {"matrix": matrix, "high_risk_pairs": high_risk_pairs, "symbols": symbols,
            "lookback_days": lookback_days, "sample_days": len(df), "available": True}


def compute_pnl_attribution(conn, lookback_days: int = 90,
                             chain: str = "auto_ai") -> dict:
    """
    Decompose P&L into alpha (skill) and beta (BTC market move) for a single
    chain. Fixed 2026-05-26 — previously had multiple math bugs:
      - mixed manual+auto_ai (manual has corrupt size_usdt = contract count
        instead of USDT; max $200M in DB), so a corruption-driven outlier
        could swing the entire calculation. Now filtered to a single chain.
      - daily BTC close on open/close DATE → same-day intraday trades had
        btc_o == btc_c → beta = 0 → entire P&L attributed to alpha. Now uses
        hourly BTC OHLCV with actual TIMESTAMP precision.
      - skipped-bad-size rows still added to alpha (contaminating the metric).
        Now EXCLUDED entirely from both alpha and total_pnl.
      - alpha_pct as % of |total_pnl| produced nonsensical ratios > ±100%
        when alpha and beta have opposite signs. Now uses meaningful labels
        + raw $ amounts. JS layer formats the qualitative interpretation.

    Returns:
      alpha_pnl, beta_pnl, total_pnl   — raw $ amounts, attributed trades only
      alpha_label                      — "outperforming BTC" / "underperforming BTC"
      vs_passive_btc_usd               — how much your strategy diverged from buy-and-hold
      sample_size, attributed, skipped_bad_size, available, lookback_days, chain
    """
    import datetime as _dt

    rows = conn.execute("""
        SELECT id, symbol, direction, realized_pnl, size_usdt,
               open_time, close_time
        FROM positions
        WHERE realized_pnl IS NOT NULL AND size_usdt > 0
          AND open_time IS NOT NULL AND close_time IS NOT NULL
          AND close_time >= datetime('now', ? || ' days')
          AND COALESCE(chain, 'manual') = ?
        ORDER BY close_time DESC LIMIT 200
    """, (str(-lookback_days), chain)).fetchall()

    if not rows:
        return {"alpha_pnl": 0.0, "beta_pnl": 0.0, "total_pnl": 0.0,
                "alpha_label": "no data", "vs_passive_btc_usd": 0.0,
                "sample_size": 0, "attributed": 0, "skipped_bad_size": 0,
                "available": False, "chain": chain, "lookback_days": lookback_days}

    # Fetch BTC HOURLY OHLCV via Binance (free, public). Bound the window to
    # actual trade range to keep the response under 1MB.
    min_ts = min(r["open_time"] for r in rows)
    max_ts = max(r["close_time"] for r in rows)
    btc_hourly = pd.Series(dtype=float)
    try:
        # Convert min/max to estimate hours needed — limit at 1500 (~62 days)
        # which covers most lookback windows; older trades use daily yfinance fallback.
        df_btc = _fetch_ohlcv_df("BTCUSDT", tf="1h", limit=1500)
        if not df_btc.empty:
            btc_hourly = df_btc["close"].dropna()
    except Exception:
        pass

    # yfinance daily fallback if 1H fetch failed or for trades older than the
    # 1H window
    btc_daily = pd.Series(dtype=float)
    try:
        min_date = pd.Timestamp(min_ts).strftime("%Y-%m-%d")
        max_date = pd.Timestamp(max_ts).strftime("%Y-%m-%d")
        end_plus1 = (_dt.datetime.strptime(max_date, "%Y-%m-%d")
                     + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        btc = yf.download("BTC-USD", start=min_date, end=end_plus1,
                          progress=False, auto_adjust=True)
        if not btc.empty:
            if isinstance(btc.columns, pd.MultiIndex):
                close = btc.xs("Close", axis=1, level=0)
            else:
                close = btc.get("Close")
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            if close is not None:
                btc_daily = close.dropna()
    except Exception:
        pass

    def _btc_at(ts) -> float:
        """Resolve BTC price at a timestamp, preferring hourly precision."""
        pts = pd.Timestamp(ts)
        # Hourly: nearest <= ts
        if not btc_hourly.empty:
            v = btc_hourly.asof(pts)
            if hasattr(v, "iloc"): v = v.iloc[0]
            if v is not None and not pd.isna(v):
                return float(v)
        # Daily fallback
        if not btc_daily.empty:
            v = btc_daily.asof(pts)
            if hasattr(v, "iloc"): v = v.iloc[0]
            if v is not None and not pd.isna(v):
                return float(v)
        return 0.0

    # Corrupt-size guard: manual chain has rows with size_usdt populated
    # as raw contract count (max observed $200M in DB). Auto_ai is clean
    # but the bound stays as defense.
    MAX_REASONABLE_SIZE_USDT = 10_000
    alpha_pnl = beta_pnl = total_pnl = 0.0
    attributed = 0
    skipped_bad_size = 0
    for r in rows:
        pnl  = float(r["realized_pnl"])
        size = float(r["size_usdt"])
        if size > MAX_REASONABLE_SIZE_USDT:
            # EXCLUDED — corrupt size data; don't contaminate alpha or total.
            skipped_bad_size += 1
            continue
        total_pnl += pnl
        try:
            btc_o = _btc_at(r["open_time"])
            btc_c = _btc_at(r["close_time"])
            if btc_o and btc_c:
                btc_ret = (btc_c - btc_o) / btc_o
                is_long = (r["direction"] or "Long").lower() == "long"
                beta_contribution = size * btc_ret * (1 if is_long else -1)
                beta_pnl  += beta_contribution
                alpha_pnl += pnl - beta_contribution
                attributed += 1
            else:
                # BTC price unresolvable — fall back to "all alpha" but flag.
                alpha_pnl += pnl
        except Exception:
            alpha_pnl += pnl

    # Qualitative label: "you outperformed BTC by $X" vs "you underperformed by $X"
    vs_passive = alpha_pnl  # how much your strategy added/subtracted vs holding BTC
    if vs_passive > 0:
        alpha_label = f"+${vs_passive:.2f} alpha — outperforming passive BTC"
    elif vs_passive < 0:
        alpha_label = f"-${abs(vs_passive):.2f} alpha — underperforming passive BTC"
    else:
        alpha_label = "neutral alpha"

    return {"alpha_pnl": round(alpha_pnl, 2),
            "beta_pnl":  round(beta_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "alpha_label": alpha_label,
            "vs_passive_btc_usd": round(vs_passive, 2),
            "sample_size": len(rows),
            "attributed": attributed,
            "skipped_bad_size": skipped_bad_size,
            "available": attributed > 0,
            "chain": chain,
            "lookback_days": lookback_days}


def compute_kelly_by_bucket(conn) -> dict:
    """
    Compute half-Kelly fraction per setup score bucket from historical trade data.
    Kelly f = (win_rate * avg_win - loss_rate * avg_loss) / avg_win, capped at 20%.
    Returns buckets [{score_range, trade_count, win_rate, avg_win_usd, avg_loss_usd,
    kelly_full_pct, kelly_half_pct, recommended_size_pct}] and available.
    """
    rows = conn.execute("""
        SELECT
            CASE
                WHEN setup_score <= 6 THEN '6'
                WHEN setup_score <= 8 THEN '7-8'
                ELSE '9-10'
            END AS bucket,
            COUNT(*) AS n,
            AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END)  AS avg_win,
            AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END)  AS avg_loss
        FROM positions
        WHERE setup_score IS NOT NULL AND realized_pnl IS NOT NULL
        GROUP BY bucket HAVING COUNT(*) >= 5 ORDER BY bucket
    """).fetchall()

    if not rows:
        overall = conn.execute("""
            SELECT COUNT(*) AS n,
                   AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                   AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END)  AS avg_win,
                   AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END)  AS avg_loss
            FROM positions WHERE realized_pnl IS NOT NULL
        """).fetchone()
        if not overall or not overall["n"] or overall["n"] < 5:
            return {"buckets": [], "available": False,
                    "reason": "Need at least 5 trades with setup_score"}
        rows = [{"bucket": "all", **dict(overall)}]

    buckets = []
    for r in rows:
        wr = float(r["win_rate"] or 0)
        lr = 1 - wr
        aw = float(r["avg_win"]  or 0)
        al = abs(float(r["avg_loss"] or 1))
        # Compute UNCLAMPED Kelly first so we can distinguish "clamped at 0"
        # (negative edge) from "no data yet" — UX-critical because a 0% value
        # with healthy-looking stats (high WR) looks like a bug otherwise.
        kelly_raw  = (wr * aw - lr * al) / aw if aw > 0 else 0.0
        kelly_full = max(0.0, kelly_raw)
        kelly_half = kelly_full / 2
        rr        = round(aw / al, 2) if al > 0 else None
        wr_breakeven = round(al / (al + aw) * 100, 1) if (aw + al) > 0 else None
        reason = ""
        if kelly_raw <= 0 and aw > 0 and al > 0:
            reason = (f"Negative edge: avg loss (${al:.2f}) is "
                      f"{al/aw:.2f}× avg win (${aw:.2f}). "
                      f"Need win rate ≥ {wr_breakeven}% to be profitable; "
                      f"currently {round(wr*100,1)}%. Kelly clamped at 0%.")
        buckets.append({
            "score_range":          r["bucket"],
            "trade_count":          r["n"],
            "win_rate":             round(wr * 100, 1),
            "win_rate_breakeven":   wr_breakeven,
            "avg_win_usd":          round(aw, 2),
            "avg_loss_usd":         round(al, 2),
            "reward_ratio":         rr,
            "kelly_raw_pct":        round(kelly_raw * 100, 1),
            "kelly_full_pct":       round(kelly_full * 100, 1),
            "kelly_half_pct":       round(kelly_half * 100, 1),
            "recommended_size_pct": min(round(kelly_half * 100, 1), 20.0),
            "reason":               reason,
        })
    return {"buckets": buckets, "available": True}


def compute_alpha_decay(conn) -> dict:
    """
    Measure how execution lag affects P&L. Groups trades by lag bucket.
    A negative correlation (P&L drops as lag increases) = edge decays.
    Returns lag_buckets, correlation, edge_decays, sample_size, available.
    """
    rows = conn.execute("""
        SELECT execution_lag_minutes, realized_pnl
        FROM positions
        WHERE execution_lag_minutes IS NOT NULL AND realized_pnl IS NOT NULL
        ORDER BY close_time DESC LIMIT 200
    """).fetchall()

    if len(rows) < 5:
        return {"lag_buckets": [], "correlation": None, "available": False,
                "reason": f"Need 5+ trades with execution lag data, have {len(rows)}"}

    lags = [float(r["execution_lag_minutes"]) for r in rows]
    pnls = [float(r["realized_pnl"]) for r in rows]

    try:
        corr = float(np.corrcoef(lags, pnls)[0, 1])
    except Exception:
        corr = None

    buckets_raw = {"< 30m": [], "30m-2h": [], "2h-8h": [], "> 8h": []}
    for lag, pnl in zip(lags, pnls):
        if lag < 30:             buckets_raw["< 30m"].append(pnl)
        elif lag < 120:          buckets_raw["30m-2h"].append(pnl)
        elif lag < 480:          buckets_raw["2h-8h"].append(pnl)
        else:                    buckets_raw["> 8h"].append(pnl)

    lag_buckets = []
    for label, ps in buckets_raw.items():
        if not ps:
            continue
        wins = sum(1 for p in ps if p > 0)
        lag_buckets.append({
            "lag_range":   label,
            "trade_count": len(ps),
            "avg_pnl":     round(sum(ps) / len(ps), 2),
            "win_rate":    round(wins / len(ps) * 100, 1),
        })

    return {"lag_buckets": lag_buckets, "correlation": round(corr, 3) if corr is not None else None,
            "edge_decays": corr is not None and corr < -0.15,
            "sample_size": len(rows), "available": True}
