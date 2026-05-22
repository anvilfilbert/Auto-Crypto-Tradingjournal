"""List all pending plan / TPSL orders to verify protection is actually live."""
import json, sys
sys.path.insert(0, "/home/fbauer/trading-journal")
from trading import bitget_trader as t

print("=== orders-plan-pending ===")
try:
    d = t._request("GET", "/api/v2/mix/order/orders-plan-pending", params={
        "productType": "USDT-FUTURES",
        "planType":    "normal_plan",
    })
    print(json.dumps(d, indent=2))
except Exception as e:
    print(f"normal_plan: {e}")

print()
print("=== tpsl-orders pending ===")
try:
    d = t._request("GET", "/api/v2/mix/order/orders-plan-pending", params={
        "productType": "USDT-FUTURES",
        "planType":    "profit_loss",
    })
    print(json.dumps(d, indent=2))
except Exception as e:
    print(f"profit_loss: {e}")
