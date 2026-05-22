"""
Rewrite SL/TP on the 3 positions that got nudged-to-mark*1.005 TPs.
Replaces with ATR-based ATR×1 (SL) and ATR×2 (TP), matching the
auto-trader's intended behaviour.

For each symbol:
  1. Fetch 4H ATR
  2. Compute new SL = entry - 1×ATR, new TP = entry + 2×ATR (long)
  3. Cancel ALL existing plan orders on the symbol
  4. Attach the new SL + TP via place-tpsl-order
"""
import json, sys
sys.path.insert(0, "/home/fbauer/trading-journal")

from trading import bitget_trader as t
import chart_context

BROKEN = ["CUSDT", "QNTUSDT", "INJUSDT"]

# Pull current positions for entry prices
positions = {p["symbol"]: p for p in t.get_open_positions()}

# Pull all plans so we know what to cancel
all_plans = t.get_pending_plan_orders()
plans_by_sym = {}
for p in all_plans:
    plans_by_sym.setdefault(p["symbol"], []).append(p)

for sym in BROKEN:
    if sym not in positions:
        print(f"{sym}: not open — skipping")
        continue
    pos = positions[sym]
    entry   = pos["entry_price"]
    side    = pos["direction"].lower()
    size    = pos["size_contracts"]
    spec    = t.get_contract_spec(sym)
    pp      = spec["price_place"]

    # 4H ATR
    try:
        ctx = chart_context.get_chart_context(sym, ["4H"]) or {}
        atr = float(((ctx.get("4H", {}).get("indicators", {})
                       .get("atr") or {}).get("value") or 0))
    except Exception:
        atr = 0
    if atr <= 0:
        print(f"{sym}: ATR fetch failed — skipping")
        continue

    sign = 1 if side == "long" else -1
    new_sl = round(entry - sign * atr * 1.0, pp)
    new_tp = round(entry + sign * atr * 2.0, pp)

    print(f"\n=== {sym} {side} entry={entry} ATR={atr:.6g} ({atr/entry*100:.2f}%) ===")
    print(f"  new SL @ {new_sl}  (-{atr/entry*100:.2f}%)")
    print(f"  new TP @ {new_tp}  (+{2*atr/entry*100:.2f}%)")

    # Cancel ALL existing plans on this symbol
    for plan in plans_by_sym.get(sym, []):
        body = {
            "symbol":      sym,
            "productType": "USDT-FUTURES",
            "marginCoin":  "USDT",
            "orderIdList": [{"orderId": plan["order_id"], "clientOid": ""}],
            "planType":    plan["plan_type"],
        }
        try:
            t._request("POST", "/api/v2/mix/order/cancel-plan-order", body=body)
            print(f"  cancelled {plan['plan_type']} @ ${plan['trigger_price']}")
        except Exception as e:
            print(f"  cancel failed for {plan['order_id']}: {e}")

    # Attach new SL + TP
    sl_ok = t._try_place_tpsl(sym, side, new_sl, size, plan_type="loss_plan")
    tp_ok = t._try_place_tpsl(sym, side, new_tp, size, plan_type="profit_plan")
    print(f"  attached SL: {sl_ok}  TP: {tp_ok}")

print("\n=== Final plan-order state ===")
for p in t.get_pending_plan_orders():
    print(f"  {p['symbol']:12s} {p['plan_type']:12s} trigger={p['trigger_price']:.6g}  source={p.get('source')}")
