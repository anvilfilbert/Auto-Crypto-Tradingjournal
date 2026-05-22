"""
routes.futures_ai — Futures-AI auto-trader endpoints.

GET  /api/futures-ai/state    — full snapshot for the UI page
POST /api/futures-ai/state    — set state ("active"|"pause_now"|"pause_after_close")
GET  /api/futures-ai/log      — recent decisions (paginated)
"""
import traceback

from flask import Blueprint, request

from database import db_conn
from helpers import _ok, _err


bp = Blueprint("futures_ai", __name__)


@bp.route("/api/futures-ai/state")
def api_futures_ai_state():
    try:
        from trading import config as fa_config
        from trading import kill_switch
        with db_conn() as conn:
            return _ok({
                "config":       fa_config.snapshot(),
                "runtime":      kill_switch.evaluate(conn),
                "mode":         fa_config.get_mode(),
                "real_mode":    fa_config.is_real_mode(),
            })
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/state", methods=["POST"])
def api_futures_ai_set_state():
    try:
        from trading import config as fa_config
        body  = request.get_json(force=True, silent=True) or {}
        new   = (body.get("state") or "").strip().lower()
        if new not in fa_config.VALID_STATES:
            return _err(
                f"state must be one of {fa_config.VALID_STATES}", 400
            )
        # Refuse to flip to "active" when the env-level switch is off
        if new == "active" and not fa_config.is_enabled():
            return _err(
                "Cannot activate — FUTURES_AI_ENABLED=0 in env. Set it to 1 "
                "and restart the service to enable the chain.",
                409,
            )
        reason = (body.get("reason") or "user").strip()[:120]
        with db_conn() as conn:
            updated = fa_config.set_state(new, conn, reason=reason)
        return _ok({"state": updated})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/orchestrate-now", methods=["POST"])
def api_futures_ai_orchestrate_now():
    """Manually fire the orchestrator against the CURRENT in-Flask scan
    state. Used when a forced scan completes — the scheduler hook only
    fires for periodic scans, so this endpoint covers the gap until that
    hook is moved into ai_scanner._scan_thread itself."""
    try:
        import ai_scanner
        from trading import orchestrator
        scan_state = ai_scanner.get_state()
        if (scan_state or {}).get("status") != "completed":
            return _err(
                f"current scan status is {(scan_state or {}).get('status')}, "
                f"not 'completed' — wait for scan to finish",
                409,
            )
        result = orchestrator.on_scan_completed(scan_state)
        return _ok({
            "scan_setups":   len(scan_state.get("setups") or []),
            "orchestrator":  result,
        })
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/log")
def api_futures_ai_log():
    """Last N decision log entries."""
    try:
        n = int(request.args.get("n", "30"))
        n = max(1, min(n, 200))
        with db_conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, ts, event, symbol, direction, score, payload_json "
                "FROM futures_ai_log ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()]
        return _ok({"rows": rows})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
