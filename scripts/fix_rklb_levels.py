"""
Retro-attach sane SL/TP to the live RKLBUSDT Long position.

Based on inspect_rklb.py output:
  Entry:        $134.43
  Mark:         $134.42 (essentially flat)
  Liquidation:  $124.17  (-7.6%)
  4H ATR:       $4.89   (3.64% of entry)

Chosen levels (matches the ATR-based defaults the auto-trader uses):
  SL  @ -1× ATR_4H = $129.54  (well above liquidation)
  TP1 @ +2× ATR_4H = $144.21  (R:R 1:2)

Risk on $25 notional × 10x leverage:
  SL hit  → loss = -3.64% × $25 = -$0.91
  TP1 hit → gain = +7.28% × $25 = +$1.82
"""
import json, sys
sys.path.insert(0, "/home/fbauer/trading-journal")

from trading import bitget_trader as t

ENTRY    = 134.43
SL_PRICE = 129.54
TP_PRICE = 144.21

print(f"=== Before ===")
for p in t.get_open_positions():
    if p["symbol"] == "RKLBUSDT":
        print(json.dumps(p, indent=2))

print()
print(f"=== Attaching SL=${SL_PRICE} TP=${TP_PRICE} ===")
result = t.attach_sl_tp_to_existing("RKLBUSDT", "long",
                                      sl_price=SL_PRICE,
                                      tp_price=TP_PRICE)
print(json.dumps(result, indent=2))

print()
print(f"=== After (verify SL/TP attached) ===")
for p in t.get_open_positions():
    if p["symbol"] == "RKLBUSDT":
        print(json.dumps(p, indent=2))
