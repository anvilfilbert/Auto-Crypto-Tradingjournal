"""
scanner_event_trigger.py — event-driven mini-scan trigger.

The 30-min scheduled scan covers the slow-burn case. This module covers the
fast-move case: when BTC or ETH posts a sharp 15-min candle (default ≥2%),
we kick off an out-of-cycle `force_scan()` because the broader market is
likely about to dislocate other coins too.

Cheap insurance — fires only on big moves, has its own cooldown so we
never run a flood of mini-scans during a volatile session.

Cost impact: at most ~3-6 extra scans / week on typical volatility (BTC
sees a 2%+ 15m candle ~5-10× / month). Operator-tunable thresholds.

Env knobs:
  SCANNER_EVENT_TRIGGER       — 'off' disables this thread (default on)
  SCANNER_EVENT_THRESHOLD_PCT — abs 15-min close-to-close move (default 0.02)
  SCANNER_EVENT_COOLDOWN_SEC  — min seconds between event-driven scans
                                (default 1800 = 30 min, same as scheduler cadence)
  SCANNER_EVENT_POLL_SEC      — how often to check the trigger (default 60)
  SCANNER_EVENT_SYMBOLS       — comma-separated list of triggers
                                (default 'BTCUSDT,ETHUSDT')

Lifecycle:
  start() spawns a daemon thread. The thread is no-op while
  journal_paused.is_paused() — same pause discipline as the scheduler.
  Triggered scans go through ai_scanner.force_scan() and log to
  `futures_ai_log` as 'event_scan_triggered'.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional


# ── Config ─────────────────────────────────────────────────────────────────
_ENABLED            = os.environ.get("SCANNER_EVENT_TRIGGER", "on").lower() != "off"
_THRESHOLD_PCT      = float(os.environ.get("SCANNER_EVENT_THRESHOLD_PCT", "0.02"))
_COOLDOWN_SEC       = int(os.environ.get("SCANNER_EVENT_COOLDOWN_SEC",    "1800"))
_POLL_SEC           = int(os.environ.get("SCANNER_EVENT_POLL_SEC",        "60"))
_TRIGGER_SYMBOLS    = [s.strip() for s in
                       (os.environ.get("SCANNER_EVENT_SYMBOLS") or "BTCUSDT,ETHUSDT").split(",")
                       if s.strip()]

_last_trigger_ts: float = 0.0
_thread: Optional[threading.Thread] = None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _last_15m_move(symbol: str) -> Optional[float]:
    """Returns the absolute close-to-close % change between the latest two
    closed 15m candles. None on any data error."""
    try:
        import chart_candles
        df = chart_candles.get_candles(symbol, "15m", limit=3)
    except Exception:
        return None
    if df is None or len(df) < 2:
        return None
    try:
        closes = df["close"].astype(float).tolist()
    except Exception:
        return None
    # Use the last fully-closed candle vs the previous closed candle.
    # df may include an in-flight current candle as the last row depending
    # on the data source — take the more conservative -3,-2 pair when ≥3
    # rows are present so we always compare two CLOSED candles.
    if len(closes) >= 3:
        prev, curr = closes[-3], closes[-2]
    else:
        prev, curr = closes[-2], closes[-1]
    if not prev:
        return None
    return abs(curr - prev) / prev


def _log_trigger(symbol: str, move_pct: float, scan_started: bool) -> None:
    """Persist trigger event to futures_ai_log."""
    try:
        from database import db_conn
        with db_conn() as conn:
            conn.execute("""
                INSERT INTO futures_ai_log(ts, event, symbol, direction, score, payload_json)
                VALUES (datetime('now'), 'event_scan_triggered', ?, '', 0, ?)
            """, (symbol, json.dumps({
                "trigger_symbol": symbol,
                "move_pct":       round(move_pct * 100, 3),
                "threshold_pct":  _THRESHOLD_PCT * 100,
                "scan_started":   scan_started,
                "cooldown_sec":   _COOLDOWN_SEC,
            })))
            conn.commit()
    except Exception:
        pass


# ── Main loop ──────────────────────────────────────────────────────────────

def _loop() -> None:
    print(f"[Scanner EventTrigger] watching {_TRIGGER_SYMBOLS} "
          f"@ ±{_THRESHOLD_PCT*100:.1f}% / 15m  cooldown {_COOLDOWN_SEC//60}min",
          flush=True)
    global _last_trigger_ts
    while True:
        time.sleep(_POLL_SEC)

        try:
            import journal_paused
            if journal_paused.is_paused():
                continue
        except Exception:
            pass

        # Cooldown — short-circuit before doing any network I/O
        if time.time() - _last_trigger_ts < _COOLDOWN_SEC:
            continue

        # Check each trigger symbol; first one to breach fires the scan
        for sym in _TRIGGER_SYMBOLS:
            move = _last_15m_move(sym)
            if move is None:
                continue
            if move >= _THRESHOLD_PCT:
                # Don't fire if scanner is already running — let the
                # scheduled scan complete first.
                try:
                    import ai_scanner
                    state = ai_scanner.get_state() or {}
                    if state.get("status") == "running":
                        continue
                    started = ai_scanner.force_scan(
                        min_score=1,  # show everything per scheduler convention
                    )
                except Exception as e:
                    started = False
                    print(f"[Scanner EventTrigger] force_scan error: {e}", flush=True)

                _log_trigger(sym, move, started)
                if started:
                    _last_trigger_ts = time.time()
                    print(f"[Scanner EventTrigger] {sym} moved {move*100:+.2f}% "
                          f"in last 15m — kicked off scan", flush=True)
                break  # one trigger per cycle; cooldown takes over


# ── Public start ───────────────────────────────────────────────────────────

def start() -> None:
    """Boot the trigger thread. No-op if SCANNER_EVENT_TRIGGER=off."""
    global _thread
    if not _ENABLED:
        print("[Scanner EventTrigger] Disabled via SCANNER_EVENT_TRIGGER=off",
              flush=True)
        return
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True,
                                name="scanner-event-trigger")
    _thread.start()
