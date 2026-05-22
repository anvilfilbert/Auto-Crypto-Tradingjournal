"""
End-to-end test of the Futures-AI orchestrator + Bitget Elite write API.

Steps:
  1. Pull the last completed scan state from ai_scanner._state
  2. Manually invoke orchestrator.on_scan_completed() — proves the loop
     works without waiting 30 min for the next periodic scan
  3. Test Bitget Elite write API endpoints — try several to find one
     that's permitted for this Elite/copy-trading account type:
        a) GET  /api/v2/mix/account/accounts        (list balances)
        b) GET  /api/v2/mix/position/all-position   (open positions)
        c) GET  /api/v2/mix/order/orders-pending    (live orders)
  4. Print what's working + what's not. Does NOT place orders.
"""
import json
import sys
sys.path.insert(0, "/home/fbauer/trading-journal")

from database import db_conn


def _section(title):
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")


# ── 1. Pull scan state + invoke orchestrator ────────────────────────────────

_section("1. Manually invoke orchestrator on last scan state")
try:
    import ai_scanner
    scan_state = ai_scanner.get_state()
    setups = scan_state.get("setups") or []
    print(f"  scan status:   {scan_state.get('status')}")
    print(f"  scan duration: {scan_state.get('duration_sec')}s")
    print(f"  setups:        {len(setups)}")
    if setups:
        from collections import Counter
        sc = Counter(int(s.get("setup_score") or 0) for s in setups)
        print(f"  score dist:    {dict(sorted(sc.items()))}")

    if setups and scan_state.get("status") == "completed":
        from trading import orchestrator as orch
        result = orch.on_scan_completed(scan_state)
        print(f"  orchestrator result: {result}")
    else:
        print("  no completed scan available — skip orchestrator invocation")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"  FAILED: {e}")


# ── 2. Test Bitget Elite write API endpoints ────────────────────────────────

_section("2. Bitget Elite trader API connectivity probes")
try:
    from trading import bitget_trader as t

    probes = [
        ("test_connection (single-symbol account)", t.test_connection),
        ("get_balance (list all balances)",          t.get_balance),
        ("get_open_positions",                       t.get_open_positions),
    ]
    for name, fn in probes:
        print(f"\n  {name}:")
        try:
            result = fn()
            if isinstance(result, list):
                print(f"    ✓ ok — {len(result)} rows")
                if result:
                    print(f"      first row: {json.dumps(result[0], default=str)[:200]}")
            else:
                print(f"    ✓ {json.dumps(result, default=str)[:200]}")
        except Exception as e:
            print(f"    ✗ {type(e).__name__}: {str(e)[:200]}")
except Exception as e:
    print(f"  module import failed: {e}")


# ── 3. Show recent decision log ──────────────────────────────────────────────

_section("3. Recent decision-log entries")
with db_conn() as conn:
    rows = conn.execute(
        "SELECT ts, event, symbol, direction, score, "
        "substr(COALESCE(payload_json,''),1,120) AS payload "
        "FROM futures_ai_log ORDER BY id DESC LIMIT 15"
    ).fetchall()
    for r in rows:
        print(f"  {r[0][11:19]}  {r[1]:24s} {r[2] or '':12s} "
              f"{r[3] or '':5s} sc={r[4] or '':<3}  {r[5]}")
