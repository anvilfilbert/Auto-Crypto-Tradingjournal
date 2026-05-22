"""Dump raw Bitget API responses for open positions + closed-position
history so I can see the actual field names + values. Then I can fix
both:
  (A) preset_sl / preset_tp parsing — wrong field name
  (B) realized_pnl on auto-close — need to query position-history
"""
import json
import sys
sys.path.insert(0, "/home/fbauer/trading-journal")

from trading import bitget_trader as t


# --- (A) Raw open positions ---
print("=" * 72)
print("  /api/v2/mix/position/all-position raw response (for SL/TP fields)")
print("=" * 72)
try:
    d = t._request("GET", "/api/v2/mix/position/all-position", params={
        "productType": "USDT-FUTURES",
        "marginCoin":  "USDT",
    })
    rows = d if isinstance(d, list) else []
    for r in rows:
        if (r.get("total") or 0) and float(r["total"]) > 0:
            print(f"\n{r.get('symbol')} {r.get('holdSide')}:")
            for k, v in sorted(r.items()):
                print(f"  {k:30s} = {v!r}")
            break   # just one example
except Exception as e:
    print(f"FAILED: {e}")


# --- (B) Position history (to get realized_pnl on closed trades) ---
print()
print("=" * 72)
print("  /api/v2/mix/position/history-position raw response (last 7 days)")
print("=" * 72)
import time
end_ms   = int(time.time() * 1000)
start_ms = end_ms - 7 * 86400 * 1000
try:
    d = t._request("GET", "/api/v2/mix/position/history-position", params={
        "productType": "USDT-FUTURES",
        "startTime":   str(start_ms),
        "endTime":     str(end_ms),
        "pageSize":    "5",
    })
    # Response is usually {"list":[...], "endId": ...}
    if isinstance(d, dict) and "list" in d:
        rows = d.get("list") or []
    else:
        rows = d if isinstance(d, list) else []
    print(f"got {len(rows)} historical positions")
    for r in rows[:3]:
        print(f"\n{r.get('symbol')} {r.get('holdSide')}:")
        for k, v in sorted(r.items()):
            print(f"  {k:30s} = {v!r}")
except Exception as e:
    print(f"FAILED: {e}")
