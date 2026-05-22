"""
system_state.py — Single source of truth for subsystem freshness.

Answers "is the persisted data aligned with the current code?" by
comparing per-subsystem last-update timestamps against expected
freshness rules, and by reporting current calibration constants.

Subsystems tracked:
  - scanner               (every 30 min via scheduler)
  - monitor               (every 10 min via scheduler)
  - rulebook              (regen on >=5 new closes, manual force OK)
  - hindsight             (manual or after closes; verdicts re-derivable)
  - self-review           (every 24h via scheduler)
  - MFE/MAE               (on every close)
  - call-link backfill    (one-shot historical)
  - setup_type labels     (AI-labeled all-trades, periodic refresh)
  - stale-call cleanup    (every scanner cycle)
  - cache hit rate        (24h Anthropic prompt-cache observation)

Provides:
  - get_state(conn): dict of all subsystem statuses + current calibration
  - refresh_all(conn, ai_classify=False): run every backfill in dep order
"""
from __future__ import annotations

import datetime as _dt
import os
import threading
from typing import Optional

from constants import (
    KNOWLEDGE_VERSION,
    SCANNER_MIN_SCORE,
    SCANNER_FULL_DETAIL_TOP_N,
    MONITOR_INTERVAL,
)


# ── State collector ──────────────────────────────────────────────────────────

def _ts_to_str(s: Optional[str]) -> Optional[str]:
    """Normalise SQLite ISO strings."""
    if not s:
        return None
    return s.replace("T", " ")[:19]


def _age_minutes(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        dt = _dt.datetime.fromisoformat(iso[:19]).replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return None


def _status_from_age(age_min: Optional[float], yellow_after_min: int,
                      red_after_min: int) -> str:
    if age_min is None:
        return "unknown"
    if age_min >= red_after_min:
        return "red"
    if age_min >= yellow_after_min:
        return "yellow"
    return "green"


def _calibration_snapshot() -> dict:
    """Pull current calibration constants from the modules that own them.
    Lazy-imported so the function works even when chart_indicators
    transitively-required deps aren't available in a test environment."""
    snap: dict = {
        "knowledge_version": KNOWLEDGE_VERSION,
        "scanner_min_score": SCANNER_MIN_SCORE,
        "scanner_top_n":     SCANNER_FULL_DETAIL_TOP_N,
        "monitor_interval_s": MONITOR_INTERVAL,
    }
    try:
        from ai_hindsight import ENTER_THRESHOLD
        snap["enter_threshold"] = ENTER_THRESHOLD
    except Exception:
        pass
    try:
        from trade_utils import TP1_MIN_ATR_MULTIPLE, TP2_MIN_ATR_MULTIPLE
        snap["tp1_min_atr"] = TP1_MIN_ATR_MULTIPLE
        snap["tp2_min_atr"] = TP2_MIN_ATR_MULTIPLE
    except Exception:
        pass
    try:
        from position_risk_monitor import (
            BE_ATR_MULTIPLE, MAE_ATR_MULTIPLE, TRAIL_ATR_MULTIPLE,
        )
        snap["be_atr"]    = BE_ATR_MULTIPLE
        snap["mae_atr"]   = MAE_ATR_MULTIPLE
        snap["trail_atr"] = TRAIL_ATR_MULTIPLE
    except Exception:
        pass
    try:
        from scanner_criteria import (
            REVERSAL_CAP, PERSONAL_BAD_HOURS_UTC, PERSONAL_BAD_HOUR_CAP,
        )
        snap["reversal_cap"]            = REVERSAL_CAP
        snap["personal_bad_hours_utc"]  = sorted(PERSONAL_BAD_HOURS_UTC)
        snap["personal_bad_hour_cap"]   = PERSONAL_BAD_HOUR_CAP
    except Exception:
        pass
    try:
        from setup_classifier import ARCHETYPES, _MIN_CONFIDENCE_FLOOR
        snap["classifier_archetypes"]   = list(ARCHETYPES)
        snap["classifier_floor"]        = _MIN_CONFIDENCE_FLOOR
    except Exception:
        pass
    return snap


def _subsystem_rows(conn) -> list[dict]:
    """Build one row per subsystem with last-update + status."""
    cur = conn.cursor()

    def _one(sql: str, params: tuple = ()) -> Optional[str]:
        try:
            r = cur.execute(sql, params).fetchone()
            return r[0] if r and r[0] else None
        except Exception:
            return None

    def _count(sql: str, params: tuple = ()) -> int:
        try:
            r = cur.execute(sql, params).fetchone()
            return int(r[0] or 0) if r else 0
        except Exception:
            return 0

    rows: list[dict] = []

    # 1. Scanner — last token row from scanner_quick
    last = _ts_to_str(_one(
        "SELECT MAX(ts) FROM token_usage WHERE module LIKE 'scanner%'"))
    rows.append({
        "name":           "scanner",
        "last_update":    last,
        "age_min":        round(_age_minutes(last) or -1, 1),
        "expected_min":   30,
        "status":         _status_from_age(_age_minutes(last), 60, 120),
        "refresh_cmd":    "POST /api/scanner/run?force=1",
        "note":           "Auto every 30 min",
    })

    # 2. Monitor — last token row from live_trade
    last = _ts_to_str(_one(
        "SELECT MAX(ts) FROM token_usage WHERE module LIKE 'live_trade%'"))
    rows.append({
        "name":           "monitor",
        "last_update":    last,
        "age_min":        round(_age_minutes(last) or -1, 1),
        "expected_min":   10,
        "status":         _status_from_age(_age_minutes(last), 30, 60),
        "refresh_cmd":    "(auto — no manual trigger)",
        "note":           "Auto every 10 min (only on open positions)",
    })

    # 3. Rulebook
    last = _ts_to_str(_one("SELECT MAX(generated_at) FROM trader_rulebook"))
    n_rules = _count("SELECT COUNT(*) FROM trader_rulebook")
    rows.append({
        "name":           "rulebook",
        "last_update":    last,
        "age_min":        round(_age_minutes(last) or -1, 1),
        "expected_min":   60*24*3,    # 3 days reasonable
        "status":         _status_from_age(_age_minutes(last), 60*24*3, 60*24*7),
        "refresh_cmd":    "POST /api/rulebook/update (body: {\"force\":true})",
        "note":           f"{n_rules} active rules",
    })

    # 4. Hindsight
    last = _ts_to_str(_one("SELECT MAX(analyzed_at) FROM trade_hindsight"))
    n_h = _count("SELECT COUNT(*) FROM trade_hindsight")
    rows.append({
        "name":           "hindsight",
        "last_update":    last,
        "age_min":        round(_age_minutes(last) or -1, 1),
        "expected_min":   60*24*7,    # weekly cadence is fine
        "status":         _status_from_age(_age_minutes(last), 60*24*7, 60*24*14),
        "refresh_cmd":    "POST /api/hindsight/run?n=200",
        "note":           f"{n_h} graded trades",
    })

    # 5. Self-review
    last = _ts_to_str(_one("SELECT MAX(created_at) FROM ai_self_review"))
    n_sr = _count("SELECT COUNT(*) FROM ai_self_review")
    rows.append({
        "name":           "self_review",
        "last_update":    last,
        "age_min":        round(_age_minutes(last) or -1, 1),
        "expected_min":   60*24,      # daily
        "status":         _status_from_age(_age_minutes(last), 60*30, 60*48),
        "refresh_cmd":    "POST /api/self-review/run?limit=10",
        "note":           f"{n_sr} reviewed trades",
    })

    # 6. MFE/MAE
    n_total  = _count("SELECT COUNT(*) FROM positions")
    n_filled = _count("SELECT COUNT(*) FROM positions WHERE mfe_pct IS NOT NULL")
    pct = round(100.0 * n_filled / max(n_total, 1), 1)
    status = "green" if pct >= 90 else ("yellow" if pct >= 70 else "red")
    rows.append({
        "name":           "mfe_mae_coverage",
        "last_update":    None,
        "age_min":        None,
        "expected_min":   None,
        "status":         status,
        "refresh_cmd":    "python3 scripts/backfill_call_links.py "
                          "(also backfills MFE/MAE)",
        "note":           f"{n_filled}/{n_total} positions have MFE/MAE ({pct}%)",
    })

    # 7. setup_type AI labels — proxy: count of garbage / untagged rows
    n_garbage = _count("""
        SELECT COUNT(*) FROM positions WHERE setup_type IS NULL OR setup_type = ''
           OR LOWER(setup_type) LIKE '%claude%' OR LOWER(setup_type) LIKE '%sonnet%'
           OR LOWER(setup_type) LIKE '%haiku%'  OR LOWER(setup_type) LIKE '%opus%'
           OR setup_type = 'Quick score only'
    """)
    status = "green" if n_garbage == 0 else ("yellow" if n_garbage < 10 else "red")
    rows.append({
        "name":           "setup_type_labels",
        "last_update":    None,
        "age_min":        None,
        "expected_min":   None,
        "status":         status,
        "refresh_cmd":    "python3 scripts/reclassify_setup_types.py",
        "note":           f"{n_garbage} garbage / untagged rows" if n_garbage
                          else "All positions have a clean archetype label",
    })

    # 8. Stale-call cleanup — count expired+invalidated
    n_stale = _count("""
        SELECT COUNT(*) FROM analyzed_calls
        WHERE status='saved' AND created_at < datetime('now','-24 hours')
    """)
    status = "green" if n_stale == 0 else ("yellow" if n_stale < 20 else "red")
    rows.append({
        "name":           "stale_call_cleanup",
        "last_update":    None,
        "age_min":        None,
        "expected_min":   None,
        "status":         status,
        "refresh_cmd":    "POST /api/calls/invalidate-stale",
        "note":           f"{n_stale} 'saved' calls older than 24h "
                          "(should be 0 — scanner cycle catches these)",
    })

    # 9. Cache hit rate (24h Anthropic only)
    try:
        r = cur.execute("""
            SELECT SUM(input_tokens), SUM(cached_tokens), COUNT(*)
            FROM token_usage
            WHERE ts > datetime('now','-24 hours')
              AND model LIKE 'claude-%' AND model NOT LIKE 'gemini%'
        """).fetchone()
        in_tok, cached_tok, n_calls = (r or (0, 0, 0))
        in_tok    = int(in_tok or 0)
        cached_tok = int(cached_tok or 0)
        pct = round(100.0 * cached_tok / max(in_tok, 1), 1)
    except Exception:
        in_tok, cached_tok, n_calls, pct = 0, 0, 0, 0
    if n_calls < 10:
        status = "unknown"
        note = f"only {n_calls} Anthropic calls in 24h — not enough to measure"
    else:
        status = "green" if pct >= 50 else ("yellow" if pct >= 20 else "red")
        note = f"{cached_tok:,}/{in_tok:,} tokens cached ({pct}% hit rate, {n_calls} calls)"
    rows.append({
        "name":           "anthropic_cache",
        "last_update":    None,
        "age_min":        None,
        "expected_min":   None,
        "status":         status,
        "refresh_cmd":    "(auto — verifies prompt-cache wiring)",
        "note":           note,
    })

    return rows


def get_state(conn) -> dict:
    return {
        "calibration": _calibration_snapshot(),
        "subsystems":  _subsystem_rows(conn),
        "token_cost":  _token_cost_breakdown(conn),
        "computed_at": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
    }


# ── Token cost projection ────────────────────────────────────────────────────

# Anthropic public pricing per million tokens (USD), as of model launch.
# When new models come out, append here. Unknown models fall back to Haiku.
_PRICING = {
    # model_prefix : (input_per_mtok, output_per_mtok, cache_read_per_mtok, cache_write_per_mtok)
    "claude-haiku-4-5":   (1.00, 5.00,  0.10, 1.25),
    "claude-sonnet-4-6":  (3.00, 15.00, 0.30, 3.75),
    "claude-opus-4-7":    (15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-6":    (15.00, 75.00, 1.50, 18.75),
}


def _pricing_for(model: str) -> tuple:
    m = (model or "").lower()
    for prefix, prices in _PRICING.items():
        if m.startswith(prefix):
            return prices
    return _PRICING["claude-haiku-4-5"]   # safe default


def _token_cost_breakdown(conn) -> dict:
    """
    Compute 7-day Anthropic spend by module and project weekly $.
    Free-tier cascade providers (groq/cerebras/openrouter/gemini) cost
    nothing so they're excluded — listed separately as 'cascade_calls'.
    """
    try:
        rows = conn.execute("""
            SELECT module, model,
                   COALESCE(SUM(input_tokens),0)  AS in_tok,
                   COALESCE(SUM(output_tokens),0) AS out_tok,
                   COALESCE(SUM(cached_tokens),0) AS cached_tok,
                   COUNT(*) AS n_calls
            FROM token_usage
            WHERE ts > datetime('now','-7 days')
              AND model LIKE 'claude-%'
              AND model NOT LIKE 'gemini%'
            GROUP BY module, model
            ORDER BY in_tok DESC
        """).fetchall()
    except Exception:
        return {"available": False}

    # 7d cascade-call count (Anthropic-fallback aggregate, just for context)
    try:
        cascade_n = int(conn.execute("""
            SELECT COUNT(*) FROM token_usage
            WHERE ts > datetime('now','-7 days')
              AND (model NOT LIKE 'claude-%'  OR  model LIKE 'gemini%')
        """).fetchone()[0])
    except Exception:
        cascade_n = 0

    by_module: dict[str, dict] = {}
    total_cost = 0.0

    for module, model, in_tok, out_tok, cached_tok, n_calls in rows:
        in_p, out_p, cache_r_p, cache_w_p = _pricing_for(model)
        # input_tokens in our log is the uncached portion paid at full rate.
        # cached_tokens is cache_read volume (10% input price).
        # cache_creation is paid at 1.25x input price BUT only on the first
        # write — we don't log it separately, so we approximate by treating
        # the first input batch per module as a cache_creation event.
        uncached_cost = in_tok / 1_000_000 * in_p
        cache_read_cost = cached_tok / 1_000_000 * cache_r_p
        output_cost = out_tok / 1_000_000 * out_p
        row_cost = uncached_cost + cache_read_cost + output_cost
        total_cost += row_cost

        key = f"{module}|{model}"
        by_module[key] = {
            "module":          module,
            "model":           model,
            "n_calls":         int(n_calls),
            "input_tokens":    int(in_tok),
            "cached_tokens":   int(cached_tok),
            "output_tokens":   int(out_tok),
            "cache_hit_pct":   round(100.0 * cached_tok / max(in_tok + cached_tok, 1), 1),
            "cost_7d_usd":     round(row_cost, 4),
            "cost_per_call":   round(row_cost / max(n_calls, 1), 4),
        }

    weekly_projection = round(total_cost, 2)
    rows_sorted = sorted(by_module.values(), key=lambda r: -r["cost_7d_usd"])

    return {
        "available":            True,
        "weekly_total_usd":     weekly_projection,
        "monthly_projection":   round(total_cost * 4.33, 2),
        "cascade_call_count":   cascade_n,
        "by_module":            rows_sorted,
    }


# ── Unified refresh pipeline ─────────────────────────────────────────────────

_refresh_lock = threading.Lock()
_refresh_state: dict = {"status": "idle", "log": [], "started_at": None}


def _log(msg: str) -> None:
    print(f"[refresh-all] {msg}", flush=True)
    _refresh_state["log"].append(msg)


def refresh_all(conn, run_ai_classify: bool = False) -> dict:
    """
    Run every dependent backfill in correct order:
      1. Mark stale scanner setups (cheap, fast)
      2. Recompute hindsight verdicts (in-place SQL — no AI calls)
      3. Recompute hindsight feedback summary (settings row)
      4. Process pending self-review trades (Haiku, ~5-10 calls)
      5. Force rulebook regeneration (1 Sonnet call)
      6. (Optional) AI re-classify all setup_type labels (~110 Haiku calls)

    `run_ai_classify` is False by default because it costs ~$0.50 and
    is rarely necessary (only when the classifier taxonomy changes).

    Returns the final state plus a per-step log.
    """
    with _refresh_lock:
        if _refresh_state["status"] == "running":
            return {"error": "Another refresh is already running",
                    "state": dict(_refresh_state)}
        _refresh_state["status"]     = "running"
        _refresh_state["log"]        = []
        _refresh_state["started_at"] = _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    try:
        # Step 1
        _log("step 1/6  invalidate-stale")
        try:
            import scanner_invalidator
            r = scanner_invalidator.run_full_pass()
            _log(f"  expired={r.get('expired',0)} invalidated={r.get('invalidated',0)}")
        except Exception as e:
            _log(f"  step 1 failed: {e}")

        # Step 2
        _log("step 2/6  recompute hindsight verdicts under current ENTER_THRESHOLD")
        try:
            from ai_hindsight import ENTER_THRESHOLD
            sql = (
                "UPDATE trade_hindsight SET verdict = CASE "
                f"WHEN setup_score < 5 AND COALESCE((SELECT realized_pnl FROM positions WHERE id=trade_hindsight.position_id),0) > 0 THEN 'FN' "
                f"WHEN setup_score < 5 THEN 'TN' "
                f"WHEN setup_score < {ENTER_THRESHOLD} THEN 'NEUTRAL' "
                f"WHEN direction_match=1 AND COALESCE((SELECT realized_pnl FROM positions WHERE id=trade_hindsight.position_id),0) > 0 THEN 'TP' "
                f"WHEN direction_match=1 THEN 'FP' "
                f"WHEN direction_match=0 AND COALESCE((SELECT realized_pnl FROM positions WHERE id=trade_hindsight.position_id),0) <= 0 THEN 'TN' "
                f"WHEN direction_match=0 THEN 'FN' "
                f"ELSE 'NEUTRAL' END"
            )
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            _log(f"  verdicts recomputed under ENTER_THRESHOLD={ENTER_THRESHOLD}")
        except Exception as e:
            _log(f"  step 2 failed: {e}")

        # Step 3
        _log("step 3/6  recompute hindsight feedback summary")
        try:
            import ai_hindsight
            fb = ai_hindsight.compute_feedback(conn)
            _log(f"  buckets={len(fb.get('buckets',[]))} recommendation={fb.get('recommendation')}")
        except Exception as e:
            _log(f"  step 3 failed: {e}")

        # Step 4
        _log("step 4/6  process pending self-review trades")
        try:
            import ai_self_review
            r = ai_self_review.run_pending_reviews(conn, limit=10)
            _log(f"  reviewed={r.get('reviewed',0)}")
        except Exception as e:
            _log(f"  step 4 failed: {e}")

        # Step 5
        _log("step 5/6  force rulebook regeneration")
        try:
            import ai_rulebook
            r = ai_rulebook.update_rulebook(conn, force=True)
            _log(f"  rules={r.get('count',0)}")
        except Exception as e:
            _log(f"  step 5 failed: {e}")

        # Step 6 (optional)
        if run_ai_classify:
            _log("step 6/6  AI re-classify setup_type for all positions (expensive)")
            try:
                import subprocess, shlex
                env = os.environ.copy()
                # The script needs ANTHROPIC_API_KEY — caller is expected to
                # invoke this endpoint from inside the Flask process which
                # already has it loaded via systemd EnvironmentFile.
                p = subprocess.run(
                    shlex.split("python3 /home/fbauer/trading-journal/scripts/reclassify_setup_types.py"),
                    capture_output=True, text=True, timeout=900, env=env,
                )
                tail = (p.stdout.splitlines() or ["(no output)"])[-10:]
                for ln in tail:
                    _log(f"  {ln}")
            except Exception as e:
                _log(f"  step 6 failed: {e}")
        else:
            _log("step 6/6  AI re-classify skipped (run_ai_classify=False)")

        _refresh_state["status"] = "completed"
        return {"state": dict(_refresh_state), "final": get_state(conn)}

    except Exception as e:
        _refresh_state["status"] = "error"
        _refresh_state["error"]  = str(e)[:200]
        return {"state": dict(_refresh_state)}


def get_refresh_state() -> dict:
    return dict(_refresh_state)
