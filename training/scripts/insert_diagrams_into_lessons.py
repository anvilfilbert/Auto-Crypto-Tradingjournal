"""Insert image blocks into lesson JSONs at the right positions.

Each entry maps a lesson slug to a list of (anchor, image) pairs where:
  anchor = a substring of the heading content to find (insert after that block)
  image  = {src, alt, caption}

Run: venv/bin/python3 training/scripts/insert_diagrams_into_lessons.py
"""
import json
from pathlib import Path

LESSONS = Path(__file__).parent.parent / "content" / "lessons"

# Format: slug -> list of (insert_after_heading_substring, image_dict)
# image src is relative to static/ (so 'charts/01-foo.png' → /static/charts/01-foo.png)
WIRING = {
    # ── TIER 1 ──
    "06-risk-per-trade": [
        ("The drawdown asymmetry", {
            "src": "charts/10-drawdown-recovery.png",
            "alt": "Bar chart showing gain percentage needed to recover from drawdowns from 5% up to 90%",
            "caption": "Recovery math: small losses compound benignly; big losses compound viciously."
        }),
    ],
    "08-leverage-iso-vs-cross": [
        ("Liquidation prices", {
            "src": "charts/11-liquidation-distance.png",
            "alt": "Horizontal bar chart showing liquidation distances at 1x through 100x leverage",
            "caption": "Each leverage step roughly halves your liquidation distance. 100x = liquidated on any normal 1% move."
        }),
    ],
    # ── TIER 2 ──
    "12-candle-anatomy": [
        ("Body and wicks", {
            "src": "charts/01-candle-anatomy.png",
            "alt": "Labeled bullish and bearish candles showing body, upper wick, lower wick",
            "caption": "Body shows where price SETTLED. Wicks show rejected attempts. Long wick = strong rejection."
        }),
    ],
    "13-bullish-reversal-candles": [
        ("Pattern #1 — Hammer", {
            "src": "charts/02-bullish-reversal-patterns.png",
            "alt": "Four bullish reversal candle patterns side by side",
            "caption": "All four bullish reversal patterns. Notice each requires a small body + rejection of the prior direction."
        }),
    ],
    "14-bearish-reversal-candles": [
        ("Pattern #1 — Shooting Star", {
            "src": "charts/03-bearish-reversal-patterns.png",
            "alt": "Four bearish reversal candle patterns side by side",
            "caption": "Mirror of bullish patterns. Shooting Star, Bearish Engulfing, Evening Star, Dark Cloud."
        }),
    ],
    "15-indecision-candles": [
        ("Pattern #1 — Doji", {
            "src": "charts/04-indecision-candles.png",
            "alt": "Four doji and spinning top variants",
            "caption": "Doji variants. Dragonfly at support and Gravestone at resistance behave like Hammer/Shooting Star."
        }),
    ],
    "16-gaps-fvg": [
        ("How to identify an FVG", {
            "src": "charts/08-fvg-example.png",
            "alt": "Three-candle pattern showing a Fair Value Gap imbalance zone",
            "caption": "Bullish FVG: zone between candle 1's high and candle 3's low is the imbalance. Price often returns to fill."
        }),
    ],
    "17-support-resistance": [
        ("How to identify S/R levels", {
            "src": "charts/05-support-resistance.png",
            "alt": "Price chart showing horizontal support and resistance zones with multiple touches",
            "caption": "Price bouncing between support (green) and resistance (red) zones over time. Each touch validates the level."
        }),
    ],
    "18-trendlines": [
        ("The 3-touch rule", {
            "src": "charts/07-trendlines.png",
            "alt": "Uptrend and downtrend trendlines with marked touch points",
            "caption": "Uptrend support trendline (left) connects higher lows. Downtrend resistance (right) connects lower highs. Touches marked in green/red."
        }),
    ],
    "19-market-structure": [
        ("The core concept — swings define structure", {
            "src": "charts/06-market-structure.png",
            "alt": "Side-by-side comparison of uptrend HH/HL pattern and downtrend LH/LL pattern",
            "caption": "Uptrend = Higher Highs + Higher Lows. Downtrend = Lower Highs + Lower Lows. Three-second trend identification."
        }),
        ("Break of Structure (BoS) — trend continuation", {
            "src": "charts/09-bos-choch.png",
            "alt": "BoS (continuation) vs CHoCH (reversal) diagrams side by side",
            "caption": "BoS: price breaks beyond prior swing = trend continues. CHoCH: price breaks the OTHER side = trend may be reversing."
        }),
    ],
    # ── TIER 3 ──
    "22-moving-averages": [
        ("Golden Cross / Death Cross", {
            "src": "charts/20-ma-golden-cross.png",
            "alt": "Price chart with 50 MA crossing above 200 MA — golden cross marked",
            "caption": "Golden Cross: fast MA crosses above slow MA. Confirmation of regime change, not entry trigger (lagging signal)."
        }),
    ],
    "23-rsi-mastery": [
        ("The regime concept", {
            "src": "charts/21-rsi-regimes.png",
            "alt": "Two RSI charts showing bullish regime (40-80 range) and bearish regime (20-60 range)",
            "caption": "Same RSI values mean different things in different regimes. RSI 70+ in bullish trend = normal, NOT a sell signal."
        }),
        ("Divergences — the second most powerful RSI signal", {
            "src": "charts/22-rsi-divergence.png",
            "alt": "Price making higher highs while RSI makes lower highs — bearish divergence",
            "caption": "Bearish RSI divergence: price up but momentum down. Warning signal — combine with key level + reversal candle to trade."
        }),
    ],
    "25-bollinger-bands": [
        ("The squeeze — the most useful BB signal", {
            "src": "charts/23-bollinger-squeeze.png",
            "alt": "Bollinger bands showing low-volatility squeeze followed by explosive expansion",
            "caption": "Squeeze (narrow bands) precedes expansion. Direction not predicted by squeeze itself — wait for breakout to confirm."
        }),
    ],
    "28-fibonacci": [
        ("The key Fibonacci ratios", {
            "src": "charts/24-fibonacci-retracement.png",
            "alt": "Price chart with Fibonacci retracement levels 0.236 through 0.786 marked",
            "caption": "Fib retracement levels on a swing from 55 to 68. 0.5 and 0.618 are the most-watched institutional pullback zones."
        }),
    ],
    # ── TIER 4 ──
    "31-order-flow-cvd": [
        ("CVD divergence — the most powerful order flow signal", {
            "src": "charts/30-cvd-divergence.png",
            "alt": "Top: price rising. Bottom: CVD falling — bearish divergence",
            "caption": "Hollow trend: price rises but order flow (CVD) falls. Passive bids absorbing aggressive selling — often precedes reversal."
        }),
    ],
    "33-wyckoff-phases": [
        ("The four phases", {
            "src": "charts/31-wyckoff-cycle.png",
            "alt": "Full Wyckoff cycle showing accumulation, markup, distribution, markdown phases over time",
            "caption": "The Wyckoff cycle. Each phase has its own characteristics — recognize which one you're in to pick the right trade type."
        }),
    ],
    "34-wyckoff-spring-upthrust": [
        ("Spring — the accumulation finale", {
            "src": "charts/32-spring-upthrust.png",
            "alt": "Spring pattern at range support (left) and Upthrust at range resistance (right)",
            "caption": "Spring: wick below support, close back above = accumulation completes. Upthrust: mirror at distribution. Tight SL + wide TP = exceptional R:R."
        }),
    ],
    "35-order-blocks-fvg-pools": [
        ("Order Block — where institutions entered", {
            "src": "charts/33-order-block.png",
            "alt": "Price chart showing bullish order block, impulsive move up, then pullback returning to OB",
            "caption": "Bullish OB: last red candle before impulsive up-move. Price often returns to retest the zone — high-quality long entry."
        }),
    ],
    # ── TIER 5 ──
    "40-macro-stack": [
        ("DXY in detail", {
            "src": "charts/40-macro-correlation.png",
            "alt": "DXY rising (top) and BTC falling (bottom) — inverse correlation example",
            "caption": "DXY (USD) and BTC trade inversely most of the time. Rising DXY = headwind for crypto via both pricing and risk-off dynamics."
        }),
    ],
    "42-fear-greed-contrarian": [
        ("The Fear & Greed scale", {
            "src": "charts/41-fear-greed-zones.png",
            "alt": "Fear and Greed Index scale with color-coded zones — Extreme Fear at bottom (red, BUY ZONE), Extreme Greed at top (green, SELL ZONE)",
            "caption": "Under 25 = contrarian buy zone. Over 75 = contrarian sell zone. Middle 25-75 = neutral, trade technicals normally."
        }),
    ],
    # ── TIER 6 ──
    "46-trade-apgar": [
        ("The 5 dimensions", {
            "src": "charts/50-apgar-scoreboard.png",
            "alt": "Trade Apgar 5-dimension scorecard with scoring scale 0-2 per dimension",
            "caption": "Five dimensions, scored 0-2 each, max 10. Pass: total ≥7 AND no zeros in any dimension."
        }),
    ],
    "47-pre-session-readiness": [
        ("The four dimensions", {
            "src": "charts/51-readiness-traffic-light.png",
            "alt": "Pre-session readiness traffic-light grid: Sleep / Mood / Recent P&L / Preparation, each with green/yellow/red criteria",
            "caption": "Four-dimension check. ALL GREEN = full trading. Mixed = reduced mode. ANY RED = no trading today."
        }),
    ],
}


def insert_block(blocks, anchor, image_block):
    """Insert image block right AFTER the first block whose content/title contains anchor."""
    new_block = {"type": "image", **image_block}
    for i, b in enumerate(blocks):
        # Match against content (for text/heading/callout body) OR title (for callouts)
        if anchor in (b.get("content") or "") or anchor in (b.get("title") or ""):
            return blocks[:i+1] + [new_block] + blocks[i+1:]
    print(f"    ⚠ anchor not found: {anchor!r}")
    return blocks


def main():
    total_wired = 0
    for slug, entries in WIRING.items():
        path = LESSONS / f"{slug}.json"
        if not path.exists():
            print(f"  ✗ {slug}: lesson file not found")
            continue
        lesson = json.loads(path.read_text())
        original_count = len(lesson["blocks"])
        # Apply each wiring entry (process in order — anchors are independent)
        for anchor, image in entries:
            # Skip if image already wired (idempotent)
            if any(b.get("type") == "image" and b.get("src") == image["src"]
                   for b in lesson["blocks"]):
                continue
            lesson["blocks"] = insert_block(lesson["blocks"], anchor, image)
        added = len(lesson["blocks"]) - original_count
        if added > 0:
            path.write_text(json.dumps(lesson, indent=2, ensure_ascii=False))
            print(f"  ✓ {slug}: +{added} image block(s)")
            total_wired += added
        else:
            print(f"  · {slug}: no changes (already wired or anchor missing)")
    print(f"\nTotal image blocks added: {total_wired}")


if __name__ == "__main__":
    main()
