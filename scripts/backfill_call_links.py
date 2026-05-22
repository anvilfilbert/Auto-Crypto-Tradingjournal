"""
One-shot backfill: link historical positions to analyzed_calls based on
symbol + direction + price scale within 20% + call created within 30 days
before position open_time.

For each linked position, also propagates trade_type/setup_type/setup_label
into positions.setup_type via the existing _populate_setup_type_from_call.

Safe to re-run — only touches rows where positions.call_id IS NULL.
"""

import sys
sys.path.insert(0, "/home/fbauer/trading-journal")

from database import db_conn
from trade_utils import price_scale_matches
from sync_base import _populate_setup_type_from_call


def main():
    with db_conn() as conn:
        cur = conn.cursor()

        positions = cur.execute("""
            SELECT id, symbol, direction, entry_price, open_time
            FROM positions
            WHERE call_id IS NULL
            ORDER BY open_time DESC
        """).fetchall()

        print(f"Scanning {len(positions)} unlinked positions…")
        linked   = 0
        skipped  = 0
        no_match = 0

        for pos_id, symbol, direction, pos_entry, open_time in positions:
            dir_filter = "Long" if "long" in (direction or "").lower() else "Short"

            candidates = cur.execute("""
                SELECT id, avg_entry, entry_price
                FROM analyzed_calls
                WHERE symbol = ?
                  AND direction LIKE ?
                  AND status IN ('saved','matched','closed','dismissed')
                  AND entry_price IS NOT NULL
                  AND created_at >= datetime(?, '-30 days')
                  AND created_at <= ?
                  AND id NOT IN (SELECT call_id FROM positions
                                 WHERE call_id IS NOT NULL)
                ORDER BY created_at DESC
                LIMIT 5
            """, (symbol, dir_filter + "%",
                  open_time or "9999", open_time or "9999")).fetchall()

            if not candidates:
                no_match += 1
                continue

            chosen = None
            for c in candidates:
                call_ref = c[1] or c[2]
                if price_scale_matches(call_ref, pos_entry):
                    chosen = c
                    break

            if not chosen:
                skipped += 1
                continue

            call_id = chosen[0]
            cur.execute("UPDATE positions SET call_id=? WHERE id=?", (call_id, pos_id))
            _populate_setup_type_from_call(conn, pos_id, call_id)
            linked += 1

        conn.commit()

    print(f"linked:   {linked}")
    print(f"no_match: {no_match}   (no candidate calls in 30d window)")
    print(f"skipped:  {skipped}   (candidate found but failed price-scale guard)")

    # Show propagation result
    with db_conn() as conn:
        breakdown = conn.execute("""
            SELECT COALESCE(NULLIF(setup_type,''),'(untagged)') AS tag, COUNT(*)
            FROM positions
            GROUP BY tag
            ORDER BY COUNT(*) DESC
        """).fetchall()
    print("\nFinal setup_type distribution:")
    for tag, n in breakdown:
        print(f"  {tag:30s} {n:4d}")


def backfill_mfe_mae(limit: int = 200):
    """
    Backfill MFE/MAE on closed positions that lack the values. Caps to the
    most recent `limit` positions to keep the run reasonable — each iteration
    fetches 1H candles for the trade window.
    """
    import mfe_mae as _mfe
    with db_conn() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM positions WHERE mfe_pct IS NULL "
            "  AND close_time IS NOT NULL AND close_time != '' "
            "ORDER BY close_time DESC LIMIT ?", (limit,)
        ).fetchall()]
        print(f"\nBackfilling MFE/MAE for {len(ids)} positions…")
        done = 0
        for i, pid in enumerate(ids, 1):
            if _mfe.update_position(conn, pid):
                done += 1
            if i % 25 == 0:
                conn.commit()
                print(f"  …{i}/{len(ids)}  populated={done}")
        conn.commit()
        print(f"\nMFE/MAE populated for {done}/{len(ids)} positions.")


if __name__ == "__main__":
    main()
    backfill_mfe_mae()
