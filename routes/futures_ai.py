"""
routes.futures_ai — Futures-AI auto-trader endpoints.

GET  /api/futures-ai/state    — full snapshot for the UI page
POST /api/futures-ai/state    — set state ("active"|"pause_now"|"pause_after_close")
GET  /api/futures-ai/log      — recent decisions (paginated)
"""
import json
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


@bp.route("/api/futures-ai/streak-mode", methods=["POST"])
def api_futures_ai_set_streak_mode():
    """
    Set streak mode (Feature 10 UI toggle): compound | euphoria_dampener | off.
    Persisted in settings.futures_ai_streak_mode. Takes effect immediately
    on next sized trade.
    """
    try:
        from trading import config as fa_config
        body = request.get_json(force=True, silent=True) or {}
        new  = (body.get("mode") or "").strip().lower()
        if new not in ("compound", "euphoria_dampener", "off"):
            return _err("mode must be one of: compound, euphoria_dampener, off", 400)
        with db_conn() as conn:
            updated = fa_config.set_streak_mode(new, conn=conn)
        return _ok({"streak_mode": updated})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/apgar", methods=["POST"])
def api_futures_ai_apgar():
    """
    Feature 7 — Submit Trade Apgar scorecard for today.
    Body: {q1, q2, q3, q4, q5: 0|1|2, notes?}
    Returns {total, passed} where passed = (total >= 7 AND no q is 0).
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        qs = []
        for k in ("q1", "q2", "q3", "q4", "q5"):
            v = body.get(k)
            if v is None:
                return _err(f"missing {k}", 400)
            try:
                qs.append(int(v))
            except (TypeError, ValueError):
                return _err(f"{k} must be 0/1/2", 400)
            if qs[-1] not in (0, 1, 2):
                return _err(f"{k} must be 0/1/2", 400)
        total = sum(qs)
        passed = 1 if (total >= 7 and 0 not in qs) else 0
        notes = (body.get("notes") or "")[:200]
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO apgar_sessions (q1,q2,q3,q4,q5,total,passed,notes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (qs[0], qs[1], qs[2], qs[3], qs[4], total, passed, notes))
            conn.commit()
        return _ok({"total": total, "passed": bool(passed), "qs": qs})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/apgar", methods=["GET"])
def api_futures_ai_apgar_today():
    """Return today's most recent Apgar scorecard (or 404 if none)."""
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM apgar_sessions WHERE ts >= date('now') "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return _ok({"present": False})
        return _ok({"present": True, "scorecard": dict(row)})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/readiness", methods=["POST"])
def api_futures_ai_readiness():
    """
    Feature 8 — Submit pre-session readiness check for today.
    Body: {mood, sleep, prior_pnl_flag, prep: 0-2, color: red|yellow|green, notes?}
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        fields = {}
        for k in ("mood", "sleep", "prior_pnl_flag", "prep"):
            v = body.get(k)
            if v is None:
                return _err(f"missing {k}", 400)
            try:
                fields[k] = int(v)
            except (TypeError, ValueError):
                return _err(f"{k} must be int 0-2", 400)
        color = (body.get("color") or "").strip().lower()
        if color not in ("red", "yellow", "green"):
            return _err("color must be one of: red, yellow, green", 400)
        notes = (body.get("notes") or "")[:200]
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO session_readiness (mood, sleep, prior_pnl_flag, prep, color, notes) "
                "VALUES (?,?,?,?,?,?)",
                (fields["mood"], fields["sleep"], fields["prior_pnl_flag"],
                 fields["prep"], color, notes))
            conn.commit()
        return _ok({"color": color, **fields})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/readiness", methods=["GET"])
def api_futures_ai_readiness_today():
    """Return today's most recent readiness self-report."""
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM session_readiness WHERE ts >= date('now') "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return _ok({"present": False})
        return _ok({"present": True, "report": dict(row)})
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


@bp.route("/api/futures-ai/positions")
def api_futures_ai_positions():
    """
    Returns the auto-trader's positions:
      open:          live positions on Bitget (real mode) OR open paper
                     rows (paper mode), enriched with current mark + unreal P&L
      recent_closed: last N closed trades (paper or real depending on mode)
    """
    try:
        from trading import config as fa_config
        from trading import bitget_trader
        with db_conn() as conn:
            n_closed = int(request.args.get("closed", "10"))
            n_closed = max(1, min(n_closed, 100))

            if fa_config.is_real_mode():
                # Live data from Bitget for currently-open positions
                try:
                    live = bitget_trader.get_open_positions()
                except Exception:
                    live = []
                open_rows = []
                # Pre-fetch tp_levels JSON for each auto-AI position so the UI
                # can show the full ladder + Opus's per-level percentages.
                tp_levels_by_sym = {}
                try:
                    for r in conn.execute(
                        "SELECT symbol, tp_levels FROM positions "
                        "WHERE chain='auto_ai' AND (close_time IS NULL OR close_time='') "
                        "AND tp_levels IS NOT NULL"
                    ).fetchall():
                        try:
                            tp_levels_by_sym[r["symbol"]] = json.loads(r["tp_levels"])
                        except Exception:
                            pass
                except Exception:
                    pass

                for p in live:
                    entry = float(p.get("entry_price") or 0)
                    mark  = float(p.get("mark_price") or 0)
                    is_long = (p.get("direction") or "").lower() == "long"
                    sign = 1 if is_long else -1
                    move_pct = ((mark - entry) / entry * 100 * sign) if entry else 0
                    open_rows.append({
                        "symbol":          p.get("symbol"),
                        "direction":       p.get("direction"),
                        "entry_price":     entry,
                        "mark_price":      mark,
                        "unrealized_pnl":  p.get("unrealized_pnl"),
                        "unrealized_pct":  round(move_pct, 2),
                        "size_contracts":  p.get("size_contracts"),
                        "notional_usdt":   p.get("notional_usdt"),
                        "leverage":        p.get("leverage"),
                        "preset_sl":       p.get("preset_sl"),
                        "preset_tp":       p.get("preset_tp"),
                        "tp_levels":       tp_levels_by_sym.get(p.get("symbol")) or [],
                    })
                closed = [dict(r) for r in conn.execute(
                    "SELECT symbol, direction, entry_price, close_price, "
                    "realized_pnl, open_time, close_time, setup_type, "
                    "setup_score, close_reason, is_hedge, "
                    "size_usdt, leverage "
                    "FROM positions WHERE chain='auto_ai' AND "
                    "close_time IS NOT NULL AND close_time != '' "
                    "ORDER BY close_time DESC LIMIT ?", (n_closed,)
                ).fetchall()]
                source = "real"
            else:
                # Paper mode — paper_positions table
                open_rows = [dict(r) for r in conn.execute("""
                    SELECT id, symbol, direction, score_consensus, entry_price,
                           current_sl, tp1_price, tp2_price, notional_usdt,
                           leverage, opened_at, tp1_hit, archetype
                    FROM paper_positions
                    WHERE status='open'
                    ORDER BY opened_at DESC
                """).fetchall()]
                closed = [dict(r) for r in conn.execute("""
                    SELECT id, symbol, direction, score_consensus, entry_price,
                           tp2_price, realized_pnl, close_reason, opened_at,
                           closed_at, archetype
                    FROM paper_positions
                    WHERE status='closed'
                    ORDER BY closed_at DESC LIMIT ?
                """, (n_closed,)).fetchall()]
                source = "paper"

        # Equity passed so the frontend can render "% of portfolio" on each
        # closed trade without a second fetch. Best-effort — falls back to
        # starting equity if the live read fails.
        equity_now = None
        try:
            from trading import kill_switch as _ks
            with db_conn() as _conn2:
                equity_now = _ks._equity_now(_conn2)
        except Exception:
            equity_now = None
        return _ok({
            "source":         source,
            "open":           open_rows,
            "recent_closed":  closed,
            "equity_usdt":    equity_now,
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
