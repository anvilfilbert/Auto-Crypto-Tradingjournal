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
            except Exception as e:
                print(f"[Monitor] Unexpected error in monitor loop: {e}", flush=True)
            time.sleep(MONITOR_INTERVAL)

    t = threading.Thread(target=_loop, name="monitor-scheduler", daemon=True)
    t.start()
    print(f"[Monitor] Background monitor started (every {MONITOR_INTERVAL}s, "
          f"first run in {FIRST_DELAY}s)", flush=True)
