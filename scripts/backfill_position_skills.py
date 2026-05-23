#!/usr/bin/env python3
"""
backfill_position_skills.py — populate the new skill-provenance columns
on existing positions by joining against the futures_ai_log shadow logs.

Run once after migration v52-57 lands. Idempotent: rows that already have
a populated `consensus_model_used` are skipped (re-running is safe).

Mapping:
  positions row (auto_ai)  ←  futures_ai_log event=consensus_approved
                              same symbol + direction
                              within ±5 min of position.open_time
                              payload_json carries the snapshot fields

Pre-Opus-switch (before 2026-05-23 ~19:30) trades default to
consensus_model_used='sonnet' since that was the consensus model.

Run:
    python3 scripts/backfill_position_skills.py [--dry-run] [--verbose]
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

# Switchover boundary — anything opened before this used Sonnet consensus.
# Set to a few minutes BEFORE the actual deploy so we don't mislabel anything
# that opened during the switch.
OPUS_SWITCH_TS = "2026-05-23 17:25:00"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="don't write — just report what would change")
    ap.add_argument("--verbose", action="store_true",
                    help="per-position diagnostics")
    args = ap.parse_args(argv)

    if not DB.exists():
        print(f"✗ DB not found at {DB}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # Pull auto_ai positions that haven't been backfilled yet
    positions = conn.execute("""
        SELECT id, symbol, direction, open_time, setup_type, setup_score
        FROM positions
        WHERE chain='auto_ai'
          AND (consensus_model_used IS NULL OR consensus_model_used = '')
        ORDER BY open_time
    """).fetchall()

    if not positions:
        print("nothing to backfill (all auto_ai positions already tagged)")
        return 0

    print(f"backfilling {len(positions)} auto_ai positions")
    updated = 0
    no_match = 0

    for p in positions:
        pid = p["id"]
        sym = p["symbol"]
        direction = p["direction"]
        open_time = p["open_time"]
        setup_type_existing = p["setup_type"]

        # Search the shadow log for the consensus_approved event that produced
        # this trade. Window: ±5 minutes around open_time, same symbol+direction.
        log_rows = conn.execute("""
            SELECT ts, payload_json
            FROM futures_ai_log
            WHERE event='consensus_approved'
              AND symbol=? AND direction=?
              AND ts >= datetime(?, '-5 minutes')
              AND ts <= datetime(?, '+5 minutes')
            ORDER BY ABS(strftime('%s', ts) - strftime('%s', ?))
            LIMIT 1
        """, (sym, direction, open_time, open_time, open_time)).fetchall()

        # Default values (used when no shadow log match is found)
        consensus_model = "sonnet" if open_time < OPUS_SWITCH_TS else "opus"
        bear_phase = None
        archetype = setup_type_existing
        po3_total = None
        opus_had_overrides = 0
        tp_levels_count = 0

        if log_rows:
            try:
                payload = json.loads(log_rows[0]["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}

            # The shadow log was added 2026-05-23 afternoon — older
            # consensus_approved payloads won't have these fields. Pull
            # defensively.
            bear_phase = payload.get("bear_phase")
            archetype = payload.get("archetype") or archetype
            # po3_total is reconstructed from the components inside the snapshot
            po3_total = 0.0
            for k in ("po3_range", "po3_fvg", "po3_session"):
                v = payload.get(k)
                try:
                    po3_total += float(v or 0)
                except (TypeError, ValueError):
                    pass
            if po3_total == 0.0 and not any(payload.get(k) for k in ("po3_range","po3_fvg","po3_session")):
                po3_total = None  # truly absent vs explicit zero
            else:
                po3_total = round(po3_total, 3)

            overrides = payload.get("overrides") or {}
            opus_had_overrides = 1 if overrides else 0

        # tp_levels_count comes from positions.tp_levels (already populated by
        # Phase-1 multi-TP shipping for new positions; NULL for old ones).
        tp_levels_json_row = conn.execute(
            "SELECT tp_levels FROM positions WHERE id=?", (pid,)
        ).fetchone()
        if tp_levels_json_row and tp_levels_json_row["tp_levels"]:
            try:
                tp_levels_count = len(json.loads(tp_levels_json_row["tp_levels"]))
            except json.JSONDecodeError:
                pass

        if args.verbose:
            print(f"  pid={pid} {sym} {direction} open_time={open_time}")
            print(f"     → consensus_model={consensus_model}, archetype={archetype}, "
                  f"bear_phase={bear_phase}, po3_total={po3_total}, "
                  f"opus_had_overrides={opus_had_overrides}, tp_count={tp_levels_count}")
            if not log_rows:
                print(f"     ⚠ no shadow log match — using defaults only")

        if not log_rows:
            no_match += 1

        if not args.dry_run:
            conn.execute("""
                UPDATE positions
                SET consensus_model_used = ?,
                    bear_phase_at_open   = ?,
                    archetype_at_open    = ?,
                    po3_total            = ?,
                    opus_had_overrides   = ?,
                    tp_levels_count      = ?
                WHERE id=?
            """, (consensus_model, bear_phase, archetype, po3_total,
                  opus_had_overrides, tp_levels_count, pid))
            updated += 1

    if not args.dry_run:
        conn.commit()

    print(f"\nsummary:")
    print(f"  scanned: {len(positions)}")
    print(f"  updated: {updated}")
    print(f"  no shadow log match (defaults applied): {no_match}")
    if args.dry_run:
        print(f"  (dry run — no rows written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
