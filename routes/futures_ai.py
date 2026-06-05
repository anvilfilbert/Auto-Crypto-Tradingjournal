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
                # Pre-fetch tp_levels JSON + open_time for each auto-AI position
                # so the UI can show the full ladder + Opus's per-level
                # percentages + when the position was entered.
                tp_levels_by_sym = {}
                open_time_by_sym = {}
                try:
                    for r in conn.execute(
                        "SELECT symbol, tp_levels, open_time FROM positions "
                        "WHERE chain='auto_ai' AND (close_time IS NULL OR close_time='') "
                    ).fetchall():
                        if r["tp_levels"]:
                            try:
                                tp_levels_by_sym[r["symbol"]] = json.loads(r["tp_levels"])
                            except Exception:
                                pass
                        if r["open_time"]:
                            open_time_by_sym[r["symbol"]] = r["open_time"]
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
                        "achieved_profits": p.get("achieved_profits", 0),
                        "unrealized_pct":  round(move_pct, 2),
                        "size_contracts":  p.get("size_contracts"),
                        "notional_usdt":   p.get("notional_usdt"),
                        "leverage":        p.get("leverage"),
                        "preset_sl":       p.get("preset_sl"),
                        "preset_tp":       p.get("preset_tp"),
                        "tp_levels":       tp_levels_by_sym.get(p.get("symbol")) or [],
                        "open_time":       open_time_by_sym.get(p.get("symbol")),
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
                # Paper mode — enrich paper_positions with a live mark-price
                # lookup + derived fields so the UI renders the SAME shape
                # as real mode. Paper must be 1:1 with live: same columns,
                # same computed semantics, same color logic.
                raw = [dict(r) for r in conn.execute("""
                    SELECT id, symbol, direction, entry_price,
                           current_sl, sl_price, tp1_price, tp2_price,
                           notional_usdt, leverage, opened_at, tp1_hit
                    FROM paper_positions
                    WHERE status='open'
                    ORDER BY opened_at DESC
                """).fetchall()]
                # Batched mark-price lookup — same client the orchestrator
                # uses for paper position management.
                mark_by_sym = {}
                syms_to_lookup = [r["symbol"] for r in raw if r.get("symbol")]
                if syms_to_lookup:
                    try:
                        import bitget_client
                        mark_by_sym = bitget_client.get_mark_prices(syms_to_lookup) or {}
                    except Exception:
                        mark_by_sym = {}
                open_rows = []
                for r in raw:
                    sym      = r["symbol"]
                    entry    = float(r["entry_price"] or 0)
                    notional = float(r["notional_usdt"] or 0)
                    is_long  = (r["direction"] or "").lower() == "long"
                    sign     = 1 if is_long else -1
                    mark     = float(mark_by_sym.get(sym) or mark_by_sym.get((sym or "").upper()) or 0)
                    move_pct = ((mark - entry) / entry * 100 * sign) if (entry and mark) else 0.0
                    unreal   = (notional * move_pct / 100.0) if notional else 0.0
                    size_ct  = (notional / entry) if entry else 0.0
                    # TP1 partial parity: when tp1_hit=1, half-size closed at
                    # tp1 — mirror Bitget's `achieved_profits` semantics.
                    ach = 0.0
                    if r.get("tp1_hit") and r.get("tp1_price") and entry:
                        tp1 = float(r["tp1_price"])
                        ach = (notional * 0.5) * ((tp1 - entry) / entry) * sign
                    tp_levels = []
                    if r.get("tp1_price"):
                        tp_levels.append({"idx": 1, "price": r["tp1_price"],
                                          "pct": 50, "hit": bool(r.get("tp1_hit"))})
                    if r.get("tp2_price"):
                        tp_levels.append({"idx": 2, "price": r["tp2_price"],
                                          "pct": 50, "hit": False})
                    open_rows.append({
                        "symbol":           sym,
                        "direction":        r["direction"],
                        "entry_price":      entry,
                        "mark_price":       mark,
                        "unrealized_pnl":   round(unreal, 4),
                        "achieved_profits": round(ach, 4),
                        "unrealized_pct":   round(move_pct, 2),
                        "size_contracts":   round(size_ct, 4),
                        "notional_usdt":    notional,
                        "leverage":         r["leverage"],
                        "preset_sl":        r.get("current_sl") or r.get("sl_price"),
                        "preset_tp":        r.get("tp1_price"),
                        "tp_levels":        tp_levels,
                        "open_time":        r.get("opened_at"),
                    })
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


@bp.route("/api/futures-ai/model-comparison")
def api_futures_ai_model_comparison():
    """
    Model Comparison analytics. Aggregates `shadow_responses` + joins
    paper_positions on (symbol, ts) to produce:
      - per_model:   n_calls, score_distribution, agreement-rate vs primary,
                     would_open count (score≥threshold), mean/p95 latency,
                     cost_usd, error_rate, mean score, score-direction breakdown
      - per_trade:   one row per opened paper trade (or real auto_ai trade),
                     listing what each shadow model scored at the time of
                     the scanner pass
      - insights:    rule-based summaries surfaced as bullet points

    Query params:
      window  — '24h' (default), '7d', '30d', 'all'
      min_score — score threshold for the "would_open" rollup (default 6 to
                  match the historical Stage-2/3a CONSENSUS gate)
    """
    import re as _re, json as _json
    try:
        window = (request.args.get("window") or "24h").lower()
        try:
            min_score = int(request.args.get("min_score") or 6)
        except Exception:
            min_score = 6

        window_sql = {
            "24h": "datetime('now','-24 hours')",
            "7d":  "datetime('now','-7 days')",
            "30d": "datetime('now','-30 days')",
            "all": "datetime('1970-01-01')",
        }.get(window, "datetime('now','-24 hours')")

        # Robust score+reason extractor — shadow_text and primary_text are
        # LLM outputs that may be {"score":7,...} OR wrapped in ```json
        # fences OR include prose after the JSON. We pull the FIRST {...}
        # JSON-shaped chunk for score/direction/reason, plus any prose that
        # follows (some models add "**Rationale:** ..." after the JSON).
        _JSON_RE = _re.compile(r"\{[^{}]*\}", _re.DOTALL)
        _PROSE_RE = _re.compile(
            r"(?:rationale|reason(?:ing)?)\s*[:\-—]\s*(.+?)(?:\n\n|$)",
            _re.IGNORECASE | _re.DOTALL,
        )

        def _parse(text):
            """Returns dict {score, direction, reason} — any field may be None."""
            out = {"score": None, "direction": None, "reason": None}
            if not text:
                return out
            cleaned = text.replace("```json", "").replace("```", "")
            obj = None
            # 1) Try whole-string JSON first
            try:
                tmp = _json.loads(cleaned)
                if isinstance(tmp, dict):
                    obj = tmp
            except Exception:
                pass
            # 2) Fall back to first {...} chunk
            if obj is None:
                m = _JSON_RE.search(cleaned)
                if m:
                    try:
                        tmp = _json.loads(m.group(0))
                        if isinstance(tmp, dict):
                            obj = tmp
                    except Exception:
                        pass
            if obj:
                s = obj.get("score")
                d = obj.get("direction")
                r = obj.get("reason")
                out["score"] = int(s) if s is not None else None
                out["direction"] = (d or "").lower() if isinstance(d, str) else None
                if isinstance(r, str) and r.strip():
                    out["reason"] = r.strip()[:500]
            # 3) Look for prose rationale AFTER the JSON (common pattern)
            if not out["reason"]:
                m = _PROSE_RE.search(cleaned)
                if m:
                    out["reason"] = m.group(1).strip()[:500]
            return out

        with db_conn() as conn:
            rows = conn.execute(f"""
                SELECT id, ts, primary_request_id, primary_module,
                       primary_model, primary_text,
                       shadow_provider, shadow_model, shadow_text,
                       shadow_latency_ms, shadow_input_tokens, shadow_output_tokens,
                       shadow_cost_usd, shadow_error, symbol
                FROM shadow_responses
                WHERE primary_module='scanner_quick'
                  AND ts >= {window_sql}
                ORDER BY ts DESC
            """).fetchall()

            # ── Per-model aggregation ──────────────────────────────────────
            from collections import defaultdict
            per_model = defaultdict(lambda: {
                "n_calls":           0,
                "n_with_score":      0,
                "errors":            0,
                "score_sum":         0,
                "score_histogram":   {i: 0 for i in range(11)},
                "direction_long":    0,
                "direction_short":   0,
                "would_open":        0,
                "agree_with_primary": 0,
                "disagree_strong":   0,   # |Δscore| ≥ 3
                "direction_flips":   0,
                "latency_ms":        [],
                "cost_usd":          0.0,
                "input_tokens":      0,
                "output_tokens":     0,
            })
            # Group by primary_request_id so we can compare primary vs shadows.
            by_req = defaultdict(list)
            for r in rows:
                by_req[r["primary_request_id"]].append(dict(r))

            for req_id, group in by_req.items():
                primary_text = group[0].get("primary_text")
                p = _parse(primary_text)
                p_score, p_dir = p["score"], p["direction"]
                for r in group:
                    m = r["shadow_model"] or "unknown"
                    pm = per_model[m]
                    pm["n_calls"]      += 1
                    pm["cost_usd"]     += float(r.get("shadow_cost_usd") or 0)
                    pm["input_tokens"] += int(r.get("shadow_input_tokens") or 0)
                    pm["output_tokens"]+= int(r.get("shadow_output_tokens") or 0)
                    lat = r.get("shadow_latency_ms")
                    if lat is not None:
                        pm["latency_ms"].append(int(lat))
                    if r.get("shadow_error"):
                        pm["errors"] += 1
                    sp = _parse(r.get("shadow_text"))
                    s, d = sp["score"], sp["direction"]
                    if s is None:
                        continue
                    pm["n_with_score"] += 1
                    pm["score_sum"]    += s
                    pm["score_histogram"][max(0, min(10, s))] += 1
                    if d == "long":  pm["direction_long"]  += 1
                    if d == "short": pm["direction_short"] += 1
                    if s >= min_score:
                        pm["would_open"] += 1
                    if p_score is not None:
                        if s == p_score:
                            pm["agree_with_primary"] += 1
                        if abs(s - p_score) >= 3:
                            pm["disagree_strong"] += 1
                        if p_dir and d and p_dir != d:
                            pm["direction_flips"] += 1

            def _pcts(arr, qs):
                if not arr: return {q: None for q in qs}
                s = sorted(arr); n = len(s)
                return {q: s[min(n-1, max(0, int(round(q*(n-1)))))] for q in qs}

            # Primary-model aggregates derived from the by_req grouping.
            # We pool over all primary responses observed, so the page
            # can show Haiku side-by-side with the shadows.
            primary_agg = {
                "name":             "claude-haiku-4-5 (primary)",
                "n_calls":          0,
                "n_with_score":     0,
                "score_sum":        0,
                "score_histogram":  {i: 0 for i in range(11)},
                "direction_long":   0,
                "direction_short":  0,
                "would_open":       0,
            }
            for req_id, group in by_req.items():
                primary_text = group[0].get("primary_text")
                p2 = _parse(primary_text)
                s, d = p2["score"], p2["direction"]
                primary_agg["n_calls"] += 1
                if s is None: continue
                primary_agg["n_with_score"] += 1
                primary_agg["score_sum"] += s
                primary_agg["score_histogram"][max(0, min(10, s))] += 1
                if d == "long":  primary_agg["direction_long"]  += 1
                if d == "short": primary_agg["direction_short"] += 1
                if s >= min_score: primary_agg["would_open"] += 1

            # Finalize per-model dicts
            models_out = []
            primary_n = primary_agg["n_with_score"] or 1
            for model, pm in per_model.items():
                lat = pm["latency_ms"]
                pcts = _pcts(lat, (0.5, 0.95))
                n_score = pm["n_with_score"] or 1
                models_out.append({
                    "model":             model,
                    "n_calls":           pm["n_calls"],
                    "n_with_score":      pm["n_with_score"],
                    "errors":            pm["errors"],
                    "error_rate":        (pm["errors"] / pm["n_calls"]) if pm["n_calls"] else 0,
                    "mean_score":        (pm["score_sum"] / n_score) if pm["n_with_score"] else None,
                    "score_histogram":   pm["score_histogram"],
                    "direction_long":    pm["direction_long"],
                    "direction_short":   pm["direction_short"],
                    "long_share":        (pm["direction_long"] /
                                          max(1, pm["direction_long"] + pm["direction_short"])),
                    "would_open":        pm["would_open"],
                    "would_open_rate":   (pm["would_open"] / n_score) if pm["n_with_score"] else 0,
                    "agree_with_primary": pm["agree_with_primary"],
                    "agreement_rate":    (pm["agree_with_primary"] / n_score) if pm["n_with_score"] else 0,
                    "disagree_strong":   pm["disagree_strong"],
                    "strong_disagree_rate": (pm["disagree_strong"] / n_score) if pm["n_with_score"] else 0,
                    "direction_flips":   pm["direction_flips"],
                    "latency_p50_ms":    pcts.get(0.5),
                    "latency_p95_ms":    pcts.get(0.95),
                    "cost_usd":          round(pm["cost_usd"], 5),
                    "cost_per_call":     round(pm["cost_usd"] / pm["n_calls"], 6) if pm["n_calls"] else 0,
                    "input_tokens":      pm["input_tokens"],
                    "output_tokens":     pm["output_tokens"],
                })
            models_out.sort(key=lambda m: -m["n_calls"])
            # Primary in the same shape — first row, badged
            primary_out = {
                "model":             primary_agg["name"],
                "is_primary":        True,
                "n_calls":           primary_agg["n_calls"],
                "n_with_score":      primary_agg["n_with_score"],
                "errors":            0,
                "error_rate":        0,
                "mean_score":        (primary_agg["score_sum"] / primary_n) if primary_agg["n_with_score"] else None,
                "score_histogram":   primary_agg["score_histogram"],
                "direction_long":    primary_agg["direction_long"],
                "direction_short":   primary_agg["direction_short"],
                "long_share":        (primary_agg["direction_long"] /
                                      max(1, primary_agg["direction_long"] + primary_agg["direction_short"])),
                "would_open":        primary_agg["would_open"],
                "would_open_rate":   (primary_agg["would_open"] / primary_n) if primary_agg["n_with_score"] else 0,
                "agree_with_primary": primary_agg["n_with_score"],
                "agreement_rate":    1.0,
                "disagree_strong":   0,
                "strong_disagree_rate": 0,
                "direction_flips":   0,
                "latency_p50_ms":    None,
                "latency_p95_ms":    None,
                "cost_usd":          0.0,
                "cost_per_call":     0.0,
                "input_tokens":      0,
                "output_tokens":     0,
            }

            # ── Per-trade detail ───────────────────────────────────────────
            # Join shadow rows to paper_positions (paper mode) or positions
            # (real mode) on symbol + ±10min ts window. Closed trades carry
            # P&L; open trades show live mark / unrealised.
            from trading import config as fa_config
            tbl = "positions" if fa_config.is_real_mode() else "paper_positions"
            if tbl == "positions":
                trades = [dict(r) for r in conn.execute(f"""
                    SELECT id, symbol, direction, entry_price,
                           close_price, realized_pnl, open_time, close_time,
                           setup_score AS score_consensus,
                           archetype_at_open AS archetype,
                           tp_levels,
                           CASE WHEN close_time IS NULL OR close_time='' THEN 'open' ELSE 'closed' END AS status
                    FROM positions
                    WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0)
                      AND open_time >= {window_sql}
                    ORDER BY open_time DESC LIMIT 200
                """).fetchall()]
                trades_ts_col = "open_time"
            else:
                trades = [dict(r) for r in conn.execute(f"""
                    SELECT id, symbol, direction, entry_price,
                           NULL AS close_price, realized_pnl,
                           opened_at AS open_time, closed_at AS close_time,
                           score_consensus,
                           COALESCE(archetype_at_open, archetype) AS archetype,
                           tp_levels, status
                    FROM paper_positions
                    WHERE (is_hedge IS NULL OR is_hedge=0)
                      AND opened_at >= {window_sql}
                    ORDER BY opened_at DESC LIMIT 200
                """).fetchall()]
                trades_ts_col = "opened_at"

            # Build symbol+ts buckets from shadow rows (±10 min window).
            # Sparse data so we just iterate per-trade — N×M is fine at 200×1000.
            shadow_idx = defaultdict(list)  # symbol -> list of dict
            for r in rows:
                sym = r["symbol"]
                if not sym: continue
                shadow_idx[sym].append(dict(r))

            # Tally each trade's LLM cost — sum of shadow_cost_usd across all
            # shadow rows matched within ±10min of trade open. Lets the UI
            # surface "is the LLM spend worth the trade P&L?" per trade.
            trade_cost_index = defaultdict(float)
            trade_cost_calls = defaultdict(int)

            for t in trades:
                sym = t.get("symbol")
                t_ts = t.get("open_time")
                if not sym or not t_ts:
                    t["models"] = {}
                    continue
                model_scores = {}
                # Parse t_ts (sqlite local time strings)
                try:
                    import datetime as _dt2
                    t_dt = _dt2.datetime.fromisoformat(str(t_ts).replace(" ", "T"))
                except Exception:
                    t["models"] = {}
                    continue
                window_sec = 600   # ±10 min
                for r in shadow_idx.get(sym, []):
                    try:
                        r_dt = _dt2.datetime.fromisoformat(str(r["ts"]).replace(" ", "T"))
                    except Exception:
                        continue
                    if abs((r_dt - t_dt).total_seconds()) > window_sec:
                        continue
                    m = r.get("shadow_model")
                    sp = _parse(r.get("shadow_text"))
                    if sp["score"] is None:
                        continue
                    delta = abs((r_dt - t_dt).total_seconds())
                    cur = model_scores.get(m)
                    if cur is None or cur["delta_sec"] > delta:
                        model_scores[m] = {
                            "score":     sp["score"],
                            "direction": sp["direction"],
                            "reason":    sp["reason"],
                            "delta_sec": int(delta),
                        }
                # Add the primary (Haiku) read from primary_text on the
                # closest-matching shadow row.
                primary_best = None
                primary_best_delta = None
                for r in shadow_idx.get(sym, []):
                    try:
                        r_dt = _dt2.datetime.fromisoformat(str(r["ts"]).replace(" ", "T"))
                    except Exception:
                        continue
                    delta = abs((r_dt - t_dt).total_seconds())
                    if delta > window_sec:
                        continue
                    pp = _parse(r.get("primary_text"))
                    if pp["score"] is None:
                        continue
                    if primary_best is None or delta < primary_best_delta:
                        primary_best = pp
                        primary_best_delta = delta
                if primary_best is not None:
                    model_scores["claude-haiku-4-5 (primary)"] = {
                        "score":      primary_best["score"],
                        "direction":  primary_best["direction"],
                        "reason":     primary_best["reason"],
                        "delta_sec":  int(primary_best_delta),
                        "is_primary": True,
                    }
                t["models"] = model_scores

                # Tally LLM cost attributable to this trade — sum of
                # shadow_cost_usd for shadow rows matched within window.
                # (Primary Haiku cost lives in token_usage; we approximate
                # it as ~5× the cheapest shadow per call to keep this
                # endpoint self-contained.)
                tcost = 0.0; ncalls = 0
                cheapest_per_call = None
                for r in shadow_idx.get(sym, []):
                    try:
                        r_dt = _dt2.datetime.fromisoformat(str(r["ts"]).replace(" ", "T"))
                    except Exception:
                        continue
                    if abs((r_dt - t_dt).total_seconds()) > window_sec:
                        continue
                    c = float(r.get("shadow_cost_usd") or 0)
                    tcost += c
                    ncalls += 1
                    if c > 0 and (cheapest_per_call is None or c < cheapest_per_call):
                        cheapest_per_call = c
                # Estimate Haiku's primary call cost: Anthropic Haiku 4.5
                # input ~$0.80/M, output ~$4/M. Rough flat $0.001 per scanner_quick.
                primary_cost_est = 0.001 if primary_best is not None else 0.0
                total_cost = tcost + primary_cost_est
                t["shadow_cost_usd"]   = round(tcost, 6)
                t["primary_cost_est"]  = round(primary_cost_est, 6)
                t["total_cost_usd"]    = round(total_cost, 6)
                t["n_shadow_calls"]    = ncalls
                pnl_val = t.get("realized_pnl")
                if pnl_val is not None:
                    t["net_pnl"] = round(float(pnl_val) - total_cost, 4)
                else:
                    t["net_pnl"] = None

            # ── Insights ───────────────────────────────────────────────────
            insights = []
            if primary_out["n_with_score"]:
                # 1. Throughput contrast — which shadow would open more trades?
                contrast = []
                for m in models_out:
                    if m["n_with_score"] < 20: continue  # ignore tiny samples
                    delta = m["would_open_rate"] - primary_out["would_open_rate"]
                    if abs(delta) >= 0.05:
                        contrast.append((m["model"], delta, m["n_with_score"]))
                contrast.sort(key=lambda x: -abs(x[1]))
                for model, delta, n_scored in contrast[:3]:
                    direction = "MORE" if delta > 0 else "FEWER"
                    insights.append({
                        "tag": "throughput",
                        "severity": "medium",
                        "text": f"{model.split('/')[-1]} would open {direction} trades than Haiku — "
                                f"would_open_rate {abs(delta)*100:.0f}pp {'higher' if delta>0 else 'lower'} "
                                f"(n={n_scored} scored).",
                    })

                # 2. Direction bias contrast
                for m in models_out:
                    if m["n_with_score"] < 20: continue
                    p_long_share = primary_out["long_share"]
                    delta = m["long_share"] - p_long_share
                    if abs(delta) >= 0.10:
                        insights.append({
                            "tag": "direction_bias",
                            "severity": "medium",
                            "text": f"{m['model'].split('/')[-1]} direction mix diverges from Haiku — "
                                    f"Long-share {m['long_share']*100:.0f}% vs Haiku {p_long_share*100:.0f}%.",
                        })

                # 3. Cost efficiency
                if models_out:
                    cheapest = min(models_out, key=lambda x: x["cost_per_call"] if x["n_calls"] else 1)
                    if cheapest["n_calls"] >= 50:
                        insights.append({
                            "tag": "cost",
                            "severity": "low",
                            "text": f"Cheapest shadow per call: {cheapest['model'].split('/')[-1]} at "
                                    f"${cheapest['cost_per_call']:.6f}/call (n={cheapest['n_calls']}).",
                        })

                # 4. Strong-disagreement flag
                for m in models_out:
                    if m["n_with_score"] < 20: continue
                    if m["strong_disagree_rate"] >= 0.25:
                        insights.append({
                            "tag": "disagreement",
                            "severity": "high",
                            "text": f"{m['model'].split('/')[-1]} disagrees STRONGLY with Haiku "
                                    f"({m['strong_disagree_rate']*100:.0f}% of calls have |Δscore|≥3). "
                                    f"This is the model worth analysing for missed wins / saved losses.",
                        })

            if not insights:
                insights.append({
                    "tag": "info",
                    "severity": "info",
                    "text": "Not enough scored shadow data yet. Insights surface once each model has ≥20 scored calls in the window.",
                })

            # ── Cost-vs-P&L summary (aggregate across all joined trades) ───
            total_pnl   = 0.0
            total_cost  = 0.0
            won = lost = breakeven = open_ct = 0
            for tr_ in trades:
                pnl = tr_.get("realized_pnl")
                cost = float(tr_.get("total_cost_usd") or 0)
                total_cost += cost
                if pnl is None:
                    open_ct += 1
                    continue
                pnl = float(pnl)
                total_pnl += pnl
                if pnl > 0.0001:  won += 1
                elif pnl < -0.0001: lost += 1
                else: breakeven += 1

            closed_n = won + lost + breakeven
            # Also: overall shadow spend in window (independent of trade join).
            # `rows` is sqlite3.Row (not dict), so use index access.
            window_shadow_cost = 0.0
            for r in rows:
                try:
                    window_shadow_cost += float(r["shadow_cost_usd"] or 0)
                except Exception:
                    pass
            cost_summary = {
                "total_realized_pnl_usd":   round(total_pnl, 4),
                "total_attributed_cost_usd": round(total_cost, 6),
                "net_pnl_usd":              round(total_pnl - total_cost, 4),
                "trades_closed":            closed_n,
                "trades_open":              open_ct,
                "trades_won":               won,
                "trades_lost":              lost,
                "trades_breakeven":         breakeven,
                "win_rate":                 (won / closed_n) if closed_n else None,
                "avg_pnl_per_trade":        (total_pnl / closed_n) if closed_n else None,
                "avg_cost_per_trade":       (total_cost / max(1, len(trades))),
                "roi_on_llm_spend":         ((total_pnl - total_cost) / total_cost)
                                            if total_cost > 0 else None,
                "window_shadow_cost_usd":   round(window_shadow_cost, 4),
            }

            return _ok({
                "window":         window,
                "min_score":      min_score,
                "n_shadow_rows":  len(rows),
                "n_primary_reqs": len(by_req),
                "primary":        primary_out,
                "models":         models_out,
                "trades":         trades,
                "insights":       insights,
                "table_source":   tbl,
                "cost_summary":   cost_summary,
            })
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/learner/symbol", methods=["POST"])
def api_learner_symbol_run():
    """L-0 (Master plan): trigger the per-symbol learner manually. Will
    eventually be called by a cron every 6h once the daily-report
    scheduler ships in Week 3."""
    try:
        from trading import learner_symbol
        with db_conn() as conn:
            return _ok(learner_symbol.evaluate_and_update(conn))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/learned-params")
def api_learned_params():
    """L-0 (Master plan): current learned_params + recent learner_log."""
    try:
        from trading import learned
        limit = int(request.args.get("limit", "20"))
        with db_conn() as conn:
            return _ok({
                "params": learned.all_params(conn),
                "recent_log": learned.recent_log(conn, limit=limit),
            })
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/edge-decay")
def api_edge_decay():
    """R-4 (Master plan): per-archetype CUSUM + Page-Hinkley edge-decay state.

    Query params:
      window_days: default 30
    """
    try:
        from trading import edge_decay
        wd = int(request.args.get("window_days", "30"))
        wd = max(7, min(wd, 180))
        with db_conn() as conn:
            return _ok({"window_days": wd, "by_archetype": edge_decay.evaluate(conn, window_days=wd)})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/futures-ai/r3-backfill", methods=["POST"])
def api_r3_backfill():
    """R-3 (Master plan): run funding + liq_distance backfill inside the
    journal process where Bitget env vars are loaded."""
    try:
        from trading import r3_funding_liq
        with db_conn() as conn:
            return _ok(r3_funding_liq.run_all(conn))
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


# ── /api/futures-ai/stats — full statistics page payload ──────────────────
#
# Returns every panel for the Futures-AI Stats UI in a single response so
# the frontend can render the page from one fetch. Window is configurable
# via ?window=7d|30d|90d|all (default 30d). Scope: chain='auto_ai' AND
# hedge positions excluded.

_WINDOW_HOURS = {"7d": 7*24, "30d": 30*24, "90d": 90*24, "all": None}

_SESSION_BUCKETS = [
    # (label, lo_hour_inclusive, hi_hour_exclusive) — UTC
    ("Asia",      0,  8),
    ("London",    8, 13),
    ("NY-AM",    13, 16),
    ("NY-Overlap", 16, 18),
    ("NY-PM",    18, 22),
    ("Off-hours", 22, 24),
]
_DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _session_of(hour):
    for lbl, lo, hi in _SESSION_BUCKETS:
        if lo <= hour < hi:
            return lbl
    return "Off-hours"


def _hold_bucket(hours):
    if hours is None:           return "?"
    if hours < 1:               return "<1h"
    if hours < 4:               return "1-4h"
    if hours < 24:              return "4-24h"
    if hours < 24*7:            return "1-7d"
    return ">7d"


def _r_multiple(row):
    """R per trade.

    Preferred path (migration 67+): when sl_price is persisted on the
    position, compute true realized R = realized_pnl / (|entry - sl| × size).
    This anchors R to the initial 1R risk plan and is accurate regardless
    of close_reason or how many TPs hit.

    Fallback (historical positions, sl_price NULL): approximate from
    close_reason + first_tp_rr:
        SL                                      → -1.0
        BE_stop / BE / MAE_cut                  →  0.0
        TP (and first_tp_rr present)            →  first_tp_rr
        manual_close + profit, with rr planned  →  first_tp_rr * sign(pnl) * 0.5
        otherwise                               →  None
    """
    try:
        pnl  = float(row.get("realized_pnl") or 0)
        sl   = row.get("sl_price")
        entry = row.get("entry_price")
        size  = row.get("size_contracts") or row.get("size_usdt") or 0

        # Preferred: true R from sl_price snapshot
        if sl is not None and entry is not None and float(sl) > 0 and float(entry) > 0:
            try:
                size_f = float(size) if not isinstance(size, str) else _strip_unit_to_float(size)
            except Exception:
                size_f = 0
            risk = abs(float(entry) - float(sl)) * (size_f or 0)
            if risk > 0:
                return pnl / risk

        # Fallback: close_reason + first_tp_rr heuristic
        reason = (row.get("close_reason") or "").upper()
        rr_planned = row.get("first_tp_rr")
        if reason in ("SL",):
            return -1.0
        if reason in ("BE_STOP", "BE", "MAE_CUT"):
            return 0.0
        if reason == "TP" and rr_planned:
            try: return float(rr_planned)
            except Exception: return None
        if reason and pnl != 0 and rr_planned:
            try: return float(rr_planned) * (0.5 if pnl > 0 else -0.5)
            except Exception: return None
        return None
    except Exception:
        return None


def _strip_unit_to_float(s: str) -> float:
    """size_contracts is sometimes stored as '208.0XPL' (number + base asset).
    Strip the trailing letters and parse the numeric prefix."""
    try:
        import re
        m = re.match(r"^[+-]?\d*\.?\d+", str(s) or "")
        return float(m.group(0)) if m else 0.0
    except Exception:
        return 0.0


def _bucket_table(rows, key_fn, label_order=None):
    """Group rows by key_fn(row), aggregate count / WR / avg P&L / avg R / total P&L."""
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        buckets[k].append(r)
    out = []
    keys = label_order or sorted(buckets.keys())
    for k in keys:
        if k not in buckets:
            continue
        b = buckets[k]
        n = len(b)
        wins = sum(1 for x in b if (x.get("realized_pnl") or 0) > 0)
        pnl_sum = sum((x.get("realized_pnl") or 0) for x in b)
        rs = [_r_multiple(x) for x in b]
        rs = [r for r in rs if r is not None]
        out.append({
            "key":         k,
            "count":       n,
            "wins":        wins,
            "wr_pct":      round(wins / n * 100, 1) if n else 0,
            "avg_pnl":     round(pnl_sum / n, 2) if n else 0,
            "total_pnl":   round(pnl_sum, 2),
            "avg_r":       round(sum(rs) / len(rs), 2) if rs else None,
            "total_r":     round(sum(rs), 2) if rs else None,
            "r_n":         len(rs),
        })
    return out


@bp.route("/api/futures-ai/stats")
def api_futures_ai_stats():
    """Full stats payload for the Futures-AI Stats page.

    Query params:
      window:  7d | 30d | 90d | all   (default 30d)
    """
    try:
        from statistics import mean, pstdev

        window = (request.args.get("window") or "30d").lower()
        if window not in _WINDOW_HOURS:
            window = "30d"
        hours = _WINDOW_HOURS[window]

        with db_conn() as conn:
            base_sql = (
                "SELECT id, symbol, direction, entry_price, close_price, "
                "size_contracts, size_usdt, leverage, sl_price, "
                "open_time, close_time, realized_pnl, close_reason, "
                "setup_type, archetype_at_open, execution_grade, "
                "setup_score, ai_score_at_open, first_tp_rr, "
                "funding_paid_usd, liq_distance_atr "  # R-3 additions
                "FROM positions "
                "WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0) "
                "AND close_time IS NOT NULL AND close_time != ''"
            )
            params: tuple = ()
            if hours is not None:
                base_sql += " AND close_time >= datetime('now', ?)"
                params = (f"-{int(hours)} hours",)
            base_sql += " ORDER BY close_time ASC"
            rows = [dict(r) for r in conn.execute(base_sql, params).fetchall()]

        # ── Summary tiles ─────────────────────────────────────────────────
        n_total = len(rows)
        pnls = [(r.get("realized_pnl") or 0) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        pnl_sum = sum(pnls)
        avg_pnl = pnl_sum / n_total if n_total else 0
        wr_pct  = (len(wins) / n_total * 100) if n_total else 0

        # R per trade (skip if no SL)
        rs = [r for r in (_r_multiple(x) for x in rows) if r is not None]
        total_r = sum(rs) if rs else 0
        avg_r   = (total_r / len(rs)) if rs else 0

        # Expectancy ($ per trade)
        expectancy = avg_pnl

        # Profit factor = sum(wins) / |sum(losses)|
        sum_wins   = sum(wins)
        sum_losses = abs(sum(losses)) if losses else 0
        profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else None

        # Per-day return series for Sharpe / Sortino / Calmar
        from collections import defaultdict
        daily_pnl = defaultdict(float)
        for r in rows:
            d = (r["close_time"] or "")[:10]
            if d:
                daily_pnl[d] += (r.get("realized_pnl") or 0)
        days_sorted = sorted(daily_pnl.keys())

        # Equity curve (cumulative) — over closes, value at each close
        cum = 0.0
        equity_curve = []
        for r in rows:
            cum += (r.get("realized_pnl") or 0)
            equity_curve.append({
                "t":   r["close_time"],
                "v":   round(cum, 4),
                "sym": r["symbol"],
                "pnl": round(r.get("realized_pnl") or 0, 2),
            })

        # Daily bars (one per calendar day in the window)
        daily_bars = [{"d": d, "pnl": round(daily_pnl[d], 2)} for d in days_sorted]

        # Risk metrics — Sharpe (daily, annualize × sqrt(365))
        daily_returns = [daily_pnl[d] for d in days_sorted]
        sharpe = None
        sortino = None
        if len(daily_returns) >= 3:
            mu = mean(daily_returns)
            sigma = pstdev(daily_returns)
            if sigma > 0:
                sharpe = round((mu / sigma) * (365 ** 0.5), 2)
            downside = [r for r in daily_returns if r < 0]
            if downside:
                ds = pstdev(downside) if len(downside) >= 2 else abs(downside[0])
                if ds > 0:
                    sortino = round((mu / ds) * (365 ** 0.5), 2)

        # Max DD (% of running peak, on cumulative curve)
        max_dd_pct = 0.0
        peak = 0.0
        run = 0.0
        for p in pnls:
            run += p
            if run > peak:
                peak = run
            if peak > 0:
                dd = (run - peak) / peak * 100
                if dd < max_dd_pct:
                    max_dd_pct = dd
        max_dd_pct = round(max_dd_pct, 2)

        # Calmar = annualized return / |max DD %|. Approximate annualized return
        # from window: pnl_sum / days × 365 / starting_value(=abs first peak or 100).
        # Use 100 as a stable denominator since starting_equity varies.
        days_n = max(len(days_sorted), 1)
        annualized_pnl = (pnl_sum / days_n) * 365
        calmar = round((annualized_pnl / abs(max_dd_pct)), 2) if max_dd_pct < 0 else None

        # ── R-1 Advanced KPIs via quantstats ─────────────────────────────────
        # Master plan Phase R-1 — DSR / K-Ratio / Ulcer / Omega / Tail / GPR /
        # Information Ratio. All require a daily returns series; we convert
        # daily P&L into returns vs a stable $100 denominator (matches the
        # existing Calmar approximation).
        adv = {}
        try:
            import pandas as _pd
            if len(daily_returns) >= 3:
                # Daily returns series indexed by date — quantstats expects this
                returns_series = _pd.Series(
                    [r / 100.0 for r in daily_returns],   # express as % returns on $100
                    index=_pd.to_datetime(days_sorted),
                )

                # quantstats helpers — wrap each in try/except since the lib
                # occasionally raises on degenerate series
                def _qs(fn, *args, **kw):
                    try:
                        import quantstats as qs
                        v = getattr(qs.stats, fn)(returns_series, *args, **kw)
                        if v is None or (isinstance(v, float) and (v != v)):  # NaN check
                            return None
                        return round(float(v), 3)
                    except Exception:
                        return None

                adv["psr"]                = _qs("probabilistic_sharpe_ratio")  # ≈ DSR proxy
                adv["ulcer_index"]        = _qs("ulcer_index")
                adv["ulcer_performance"]  = _qs("ulcer_performance_index")     # Martin ratio
                adv["omega"]              = _qs("omega")
                adv["tail_ratio"]         = _qs("tail_ratio")
                adv["gain_to_pain"]       = _qs("gain_to_pain_ratio")
                adv["common_sense_ratio"] = _qs("common_sense_ratio")          # tail × profit factor
                adv["recovery_factor"]    = _qs("recovery_factor")
                adv["risk_of_ruin"]       = _qs("risk_of_ruin")
                adv["cvar_95"]            = _qs("conditional_value_at_risk")
                adv["kelly_fraction"]     = _qs("kelly_criterion")
                adv["smart_sharpe"]       = _qs("smart_sharpe")                # autocorr-adjusted
                adv["smart_sortino"]      = _qs("smart_sortino")

                # K-Ratio (Kestner) — slope ÷ standard-error of OLS through log(equity).
                # Computed manually since quantstats doesn't have it.
                try:
                    import numpy as _np
                    eq = _np.cumsum(daily_returns) + 100.0  # $100 start
                    if (eq > 0).all():
                        log_eq = _np.log(eq)
                        x = _np.arange(len(log_eq))
                        # OLS slope + standard error of slope
                        mean_x = x.mean()
                        ss_xx = ((x - mean_x) ** 2).sum()
                        slope, intercept = _np.polyfit(x, log_eq, 1)
                        resid = log_eq - (slope * x + intercept)
                        sigma = (resid ** 2).sum() / (len(x) - 2) if len(x) > 2 else 0
                        se_slope = (sigma / ss_xx) ** 0.5 if ss_xx > 0 else 0
                        if se_slope > 0:
                            adv["k_ratio"] = round((slope / se_slope) * (len(x) ** 0.5), 3)
                        else:
                            adv["k_ratio"] = None
                    else:
                        adv["k_ratio"] = None
                except Exception:
                    adv["k_ratio"] = None
        except Exception:
            adv = {}

        # ── R histogram (-3R / -2R / -1R / 0 / +1R / +2R / +3R+) ──────────
        r_bins_def = [(-99, -2.0, "<-2R"), (-2.0, -1.0, "-2R..-1R"),
                      (-1.0, 0.0, "-1R..0"), (0.0, 1.0, "0..+1R"),
                      (1.0, 2.0, "+1R..+2R"), (2.0, 3.0, "+2R..+3R"),
                      (3.0, 99, "+3R+")]
        r_hist = []
        for lo, hi, lbl in r_bins_def:
            n = sum(1 for x in rs if lo <= x < hi)
            r_hist.append({"bucket": lbl, "n": n})

        # ── Hold time histogram ──────────────────────────────────────────
        from collections import OrderedDict
        hold_buckets = OrderedDict([("<1h",0), ("1-4h",0), ("4-24h",0), ("1-7d",0), (">7d",0)])
        for r in rows:
            ot, ct = r.get("open_time"), r.get("close_time")
            try:
                if ot and ct:
                    from datetime import datetime as _dt
                    o = _dt.fromisoformat(ot[:19])
                    c = _dt.fromisoformat(ct[:19])
                    hrs = (c - o).total_seconds() / 3600
                    b = _hold_bucket(hrs)
                    if b in hold_buckets:
                        hold_buckets[b] += 1
            except Exception:
                pass

        # ── Bucket tables ────────────────────────────────────────────────
        by_setup_type     = _bucket_table(rows, lambda r: r.get("setup_type") or "untagged")
        by_archetype_open = _bucket_table(rows, lambda r: r.get("archetype_at_open") or "—")
        by_trade_grade    = _bucket_table(rows, lambda r: r.get("execution_grade") or "—",
                                          label_order=["A","B","C","D","F","—"])

        def _dow(r):
            ct = r.get("close_time")
            if not ct:
                return None
            try:
                from datetime import datetime as _dt
                d = _dt.fromisoformat(ct[:19])
                return _DOW_LABELS[(d.weekday() + 1) % 7]  # weekday: Mon=0..Sun=6 → map to Sun=0..Sat=6
            except Exception:
                return None

        def _sess(r):
            ct = r.get("close_time")
            if not ct: return None
            try:
                from datetime import datetime as _dt
                return _session_of(_dt.fromisoformat(ct[:19]).hour)
            except Exception:
                return None

        def _score_bucket(r):
            s = r.get("setup_score") or r.get("ai_score_at_open")
            if not s: return None
            try:
                s = int(round(float(s)))
                return f"score {s}"
            except Exception:
                return None

        by_dow     = _bucket_table(rows, _dow, label_order=_DOW_LABELS)
        by_session = _bucket_table(rows, _sess, label_order=[s[0] for s in _SESSION_BUCKETS])
        by_score   = _bucket_table(rows, _score_bucket,
                                    label_order=[f"score {i}" for i in range(5, 11)])

        # ── Last 20 closed trades — full row with extras ─────────────────
        # R-4 edge-decay monitors per archetype — same window as the bucket tables
        try:
            from trading import edge_decay as _ed
            with db_conn() as _ed_conn:
                _edge_decay_inline = _ed.evaluate(_ed_conn, window_days=30)
        except Exception:
            _edge_decay_inline = {}

        # R-5 Bayesian credible intervals on overall WR + per-trade P&L
        try:
            from trading import bayes as _bayes
            _wr_ci   = _bayes.posterior_win_rate(len(wins), len(losses))
            _wr_wilson = _bayes.wilson_score(len(wins), n_total)
            _pnl_ci  = _bayes.posterior_expectancy(pnls)
            _pnl_boot = _bayes.bootstrap_ci(pnls, n_resamples=3000) if n_total >= 5 else {}
        except Exception:
            _wr_ci, _wr_wilson, _pnl_ci, _pnl_boot = {}, {}, {}, {}

        last20 = []
        for r in rows[-20:][::-1]:
            rm = _r_multiple(r)
            last20.append({
                "close_time":  r["close_time"],
                "symbol":      r["symbol"],
                "direction":   r["direction"],
                "entry_price": r["entry_price"],
                "close_price": r["close_price"],
                "realized_pnl": round(r["realized_pnl"] or 0, 2),
                "r":           round(rm, 2) if rm is not None else None,
                "close_reason": r.get("close_reason"),
                "setup_type":  r.get("setup_type"),
                "archetype":   r.get("archetype_at_open"),
                "grade":       r.get("execution_grade"),
                "score":       r.get("setup_score") or r.get("ai_score_at_open"),
            })

        return _ok({
            "window":   window,
            "n_total":  n_total,
            "tiles": {
                "total_pnl":       round(pnl_sum, 2),
                "wr_pct":          round(wr_pct, 1),
                "wins":            len(wins),
                "losses":          len(losses),
                "avg_pnl":         round(avg_pnl, 2),
                "expectancy":      round(expectancy, 2),
                "profit_factor":   round(profit_factor, 2) if profit_factor else None,
                "total_r":         round(total_r, 2),
                "avg_r":           round(avg_r, 2),
                "r_n":             len(rs),
                "sharpe":          sharpe,
                "sortino":         sortino,
                "max_dd_pct":      max_dd_pct,
                "calmar":          calmar,
                "best_trade_pnl":  round(max(pnls), 2) if pnls else 0,
                "worst_trade_pnl": round(min(pnls), 2) if pnls else 0,
                # R-3 funding & liq distance aggregates
                "funding_total_usd": round(sum((r.get("funding_paid_usd") or 0) for r in rows), 2),
                "funding_n_known":   sum(1 for r in rows if r.get("funding_paid_usd") is not None),
                "net_pnl_after_funding": round(pnl_sum - sum((r.get("funding_paid_usd") or 0) for r in rows), 2),
                "liq_distance_avg_atr": (
                    round(sum((r.get("liq_distance_atr") or 0) for r in rows if r.get("liq_distance_atr") is not None)
                          / max(sum(1 for r in rows if r.get("liq_distance_atr") is not None), 1), 2)
                    if any(r.get("liq_distance_atr") is not None for r in rows) else None
                ),
                "liq_distance_n_known": sum(1 for r in rows if r.get("liq_distance_atr") is not None),
            },
            "advanced_kpis": adv,
            "edge_decay":    _edge_decay_inline,
            "bayes": {
                "wr_posterior":     _wr_ci,
                "wr_wilson":        _wr_wilson,
                "pnl_posterior":    _pnl_ci,
                "pnl_bootstrap":    _pnl_boot,
            },
            "equity_curve":  equity_curve,
            "daily_bars":    daily_bars,
            "r_histogram":   r_hist,
            "hold_buckets":  [{"bucket": k, "n": v} for k, v in hold_buckets.items()],
            "by_setup_type":     by_setup_type,
            "by_archetype_open": by_archetype_open,
            "by_trade_grade":    by_trade_grade,
            "by_dow":            by_dow,
            "by_session":        by_session,
            "by_score":          by_score,
            "last20":            last20,
        })
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


# ─── L-7 (Master plan Week 11): Stats UI panels ────────────────────────────
# Returns the four lower panels for the Futures-AI Statistics page:
#   1) Recent auto-adjustments (last 20 from learner_log)
#   2) Noise gates — 24h rejection counters
#   3) Reminders & countdowns (Red-Team review, Strategy Selector, etc.)
#   4) Edge-decay watch (per-archetype CUSUM + Page-Hinkley)
@bp.route("/api/futures-ai/l7-panels")
def l7_panels():
    import datetime as _dt
    try:
        with db_conn() as conn:
            # 1. Recent learned-param changes — newest first, last 20
            try:
                rows = conn.execute(
                    "SELECT ts, learner_name, param_key, old_value, new_value, "
                    "action, gate_reason, sample_size FROM learner_log "
                    "ORDER BY ts DESC LIMIT 20"
                ).fetchall()
                learned_log = [
                    {"ts": r[0], "learner_name": r[1], "param_key": r[2],
                     "old_value": r[3], "new_value": r[4], "action": r[5],
                     "gate_reason": r[6], "sample_size": r[7]}
                    for r in rows
                ]
            except Exception:
                learned_log = []

            # 2. Noise-gate rejection counts in last 24h
            try:
                rows = conn.execute(
                    "SELECT event, COUNT(*) FROM futures_ai_log "
                    "WHERE ts >= datetime('now', '-1 day') "
                    "AND event LIKE 'rejected%' OR event LIKE 'red_team%' "
                    "OR event = 'consensus_rejected' "
                    "GROUP BY event"
                ).fetchall()
                noise_gates = {r[0]: int(r[1]) for r in rows}
            except Exception:
                noise_gates = {}

            # 3. Reminders — countdown from each anchor date
            today = _dt.date.today()
            reminders = []
            anchors = [
                ("Red-Team soft→hard review", _dt.date(2026, 6, 14),
                 "After 14d of soft-mode data, decide hard-veto switch"),
                ("Strategy Selector revisit",  _dt.date(2026, 6, 30),
                 "Post-L-3 data should justify the per-archetype selector"),
                ("DSPy active prompts",        _dt.date(2026, 6, 1),
                 "Tomorrow's job — install + tune classifier prompts"),
            ]
            for title, target, note in anchors:
                reminders.append({
                    "title": title,
                    "days_remaining": (target - today).days,
                    "due_date": target.isoformat(),
                    "note": note,
                })

            # 4. Edge decay — per-archetype CUSUM + Page-Hinkley
            try:
                from trading import edge_decay
                edge = edge_decay.evaluate(conn, window_days=30)
            except Exception:
                edge = {}

            # 5. Cost optimisation surfaces (cache stats etc.)
            try:
                from scanner_prompts import quick_score_cache_stats
                quick_cache = quick_score_cache_stats()
            except Exception:
                quick_cache = {}

            # 6. API-cost-vs-P&L profitability tile
            # Auto_ai-attributable spend: modules whose work exists because
            # the auto-trader chain exists. Manual chain shares the scanner
            # feed but the scanner runs every 30min PRIMARILY for auto_ai —
            # we attribute it fully here for honest accounting.
            AUTO_AI_MODULES = (
                "call_analyzer", "scanner_quick", "live_trade",
                "red_team_agent", "post_mortem", "post_mortem_dspy",
                "setup_classifier",
            )
            cost_pnl: dict = {}
            try:
                for window_label, days in (("24h", 1), ("7d", 7), ("30d", 30)):
                    placeholders = ",".join("?" * len(AUTO_AI_MODULES))
                    # Pricing per 1M tokens (current as of 2026-05):
                    #   Opus 4.6/4.7/4.8: $5 input / $0.50 cache_read / $6.25 cache_write / $25 output
                    #     (was 3x overstated as $15/$75 — old Opus 4.0 prices)
                    #   Sonnet 4.6: $3 / $0.30 / $3.75 / $15
                    #   Haiku 4.5:  $1 / $0.10 / $1.25 / $5
                    cost_row = conn.execute(
                        f"""SELECT COALESCE(SUM(
                              CASE
                                WHEN model LIKE 'claude-opus%%'   THEN input_tokens*5.0/1e6 + cached_tokens*0.5/1e6 + cache_creation_tokens*6.25/1e6  + output_tokens*25.0/1e6
                                WHEN model LIKE 'claude-sonnet%%' THEN input_tokens*3.0/1e6 + cached_tokens*0.3/1e6 + cache_creation_tokens*3.75/1e6  + output_tokens*15.0/1e6
                                WHEN model LIKE 'claude-haiku%%'  THEN input_tokens*1.0/1e6 + cached_tokens*0.1/1e6 + cache_creation_tokens*1.25/1e6  + output_tokens*5.0/1e6
                                ELSE 0
                              END), 0) AS usd
                          FROM token_usage
                          WHERE ts >= datetime('now', '-{int(days)} day')
                          AND model NOT LIKE 'gemini%%'
                          AND module IN ({placeholders})""",
                        list(AUTO_AI_MODULES)
                    ).fetchone()
                    api_cost = float(cost_row[0] or 0.0)

                    pnl_row = conn.execute(
                        f"""SELECT COALESCE(SUM(realized_pnl), 0) AS pnl,
                                   COUNT(*) AS trades,
                                   SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
                            FROM positions
                            WHERE chain='auto_ai' AND (is_hedge IS NULL OR is_hedge=0)
                            AND close_time IS NOT NULL AND close_time != ''
                            AND close_time >= datetime('now', '-{int(days)} day')"""
                    ).fetchone()
                    pnl = float(pnl_row[0] or 0.0)
                    trades = int(pnl_row[1] or 0)
                    wins = int(pnl_row[2] or 0)

                    cost_pnl[window_label] = {
                        "api_cost_usd":   round(api_cost, 2),
                        "realized_pnl":   round(pnl, 2),
                        "net":            round(pnl - api_cost, 2),
                        "ratio":          round(pnl / api_cost, 3) if api_cost > 0 else None,
                        "trades":         trades,
                        "wins":           wins,
                        "wr_pct":         round(100.0 * wins / trades, 1) if trades else None,
                        "days":           days,
                    }

                # Break-even-equity calc: at current daily P&L rate, what equity
                # would zero out the daily API spend?
                eq_now = 0.0
                try:
                    from trading import kill_switch
                    eq_now = float(kill_switch._equity_now(conn) or 0.0)
                except Exception:
                    pass
                daily_pct_24h = (cost_pnl["24h"]["realized_pnl"] / eq_now) if eq_now else None
                daily_pct_7d = (cost_pnl["7d"]["realized_pnl"] / 7 / eq_now) if eq_now else None
                cost_pnl["break_even"] = {
                    "equity_now":           round(eq_now, 2),
                    "daily_api_cost_usd":   round(cost_pnl["24h"]["api_cost_usd"], 2),
                    "today_daily_pct":      round(daily_pct_24h * 100, 3) if daily_pct_24h else None,
                    "trailing_daily_pct":   round(daily_pct_7d * 100, 3) if daily_pct_7d else None,
                    "break_even_equity_today":
                        round(cost_pnl["24h"]["api_cost_usd"] / daily_pct_24h, 0)
                        if daily_pct_24h and daily_pct_24h > 0 else None,
                    "break_even_equity_trailing":
                        round(cost_pnl["24h"]["api_cost_usd"] / daily_pct_7d, 0)
                        if daily_pct_7d and daily_pct_7d > 0 else None,
                }
            except Exception:
                traceback.print_exc()

            return _ok({
                "learned_log": learned_log,
                "noise_gates": noise_gates,
                "reminders":   reminders,
                "edge_decay":  edge,
                "quick_score_cache": quick_cache,
                "cost_vs_pnl": cost_pnl,
            })
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
