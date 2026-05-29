import traceback

from flask import Blueprint, request, render_template

from database import db_conn
from helpers import _ok, _err, _filters_from_args
from analytics import (get_dashboard_kpis, get_deep_stats, get_rr_analysis, get_heatmap_data,
                        get_mfe_mae, get_ev_by_setup, get_rolling_stats, get_sharpe_calmar,
                        get_accuracy_trend)
import ai_pattern_detector
import chart_context

bp = Blueprint("analytics", __name__)


@bp.route("/chart")
def chart_page():
    return render_template("chart.html")


@bp.route("/api/dashboard/kpis")
def api_dashboard_kpis():
    try:
        return _ok(get_dashboard_kpis(filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/deep")
def api_analytics_deep():
    try:
        return _ok(get_deep_stats(filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/heatmap")
def api_analytics_heatmap():
    try:
        filters = _filters_from_args()
        with db_conn() as conn:
            data = get_heatmap_data(conn=conn, filters=filters)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/patterns", methods=["POST"])
def api_analytics_patterns():
    try:
        body    = request.get_json(silent=True) or {}
        filters = {**_filters_from_args(), **{k: v for k, v in body.items() if k == "exchange"}}
        with db_conn() as conn:
            result = ai_pattern_detector.detect_patterns(conn=conn, filters=filters)
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/rr")
def api_analytics_rr():
    try:
        filters = _filters_from_args()
        with db_conn() as conn:
            data = get_rr_analysis(conn=conn, filters=filters)
        return _ok({"items": data})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/by-setup")
def api_analytics_by_setup():
    """GET /api/analytics/by-setup — P&L breakdown by setup type."""
    try:
        from analytics import get_setup_type_stats
        with db_conn() as conn:
            data = get_setup_type_stats(filters=_filters_from_args(), conn=conn)
        return _ok({"setups": data})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/benchmark")
def api_analytics_benchmark():
    """GET /api/analytics/benchmark -- trader return vs BTC buy-and-hold."""
    try:
        from analytics import get_benchmark_comparison
        with db_conn() as conn:
            data = get_benchmark_comparison(filters=_filters_from_args(), conn=conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/execution-quality")
def api_analytics_execution_quality():
    """GET /api/analytics/execution-quality -- signal lag and slippage stats."""
    try:
        from analytics import get_execution_quality
        with db_conn() as conn:
            data = get_execution_quality(conn=conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/tearsheet")
def api_analytics_tearsheet():
    """GET /api/analytics/tearsheet -- professional performance metrics."""
    try:
        from analytics import get_tearsheet_metrics
        with db_conn() as conn:
            data = get_tearsheet_metrics(conn=conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/tearsheet/download")
def api_analytics_tearsheet_download():
    """GET /api/analytics/tearsheet/download -- full quantstats HTML report."""
    try:
        import io, pandas as pd
        import quantstats as qs
        from flask import Response
        rows = []
        with db_conn() as conn:
            rows = conn.execute("""
                SELECT date(date) AS day, MAX(wallet_balance) AS balance
                FROM wallet_snapshots
                WHERE wallet_balance IS NOT NULL AND wallet_balance > 1
                GROUP BY day ORDER BY day ASC
            """).fetchall()
        if len(rows) < 20:
            return _err("Need at least 20 days of wallet data", 400)
        balances = pd.Series(
            [float(r["balance"]) for r in rows],
            index=pd.to_datetime([r["day"] for r in rows]),
        )
        returns = balances.pct_change().dropna()
        buf = io.StringIO()
        qs.reports.html(returns, output=buf, title="Trading Journal Tearsheet",
                        benchmark=None, download_filename=None)
        return Response(buf.getvalue(), mimetype="text/html",
                        headers={"Content-Disposition": "attachment; filename=tearsheet.html"})
    except Exception:
        traceback.print_exc()
        return _err("Tearsheet generation failed", 500)


@bp.route("/api/chart/candles")
def api_chart_candles():
    """
    GET /api/chart/candles?symbol=BTCUSDT&timeframe=4H&limit=200
    Returns OHLCV candles + detected S/R levels for the frontend chart modal.
    """
    try:
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return _err("symbol is required")
        timeframe = request.args.get("timeframe", "4H").strip()
        limit     = int(request.args.get("limit", 200))
        return _ok(chart_context.get_candles_for_chart(symbol, timeframe, limit))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/chart/annotated/<symbol>")
def api_chart_annotated(symbol):
    """
    GET /api/chart/annotated/CHZUSDT?direction=Long&entry=0.044&sl=0.041&tp1=0.048&tp2=0.053
    Generate annotated chart PNG (base64) for any symbol.
    All trade level params are optional — omit for a plain S/R chart.
    Used by Hermes to send charts via Telegram.
    """
    try:
        import agent_chart_draw
        from chart_context import get_candles
        from chart_sr import detect_support_resistance

        sym       = symbol.strip().upper()
        direction = request.args.get("direction", "Long")

        def _flt(key):
            v = request.args.get(key)
            return float(v) if v else None

        entry      = _flt("entry") or 0
        entry_high = _flt("entry_high")
        sl         = _flt("sl") or 0
        tp1        = _flt("tp1") or 0
        tp2        = _flt("tp2") or 0
        tf         = request.args.get("tf", "4H")

        candles = get_candles(sym, tf)
        if candles is None or candles.empty:
            return _err(f"No candle data for {sym}")

        sr_levels = detect_support_resistance(candles)
        chart_b64 = agent_chart_draw.draw(
            candles    = candles,
            symbol     = sym,
            direction  = direction,
            entry      = entry,
            entry_high = entry_high,
            sl         = sl,
            tp1        = tp1,
            tp2        = tp2,
            sr_levels  = sr_levels,
        )
        if not chart_b64:
            return _err("Chart generation failed")
        return _ok({"symbol": sym, "direction": direction, "chart_b64": chart_b64})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/mfe-mae")
def api_mfe_mae():
    try:
        with db_conn() as conn:
            return _ok(get_mfe_mae(conn=conn, filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/ev-by-setup")
def api_ev_by_setup():
    try:
        with db_conn() as conn:
            return _ok(get_ev_by_setup(conn=conn, filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/rolling")
def api_rolling_stats():
    try:
        days = int(request.args.get("days", 30))
        with db_conn() as conn:
            return _ok(get_rolling_stats(conn=conn, filters=_filters_from_args(), days=days))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/accuracy-trend")
def api_accuracy_trend():
    try:
        with db_conn() as conn:
            return _ok(get_accuracy_trend(conn=conn, filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/sharpe-calmar")
def api_sharpe_calmar():
    try:
        with db_conn() as conn:
            return _ok(get_sharpe_calmar(conn=conn, filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/token-usage")
def api_token_usage():
    """GET /api/token-usage?days=7  — rolling usage + per-module breakdown."""
    try:
        days = int(request.args.get("days", 7))
        with db_conn() as conn:
            rows = [dict(r) for r in conn.execute("""
                SELECT module, model,
                       COUNT(*) AS calls,
                       SUM(input_tokens)  AS total_input,
                       SUM(output_tokens) AS total_output,
                       SUM(cached_tokens) AS total_cached
                FROM token_usage
                WHERE ts >= datetime('now', ? || ' days')
                GROUP BY module, model
                ORDER BY total_input DESC
            """, (f"-{days}",)).fetchall()]

            totals = dict(conn.execute("""
                SELECT SUM(input_tokens) AS total_input,
                       SUM(output_tokens) AS total_output,
                       SUM(cached_tokens) AS total_cached,
                       COUNT(*) AS total_calls
                FROM token_usage
                WHERE ts >= datetime('now', ? || ' days')
            """, (f"-{days}",)).fetchone() or {})

            all_time = dict(conn.execute("""
                SELECT SUM(input_tokens) AS total_input,
                       SUM(output_tokens) AS total_output,
                       COUNT(*) AS total_calls
                FROM token_usage
            """).fetchone() or {})

        sonnet_in_cost  = 3.0 / 1_000_000   # $/token (input)
        sonnet_out_cost = 15.0 / 1_000_000  # $/token (output)
        haiku_in_cost   = 0.8 / 1_000_000
        haiku_out_cost  = 4.0 / 1_000_000

        def cost(row):
            if "haiku" in row.get("model", "").lower():
                return round(row["total_input"] * haiku_in_cost + row["total_output"] * haiku_out_cost, 4)
            return round(row["total_input"] * sonnet_in_cost + row["total_output"] * sonnet_out_cost, 4)

        for r in rows:
            r["est_cost_usd"] = cost(r)

        total_cost = sum(r["est_cost_usd"] for r in rows)
        return _ok({
            "days": days,
            "by_module": rows,
            "totals": totals,
            "all_time": all_time,
            "est_cost_usd": round(total_cost, 4),
        })
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/chart/vmc-cipher/<symbol>")
def api_chart_vmc_cipher(symbol):
    """
    GET /api/chart/vmc-cipher/BTCUSDT?timeframe=4H&limit=200&format=json|png
    VMC Cipher B indicator data for a symbol.
      format=json (default) → JSON payload for LightweightCharts popup
      format=png            → base64 PNG (price+VMC dual-pane) for static use
      format=pane           → base64 PNG (oscillator pane only)
    """
    try:
        import chart_candles
        import chart_vmc_cipher
        import chart_vmc_draw
        sym = symbol.strip().upper()
        if not sym:
            return _err("symbol is required")
        timeframe = request.args.get("timeframe", "4H").strip()
        limit     = max(50, min(500, int(request.args.get("limit", 200))))
        fmt       = (request.args.get("format") or "json").lower()

        df = chart_candles.get_candles(sym, timeframe, limit=limit)
        if df is None or df.empty:
            return _err(f"no candles available for {sym} {timeframe}")

        if fmt == "png":
            b64 = chart_vmc_draw.draw_price_and_vmc(df, symbol=sym)
            return _ok({"symbol": sym, "timeframe": timeframe,
                        "format": "png", "image_b64": b64})
        if fmt == "pane":
            b64 = chart_vmc_draw.draw_vmc_only(df, symbol=sym)
            return _ok({"symbol": sym, "timeframe": timeframe,
                        "format": "pane", "image_b64": b64})

        vmc = chart_vmc_cipher.compute_vmc_cipher(df)
        payload = chart_vmc_cipher.to_json_payload(vmc, df)
        payload["symbol"]    = sym
        payload["timeframe"] = timeframe
        payload["bars"]      = len(df)
        return _ok(payload)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/chart/vmc-cipher-a/<symbol>")
def api_chart_vmc_cipher_a(symbol):
    """
    GET /api/chart/vmc-cipher-a/BTCUSDT?timeframe=4H&limit=200
    Cipher A — EMA ribbon (8 EMAs) + signal markers (long_ema, short_ema,
    red_cross, blue_triangle, red_diamond, blood_diamond, yellow_x, bull_candle).
    Always JSON — Cipher A is an on-chart overlay, no standalone PNG.
    """
    try:
        import chart_candles
        import chart_vmc_cipher_a
        sym = symbol.strip().upper()
        if not sym:
            return _err("symbol is required")
        timeframe = request.args.get("timeframe", "4H").strip()
        limit     = max(50, min(500, int(request.args.get("limit", 200))))
        df = chart_candles.get_candles(sym, timeframe, limit=limit)
        if df is None or df.empty:
            return _err(f"no candles available for {sym} {timeframe}")
        cipher_a = chart_vmc_cipher_a.compute_cipher_a(df)
        payload = chart_vmc_cipher_a.to_json_payload(cipher_a, df)
        payload["symbol"]    = sym
        payload["timeframe"] = timeframe
        return _ok(payload)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/chart/mtf-ema/<symbol>")
def api_chart_mtf_ema(symbol):
    """
    GET /api/chart/mtf-ema/BTCUSDT?length=200
    Multi-Timeframe EMA average — average of N-period EMA across
    1H/4H/12H/1D/3D/1W. Returns scalar + bias (long/short/neutral).
    """
    try:
        import chart_mtf_ema
        sym = symbol.strip().upper()
        if not sym:
            return _err("symbol is required")
        length = max(20, min(500, int(request.args.get("length", 200))))
        result = chart_mtf_ema.compute_mtf_ema_avg(sym, length=length)
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/chart/vmc-signal/<symbol>")
def api_chart_vmc_signal(symbol):
    """
    GET /api/chart/vmc-signal/BTCUSDT?timeframe=4H
    Unified VuManChu signal — combines Cipher A + Cipher B + MTF EMA into a
    signed score [-1.0, +1.0] with active-signal breakdown and label.
    """
    try:
        import chart_candles
        import chart_vmc_signals
        sym = symbol.strip().upper()
        if not sym:
            return _err("symbol is required")
        timeframe = request.args.get("timeframe", "4H").strip()
        limit     = max(80, min(500, int(request.args.get("limit", 250))))
        include_mtf = (request.args.get("mtf", "1") not in ("0", "false", "no"))
        df = chart_candles.get_candles(sym, timeframe, limit=limit)
        if df is None or df.empty:
            return _err(f"no candles available for {sym} {timeframe}")
        result = chart_vmc_signals.compute_unified_signal(sym, df,
                                                            include_mtf=include_mtf)
        result["timeframe"] = timeframe
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/chart/indicators")
def api_chart_indicators():
    """
    GET /api/chart/indicators?symbol=BTCUSDT&timeframes=4H,1D
    Returns computed indicator suite for a symbol + timeframe(s).
    """
    try:
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return _err("symbol is required")
        tf_raw     = request.args.get("timeframes", "4H,1D")
        timeframes = [t.strip() for t in tf_raw.split(",") if t.strip()]
        ctx = chart_context.get_chart_context(symbol, timeframes)
        return _ok(ctx)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


# ── Score Comparison (scanner vs Opus vs hindsight) ────────────────────────────
# Added 2026-05-24. Backed by ai_score_comparison.compute_comparison() —
# returns per-trade rows + per-system aggregates + disagreement cases. Cached
# in settings.score_comparison_cache_json; POST recompute to refresh.

@bp.route("/api/analysis/score-comparison")
def api_score_comparison():
    """GET — returns cached result or {meta: {computed_at: null}} if never run."""
    try:
        import ai_score_comparison
        with db_conn() as conn:
            cached = ai_score_comparison.get_cached(conn)
        if cached:
            return _ok(cached)
        return _ok({"per_trade": [], "aggregates": {}, "disagreements": [],
                    "meta": {"computed_at": None, "note": "never computed — POST to recompute"}})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analysis/score-comparison/recompute", methods=["POST"])
def api_score_comparison_recompute():
    """POST — force recompute + save to cache."""
    try:
        import ai_score_comparison
        with db_conn() as conn:
            data = ai_score_comparison.recompute_and_save(conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
