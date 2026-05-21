"""
volume_baseline.py — Rolling per-(symbol, timeframe) volume baseline.

Inspired by Kaizen Tools' localStorage baseline (analysis 2026-05-21). Instead
of comparing each bar's volume to a flat 20-bar trailing average (the legacy
behavior in chart_indicators.compute_volume), we accumulate the symbol's own
recent volume samples into a ring buffer and measure surges against its
personal median.

Why this matters: a chronically-thin altcoin spiking to 1.5x its 20-bar avg
isn't unusual, while a chronically-active major doing the same IS. The
per-symbol baseline makes "surge" mean something consistent across the
watchlist.

Persistence: SQLite table `volume_baseline` keyed by (symbol, timeframe).
Samples are stored as a JSON-encoded list — small enough that the parse cost
is negligible vs. an extra table with one row per sample.

Cold-start: until MATURE_AT samples accrue (~30 min at 5m updates), callers
get None from surge_ratio() and should fall back to whatever they were doing
before (instant ratio, trailing avg, etc).
"""
import json
import time

from database import db_conn

MAX_SAMPLES     = 60   # ring buffer length (~5h of 5m samples)
MATURE_AT       = 6    # samples needed before baseline is considered trusted
MIN_GAP_SECONDS = 90   # throttle: never record samples closer than this


def _load(conn, symbol: str, timeframe: str) -> tuple[list[float], float]:
    """Return (samples, last_ts_epoch) for this (symbol, tf), or ([], 0)."""
    row = conn.execute(
        "SELECT samples, last_ts FROM volume_baseline WHERE symbol=? AND timeframe=?",
        (symbol, timeframe),
    ).fetchone()
    if not row:
        return [], 0.0
    try:
        return json.loads(row["samples"]), float(row["last_ts"])
    except (ValueError, TypeError):
        return [], 0.0


def _save(conn, symbol: str, timeframe: str, samples: list, ts: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO volume_baseline (symbol, timeframe, samples, last_ts) "
        "VALUES (?, ?, ?, ?)",
        (symbol, timeframe, json.dumps(samples), ts),
    )


def record_sample(symbol: str, timeframe: str, volume: float) -> None:
    """Append a volume sample to the ring buffer (throttled by MIN_GAP_SECONDS).
    Silently no-ops on volume <= 0 or DB errors — never propagates exceptions
    because it sits in the hot path of scanner Stage 1."""
    if not (volume and volume > 0):
        return
    try:
        with db_conn() as conn:
            samples, last_ts = _load(conn, symbol, timeframe)
            now = time.time()
            if now - last_ts < MIN_GAP_SECONDS:
                return
            samples.append(float(volume))
            if len(samples) > MAX_SAMPLES:
                samples = samples[-MAX_SAMPLES:]
            _save(conn, symbol, timeframe, samples, now)
    except Exception:
        pass


def stats(symbol: str, timeframe: str) -> dict:
    """Return {mature, n, median} for this (symbol, timeframe).
    mature is True only when n >= MATURE_AT — callers should treat the
    median as untrusted until then."""
    try:
        with db_conn() as conn:
            samples, _ = _load(conn, symbol, timeframe)
    except Exception:
        return {"mature": False, "n": 0, "median": None}
    n = len(samples)
    if n == 0:
        return {"mature": False, "n": 0, "median": None}
    sorted_s = sorted(samples)
    mid = len(sorted_s) // 2
    median = sorted_s[mid] if len(sorted_s) % 2 else (sorted_s[mid - 1] + sorted_s[mid]) / 2
    return {"mature": n >= MATURE_AT, "n": n, "median": median}


def surge_ratio(symbol: str, timeframe: str, current_volume: float) -> tuple[float | None, bool]:
    """Return (ratio, mature). ratio = current / median (None if no baseline data).
    Callers should use the ratio only when mature=True; otherwise fall back to
    their pre-baseline ratio (instant or 20-bar trailing avg)."""
    if not (current_volume and current_volume > 0):
        return None, False
    st = stats(symbol, timeframe)
    if not st["mature"] or not st["median"]:
        return None, False
    return current_volume / st["median"], True


def reset(symbol: str | None = None, timeframe: str | None = None) -> int:
    """Wipe baseline data. With no args, wipes all. Returns rows deleted."""
    try:
        with db_conn() as conn:
            if symbol and timeframe:
                cur = conn.execute(
                    "DELETE FROM volume_baseline WHERE symbol=? AND timeframe=?",
                    (symbol, timeframe),
                )
            elif symbol:
                cur = conn.execute("DELETE FROM volume_baseline WHERE symbol=?", (symbol,))
            else:
                cur = conn.execute("DELETE FROM volume_baseline")
            return cur.rowcount or 0
    except Exception:
        return 0
