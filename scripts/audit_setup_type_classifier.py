"""
Audit the rules-based archetype classifier against actual historical
indicator values. Prints the indicator profile that drove each tag so we
can see WHY the system chose 'continuation' vs 'breakout' for each trade.

Run after backfill_setup_types.py to validate (or refute) the labels.
"""
import datetime as _dt
import random
import sys

sys.path.insert(0, "/home/fbauer/trading-journal")

from database import db_conn
import chart_context


def _to_ms(iso: str) -> int:
    dt = _dt.datetime.fromisoformat(iso[:19]).replace(tzinfo=_dt.timezone.utc)
    return int(dt.timestamp() * 1000)


def profile(symbol: str, open_time: str) -> dict:
    try:
        ctx  = chart_context.get_historical_context(symbol, ["4H"], _to_ms(open_time))
        inds = (ctx.get("4H", {}) or {}).get("indicators", {}) or {}
        return {
            "rsi":  (inds.get("rsi") or {}).get("value"),
            "adx":  (inds.get("adx") or {}).get("value"),
            "vol":  (inds.get("volume") or {}).get("ratio"),
            "wt":   (inds.get("wavetrend") or {}).get("signal"),
            "ema":  (inds.get("ema") or {}).get("alignment"),
            "macd": (inds.get("macd") or {}).get("signal"),
        }
    except Exception as e:
        return {"error": str(e)[:60]}


def show(tag: str, n: int = 8) -> None:
    print(f"\n=== sample of {n} trades tagged '{tag}' ===")
    print(f"{'symbol':14s} {'dir':5s} {'pnl':>8s}  RSI    ADX    vol    WT_signal       EMA_align       MACD")
    print("-" * 110)
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT symbol, direction, ROUND(realized_pnl,2), open_time "
            "FROM positions WHERE setup_type=? ORDER BY RANDOM() LIMIT ?",
            (tag, n),
        ).fetchall()
    for r in rows:
        sym, dir_, pnl, open_time = r
        p = profile(sym, open_time)
        if "error" in p:
            print(f"{sym:14s} {dir_:5s} {pnl:>8.2f}  ERROR: {p['error']}")
            continue
        rsi = p["rsi"]
        adx = p["adx"]
        vol = p["vol"]
        wt  = p["wt"] or "none"
        ema = p["ema"] or "none"
        macd = p["macd"] or "none"
        print(f"{sym:14s} {dir_:5s} {pnl:>8.2f}  "
              f"{rsi if rsi is None else f'{rsi:5.1f}'}  "
              f"{adx if adx is None else f'{adx:5.1f}'}  "
              f"{vol if vol is None else f'{vol:4.2f}'}   "
              f"{str(wt)[:14]:14s}  {str(ema)[:14]:14s}  {macd}")


def main():
    random.seed(42)
    for tag in ("breakout", "continuation"):
        show(tag)
    print()


if __name__ == "__main__":
    main()
