"""
Cancel the duplicate SYS-created plan orders on RKLBUSDT from the
original place-order presets. Keep ONLY the sane API-attached
ones (SL $129.54 / TP $144.21).
"""
import json, sys
sys.path.insert(0, "/home/fbauer/trading-journal")
from trading import bitget_trader as t

# IDs to cancel — from list_pending_plans.py output
OLD_ORDERS = [
    ("1441835861428674561", "loss_plan",   115.49),
    ("1441835861424480256", "profit_plan", 135.02),
]

for order_id, plan_type, price in OLD_ORDERS:
    body = {
        "symbol":      "RKLBUSDT",
        "productType": "USDT-FUTURES",
        "marginCoin":  "USDT",
        "orderIdList": [{"orderId": order_id, "clientOid": ""}],
        "planType":    plan_type,
    }
    try:
        d = t._request("POST", "/api/v2/mix/order/cancel-plan-order", body=body)
        print(f"cancelled {plan_type} @ ${price} (id {order_id}): {json.dumps(d)[:200]}")
    except Exception as e:
        print(f"cancel failed {plan_type} @ ${price} (id {order_id}): {e}")

print()
print("=== Remaining plan orders ===")
d = t._request("GET", "/api/v2/mix/order/orders-plan-pending", params={
    "productType": "USDT-FUTURES", "planType": "profit_loss",
})
for o in (d.get("entrustedList") or []):
    print(f"  {o['planType']:12s} {o['symbol']} trigger={o['triggerPrice']}  source={o.get('enterPointSource')}")
