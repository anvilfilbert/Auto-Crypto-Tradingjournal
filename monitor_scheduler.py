"""
monitor_scheduler.py — Background thread that monitors open positions every 10 min.

For each open position that passes the filter (unrealized_pct < MONITOR_THRESHOLD_PCT
or duration_minutes > MONITOR_THRESHOLD_DURATION), runs the TradeMonitor agent chain:
  DataCollector → DataInterpreter → MarketSentiment → Haiku verdict

On risk_rating >= 7 or action != "Hold":
  - Sets monitor_alert=1 in analyzed_calls for UI badge
  - Sends Telegram alert
"""
import os
import threading
import time
from datetime import datetime, timezone

import bitget_client
import telegram_notify
import agent_orchestrator
import position_risk_monitor
import exposure_monitor
from constants import MONITOR_INTERVAL, MONITOR_THRESHOLD_PCT, MONITOR_THRESHOLD_DURATION
from database import db_conn

# Idempotency for portfolio-level exposure alerts. Keyed by the (kind,
# sorted symbol tuple) so the same alert only fires once per portfolio
# shape — fires again when symbols change.
_exposure_alerted: set[tuple] = set()

FIRST_DELAY = int(os.environ.get("MONITOR_FIRST_DELAY", "120"))   # 2 min

# ── Scanner-silence watchdog ───────────────────────────────────────────
# When no decisions land in futures_ai_log for a while, something is wrong
# (AI providers exhausted, scanner thread crashed, auto-trader paused).
# Catch it BEFORE the operator notices via eyeballing the manual book.
SILENCE_THRESHOLD_HOURS  = float(os.environ.get("SCANNER_SILENCE_THRESHOLD_H", "4"))
SILENCE_ALERT_COOLDOWN_H = float(os.environ.get("SCANNER_SILENCE_COOLDOWN_H", "6"))
_last_silence_alert: datetime | None = None


def _passes_filter(position: dict) -> bool:
    try:
        unrl = float(position.get("unrealized_pct", 0) or 0)
        dur  = float(position.get("duration_minutes", 0) or 0)
        return unrl < MONITOR_THRESHOLD_PCT or dur > MONITOR_THRESHOLD_DURATION
    except (TypeError, ValueError):
        return False


def _get_original_prep(conn, symbol: str) -> dict:
    try:
        row = conn.execute(
            """SELECT analysis_json FROM analyzed_calls
               WHERE symbol=? AND status IN ('matched','saved')
               ORDER BY created_at DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        if row and row["analysis_json"]:
            import json
            d = json.loads(row["analysis_json"])
            return {
                "sl_price":  d.get("sl_price"),
                "tp1_price": d.get("tp1_price") or d.get("tp1") or (d.get("risk_reward", {}) or {}).get("tp1"),
            }
    except Exception:
        pass
    return {}


def _run_once():
    try:
        positions = bitget_client.get_open_positions() or []
    except Exception as e:
        print(f"[Monitor] Failed to fetch positions: {e}", flush=True)
        return

    # Tag manual-chain positions for downstream alert routing
    for p in positions:
        p.setdefault("chain", "manual")

    # Also fetch auto-chain (auto-trader subaccount) positions so the
    # exposure monitor sees concentration risk across BOTH books, not
    # just the operator's manual trades. This is best-effort — if the
    # auto-trader creds are absent/invalid we silently skip and keep
    # the manual-only monitor working.
    auto_positions: list[dict] = []
    try:
        from trading import config as fa_config
        if fa_config.is_real_mode():
            from trading import bitget_trader as _bt
            auto_positions = _bt.get_open_positions() or []
            for p in auto_positions:
                p["chain"] = "auto_ai"
    except Exception as e:
        print(f"[Monitor] auto-chain position fetch skipped: {e}", flush=True)

    to_check = [p for p in positions if _passes_filter(p)]
    if not to_check and not auto_positions:
        return

    print(f"[Monitor] Checking {len(to_check)}/{len(positions)} manual "
          f"+ {len(auto_positions)} auto positions", flush=True)

    # Pass 1 — deterministic SL-discipline checks on EVERY open position,
    # not just the filtered ones. These are cheap (single ATR_4H lookup)
    # and idempotent. Fires per-position alerts on BE trigger / MAE breach.
    for pos in positions:
        try:
            for alert in position_risk_monitor.check(pos) or []:
                _send_risk_alert(alert)
                print(f"[Monitor] {alert['kind']} {alert['symbol']}: "
                      f"{alert['current_pct']}% (threshold {alert['threshold_pct']}%)",
                      flush=True)
        except Exception as e:
            print(f"[Monitor] Risk-check error for {pos.get('symbol','?')}: {e}",
                  flush=True)

    # Pass 1b — portfolio-level exposure / correlation alerts across
    # BOTH chains. The operator's risk is the same regardless of which
    # book opened the position, so concentration is checked on the
    # combined set. Alert keys are tagged with the chain composition so
    # a manual-only alert and an auto-only alert with the same symbols
    # don't collide.
    combined_positions = positions + auto_positions
    try:
        for alert in exposure_monitor.check(combined_positions) or []:
            syms = tuple(sorted(alert.get("symbols") or []))
            # Annotate alert with which chains the affected symbols belong to
            chains = sorted({
                p.get("chain", "manual")
                for p in combined_positions
                if p.get("symbol") in (alert.get("symbols") or [])
            })
            alert["chains"] = chains
            key = (alert["kind"], syms, tuple(chains))
            if key in _exposure_alerted:
                continue
            _exposure_alerted.add(key)
            _send_exposure_alert(alert)
            print(f"[Monitor] {alert['kind']} [{'+'.join(chains)}]: "
                  f"{alert['title']}", flush=True)
    except Exception as e:
        print(f"[Monitor] Exposure-check error: {e}", flush=True)
    # Drop stale alert keys for symbols that are no longer open
    open_syms = {p.get("symbol") for p in combined_positions}
    _exposure_alerted.intersection_update({
        k for k in _exposure_alerted
        if set(k[1]).issubset(open_syms)
    })

    # Futures-AI: tick the orchestrator so paper / real positions get
    # their BE-trigger / trail / MAE-breach lifecycle managed. Cheap when
    # the chain is disabled — orchestrator self-skips.
    try:
        from trading import orchestrator as fa_orch
        fa_result = fa_orch.on_monitor_cycle()
        if fa_result and not fa_result.get("skipped"):
            checked = fa_result.get("checked", 0)
            closed  = fa_result.get("closed", 0)
            evs     = fa_result.get("events") or []
            if checked or closed or evs:
                print(f"[Futures-AI] monitor: checked={checked} closed={closed} "
                      f"events={len(evs)}", flush=True)
                for ev in evs[:5]:
                    print(f"  {ev}", flush=True)
    except Exception as e:
        print(f"[Futures-AI] monitor hook error: {e}", flush=True)

    for pos in to_check:
        symbol = pos.get("symbol", "?")
        try:
            with db_conn() as conn:
                original_prep = _get_original_prep(conn, symbol)

            result = agent_orchestrator.run_monitor(pos, original_prep)

            should_alert = (result["risk_rating"] >= 7 or result["action"] != "Hold")

            if should_alert:
                with db_conn() as conn:
                    conn.execute(
                        """UPDATE analyzed_calls SET monitor_alert=1
                           WHERE symbol=? AND status IN ('matched','saved')""",
                        (symbol,),
                    )
                    conn.commit()
                _send_monitor_alert(pos, result)

            print(f"[Monitor] {symbol}: {result['action']} "
                  f"(risk {result['risk_rating']}/10)"
                  f"{' ⚠ ALERTED' if should_alert else ''}", flush=True)

        except Exception as e:
            print(f"[Monitor] Error for {symbol}: {e}", flush=True)


def _check_scanner_silence() -> None:
    """Alert if no futures_ai_log activity for SILENCE_THRESHOLD_HOURS.

    Detected silent states: AI provider quota exhausted, scanner thread dead,
    auto-trader paused without operator awareness. Skips when:
      - no log entries exist at all (fresh DB)
      - auto-trader pause flag is set (silence is expected)
      - we already alerted within the cooldown window
    """
    global _last_silence_alert
    if not _monitor_alerts_enabled():
        return
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT ts FROM futures_ai_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            paused_row = conn.execute(
                "SELECT value FROM settings WHERE key='futures_ai_state'"
            ).fetchone()
        if row is None:
            return
        if paused_row and paused_row[0] in ("pause_now", "pause_after_close", "circuit_breaker"):
            return  # operator explicitly paused — silence is expected
        last_ts = datetime.fromisoformat(row[0].replace(" ", "T"))
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        silence_h = (now - last_ts).total_seconds() / 3600.0
        if silence_h < SILENCE_THRESHOLD_HOURS:
            return
        # Cooldown: don't spam — once per SILENCE_ALERT_COOLDOWN_H
        if _last_silence_alert is not None:
            since_last = (now - _last_silence_alert).total_seconds() / 3600.0
            if since_last < SILENCE_ALERT_COOLDOWN_H:
                return
        msg = (
            f"⚠️ Scanner SILENT for {silence_h:.1f}h\n"
            f"Last futures_ai_log entry: {row[0]}\n"
            f"Likely cause: AI quota out, scanner crashed, or paused.\n"
            f"Check `journalctl -u trading-journal -f` to diagnose."
        )
        telegram_notify.send_message(msg)
        _last_silence_alert = now
        print(f"[Monitor] silence alert sent ({silence_h:.1f}h of inactivity)", flush=True)
    except Exception as e:
        print(f"[Monitor] silence check failed: {e}", flush=True)


def _monitor_alerts_enabled() -> bool:
    """Check whether position monitor Telegram alerts are enabled (default on)."""
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='telegram_monitor_enabled'"
            ).fetchone()
        return (row is None) or (row[0] == '1')
    except Exception:
        return True


def _send_risk_alert(alert: dict):
    """Push a position_risk_monitor alert to Telegram + flag the linked
    call so the UI shows the badge. Honors telegram_monitor_enabled."""
    sym = alert.get("symbol", "?")
    try:
        with db_conn() as conn:
            conn.execute(
                """UPDATE analyzed_calls SET monitor_alert=1
                   WHERE symbol=? AND status IN ('matched','saved')""",
                (sym,),
            )
            conn.commit()
    except Exception:
        pass

    if not _monitor_alerts_enabled():
        return

    emoji = ("⚠" if alert["kind"] == "MAE_BREACH"
             else "🎯" if alert["kind"] == "TRAIL_TRIGGER"
             else "🛡")
    msg = (
        f"{emoji} *{alert['title']}*\n"
        f"{alert['body']}\n\n"
        f"Entry: `{alert['entry']}`  Mark: `{alert['mark']}`  "
        f"ATR_4H: {alert['atr_pct']}%"
    )
    try:
        telegram_notify.send_message(msg)
    except Exception as e:
        print(f"[Monitor] Risk-alert Telegram failed: {e}", flush=True)


def _send_exposure_alert(alert: dict):
    """Telegram-only push for portfolio-level exposure alerts. No DB
    badge — these are portfolio observations, not per-call signals."""
    if not _monitor_alerts_enabled():
        return
    syms = ", ".join(alert.get("symbols") or [])
    msg = (
        f"🧮 *{alert['title']}*\n"
        f"{alert['body']}\n\n"
        f"Symbols: `{syms}`"
    )
    try:
        telegram_notify.send_message(msg)
    except Exception as e:
        print(f"[Monitor] Exposure-alert Telegram failed: {e}", flush=True)


def _send_monitor_alert(position: dict, result: dict):
    if not _monitor_alerts_enabled():
        symbol = position.get("symbol", "?")
        print(f"[Monitor] Monitor alerts disabled — skipped alert for {symbol}", flush=True)
        return

    symbol  = position.get("symbol", "?")
    unrl    = float(position.get("unrealized_pct", 0) or 0)
    action  = result["action"]
    rating  = result["risk_rating"]
    reason  = result["action_reason"]
    summary = result["summary"]
    emoji   = "🔴" if rating >= 8 else "🟡" if rating >= 6 else "🟢"

    msg = (
        f"{emoji} *Monitor Alert — {symbol}*\n"
        f"Action: `{action}` (Risk {rating}/10)\n"
        f"Reason: {reason}\n\n"
        f"{summary}"
    )
    try:
        telegram_notify.send_message(msg)
    except Exception as e:
        print(f"[Monitor] Telegram alert failed: {e}", flush=True)


def start():
    def _loop():
        import journal_paused
        time.sleep(FIRST_DELAY)
        while True:
            try:
                if journal_paused.is_paused():
                    print("[Monitor] paused — skipping monitor cycle", flush=True)
                else:
                    _run_once()
                # Scanner-silence watchdog runs every cycle regardless of
                # journal_paused (we WANT to be told when scanner is quiet).
                _check_scanner_silence()
            except Exception as e:
                print(f"[Monitor] Unexpected error in monitor loop: {e}", flush=True)
            time.sleep(MONITOR_INTERVAL)

    t = threading.Thread(target=_loop, name="monitor-scheduler", daemon=True)
    t.start()
    print(f"[Monitor] Background monitor started (every {MONITOR_INTERVAL}s, "
          f"first run in {FIRST_DELAY}s)", flush=True)

    # ── Daily Telegram report (09:00 UTC) ──────────────────────────────────
    def _daily_report_loop():
        import datetime
        last_sent = None
        while True:
            try:
                now = datetime.datetime.utcnow()
                today = now.date()
                # Fire at first cycle ≥ 09:00 UTC each day (next chance the loop runs after 09:00)
                if now.hour >= 9 and last_sent != today:
                    try:
                        from trading import daily_report
                        from database import db_conn
                        with db_conn() as _conn:
                            ok = daily_report.send_daily_report(_conn)
                        print(f"[DailyReport] sent={ok} at {now.isoformat()}", flush=True)
                    except Exception as e:
                        print(f"[DailyReport] failed: {e}", flush=True)
                    last_sent = today
            except Exception as e:
                print(f"[DailyReport] outer error: {e}", flush=True)
            time.sleep(300)  # check every 5 min — once-per-day gating done by last_sent

    t2 = threading.Thread(target=_daily_report_loop, name="daily-report", daemon=True)
    t2.start()
    print("[DailyReport] Background daily-report scheduler started (sends at first cycle ≥ 09:00 UTC)", flush=True)

    # ── Hourly R-3 backfill (funding + liq_distance) ──────────────────────
    def _r3_backfill_loop():
        while True:
            try:
                from trading import r3_funding_liq
                from database import db_conn
                with db_conn() as _conn:
                    summary = r3_funding_liq.run_all(_conn)
                if summary.get("liq", {}).get("updated", 0) > 0 or summary.get("funding", {}).get("updated", 0) > 0:
                    print(f"[R-3] backfilled: {summary}", flush=True)
            except Exception as e:
                print(f"[R-3] backfill error: {e}", flush=True)
            time.sleep(3600)  # every 1h

    t3 = threading.Thread(target=_r3_backfill_loop, name="r3-backfill", daemon=True)
    t3.start()
    print("[R-3] Background funding/liq backfill scheduler started (hourly)", flush=True)

    # ── Per-symbol learner (every 6h) ──────────────────────────────────────
    def _learner_symbol_loop():
        import time as _t
        _t.sleep(180)  # initial 3min delay so other inits finish first
        while True:
            try:
                from trading import learner_symbol
                from database import db_conn
                with db_conn() as _conn:
                    summary = learner_symbol.evaluate_and_update(_conn)
                applied = len(summary.get("applied", []))
                if applied > 0:
                    print(f"[Learner-symbol] applied {applied} change(s): {summary['applied']}", flush=True)
            except Exception as e:
                print(f"[Learner-symbol] error: {e}", flush=True)
            time.sleep(6 * 3600)  # every 6h

    t4 = threading.Thread(target=_learner_symbol_loop, name="learner-symbol", daemon=True)
    t4.start()
    print("[Learner-symbol] Background per-symbol learner started (every 6h, first run in 3min)", flush=True)

    # ── L-2 time-bucket learner (session/DoW/hour, every 6h) ─────────────
    def _learner_time_loop():
        import time as _t
        _t.sleep(210)  # offset 30s after symbol-learner
        while True:
            try:
                from trading import learner_time
                from database import db_conn
                with db_conn() as _conn:
                    summary = learner_time.run_all(_conn)
                total_applied = sum(len(d.get("applied", [])) for d in summary.values())
                if total_applied > 0:
                    print(f"[Learner-time] applied {total_applied} change(s): {summary}", flush=True)
            except Exception as e:
                print(f"[Learner-time] error: {e}", flush=True)
            time.sleep(6 * 3600)  # every 6h

    t5 = threading.Thread(target=_learner_time_loop, name="learner-time", daemon=True)
    t5.start()
    print("[Learner-time] Background session/DoW/hour learner started (every 6h, first run in ~3.5min)", flush=True)

    # ── L-3 threshold learner (consensus_min_score, once per day) ─────────
    def _learner_threshold_loop():
        import time as _t
        _t.sleep(240)  # 4min initial offset
        while True:
            try:
                from trading import learner_threshold
                from database import db_conn
                with db_conn() as _conn:
                    result = learner_threshold.evaluate_and_update(_conn)
                if result.get("action") == "applied":
                    print(f"[Learner-threshold] applied {result['old']}→{result['new']}: {result['reason']}", flush=True)
                elif result.get("action") == "rejected_by_validator":
                    print(f"[Learner-threshold] proposal rejected by A-B: {result['reason']}", flush=True)
            except Exception as e:
                print(f"[Learner-threshold] error: {e}", flush=True)
            time.sleep(24 * 3600)  # daily

    t6 = threading.Thread(target=_learner_threshold_loop, name="learner-threshold", daemon=True)
    t6.start()
    print("[Learner-threshold] Background threshold learner started (daily)", flush=True)

    # ── A-C Post-Mortem agent (hourly — picks up new closes) ──────────────
    def _post_mortem_loop():
        import time as _t
        _t.sleep(300)  # 5min initial offset
        while True:
            try:
                from trading import post_mortem
                from database import db_conn
                with db_conn() as _conn:
                    summary = post_mortem.run_pending(_conn, max_per_cycle=5)
                if summary.get("analyzed", 0) > 0:
                    print(f"[Post-mortem] analyzed {summary['analyzed']} loss(es), "
                          f"cost=${summary['total_cost_usd']:.4f}: {summary['results']}",
                          flush=True)
            except Exception as e:
                print(f"[Post-mortem] error: {e}", flush=True)
            time.sleep(3600)  # hourly

    t7 = threading.Thread(target=_post_mortem_loop, name="post-mortem", daemon=True)
    t7.start()
    print("[Post-mortem] Background post-mortem agent started (hourly)", flush=True)

    # ── N-4 VPIN snapshot loop (every 5min, top watchlist) ────────────────
    def _vpin_snapshot_loop():
        import time as _t
        _t.sleep(330)  # offset 5.5min so initial bursts don't collide
        while True:
            try:
                from trading import vpin
                from scanner_watchlist import DEFAULT_WATCHLIST
                from database import db_conn
                # Sample top 20 symbols by watchlist order — VPIN only makes
                # sense for highly-traded names anyway (low-vol names lack
                # enough aggTrades to compute meaningful buckets).
                symbols = list(DEFAULT_WATCHLIST)[:20]
                with db_conn() as _conn:
                    results = vpin.snapshot(_conn, symbols)
                veto_n = sum(1 for r in results if r.get("vpin") and r["vpin"] >= 0.7)
                print(f"[VPIN] sampled {len(results)}/{len(symbols)} symbols, "
                      f"{veto_n} in veto zone (≥0.70)", flush=True)
            except Exception as e:
                print(f"[VPIN] error: {e}", flush=True)
            time.sleep(5 * 60)  # every 5 min

    t8 = threading.Thread(target=_vpin_snapshot_loop, name="vpin", daemon=True)
    t8.start()
    print("[VPIN] Background N-4 VPIN snapshot loop started (every 5min, top 20 by watchlist order)", flush=True)

    # ── L-4 TP/SL distance learner (daily) ────────────────────────────────
    def _learner_tpsl_loop():
        import time as _t
        _t.sleep(360)  # 6min initial offset
        while True:
            try:
                from trading import learner_tpsl
                from database import db_conn
                with db_conn() as _conn:
                    summary = learner_tpsl.evaluate_and_update(_conn)
                if summary.get("applied"):
                    print(f"[Learner-TPSL] applied {len(summary['applied'])} change(s): "
                          f"{summary['applied']}", flush=True)
            except Exception as e:
                print(f"[Learner-TPSL] error: {e}", flush=True)
            time.sleep(24 * 3600)

    t9 = threading.Thread(target=_learner_tpsl_loop, name="learner-tpsl", daemon=True)
    t9.start()
    print("[Learner-TPSL] Background L-4 TP/SL learner started (daily)", flush=True)

    # ── L-5 Risk-parameter learner (daily) ────────────────────────────────
    def _learner_risk_loop():
        import time as _t
        _t.sleep(390)
        while True:
            try:
                from trading import learner_risk
                from database import db_conn
                with db_conn() as _conn:
                    summary = learner_risk.evaluate_and_update(_conn)
                if summary.get("applied"):
                    print(f"[Learner-Risk] applied {len(summary['applied'])} change(s) "
                          f"(dd_pause={summary.get('dd_pause')}): {summary['applied']}", flush=True)
            except Exception as e:
                print(f"[Learner-Risk] error: {e}", flush=True)
            time.sleep(24 * 3600)

    t10 = threading.Thread(target=_learner_risk_loop, name="learner-risk", daemon=True)
    t10.start()
    print("[Learner-Risk] Background L-5 risk learner started (daily)", flush=True)

    # ── A-D Execution-quality snapshot (hourly) ───────────────────────────
    def _exec_quality_loop():
        import time as _t
        _t.sleep(420)
        while True:
            try:
                from trading import exec_quality
                from database import db_conn
                with db_conn() as _conn:
                    agg = exec_quality.snapshot_to_settings(_conn)
                if agg.get("alert"):
                    print(f"[ExecQuality] 🚨 ALERT avg_bps={agg['avg_bps']}", flush=True)
                elif agg.get("warn"):
                    print(f"[ExecQuality] ⚠ WARN avg_bps={agg['avg_bps']}", flush=True)
            except Exception as e:
                print(f"[ExecQuality] error: {e}", flush=True)
            time.sleep(3600)

    t11 = threading.Thread(target=_exec_quality_loop, name="exec-quality", daemon=True)
    t11.start()
    print("[ExecQuality] Background A-D snapshot loop started (hourly)", flush=True)
