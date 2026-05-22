"""
One-shot backfill: classify historical positions by setup archetype.

For every position whose setup_type is empty (or contains a stale model
name like 'claude-sonnet-4-6'), reconstructs the technical picture
visible at the trade's open_time and runs scanner_prompts._detect_archetype()
against it. Result: reversal | breakout | continuation.

Same classifier the live scanner uses on fresh setups, so historical and
new rows share a vocabulary for Edge Lab analytics.

Cost: each call fetches 4H candles (one bitget API call per trade) and
computes indicators locally. Tolerant of failures — leaves setup_type
empty when historical context isn't reachable.
"""
import datetime as _dt
import sys
sys.path.insert(0, "/home/fbauer/trading-journal")

from database import db_conn
import chart_context
from scanner_prompts import _detect_archetype


# Model-name strings to scrub before reclassifying — these were leaked into
# setup_type by an older propagation path that fell through to setup_label.
_GARBAGE_TOKENS = ("claude-", "haiku", "sonnet", "opus", "gpt-",
                   "Quick score only")


def _is_garbage(s: str | None) -> bool:
    if not s:
        return True
    sl = s.lower()
    return any(tok.lower() in sl for tok in _GARBAGE_TOKENS)


def _to_ms(iso: str) -> int | None:
    try:
        s = (iso or "").strip()
        if not s:
            return None
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00")[:19])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def classify_one(symbol: str, direction: str, open_time: str) -> str | None:
    """Return reversal | breakout | continuation, or None on failure."""
    ms = _to_ms(open_time)
    if ms is None:
        return None
    try:
        ctx = chart_context.get_historical_context(symbol, ["4H"], ms)
        if not ctx:
            return None
        return _detect_archetype(ctx, direction)
    except Exception as e:
        print(f"  {symbol} {open_time[:10]} — classifier failed: {e}",
              flush=True)
        return None


def main():
    with db_conn() as conn:
        rows = conn.execute("""
            SELECT id, symbol, direction, open_time, setup_type
            FROM positions
            WHERE open_time IS NOT NULL AND open_time != ''
            ORDER BY open_time DESC
        """).fetchall()
        all_rows = [dict(r) for r in rows]

    targets = [r for r in all_rows if _is_garbage(r["setup_type"])]
    print(f"Classifying {len(targets)} of {len(all_rows)} positions…")
    print(f"  ({len(all_rows) - len(targets)} already have clean setup_type)")

    counts: dict[str, int] = {}
    done = 0
    failed = 0

    with db_conn() as conn:
        for i, r in enumerate(targets, 1):
            arch = classify_one(r["symbol"], r["direction"], r["open_time"])
            if not arch:
                failed += 1
                continue
            conn.execute(
                "UPDATE positions SET setup_type=? WHERE id=?",
                (arch, r["id"]),
            )
            counts[arch] = counts.get(arch, 0) + 1
            done += 1
            if i % 20 == 0:
                conn.commit()
                print(f"  …{i}/{len(targets)} done={done} failed={failed}")
        conn.commit()

    print(f"\nClassified {done} positions, {failed} failed historical fetch.")
    print("Distribution of newly-classified rows:")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:14s} {v:4d}")

    # Re-read final state
    with db_conn() as conn:
        final = conn.execute("""
            SELECT COALESCE(NULLIF(setup_type,''),'(untagged)') AS tag,
                   COUNT(*) AS n
            FROM positions
            GROUP BY tag
            ORDER BY n DESC
        """).fetchall()
    print("\nFinal setup_type distribution across ALL positions:")
    for tag, n in final:
        print(f"  {tag:30s} {n:4d}")


if __name__ == "__main__":
    main()
