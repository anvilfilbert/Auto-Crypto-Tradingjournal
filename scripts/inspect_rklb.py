"""Inspect RKLBUSDT live position state."""
import sys, json
sys.path.insert(0, "/home/fbauer/trading-journal")
from trading import bitget_trader as t
import chart_context

print("=== Open positions on Bitget ===")
for p in t.get_open_positions():
    print(json.dumps(p, indent=2))

print()
print("=== Current RKLBUSDT 4H ATR ===")
try:
    ctx = chart_context.get_chart_context("RKLBUSDT", ["4H"]) or {}
    atr = (ctx.get("4H", {}).get("indicators", {}).get("atr") or {}).get("value")
    print(f"ATR_4H: {atr}")
    if atr:
        entry = 134.43
        print()
        print(f"For entry={entry}:")
        print(f"  1x ATR distance: {atr:.4f} ({atr/entry*100:.2f}%)")
        print(f"  SL @ -1x ATR: {entry - atr:.4f}")
        print(f"  TP1 @ +2x ATR: {entry + 2*atr:.4f}")
        print(f"  TP2 @ +3x ATR: {entry + 3*atr:.4f}")
except Exception as e:
    print(f"chart context fetch failed: {e}")
