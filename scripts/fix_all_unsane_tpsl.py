"""
Walk every open auto-trader position, compute its current SL/TP from
plan orders, evaluate sanity (TP distance >= 50% of SL distance, i.e.
R:R at least 1:0.5). Rewrite any unsane positions with ATR-based levels
(SL @ -1xATR, TP @ +2xATR).

Sane definition for a Long:
  TP > entry
  abs(TP - entry) >= 0.5 * abs(entry - SL)
  abs(entry - SL) >= 0.5 * ATR  (not too tight)
  abs(entry - SL) <= 8 * ATR    (not absurdly wide)
"""
import json, sys
sys.path.insert(0, "/home/fbauer/trading-journal")

from trading import bitget_trader as t
import chart_context


def _atr(sym):
    try:
        ctx = chart_context.get_chart_context(sym, ["4H"]) or {}
        return float(((ctx.get("4H", {}).get("indicators", {})
                       .get("atr") or {}).get("value") or 0))
    except Exception:
        return 0


positions  = t.get_open_positions()
all_plans  = t.get_pending_plan_orders()
plans_by_sym = {}
for p in all_plans:
    plans_by_sym.setdefault(p["symbol"], []).append(p)


def evaluate(pos, atr):
    """Return (sane, reason)."""
    entry   = pos["entry_price"]
    is_long = pos["direction"] == "Long"
    sl      = pos["preset_sl"]
    tp      = pos["preset_tp"]
    if not sl or not tp:
        return False, "missing SL or TP"
    if is_long and tp <= entry:
        return False, "TP at/below entry"
    if not is_long and tp >= entry:
        return False, "TP at/above entry"
    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    if tp_dist < 0.5 * sl_dist:
        return False, f"R:R 1:{tp_dist/sl_dist:.2f} (need >=1:0.5)"
    if atr > 0:
        if sl_dist < 0.5 * atr:
            return False, f"SL too tight ({sl_dist/atr:.2f}x ATR)"
        if sl_dist > 8 * atr:
            return False, f"SL too wide ({sl_dist/atr:.2f}x ATR)"
    return True, "ok"


print("=== Audit ===")
unsane = []
for pos in positions:
    sym = pos["symbol"]
    atr = _atr(sym)
    ok, reason = evaluate(pos, atr)
    print(f"  {sym:12s} entry={pos['entry_price']} SL={pos['preset_sl']} TP={pos['preset_tp']}  ATR={atr:.6g}  →  {'✓' if ok else '✗ ' + reason}")
    if not ok and atr > 0:
        unsane.append((pos, atr))

if not unsane:
    print("\nNothing to fix.")
    sys.exit(0)

print(f"\n=== Rewriting {len(unsane)} positions ===")
for pos, atr in unsane:
    sym     = pos["symbol"]
    side    = pos["direction"].lower()
    entry   = pos["entry_price"]
    size    = pos["size_contracts"]
    spec    = t.get_contract_spec(sym)
    pp      = spec["price_place"]

    sign = 1 if side == "long" else -1
    new_sl = round(entry - sign * atr * 1.0, pp)
    new_tp = round(entry + sign * atr * 2.0, pp)

    print(f"\n{sym} {side}: new SL={new_sl} ({atr/entry*100:.2f}%) TP={new_tp} (+{2*atr/entry*100:.2f}%)")

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
            print(f"  cancelled {plan['plan_type']} @ {plan['trigger_price']:.6g}")
        except Exception as e:
            print(f"  cancel FAILED for {plan['plan_type']} @ {plan['trigger_price']:.6g}: {e}")

    sl_ok = t._try_place_tpsl(sym, side, new_sl, size, plan_type="loss_plan")
    tp_ok = t._try_place_tpsl(sym, side, new_tp, size, plan_type="profit_plan")
    print(f"  attached SL: {sl_ok}  TP: {tp_ok}")

print("\n=== Final state ===")
for p in t.get_open_positions():
    print(f"  {p['symbol']:12s} entry={p['entry_price']} SL={p['preset_sl']} TP={p['preset_tp']}")
