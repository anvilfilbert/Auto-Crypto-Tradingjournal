#!/usr/bin/env python3
"""
backfill_tp_levels.py — populate `positions.tp_levels` for auto_ai positions
that pre-date the multi-TP code (or opened in the brief deploy gap before
the multi-TP code was loaded).

Sources tp1+tp2 from the position's `real_open` event in `futures_ai_log`,
then builds a tp_levels ladder using:
  - trading.config.TP_SPLITS[n] for the per-tier percentages
  - trading.config.pick_max_tp_count(notional) to clamp by Bitget min-notional

Idempotent: skips any position that already has a non-empty tp_levels JSON.

Usage:
    python3 scripts/backfill_tp_levels.py [--dry-run] [--include-closed] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB = ROOT / "trading_journal.db"

sys.path.insert(0, str(ROOT))
from trading.config import TP_SPLITS, pick_max_tp_count  # noqa: E402


def _has_tp_levels(raw: str | None) -> bool:
    if not raw or raw.strip() in ("", "null", "[]"):
        return False
    try:
        return bool(json.loads(raw))
    except (TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-closed", action="store_true",
                    help="also backfill closed positions (default: open only)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if not DB.exists():
        print(f"✗ DB not found at {DB}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    open_filter = "" if args.include_closed else "AND (close_time IS NULL OR close_time = '')"

    positions = conn.execute(f"""
        SELECT id, symbol, direction, open_time, size_usdt, tp_levels
        FROM positions
        WHERE chain='auto_ai' {open_filter}
        ORDER BY open_time
    """).fetchall()

    eligible = [p for p in positions if not _has_tp_levels(p["tp_levels"])]
    print(f"found {len(positions)} auto_ai positions; "
          f"{len(eligible)} need backfill")

    updated = 0
    no_log = 0
    no_tps = 0

    for p in eligible:
        pid = p["id"]
        sym = p["symbol"]
        notional = float(p["size_usdt"] or 25.0)

        # Pull the matching real_open log entry
        log_row = conn.execute("""
            SELECT payload_json FROM futures_ai_log
            WHERE event='real_open'
              AND json_extract(payload_json, '$.position_id') = ?
            ORDER BY id DESC LIMIT 1
        """, (pid,)).fetchone()

        if not log_row:
            no_log += 1
            if args.verbose:
                print(f"  pid={pid} {sym}: no real_open log entry — skip")
            continue

        try:
            payload = json.loads(log_row["payload_json"])
        except json.JSONDecodeError:
            payload = {}

        tp1 = payload.get("tp1")
        tp2 = payload.get("tp2")
        tps = [float(t) for t in (tp1, tp2) if t]

        if not tps:
            no_tps += 1
            if args.verbose:
                print(f"  pid={pid} {sym}: no tp1/tp2 in log — skip")
            continue

        # Direction-aware order
        is_long = (p["direction"] or "").strip().lower() == "long"
        tps_sorted = sorted(set(tps)) if is_long else sorted(set(tps), reverse=True)

        desired = len(tps_sorted)
        allowed = pick_max_tp_count(notional, ideal=desired)
        tps_capped = tps_sorted[:allowed]
        splits = TP_SPLITS.get(allowed, [100])

        tp_levels = [
            {"idx": i + 1, "price": float(price), "pct": float(splits[i]),
             "hit": False, "hit_at": None}
            for i, price in enumerate(tps_capped)
        ]
        tp_levels_json = json.dumps(tp_levels)

        if args.verbose:
            print(f"  pid={pid} {sym} ({p['direction']}) notional=${notional} "
                  f"→ {allowed} TPs: {[f'{l[\"price\"]} ({l[\"pct\"]}%)' for l in tp_levels]}")

        if not args.dry_run:
            conn.execute(
                "UPDATE positions SET tp_levels=?, tp_levels_count=? WHERE id=?",
                (tp_levels_json, len(tp_levels), pid),
            )
            updated += 1

    if not args.dry_run:
        conn.commit()

    print(f"\nsummary:")
    print(f"  eligible:       {len(eligible)}")
    print(f"  updated:        {updated}")
    print(f"  no real_open:   {no_log}")
    print(f"  no tp1/tp2:     {no_tps}")
    if args.dry_run:
        print(f"  (dry run — no rows written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
