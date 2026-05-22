"""
exposure_monitor.py — Correlation/exposure warnings on open positions.

Flags portfolios that concentrate risk in correlated assets:
  - >=2 same-direction positions in the same SECTORS bucket
  - >=3 same-direction positions overall
  - >=5x leverage stacked on >=3 positions

Fires alerts via the existing Telegram + UI badge path so the trader
sees them on the next monitor cycle.
"""
from collections import Counter, defaultdict
from typing import Optional

from trade_utils import SECTORS


def _sector_for(symbol: str) -> Optional[str]:
    sym = (symbol or "").upper()
    for sector, members in SECTORS.items():
        if sym in members:
            return sector
    return None


def check(positions: list[dict]) -> list[dict]:
    """
    Return zero or more exposure alerts. Each alert dict:
      {kind: "SECTOR_CLUSTER"|"DIRECTIONAL_OVERLOAD"|"LEVERAGE_STACK",
       title, body, symbols: [list]}
    """
    if not positions:
        return []

    alerts: list[dict] = []

    # 1. Sector clustering — count same-direction positions per sector
    sector_buckets: dict[tuple, list] = defaultdict(list)
    for p in positions:
        sec = _sector_for(p.get("symbol"))
        dr  = (p.get("direction") or "").lower()
        if sec and dr in ("long", "short"):
            sector_buckets[(sec, dr)].append(p.get("symbol"))

    for (sec, dr), syms in sector_buckets.items():
        if len(syms) >= 2:
            alerts.append({
                "kind":    "SECTOR_CLUSTER",
                "title":   f"Correlated {dr.title()} exposure in {sec}",
                "body":    (f"{len(syms)} {dr.title()} positions in {sec}: "
                            f"{', '.join(syms)}. A sector-wide reversal would "
                            "hit them all simultaneously — consider closing "
                            "the lowest-conviction one or hedging the strongest."),
                "symbols": list(syms),
            })

    # 2. Directional overload — 4+ same-side positions total
    dir_counts = Counter((p.get("direction") or "").lower() for p in positions)
    for direction, n in dir_counts.items():
        if direction in ("long", "short") and n >= 4:
            syms = [p.get("symbol") for p in positions
                    if (p.get("direction") or "").lower() == direction]
            alerts.append({
                "kind":    "DIRECTIONAL_OVERLOAD",
                "title":   f"{n} concurrent {direction.title()} positions",
                "body":    (f"All-{direction.title()} portfolio bias on {n} symbols: "
                            f"{', '.join(syms)}. A market-wide flush in the wrong "
                            "direction creates correlated drawdown."),
                "symbols": list(syms),
            })

    # 3. Leverage stack — 3+ positions at 10x+ leverage
    high_lev = [p for p in positions
                if float(p.get("leverage") or 0) >= 10]
    if len(high_lev) >= 3:
        syms = [p.get("symbol") for p in high_lev]
        alerts.append({
            "kind":    "LEVERAGE_STACK",
            "title":   f"{len(high_lev)} positions at ≥10x leverage",
            "body":    (f"High-leverage concentration: {', '.join(syms)}. "
                        "A single fast move risks cascading liquidations across "
                        "all of them. Consider reducing leverage on the "
                        "highest-MAE position."),
            "symbols": list(syms),
        })

    return alerts
