"""Classify a single position via AI. Usage: python3 scripts/classify_one_position.py <position_id>"""
import datetime as _dt
import sys
sys.path.insert(0, "/home/fbauer/trading-journal")

from database import db_conn
import chart_context
import setup_classifier as sc


def main():
    if len(sys.argv) < 2:
        print("usage: classify_one_position.py <position_id>")
        sys.exit(1)
    pid = int(sys.argv[1])

    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, symbol, direction, open_time, setup_type "
            "FROM positions WHERE id=?", (pid,)
        ).fetchone()
        if not row:
            print(f"no position id={pid}")
            sys.exit(1)
        print(f"row: id={row[0]} {row[1]} {row[2]} open={row[3]} setup_type={row[4]!r}")

        dt = _dt.datetime.fromisoformat(row[3][:19]).replace(tzinfo=_dt.timezone.utc)
        ms = int(dt.timestamp() * 1000)
        ctx = chart_context.get_historical_context(row[1], ["4H", "1H"], ms)
        pt4 = (ctx.get("4H") or {}).get("prompt_text", "")
        pt1 = (ctx.get("1H") or {}).get("prompt_text", "")

        r = sc.classify_ai(row[1], row[2], row[3],
                           prompt_text_4h=pt4, prompt_text_1h=pt1)
        print("AI verdict:", r)

        conn.execute("UPDATE positions SET setup_type=? WHERE id=?",
                     (r["archetype"], pid))
        conn.commit()
        print(f"updated id={pid} setup_type={r['archetype']}")


if __name__ == "__main__":
    main()
