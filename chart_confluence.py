"""
chart_confluence.py — Multi-timeframe confluence scoring engine.
Single public function: confluence_score().
All _*_weight helpers are private to this module.
Extracted from chart_context.py.
"""
import threading as _threading
import time as _time
from ccxt_client import get_binance_price, get_binance_ticker_change

# VIX cache: TTL-based (5 minutes) to avoid hammering yfinance during scans.
_vix_cache: dict = {"value": None, "ts": 0.0}
_vix_lock = _threading.Lock()
_VIX_TTL = 300  # 5 minutes

# Ticker change cache: TTL-based (5 minutes) to avoid ~40 live API calls per scan.
# Cache entry: (value, timestamp, fn_id) — fn_id tracks the current function identity
# so that monkeypatching in tests automatically invalidates stale cache entries.
_ticker_change_cache: dict[str, tuple[float, float, int]] = {}
_ticker_change_lock = _threading.Lock()
_TICKER_TTL = 300  # 5 minutes


def _get_ticker_change_cached(symbol: str) -> float | None:
    """Return 24h ticker change % for symbol, cached for 5 minutes.

    Cache is invalidated automatically when get_binance_ticker_change is replaced
    (e.g. by monkeypatching in tests), tracked via function id().
    """
    import chart_confluence as _self_module
    fn = _self_module.get_binance_ticker_change
    fn_id = id(fn)
    now = _time.time()
    with _ticker_change_lock:
        entry = _ticker_change_cache.get(symbol)
        if entry is not None:
            value, ts, cached_fn_id = entry
            if cached_fn_id == fn_id and now - ts < _TICKER_TTL:
                return value
    try:
        value = fn(symbol)
    except Exception:
        return None
    with _ticker_change_lock:
        _ticker_change_cache[symbol] = (value, _time.time(), fn_id)
    return value

def _get_vix_multiplier() -> float:
    """
    Returns a regime multiplier based on VIX level.
    Cached for 5 minutes to avoid hammering yfinance during scans.
    - VIX > 30 (high fear / risk-off): 0.80 — suppress bullish confluence
    - VIX ≤ 30 (normal / risk-on):    1.00 — no suppression
    Returns 1.0 on any error or if yfinance unavailable.
    """
    global _vix_cache
    now = _time.time()
    with _vix_lock:
        if now - _vix_cache["ts"] < _VIX_TTL and _vix_cache["value"] is not None:
            return _vix_cache["value"]
    try:
        from market_context import get_macro_regime
        regime = get_macro_regime()
        vix = regime.get("vix")
        if vix is None:
            multiplier = 1.0
        elif vix > 30:
            multiplier = 0.80
        else:
            multiplier = 1.0
        with _vix_lock:
            _vix_cache = {"value": multiplier, "ts": now}
        return multiplier
    except Exception:
        return 1.0


# Correlated pairs where cross-exchange divergence is meaningful.
# All must be liquid USDT-M perpetuals available on both Bitget and Binance.
SMT_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}

# For each symbol, its correlated counterpart to compare direction against.
# Both must be available via get_binance_ticker_change() below.
SMT_PAIRS = {
    "BTCUSDT": "ETHUSDT",
    "ETHUSDT": "BTCUSDT",
    "SOLUSDT": "ETHUSDT",
    "BNBUSDT": "BTCUSDT",
    "XRPUSDT": "BTCUSDT",
}


def _rsi_weight(rsi_val: float) -> float:
    """RSI contribution: ±1 at extremes, 0 at 50. Dead-band ±5 around 50."""
    if rsi_val > 55:   return min((rsi_val - 50) / 30.0,  1.0)
    if rsi_val < 45:   return max((rsi_val - 50) / 30.0, -1.0)
    return 0.0


def _macd_weight(macd: dict) -> float:
    """MACD contribution: full ±1 when aligned + growing, ±0.5 when aligned but fading."""
    trend    = macd.get("trend", "")
    hist_dir = macd.get("histogram_trend", "")
    if trend == "bullish":
        return 1.0 if hist_dir == "growing" else 0.5
    if trend == "bearish":
        return -1.0 if hist_dir == "growing" else -0.5
    return 0.0


def _ema_weight(ema: dict) -> float:
    """EMA contribution: ±1 fully aligned stack + price, ±0.5 partial."""
    al = ema.get("alignment", "")
    sk = ema.get("stack", "")
    if "fully bullish" in al and "bullish" in sk: return  1.0
    if "fully bearish" in al and "bearish" in sk: return -1.0
    if "bullish" in sk or "fully bullish" in al:  return  0.5
    if "bearish" in sk or "fully bearish" in al:  return -0.5
    return 0.0


def _adx_weight(adx: dict) -> float:
    """ADX contribution: direction × trend strength (ADX value / 50, capped at 1)."""
    direction = adx.get("direction", "")
    adx_val   = adx.get("value", 0)
    strength  = min(adx_val / 50.0, 1.0)
    if "bullish" in direction:  return  strength
    if "bearish" in direction:  return -strength
    return 0.0


def _wt_weight(wt: dict) -> float:
    """
    WaveTrend contribution (Cipher A/B).
    Crossover signals in OB/OS zones are the strongest inputs (±1.0).
    Gold signal (extreme oversold cross) = max bullish (1.0).
    Position-only (no cross) scales WT1 value like RSI: ±0.5 max.
    """
    if not wt:
        return 0.0
    signal = wt.get("signal")
    if signal == "gold_buy":   return  1.0
    if signal == "buy":        return  0.85
    if signal == "sell":       return -0.85
    # No fresh cross — use WT1 position scaled to ±0.5
    wt1 = wt.get("wt1", 0.0)
    return max(-0.5, min(0.5, wt1 / 60.0))


def _volume_weight(inds: dict, directional_score: float,
                    symbol: str = "", timeframe: str = "") -> float:
    """
    Volume confirms the dominant direction.
    High volume (>1.5×) amplifies consensus by ±0.5.
    Low volume (<0.7×) dampens consensus by ∓0.25.
    Direction taken from the four other signals' net score.

    When `symbol` and `timeframe` are supplied AND the per-symbol baseline is
    mature (≥6 samples — see volume_baseline.py), the surge ratio is measured
    against the symbol's own historical median pace instead of the chart's
    20-bar trailing average. Falls back to the trailing ratio otherwise.

    Also records the current bar's volume into the baseline ring buffer as a
    side effect, so subsequent calls see a richer history.
    """
    vol = inds.get("volume") or {}
    ratio_raw = vol.get("ratio", 1.0)
    current   = vol.get("current")

    # Side-effect: keep the baseline learning. Throttled internally to 90s/sample.
    if symbol and timeframe and current is not None:
        try:
            import volume_baseline
            volume_baseline.record_sample(symbol, timeframe, current)
            base_ratio, mature = volume_baseline.surge_ratio(symbol, timeframe, current)
            if mature and base_ratio is not None:
                # Blend: 70% baseline (the "true" signal), 30% trailing (guards
                # against a stale median). Same blend ratio as Kaizen Tools.
                ratio = base_ratio * 0.7 + ratio_raw * 0.3
            else:
                ratio = ratio_raw
        except Exception:
            ratio = ratio_raw
    else:
        ratio = ratio_raw

    sign = 1 if directional_score > 0 else (-1 if directional_score < 0 else 0)
    if ratio > 1.5:
        return  0.5 * sign
    if ratio < 0.7:
        return -0.25 * sign
    return 0.0


def _cvd_weight(cvd: dict) -> float:
    """CVD rising = bullish signal (+0.4), falling = bearish (-0.4), flat = 0."""
    trend = cvd.get("trend", "flat")
    return 0.4 if trend == "rising" else (-0.4 if trend == "falling" else 0.0)


# ── Smart-flow quadrant (OI × CVD × Price) ───────────────────────────────────
# Open-interest change is fetched from Coinalyze and TTL-cached per symbol so
# we don't repeat the call inside a single scan. 5-minute TTL aligns with the
# rest of the live-data caches in this module.
_oi_hist_cache: dict[str, tuple[dict, float]] = {}
_oi_hist_lock  = _threading.Lock()
_OI_HIST_TTL   = 300  # 5 minutes


def _get_oi_change_cached(symbol: str, hours: int = 4) -> float | None:
    """Return OI % change over `hours` for symbol, cached. None on failure
    (no API key, no data, etc.) — caller treats None as "skip the signal"."""
    key = f"{symbol}:{hours}"
    now = _time.time()
    with _oi_hist_lock:
        cached = _oi_hist_cache.get(key)
        if cached and (now - cached[1]) < _OI_HIST_TTL:
            return cached[0].get("oi_change_pct")
    try:
        from coinalyze_client import get_open_interest_history, is_configured
        if not is_configured():
            return None
        data = get_open_interest_history(symbol, hours=hours) or {}
        with _oi_hist_lock:
            _oi_hist_cache[key] = (data, now)
        return data.get("oi_change_pct")
    except Exception:
        return None


def _smart_flow_weight(cvd_trend: str, oi_change_pct: float | None,
                        price_change_pct: float) -> tuple[float, str]:
    """4-quadrant smart-flow classification per the trader research sheet.

    Returns (weight, label). Weight contributes to confluence score:
      +0.5  Q1 New Longs       (OI↑ CVD↑ Price↑) — strong bullish
      +0.2  Q2 Short Covering  (OI↓ CVD↑ Price↑) — cautiously bullish
      -0.5  Q3 New Shorts      (OI↑ CVD↓ Price↓) — strong bearish
      -0.2  Q4 Long Liquidation (OI↓ CVD↓ Price↓) — cautiously bearish
       0.0  mixed / no edge / insufficient data

    Thresholds: OI change must exceed ±0.5% to count as a direction (avoids
    micro-flat noise). CVD uses the existing trend label. Price uses the same
    4H window the CVD trend was computed over (~ ±0.3% threshold for noise).
    """
    if oi_change_pct is None:
        return 0.0, ""
    cvd_dir = +1 if cvd_trend == "rising" else (-1 if cvd_trend == "falling" else 0)
    oi_dir  = +1 if oi_change_pct >  0.5 else (-1 if oi_change_pct < -0.5 else 0)
    px_dir  = +1 if price_change_pct >  0.3 else (-1 if price_change_pct < -0.3 else 0)

    if cvd_dir == 0 or oi_dir == 0 or px_dir == 0:
        return 0.0, ""

    # Q1: all up → new longs
    if oi_dir == +1 and cvd_dir == +1 and px_dir == +1:
        return +0.5, "new_longs"
    # Q2: OI down, CVD/price up → short covering
    if oi_dir == -1 and cvd_dir == +1 and px_dir == +1:
        return +0.2, "short_covering"
    # Q3: OI up, CVD/price down → new shorts
    if oi_dir == +1 and cvd_dir == -1 and px_dir == -1:
        return -0.5, "new_shorts"
    # Q4: all down → long liquidation
    if oi_dir == -1 and cvd_dir == -1 and px_dir == -1:
        return -0.2, "long_liquidation"
    # Mixed quadrants (e.g. OI↑ + CVD↑ + price↓) — divergent flow, no edge
    return 0.0, ""


def _stoch_weight(stoch: dict) -> float:
    """
    Classic Stochastic — counter-trend mean-reversion signal.
    Oversold (K<20) = +0.4 (potential reversal up), Overbought (K>80) = -0.4.
    Added 2026-05-21 from AI self-review wishlist (recurring missed signal).
    Grouped with WaveTrend + MFI under the oscillator cap so total
    oscillator contribution stays bounded.
    """
    if not stoch:
        return 0.0
    k = stoch.get("k")
    if k is None:
        return 0.0
    if k < 20:
        return  0.4    # oversold → bullish reversal hint
    if k > 80:
        return -0.4    # overbought → bearish reversal hint
    return 0.0


def _smt_weight(inds: dict, symbol: str) -> float:
    """
    Cross-exchange divergence check (SMT-inspired).
    Returns +0.15 when Bitget vs Binance prices diverge >= 0.5%
    (price dislocation at this level = potential SMT signal).
    Returns 0.0 when prices agree or data unavailable.
    """
    if symbol not in SMT_SYMBOLS:
        return 0.0
    bitget_price = (inds.get("ema") or {}).get("current_price")
    if not bitget_price:
        return 0.0
    try:
        binance_price = get_binance_price(symbol)
    except Exception:
        return 0.0
    if binance_price is None:
        return 0.0
    delta_pct = abs(bitget_price - binance_price) / bitget_price
    return 0.15 if delta_pct >= 0.005 else 0.0


def _smt_direction_weight(inds: dict, symbol: str) -> float:
    """
    True SMT divergence: compare 24h direction of symbol vs its correlated pair.

    Returns +0.15 when the symbol is going UP while its pair goes DOWN
    (pair fails to confirm the move — bullish SMT divergence at lows).
    Returns -0.15 when the symbol is going DOWN while its pair goes UP
    (pair fails to confirm — bearish SMT divergence at highs).
    Returns 0.0 when both move in the same direction or data unavailable.

    Threshold: divergence only counts when the directions differ by >= 1%.
    """
    pair = SMT_PAIRS.get(symbol)
    if not pair:
        return 0.0
    sym_chg  = _get_ticker_change_cached(symbol)
    pair_chg = _get_ticker_change_cached(pair)
    if sym_chg is None or pair_chg is None:
        return 0.0
    # Same direction = no divergence
    if sym_chg * pair_chg > 0:
        return 0.0
    # Directions differ — check magnitude
    if abs(sym_chg - pair_chg) < 1.0:
        return 0.0
    # Symbol up, pair down → bullish SMT
    if sym_chg > 0 and pair_chg < 0:
        return 0.15
    # Symbol down, pair up → bearish SMT
    if sym_chg < 0 and pair_chg > 0:
        return -0.15
    return 0.0


def _mfi_weight(wt: dict) -> float:
    """
    MFI (Money Flow) contribution from WaveTrend data.
    MFI > 10 = capital inflow (bullish +0.3), MFI < -10 = outflow (bearish -0.3).
    Dead-band ±10 avoids noise near zero.
    """
    mfi = wt.get("mfi", 0.0) if wt else 0.0
    if mfi > 10:   return  0.3
    if mfi < -10:  return -0.3
    return 0.0


def _liquidation_weight(liq: dict, current_price: float) -> float:
    """
    +0.20: short-liq wall within 3% above current price (short-squeeze fuel, bullish).
    -0.20: long-liq wall within 3% below current price (cascade fuel, bearish).
    0.00 otherwise.
    """
    if not liq or not liq.get("ok"):
        return 0.0
    weight = 0.0
    try:
        p = float(current_price)
        if liq.get("short_wall"):
            dist = (float(liq["short_wall"]) - p) / p
            if 0 < dist <= 0.03:
                weight += 0.20
        if liq.get("long_wall"):
            dist = (p - float(liq["long_wall"])) / p
            if 0 < dist <= 0.03:
                weight -= 0.20
    except Exception:
        pass
    return weight


def _order_flow_weight(of: dict | None) -> float:
    """
    +0.15 buying pressure (positive delta, no divergence).
    -0.15 selling pressure OR divergence (bearish fade).
    """
    if not of:
        return 0.0
    if of.get("divergence"):
        return -0.15
    sig = of.get("signal", "neutral")
    if sig == "buying_pressure":
        return 0.15
    if sig == "selling_pressure":
        return -0.15
    return 0.0


def _get_tf_weights(ctx: dict, tf: str, symbol: str = "") -> list:
    """Return signal weights for a single timeframe, with correlated-group caps applied."""
    inds = ctx.get(tf, {}).get("indicators", {})
    if not inds.get("ok"):
        return []
    rsi_w  = _rsi_weight(inds.get("rsi",  {}).get("value", 50))
    macd_w = _macd_weight(inds.get("macd", {}))
    ema_w  = _ema_weight(inds.get("ema",   {}))
    adx_w  = _adx_weight(inds.get("adx",   {}))
    wt_w   = _wt_weight(inds.get("wavetrend", {}))
    mfi_w  = _mfi_weight(inds.get("wavetrend", {}))
    stoch_w = _stoch_weight(inds.get("stochastic", {}))
    cvd_w  = _cvd_weight(inds.get("cvd", {}))
    smt_w     = _smt_weight(inds, symbol)
    smt_dir_w = _smt_direction_weight(inds, symbol)
    of_w      = _order_flow_weight(inds.get("order_flow"))

    # Cap correlated signal groups to prevent trend-inflation
    _momentum = max(-1.5, min(1.5, rsi_w + macd_w))
    # Three oscillators (WaveTrend + MFI + Stochastic) all measure short-term
    # overextension. Group cap stays at ±1.0 — adding Stoch doesn't widen the
    # bound, only adds redundancy that tilts the group when 2-3 oscillators
    # agree (e.g. all 3 oversold). Self-review wishlist (2026-05-21).
    _oscillator = max(-1.0, min(1.0, wt_w + mfi_w + stoch_w))

    base_score = _momentum + ema_w + adx_w + _oscillator + cvd_w + smt_w + smt_dir_w + of_w
    vol_w = _volume_weight(inds, base_score, symbol=symbol, timeframe=tf)

    # Return as flat list for bull/bear totals (capped momentum and oscillator as single entries)
    return [_momentum, ema_w, adx_w, _oscillator, cvd_w, smt_w, smt_dir_w, of_w, vol_w]


def confluence_score(symbol: str, timeframes: list = None, ctx: dict = None) -> dict:
    """
    Aggregate RSI/MACD/EMA/ADX direction signals across timeframes with
    magnitude weighting — strong signals contribute more than weak ones.
    Returns {score, max, bullish, bearish, label, details}.
    Pass ctx to reuse an already-computed get_chart_context() result.
    """
    tfs = timeframes or ["4H", "1D"]
    if ctx is None:
        from chart_context import get_chart_context  # lazy to avoid circular import
        ctx = get_chart_context(symbol, tfs)

    total_score = 0.0
    details     = []
    parts       = []   # human-readable strings for each contributing signal
    # Capture per-TF weight tuples once so we don't recompute via _get_tf_weights
    # later when summing bull_total / bear_total (was 2 extra calls per TF —
    # 4× redundant indicator math in the common 2-TF case).
    captured_weights: list[tuple] = []

    for tf in tfs:
        inds = ctx.get(tf, {}).get("indicators", {})
        if not inds.get("ok"):
            continue

        rsi_val = (inds.get("rsi",  {}) or {}).get("value", 50)
        rsi_w  = _rsi_weight(rsi_val)
        macd_w = _macd_weight(inds.get("macd", {}))
        ema_w  = _ema_weight(inds.get("ema",   {}))
        adx_w  = _adx_weight(inds.get("adx",   {}))
        wt_w   = _wt_weight(inds.get("wavetrend", {}))
        mfi_w  = _mfi_weight(inds.get("wavetrend", {}))
        stoch_w = _stoch_weight(inds.get("stochastic", {}))
        cvd_w  = _cvd_weight(inds.get("cvd", {}))
        smt_w     = _smt_weight(inds, symbol)
        smt_dir_w = _smt_direction_weight(inds, symbol)
        of_w      = _order_flow_weight(inds.get("order_flow"))

        # Cap correlated signal groups to prevent trend-inflation
        # RSI + MACD: both measure momentum, cap combined contribution
        _momentum_raw = rsi_w + macd_w
        _momentum = max(-1.5, min(1.5, _momentum_raw))

        # WaveTrend + MFI + Stochastic — three oscillators measuring short-term
        # overextension. Group cap stays at ±1.0; adding Stoch tilts the group
        # when 2-3 oscillators agree but doesn't widen the bound.
        _oscillator_raw = wt_w + mfi_w + stoch_w
        _oscillator = max(-1.0, min(1.0, _oscillator_raw))

        base_score = _momentum + ema_w + adx_w + _oscillator + cvd_w + smt_w + smt_dir_w + of_w
        vol_w  = _volume_weight(inds, base_score, symbol=symbol, timeframe=tf)

        tf_score = base_score + vol_w
        total_score += tf_score

        # Collect human-readable contribution strings — emitted only for
        # signals strong enough to matter (|w| >= 0.4). Used by prompt
        # builder and UI tooltips so consumers see WHY a score is what it is.
        if abs(rsi_w) >= 0.4:
            parts.append(f"{tf} RSI {rsi_val:.0f} "
                         f"{'overbought' if rsi_val > 70 else 'oversold' if rsi_val < 30 else 'bullish' if rsi_w > 0 else 'bearish'}")
        if abs(macd_w) >= 0.4:
            parts.append(f"{tf} MACD {inds.get('macd', {}).get('trend','?')}")
        if abs(ema_w) >= 0.5:
            al = inds.get('ema', {}).get('alignment', '')
            if al:
                parts.append(f"{tf} EMA {al}")
        if abs(adx_w) >= 0.4:
            adx_d = inds.get('adx', {}) or {}
            adx_val_n = adx_d.get('value', 0)
            parts.append(f"{tf} ADX {adx_val_n:.0f} {adx_d.get('direction','?')}")
        if abs(_oscillator) >= 0.5:
            parts.append(f"{tf} WaveTrend/MFI/Stoch {'bullish' if _oscillator > 0 else 'bearish'}")
        # Surface Stochastic explicitly when it's clearly extreme — separate
        # from the grouped oscillator label so the AI can act on it.
        st = inds.get("stochastic", {}) or {}
        st_k = st.get("k")
        if st_k is not None and (st_k < 20 or st_k > 80):
            parts.append(f"{tf} Stochastic K={st_k} ({'oversold' if st_k < 20 else 'overbought'})")
        if abs(cvd_w) >= 0.3:
            parts.append(f"{tf} CVD {inds.get('cvd', {}).get('trend','flat')}")
        if abs(of_w) >= 0.1:
            of_d = inds.get('order_flow', {}) or {}
            parts.append(f"{tf} order-flow {of_d.get('label','?')}")
        if abs(vol_w) >= 0.3:
            vol_r = (inds.get('volume') or {}).get('ratio', 1.0)
            # Tag whether this is from baseline or trailing window
            tag = "vs baseline" if (symbol and tf) else "vs trailing"
            parts.append(f"{tf} volume {vol_r:.1f}× {tag}")
        if abs(smt_dir_w) >= 0.1:
            parts.append(f"{tf} SMT divergence " +
                         ('bullish' if smt_dir_w > 0 else 'bearish'))

        all_w = (_momentum, ema_w, adx_w, _oscillator, cvd_w, smt_w, smt_dir_w, of_w, vol_w)
        captured_weights.append(all_w)
        pos = round(sum(w for w in all_w if w > 0), 1)
        neg = round(sum(w for w in all_w if w < 0), 1)
        details.append(f"{tf}: +{pos}/{neg}")

    # Symbol-level signals (not per-TF)
    liq_w = 0.0
    try:
        from liquidation_levels import get_liquidation_clusters
        current_price = None
        for tf in tfs:
            df_tf = ctx.get(tf, {}).get("df")
            if df_tf is not None and len(df_tf):
                current_price = float(df_tf["close"].iloc[-1])
                break
        if current_price:
            liq   = get_liquidation_clusters(symbol)
            liq_w = _liquidation_weight(liq, current_price)
            total_score += liq_w
    except Exception:
        pass

    # Smart-flow quadrant — OI × CVD × Price classification (trader research
    # Research Desk framework). Fires once per symbol using the 4H window
    # since that's where the framework is most actionable. Returns 0 when
    # OI data is unavailable (no Coinalyze key) — falls through cleanly.
    # Price fetched via chart_candles.get_candles (cached) since
    # get_chart_context() doesn't carry the raw df in its return value.
    smart_flow_w = 0.0
    smart_flow_label = ""
    try:
        inds_4h = ctx.get("4H", {}).get("indicators", {})
        if inds_4h.get("ok"):
            from chart_candles import get_candles
            df_4h = get_candles(symbol, "4H", limit=4)
            if df_4h is not None and len(df_4h) >= 2:
                close_now  = float(df_4h["close"].iloc[-1])
                close_prev = float(df_4h["close"].iloc[-2])
                if close_prev > 0:
                    price_change_4h = (close_now - close_prev) / close_prev * 100.0
                    cvd_trend = (inds_4h.get("cvd") or {}).get("trend", "flat")
                    oi_change_4h = _get_oi_change_cached(symbol, hours=4)
                    smart_flow_w, smart_flow_label = _smart_flow_weight(
                        cvd_trend, oi_change_4h, price_change_4h)
                    total_score += smart_flow_w
                    if smart_flow_label:
                        parts.append(f"smart-flow {smart_flow_label} "
                                     f"(OI {oi_change_4h:+.1f}% / CVD {cvd_trend} / "
                                     f"px {price_change_4h:+.2f}%)")
    except Exception:
        pass

    # Apply macro regime multiplier (VIX-based, cached 5 min)
    vix_mult = _get_vix_multiplier()
    if vix_mult != 1.0:
        total_score = round(total_score * vix_mult, 2)

    smt_bonus  = 0.30 if symbol in SMT_SYMBOLS else 0.0
    max_per_tf = 5.55 + smt_bonus         # +0.15 order flow vs previous 5.40
    # Symbol-level bonuses to max: liquidation +0.20 (already counted above),
    # smart-flow +0.50 (max contribution from Q1 New Longs or Q3 New Shorts).
    max_val    = (float(len(tfs) * max_per_tf)
                  + (0.20 if liq_w != 0.0 else 0.0)
                  + (0.50 if smart_flow_w != 0.0 else 0.0))
    pct     = total_score / max_val if max_val else 0.0

    # Thresholds: ±0.33 ≈ net 1/3 of max weight aligned; ±0.60 = strong consensus
    if pct >= 0.60:
        label = "Strong Bullish"
    elif pct >= 0.33:
        label = "Bullish"
    elif pct <= -0.60:
        label = "Strong Bearish"
    elif pct <= -0.33:
        label = "Bearish"
    else:
        label = "Neutral"

    # Reuse the weight tuples we already computed in the main loop (was 2× recompute)
    bull_total = round(sum(w for tup in captured_weights for w in tup if w > 0), 1)
    bear_total = round(abs(sum(w for tup in captured_weights for w in tup if w < 0)), 1)

    if liq_w != 0.0:
        parts.append(f"liquidation cluster {'support' if liq_w > 0 else 'overhead'}")

    return {
        "score":   round(total_score, 2),
        "max":     max_val,
        "bullish": bull_total,
        "bearish": bear_total,
        "label":   label,
        "details": details,
        "parts":   parts,    # human-readable signal contributions (Kaizen-style)
        "vix_regime_active": vix_mult != 1.0,
    }
