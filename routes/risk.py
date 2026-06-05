"""routes/risk.py -- Risk analytics endpoints (free Binance data + local DB)."""
import time
import traceback

from flask import Blueprint, request
from helpers import _ok, _err
import bitget_client
import blofin_client

bp = Blueprint("risk", __name__)

# Cache live-positions for 30s so VaR + correlation endpoints don't double-fetch
# during a single dashboard render. Bitget API can be slow; this prevents the
# UI from timing out when the user navigates the Risk tab.
_live_positions_cache: dict = {"ts": 0.0, "positions": [], "equity": 0.0}
_LIVE_POSITIONS_TTL = 30


def _get_live_positions() -> tuple:
    if time.time() - _live_positions_cache["ts"] < _LIVE_POSITIONS_TTL:
        return _live_positions_cache["positions"], _live_positions_cache["equity"]
    positions, equity = [], 0.0
    try:
        positions = bitget_client.get_open_positions()
        eq = bitget_client.get_account_equity()
        equity += float(eq.get("accountEquity") or 0)
    except Exception:
        pass
    try:
        if blofin_client.is_configured():
            positions += blofin_client.get_open_positions()
            bl_eq = blofin_client.get_account_equity()
            equity += float(bl_eq.get("equity") or 0)
    except Exception:
        pass
    _live_positions_cache["ts"] = time.time()
    _live_positions_cache["positions"] = positions
    _live_positions_cache["equity"] = equity
    return positions, equity


@bp.route("/api/risk/var")
def api_risk_var():
    """GET /api/risk/var -- Historical simulation VaR on open positions."""
    try:
        from risk_analytics import compute_portfolio_var
        positions, equity = _get_live_positions()
        return _ok(compute_portfolio_var(positions, equity=equity))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/correlation")
def api_risk_correlation():
    """GET /api/risk/correlation -- Pairwise correlation matrix for open positions."""
    try:
        from risk_analytics import compute_correlation_matrix
        positions, _ = _get_live_positions()
        return _ok(compute_correlation_matrix(positions))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/attribution")
def api_risk_attribution():
    """GET /api/risk/attribution?days=90&chain=auto_ai|manual -- Alpha vs Beta P&L.

    Defaults to chain=auto_ai (the algo book) since manual chain has
    corrupt size_usdt in some legacy rows.
    """
    try:
        from risk_analytics import compute_pnl_attribution
        from database import db_conn
        days = min(int(request.args.get("days", 90)), 365)
        chain = (request.args.get("chain") or "auto_ai").strip().lower()
        if chain not in ("auto_ai", "manual"):
            chain = "auto_ai"
        with db_conn() as conn:
            return _ok(compute_pnl_attribution(conn, lookback_days=days, chain=chain))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/kelly")
def api_risk_kelly():
    """GET /api/risk/kelly -- Kelly Criterion sizing by score bucket."""
    try:
        from risk_analytics import compute_kelly_by_bucket
        from database import db_conn
        with db_conn() as conn:
            return _ok(compute_kelly_by_bucket(conn))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/alpha-decay")
def api_risk_alpha_decay():
    """GET /api/risk/alpha-decay -- How execution lag affects P&L."""
    try:
        from risk_analytics import compute_alpha_decay
        from database import db_conn
        with db_conn() as conn:
            return _ok(compute_alpha_decay(conn))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/blindspots")
def api_blindspots():
    """GET /api/blindspots — phrase miner + feature calibration from closed trades."""
    try:
        import ai_blindspots
        from database import db_conn
        with db_conn() as conn:
            phrases = ai_blindspots.mine_phrase_blindspots()
            features = ai_blindspots.compute_feature_calibration()
        return _ok({
            "phrases":  phrases,
            "features": features,
            "available": bool(phrases or features),
        })
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/self-review/run", methods=["POST"])
def api_self_review_run():
    """POST /api/self-review/run?limit=5 — process pending alpha-leak trades."""
    try:
        from database import db_conn
        import ai_self_review
        limit = int(request.args.get("limit", "5"))
        with db_conn() as conn:
            return _ok(ai_self_review.run_pending_reviews(conn, limit=limit))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/self-review/wishlist")
def api_self_review_wishlist():
    """GET /api/self-review/wishlist — recurring missed-signal suggestions."""
    try:
        from database import db_conn
        import ai_self_review
        with db_conn() as conn:
            return _ok({"wishlist": ai_self_review.aggregate_wishlist(conn)})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
