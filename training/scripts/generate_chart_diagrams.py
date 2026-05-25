"""Generate candle-pattern + structure diagrams for the training module.

Run from repo root:  venv/bin/python3 training/scripts/generate_chart_diagrams.py

Outputs PNG files into training/static/charts/ — dark themed to match the
training UI. Each diagram is self-contained and small (under 100KB typical).
"""
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyArrowPatch
from matplotlib.lines import Line2D

OUT_DIR = Path(__file__).parent.parent / "static" / "charts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Dark theme colors matching training.css
BG       = "#131922"
TEXT     = "#e7eaf3"
MUTED    = "#8a93a6"
GRID     = "#2a3140"
GREEN    = "#26d96b"
RED      = "#ef5350"
ACCENT   = "#6c63ff"
ACCENT2  = "#4fc3f7"
YELLOW   = "#ffb300"


def setup_axes(ax, xlim, ylim, hide_axes=False):
    """Apply dark theme + grid to the axes."""
    ax.set_facecolor(BG)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    if hide_axes:
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
    else:
        ax.tick_params(colors=MUTED, labelsize=8)
        for s in ax.spines.values():
            s.set_color(GRID)
        ax.grid(True, color=GRID, alpha=0.3, linestyle="--", linewidth=0.5)


def draw_candle(ax, x, o, h, l, c, width=0.6, body_alpha=1.0):
    """Draw a single candlestick at x. OHLC = open/high/low/close."""
    color = GREEN if c >= o else RED
    # Wick (vertical line through high-low)
    ax.add_line(Line2D([x, x], [l, h], color=color, linewidth=1.5))
    # Body (rectangle from open to close)
    body_bottom = min(o, c)
    body_height = abs(c - o)
    if body_height < 0.05:  # doji-ish
        ax.add_line(Line2D([x - width / 2, x + width / 2], [o, o],
                           color=color, linewidth=2))
    else:
        ax.add_patch(Rectangle((x - width / 2, body_bottom), width, body_height,
                               facecolor=color, edgecolor=color, alpha=body_alpha))


def save(fig, name):
    """Save with consistent settings."""
    path = OUT_DIR / f"{name}.png"
    fig.savefig(path, facecolor=BG, dpi=150, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    print(f"  ✓ {path.name}  ({path.stat().st_size // 1024} KB)")


# ─── DIAGRAM 1: Candle Anatomy ─────────────────────────────────────────────
def candle_anatomy():
    fig, ax = plt.subplots(figsize=(9, 5))
    setup_axes(ax, (-0.5, 8), (0, 10), hide_axes=True)

    # Bullish candle
    draw_candle(ax, 1.5, o=3, h=8, l=2, c=7, width=0.8)
    ax.annotate("Upper wick\n(rejected high)", xy=(1.5, 7.5), xytext=(2.7, 8.7),
                color=TEXT, fontsize=10, ha="left",
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    ax.annotate("Body\n(close > open)", xy=(1.5, 5), xytext=(2.7, 5),
                color=TEXT, fontsize=10, ha="left",
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    ax.annotate("Lower wick\n(rejected low)", xy=(1.5, 2.5), xytext=(2.7, 1.5),
                color=TEXT, fontsize=10, ha="left",
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    ax.text(1.5, 0.6, "BULLISH", color=GREEN, fontsize=11, weight="bold", ha="center")

    # Bearish candle
    draw_candle(ax, 6.5, o=7, h=8, l=2, c=3, width=0.8)
    ax.text(6.5, 0.6, "BEARISH", color=RED, fontsize=11, weight="bold", ha="center")

    # Labels for body parts (bearish)
    ax.annotate("Body\n(close < open)", xy=(6.5, 5), xytext=(5.3, 5),
                color=TEXT, fontsize=10, ha="right",
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))

    ax.set_title("Candle Anatomy — Body & Wicks",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    save(fig, "01-candle-anatomy")


# ─── DIAGRAM 2: Bullish Reversal Patterns ──────────────────────────────────
def bullish_reversal_patterns():
    fig, axes = plt.subplots(1, 4, figsize=(14, 5))
    fig.patch.set_facecolor(BG)

    # Hammer: small body top, long lower wick
    ax = axes[0]
    setup_axes(ax, (-1, 3), (0, 10), hide_axes=True)
    draw_candle(ax, 1, o=7.5, h=8, l=2, c=7.8, width=0.8)
    ax.set_title("Hammer", color=GREEN, fontsize=12, weight="bold", pad=10)
    ax.text(1, 0.5, "Body near top\n+ long lower wick\n(rejected sellers)",
            color=MUTED, fontsize=9, ha="center")

    # Bullish Engulfing: red, then green that engulfs body
    ax = axes[1]
    setup_axes(ax, (-1, 4), (0, 10), hide_axes=True)
    draw_candle(ax, 0.5, o=7, h=7.5, l=4, c=4.5, width=0.8)  # red
    draw_candle(ax, 2.0, o=4, h=8, l=3.5, c=7.5, width=0.8)  # green engulfs
    ax.set_title("Bullish Engulfing", color=GREEN, fontsize=12, weight="bold", pad=10)
    ax.text(1.25, 0.5, "Green body\nengulfs prior red",
            color=MUTED, fontsize=9, ha="center")

    # Morning Star: red, small body (star), green
    ax = axes[2]
    setup_axes(ax, (-1, 5), (0, 10), hide_axes=True)
    draw_candle(ax, 0.5, o=7, h=7.5, l=3, c=3.5, width=0.7)  # red
    draw_candle(ax, 2.0, o=3, h=3.3, l=2.5, c=2.9, width=0.6)  # small star
    draw_candle(ax, 3.5, o=3, h=7.5, l=2.8, c=7, width=0.7)  # strong green
    ax.set_title("Morning Star", color=GREEN, fontsize=12, weight="bold", pad=10)
    ax.text(2, 0.5, "Red → indecision → green\n(3-candle reversal)",
            color=MUTED, fontsize=9, ha="center")

    # Piercing Line: red, then green opening below close + closing above midpoint
    ax = axes[3]
    setup_axes(ax, (-1, 4), (0, 10), hide_axes=True)
    draw_candle(ax, 0.5, o=7.5, h=8, l=4, c=4.5, width=0.8)  # red
    draw_candle(ax, 2.0, o=3.5, h=7, l=3.2, c=6.5, width=0.8)  # green pierces past midpoint
    ax.set_title("Piercing Line", color=GREEN, fontsize=12, weight="bold", pad=10)
    ax.text(1.25, 0.5, "Green opens below,\ncloses past mid",
            color=MUTED, fontsize=9, ha="center")

    fig.suptitle("Bullish Reversal Candle Patterns",
                 color=TEXT, fontsize=14, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.88)
    save(fig, "02-bullish-reversal-patterns")


# ─── DIAGRAM 3: Bearish Reversal Patterns ──────────────────────────────────
def bearish_reversal_patterns():
    fig, axes = plt.subplots(1, 4, figsize=(14, 5))
    fig.patch.set_facecolor(BG)

    # Shooting Star: small body bottom, long upper wick
    ax = axes[0]
    setup_axes(ax, (-1, 3), (0, 10), hide_axes=True)
    draw_candle(ax, 1, o=2.5, h=8, l=2, c=2.2, width=0.8)
    ax.set_title("Shooting Star", color=RED, fontsize=12, weight="bold", pad=10)
    ax.text(1, 0.5, "Body near bottom\n+ long upper wick\n(rejected buyers)",
            color=MUTED, fontsize=9, ha="center")

    # Bearish Engulfing
    ax = axes[1]
    setup_axes(ax, (-1, 4), (0, 10), hide_axes=True)
    draw_candle(ax, 0.5, o=3.5, h=6, l=3, c=5.5, width=0.8)  # green
    draw_candle(ax, 2.0, o=6, h=6.5, l=2, c=2.5, width=0.8)  # red engulfs
    ax.set_title("Bearish Engulfing", color=RED, fontsize=12, weight="bold", pad=10)
    ax.text(1.25, 0.5, "Red body\nengulfs prior green",
            color=MUTED, fontsize=9, ha="center")

    # Evening Star
    ax = axes[2]
    setup_axes(ax, (-1, 5), (0, 10), hide_axes=True)
    draw_candle(ax, 0.5, o=3, h=6.5, l=2.5, c=6, width=0.7)  # green
    draw_candle(ax, 2.0, o=7, h=7.3, l=6.7, c=7.1, width=0.6)  # small star top
    draw_candle(ax, 3.5, o=7, h=7.2, l=2.5, c=3, width=0.7)  # red drops
    ax.set_title("Evening Star", color=RED, fontsize=12, weight="bold", pad=10)
    ax.text(2, 0.5, "Green → indecision → red\n(3-candle reversal)",
            color=MUTED, fontsize=9, ha="center")

    # Dark Cloud Cover
    ax = axes[3]
    setup_axes(ax, (-1, 4), (0, 10), hide_axes=True)
    draw_candle(ax, 0.5, o=2.5, h=6, l=2, c=5.5, width=0.8)  # green
    draw_candle(ax, 2.0, o=6.5, h=7, l=3, c=3.5, width=0.8)  # red opens above, closes past mid
    ax.set_title("Dark Cloud Cover", color=RED, fontsize=12, weight="bold", pad=10)
    ax.text(1.25, 0.5, "Red opens above,\ncloses past mid",
            color=MUTED, fontsize=9, ha="center")

    fig.suptitle("Bearish Reversal Candle Patterns",
                 color=TEXT, fontsize=14, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.88)
    save(fig, "03-bearish-reversal-patterns")


# ─── DIAGRAM 4: Indecision Candles ─────────────────────────────────────────
def indecision_candles():
    fig, axes = plt.subplots(1, 4, figsize=(14, 5))
    fig.patch.set_facecolor(BG)

    # Standard Doji
    ax = axes[0]
    setup_axes(ax, (-1, 3), (0, 10), hide_axes=True)
    draw_candle(ax, 1, o=5, h=8, l=2, c=5, width=0.8)
    ax.set_title("Standard Doji", color=YELLOW, fontsize=12, weight="bold", pad=10)
    ax.text(1, 0.5, "Open ≈ Close\nEqual wicks\n(pure indecision)",
            color=MUTED, fontsize=9, ha="center")

    # Dragonfly Doji
    ax = axes[1]
    setup_axes(ax, (-1, 3), (0, 10), hide_axes=True)
    draw_candle(ax, 1, o=8, h=8.2, l=2, c=8, width=0.8)
    ax.set_title("Dragonfly Doji", color=GREEN, fontsize=12, weight="bold", pad=10)
    ax.text(1, 0.5, "Body at top\n+ long lower wick\n(bullish at support)",
            color=MUTED, fontsize=9, ha="center")

    # Gravestone Doji
    ax = axes[2]
    setup_axes(ax, (-1, 3), (0, 10), hide_axes=True)
    draw_candle(ax, 1, o=2, h=8, l=1.8, c=2, width=0.8)
    ax.set_title("Gravestone Doji", color=RED, fontsize=12, weight="bold", pad=10)
    ax.text(1, 0.5, "Body at bottom\n+ long upper wick\n(bearish at resistance)",
            color=MUTED, fontsize=9, ha="center")

    # Long-legged Doji
    ax = axes[3]
    setup_axes(ax, (-1, 3), (0, 10), hide_axes=True)
    draw_candle(ax, 1, o=5, h=9, l=1, c=5, width=0.8)
    ax.set_title("Long-legged Doji", color=YELLOW, fontsize=12, weight="bold", pad=10)
    ax.text(1, 0.5, "Very long wicks\n(wide indecision)",
            color=MUTED, fontsize=9, ha="center")

    fig.suptitle("Indecision Candle Variants",
                 color=TEXT, fontsize=14, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.88)
    save(fig, "04-indecision-candles")


# ─── DIAGRAM 5: Support & Resistance Zones ─────────────────────────────────
def support_resistance():
    fig, ax = plt.subplots(figsize=(11, 5))
    setup_axes(ax, (0, 20), (50, 70))

    # Synthetic price action that bounces between support and resistance
    candles = [
        # (x, o, h, l, c)
        (1, 60, 62, 58, 61),
        (2, 61, 64, 60, 63),
        (3, 63, 66, 62, 65.5),
        (4, 65.5, 67, 65, 66),  # near resistance
        (5, 66, 66.5, 64, 64.5),  # rejected
        (6, 64.5, 65, 62, 62.5),
        (7, 62.5, 63, 60, 60.5),
        (8, 60.5, 61, 58.5, 58.8),  # near support
        (9, 58.8, 60, 58, 59.5),  # bounced
        (10, 59.5, 61, 59, 60.5),
        (11, 60.5, 63, 60, 62.5),
        (12, 62.5, 65, 62, 64.5),
        (13, 64.5, 67, 64, 66.2),  # near resistance again
        (14, 66.2, 66.5, 64, 64.5),  # rejected again
        (15, 64.5, 64.8, 62, 62.5),
        (16, 62.5, 63, 60, 60.8),
        (17, 60.8, 61.5, 58.5, 59),  # near support again
        (18, 59, 60, 58.5, 59.5),
        (19, 59.5, 62, 59.2, 61.5),
    ]
    for c in candles:
        draw_candle(ax, c[0], c[1], c[2], c[3], c[4], width=0.6)

    # Resistance zone — fill horizontal band
    ax.axhspan(66, 67, color=RED, alpha=0.15)
    ax.text(0.5, 66.5, "RESISTANCE", color=RED, fontsize=10, weight="bold", va="center")

    # Support zone
    ax.axhspan(58, 59.2, color=GREEN, alpha=0.15)
    ax.text(0.5, 58.6, "SUPPORT", color=GREEN, fontsize=10, weight="bold", va="center")

    ax.set_title("Support & Resistance Zones — Price Bouncing Between Levels",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    ax.set_xlabel("Time →", color=MUTED, fontsize=9)
    ax.set_ylabel("Price", color=MUTED, fontsize=9)
    save(fig, "05-support-resistance")


# ─── DIAGRAM 6: Market Structure (HH/HL vs LH/LL) ──────────────────────────
def market_structure():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.patch.set_facecolor(BG)

    # UPTREND — HH/HL
    ax = axes[0]
    setup_axes(ax, (0, 12), (50, 80))
    # Plot ascending swing points
    swings = [(1, 55), (2.5, 60), (4, 58), (5.5, 65), (7, 63), (8.5, 70), (10, 68), (11.5, 75)]
    xs = [s[0] for s in swings]
    ys = [s[1] for s in swings]
    ax.plot(xs, ys, color=ACCENT2, linewidth=2, marker="o", markersize=6)
    # Label the swings
    for i, (x, y) in enumerate(swings):
        if i % 2 == 0:  # lows
            ax.annotate(f"HL{i//2+1}" if i > 0 else "Low",
                        xy=(x, y), xytext=(x, y - 3),
                        color=GREEN, fontsize=9, ha="center", weight="bold")
        else:  # highs
            ax.annotate(f"HH{(i-1)//2+1}",
                        xy=(x, y), xytext=(x, y + 2),
                        color=GREEN, fontsize=9, ha="center", weight="bold")
    ax.set_title("UPTREND — Higher Highs + Higher Lows",
                 color=GREEN, fontsize=12, weight="bold", pad=10)
    ax.set_xlabel("Time →", color=MUTED, fontsize=9)

    # DOWNTREND — LH/LL
    ax = axes[1]
    setup_axes(ax, (0, 12), (50, 80))
    swings = [(1, 75), (2.5, 70), (4, 72), (5.5, 65), (7, 67), (8.5, 60), (10, 62), (11.5, 55)]
    xs = [s[0] for s in swings]
    ys = [s[1] for s in swings]
    ax.plot(xs, ys, color=ACCENT2, linewidth=2, marker="o", markersize=6)
    for i, (x, y) in enumerate(swings):
        if i % 2 == 0:  # highs
            ax.annotate(f"LH{i//2+1}" if i > 0 else "High",
                        xy=(x, y), xytext=(x, y + 2),
                        color=RED, fontsize=9, ha="center", weight="bold")
        else:
            ax.annotate(f"LL{(i-1)//2+1}",
                        xy=(x, y), xytext=(x, y - 3),
                        color=RED, fontsize=9, ha="center", weight="bold")
    ax.set_title("DOWNTREND — Lower Highs + Lower Lows",
                 color=RED, fontsize=12, weight="bold", pad=10)
    ax.set_xlabel("Time →", color=MUTED, fontsize=9)

    fig.suptitle("Market Structure — Trend Identification by Swing Pattern",
                 color=TEXT, fontsize=14, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.88)
    save(fig, "06-market-structure")


# ─── DIAGRAM 7: Trendlines ─────────────────────────────────────────────────
def trendlines():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.patch.set_facecolor(BG)

    # Uptrend trendline — connect higher lows
    ax = axes[0]
    setup_axes(ax, (0, 14), (50, 75))
    # Higher lows with price action above
    candles_x = list(range(1, 14))
    lows = [56, 58, 56, 60, 58, 62, 60, 65, 63, 67, 65, 70, 67]
    highs = [60, 63, 61, 65, 64, 68, 67, 70, 69, 72, 71, 73, 72]
    closes = [58, 61, 60, 63, 62, 66, 65, 69, 68, 71, 70, 72, 70]
    opens = [57, 60, 58, 61, 60, 64, 64, 67, 67, 70, 69, 71, 68]
    for i, x in enumerate(candles_x):
        draw_candle(ax, x, opens[i], highs[i], lows[i], closes[i], width=0.5)
    # Trendline from first low to most recent — extend slightly past
    ax.plot([1, 13], [56, 71], color=GREEN, linewidth=2, linestyle="--", alpha=0.8)
    # Mark the touches
    touches = [(1, 56), (3, 56), (5, 58), (7, 60), (9, 63), (11, 65)]
    for x, y in touches:
        ax.scatter(x, y, color=GREEN, s=50, zorder=5, edgecolor="white", linewidth=1)
    ax.set_title("Uptrend Trendline (Higher Lows)",
                 color=GREEN, fontsize=12, weight="bold", pad=10)
    ax.set_xlabel("Time →", color=MUTED, fontsize=9)

    # Downtrend trendline — connect lower highs
    ax = axes[1]
    setup_axes(ax, (0, 14), (50, 75))
    highs2 = [73, 70, 72, 67, 69, 64, 66, 61, 63, 58, 60, 55, 57]
    lows2 = [68, 65, 67, 62, 64, 59, 61, 56, 58, 53, 55, 51, 53]
    opens2 = [72, 67, 70, 65, 67, 62, 64, 60, 61, 58, 58, 55, 56]
    closes2 = [69, 70, 65, 67, 62, 64, 60, 61, 58, 58, 55, 56, 54]
    for i, x in enumerate(candles_x):
        draw_candle(ax, x, opens2[i], highs2[i], lows2[i], closes2[i], width=0.5)
    ax.plot([1, 13], [73, 57], color=RED, linewidth=2, linestyle="--", alpha=0.8)
    touches2 = [(1, 73), (3, 72), (5, 69), (7, 66), (9, 63), (11, 60)]
    for x, y in touches2:
        ax.scatter(x, y, color=RED, s=50, zorder=5, edgecolor="white", linewidth=1)
    ax.set_title("Downtrend Trendline (Lower Highs)",
                 color=RED, fontsize=12, weight="bold", pad=10)
    ax.set_xlabel("Time →", color=MUTED, fontsize=9)

    fig.suptitle("Trendlines — Diagonal Support/Resistance via Swing Pivots",
                 color=TEXT, fontsize=14, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.88)
    save(fig, "07-trendlines")


# ─── DIAGRAM 8: Fair Value Gap (FVG) ───────────────────────────────────────
def fvg_example():
    fig, ax = plt.subplots(figsize=(10, 5))
    setup_axes(ax, (-0.5, 6), (45, 70))

    # 3-candle pattern with imbalance between candle 1 high and candle 3 low
    # Candle 1: standard
    draw_candle(ax, 1, o=50, h=53, l=49, c=52, width=0.6)
    # Candle 2: strong bullish impulse
    draw_candle(ax, 2.5, o=52, h=63, l=51.5, c=62, width=0.6)
    # Candle 3: opens higher than candle 1's high
    draw_candle(ax, 4, o=62, h=65, l=60, c=64, width=0.6)

    # Highlight the FVG zone between candle 1 high (53) and candle 3 low (60)
    ax.axhspan(53, 60, xmin=0.05, xmax=0.95, color=ACCENT, alpha=0.20)
    ax.text(5.3, 56.5, "FAIR\nVALUE\nGAP", color=ACCENT, fontsize=11, weight="bold",
            ha="center", va="center")

    # Annotation arrows
    ax.annotate("Candle 1 high", xy=(1, 53), xytext=(0.2, 47),
                color=MUTED, fontsize=9, ha="left",
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    ax.annotate("Candle 3 low", xy=(4, 60), xytext=(4.5, 47),
                color=MUTED, fontsize=9, ha="left",
                arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))

    ax.set_title("Fair Value Gap (FVG) — 3-Candle Imbalance Zone",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    ax.set_xlabel("Time →", color=MUTED, fontsize=9)
    ax.set_ylabel("Price", color=MUTED, fontsize=9)
    save(fig, "08-fvg-example")


# ─── DIAGRAM 9: BoS vs CHoCH ───────────────────────────────────────────────
def bos_choch():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.patch.set_facecolor(BG)

    # BoS — uptrend breaking previous swing high (continuation)
    ax = axes[0]
    setup_axes(ax, (0, 12), (50, 80))
    swings = [(1, 55), (2.5, 60), (4, 58), (5.5, 65), (7, 63), (8.5, 70), (10, 68), (11.5, 75)]
    xs = [s[0] for s in swings]
    ys = [s[1] for s in swings]
    ax.plot(xs, ys, color=ACCENT2, linewidth=2, marker="o", markersize=6)
    # Mark the most recent HH break
    ax.axhline(y=70, color=GREEN, linestyle=":", linewidth=1.5, alpha=0.6)
    ax.annotate("BoS — Break of Structure\n(price breaks above prior HH)",
                xy=(11.5, 75), xytext=(7, 78),
                color=GREEN, fontsize=10, weight="bold", ha="center",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.5))
    ax.set_title("BoS — Trend Continuation (Bullish)",
                 color=GREEN, fontsize=12, weight="bold", pad=10)

    # CHoCH — uptrend then breaking previous swing low (reversal)
    ax = axes[1]
    setup_axes(ax, (0, 12), (50, 80))
    # Uptrend then reversal
    swings = [(1, 55), (2.5, 62), (4, 58), (5.5, 68), (7, 64), (8.5, 67), (10, 60), (11.5, 56)]
    xs = [s[0] for s in swings]
    ys = [s[1] for s in swings]
    ax.plot(xs, ys, color=ACCENT2, linewidth=2, marker="o", markersize=6)
    # Mark the broken HL (was at 58, price now breaks below)
    ax.axhline(y=58, color=RED, linestyle=":", linewidth=1.5, alpha=0.6)
    ax.annotate("CHoCH — Change of Character\n(price breaks below prior HL\n→ uptrend invalidated)",
                xy=(11.5, 56), xytext=(7.5, 78),
                color=RED, fontsize=10, weight="bold", ha="center",
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.5))
    ax.set_title("CHoCH — Trend Reversal Signal",
                 color=RED, fontsize=12, weight="bold", pad=10)

    fig.suptitle("BoS vs CHoCH — Continuation vs Reversal Signals",
                 color=TEXT, fontsize=14, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.85)
    save(fig, "09-bos-choch")


# ═══════════════════════════════════════════════════════════════════════════
#   TIER 1 — Foundations diagrams
# ═══════════════════════════════════════════════════════════════════════════

def drawdown_recovery():
    """T1#6 — Drawdown asymmetry: gain needed to recover from various losses."""
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.set_facecolor(BG)
    losses = [5, 10, 20, 30, 40, 50, 60, 70, 80, 90]
    recoveries = [round(100 * l / (100 - l), 1) for l in losses]
    colors = [GREEN if r < 50 else (YELLOW if r < 100 else RED) for r in recoveries]
    bars = ax.bar(range(len(losses)), recoveries, color=colors, edgecolor=BG, linewidth=2)
    for i, (bar, r) in enumerate(zip(bars, recoveries)):
        ax.text(bar.get_x() + bar.get_width() / 2, r + 15,
                f"{r}%", color=TEXT, fontsize=9, weight="bold", ha="center")
    ax.set_xticks(range(len(losses)))
    ax.set_xticklabels([f"-{l}%" for l in losses], color=MUTED)
    ax.set_xlabel("Drawdown from peak", color=MUTED, fontsize=10)
    ax.set_ylabel("Gain needed to recover (%)", color=MUTED, fontsize=10)
    ax.set_title("Drawdown Recovery Asymmetry — Why Big Losses Are Existential",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    ax.tick_params(colors=MUTED)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(True, axis="y", color=GRID, alpha=0.3, linestyle="--", linewidth=0.5)
    ax.set_axisbelow(True)
    ax.set_ylim(0, max(recoveries) * 1.18)
    save(fig, "10-drawdown-recovery")


def liquidation_distance():
    """T1#8 — Liquidation distance shrinks with leverage."""
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.set_facecolor(BG)
    leverages = [1, 2, 3, 5, 10, 25, 50, 100]
    distances = [round(100 / l, 2) for l in leverages]
    colors = [GREEN if d > 30 else (YELLOW if d > 10 else RED) for d in distances]
    bars = ax.barh(range(len(leverages)), distances, color=colors, edgecolor=BG, linewidth=2)
    for i, (bar, d, l) in enumerate(zip(bars, distances, leverages)):
        ax.text(d + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{d}%", color=TEXT, fontsize=10, weight="bold", va="center")
    ax.set_yticks(range(len(leverages)))
    ax.set_yticklabels([f"{l}×" for l in leverages], color=MUTED)
    ax.set_xlabel("Approximate price distance to liquidation (%)",
                  color=MUTED, fontsize=10)
    ax.set_ylabel("Leverage", color=MUTED, fontsize=10)
    ax.set_title("Liquidation Distance Shrinks With Leverage",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    ax.tick_params(colors=MUTED)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(True, axis="x", color=GRID, alpha=0.3, linestyle="--", linewidth=0.5)
    ax.set_axisbelow(True)
    ax.set_xlim(0, 115)
    save(fig, "11-liquidation-distance")


# ═══════════════════════════════════════════════════════════════════════════
#   TIER 3 — Indicators diagrams
# ═══════════════════════════════════════════════════════════════════════════

def ma_golden_cross():
    """T3#22 — 50/200 MA golden cross."""
    import numpy as np
    fig, ax = plt.subplots(figsize=(11, 5.5))
    setup_axes(ax, (0, 120), (40, 80))
    x = np.arange(0, 120)
    # Simulate price that recovers from a downtrend
    price = 70 - 0.4 * x + 0.001 * x ** 2 + 5 * np.sin(x / 8)
    ma50 = np.convolve(price, np.ones(15) / 15, mode='same')
    ma200 = np.convolve(price, np.ones(40) / 40, mode='same')
    ax.plot(x, price, color=MUTED, linewidth=1, alpha=0.6, label="Price")
    ax.plot(x, ma50, color=ACCENT2, linewidth=2, label="50 MA (fast)")
    ax.plot(x, ma200, color=YELLOW, linewidth=2, label="200 MA (slow)")
    # Mark the golden cross
    cross_x = 75
    ax.axvline(x=cross_x, color=GREEN, linestyle=":", linewidth=1.5, alpha=0.6)
    ax.annotate("GOLDEN CROSS\n(fast crosses ABOVE slow)\n→ macro bullish",
                xy=(cross_x, ma50[cross_x]), xytext=(cross_x + 15, 75),
                color=GREEN, fontsize=10, weight="bold", ha="left",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.5))
    ax.set_title("Golden Cross — 50 MA Crosses Above 200 MA",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    ax.set_xlabel("Time →", color=MUTED, fontsize=9)
    ax.set_ylabel("Price", color=MUTED, fontsize=9)
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=TEXT, loc="upper left")
    save(fig, "20-ma-golden-cross")


def rsi_regimes():
    """T3#23 — RSI behaves differently in bullish vs bearish regime."""
    import numpy as np
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5))
    fig.patch.set_facecolor(BG)
    x = np.arange(0, 100)
    # Bullish regime — RSI oscillates 40-80
    rsi_bull = 60 + 20 * np.sin(x / 5) - 5 * np.cos(x / 12)
    axes[0].plot(x, rsi_bull, color=GREEN, linewidth=2)
    axes[0].axhspan(40, 80, alpha=0.1, color=GREEN)
    axes[0].axhline(50, color=MUTED, linestyle="--", alpha=0.4)
    axes[0].axhline(70, color=YELLOW, linestyle=":", alpha=0.6)
    axes[0].axhline(30, color=YELLOW, linestyle=":", alpha=0.6)
    axes[0].set_facecolor(BG)
    axes[0].set_ylim(0, 100)
    axes[0].set_title("Bullish regime — RSI averages 40-80, regularly visits 70+ (NOT a sell signal)",
                      color=GREEN, fontsize=11, weight="bold", pad=8)
    axes[0].set_ylabel("RSI", color=MUTED)
    axes[0].tick_params(colors=MUTED)
    for s in axes[0].spines.values(): s.set_color(GRID)
    axes[0].grid(True, color=GRID, alpha=0.3, linestyle="--")

    # Bearish regime — RSI 20-60
    rsi_bear = 40 - 20 * np.sin(x / 5) + 5 * np.cos(x / 12)
    axes[1].plot(x, rsi_bear, color=RED, linewidth=2)
    axes[1].axhspan(20, 60, alpha=0.1, color=RED)
    axes[1].axhline(50, color=MUTED, linestyle="--", alpha=0.4)
    axes[1].axhline(70, color=YELLOW, linestyle=":", alpha=0.6)
    axes[1].axhline(30, color=YELLOW, linestyle=":", alpha=0.6)
    axes[1].set_facecolor(BG)
    axes[1].set_ylim(0, 100)
    axes[1].set_title("Bearish regime — RSI averages 20-60, regularly visits 30- (NOT a buy signal)",
                      color=RED, fontsize=11, weight="bold", pad=8)
    axes[1].set_ylabel("RSI", color=MUTED)
    axes[1].set_xlabel("Time →", color=MUTED)
    axes[1].tick_params(colors=MUTED)
    for s in axes[1].spines.values(): s.set_color(GRID)
    axes[1].grid(True, color=GRID, alpha=0.3, linestyle="--")

    fig.suptitle("RSI Regimes — Classic 30/70 Doesn't Apply in Strong Trends",
                 color=TEXT, fontsize=13, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.91, hspace=0.4)
    save(fig, "21-rsi-regimes")


def rsi_divergence():
    """T3#23 — bearish RSI divergence example."""
    import numpy as np
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), gridspec_kw={"height_ratios": [2, 1]})
    fig.patch.set_facecolor(BG)
    # Price makes HIGHER highs
    x = np.arange(0, 100)
    price = 60 + 0.15 * x + 3 * np.sin(x / 8)
    # Add explicit peaks
    peaks_x = [20, 45, 70, 92]
    peaks_y = [65, 70, 73, 75]  # higher highs

    axes[0].plot(x, price, color=ACCENT2, linewidth=2)
    axes[0].scatter(peaks_x, peaks_y, color=RED, s=80, zorder=5, edgecolor="white", linewidth=1.5)
    axes[0].plot(peaks_x, peaks_y, color=RED, linestyle="--", linewidth=2, alpha=0.7)
    for i, (px, py) in enumerate(zip(peaks_x, peaks_y)):
        axes[0].annotate(f"HH{i+1}", xy=(px, py), xytext=(px, py + 2),
                         color=RED, fontsize=10, weight="bold", ha="center")
    axes[0].set_facecolor(BG)
    axes[0].set_title("Price makes Higher Highs",
                      color=TEXT, fontsize=11, weight="bold", pad=8)
    axes[0].set_ylabel("Price", color=MUTED)
    axes[0].tick_params(colors=MUTED)
    for s in axes[0].spines.values(): s.set_color(GRID)
    axes[0].grid(True, color=GRID, alpha=0.3, linestyle="--")

    # RSI makes LOWER highs (divergence)
    rsi = 60 + 5 * np.sin(x / 8) - 0.1 * x
    rsi_peaks_y = [75, 72, 68, 64]  # LOWER highs
    axes[1].plot(x, rsi, color=YELLOW, linewidth=2)
    axes[1].scatter(peaks_x, rsi_peaks_y, color=RED, s=80, zorder=5, edgecolor="white", linewidth=1.5)
    axes[1].plot(peaks_x, rsi_peaks_y, color=RED, linestyle="--", linewidth=2, alpha=0.7)
    for i, (px, py) in enumerate(zip(peaks_x, rsi_peaks_y)):
        axes[1].annotate(f"LH{i+1}", xy=(px, py), xytext=(px, py - 5),
                         color=RED, fontsize=10, weight="bold", ha="center")
    axes[1].axhline(50, color=MUTED, linestyle="--", alpha=0.4)
    axes[1].axhline(70, color=YELLOW, linestyle=":", alpha=0.5)
    axes[1].set_facecolor(BG)
    axes[1].set_ylim(20, 90)
    axes[1].set_title("RSI makes LOWER highs → BEARISH DIVERGENCE (momentum exhausting)",
                      color=RED, fontsize=11, weight="bold", pad=8)
    axes[1].set_ylabel("RSI", color=MUTED)
    axes[1].set_xlabel("Time →", color=MUTED)
    axes[1].tick_params(colors=MUTED)
    for s in axes[1].spines.values(): s.set_color(GRID)
    axes[1].grid(True, color=GRID, alpha=0.3, linestyle="--")

    fig.suptitle("Bearish RSI Divergence — Price ↑ but Momentum ↓",
                 color=TEXT, fontsize=13, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.92, hspace=0.4)
    save(fig, "22-rsi-divergence")


def bollinger_squeeze():
    """T3#25 — Bollinger squeeze + expansion."""
    import numpy as np
    fig, ax = plt.subplots(figsize=(11, 5.5))
    setup_axes(ax, (0, 120), (50, 80))
    x = np.arange(0, 120)
    # Calm middle (squeeze), then explosive move
    price = np.zeros(120)
    for i in range(120):
        if i < 30:
            price[i] = 65 + 3 * np.sin(i / 5)
        elif i < 70:
            price[i] = 65 + 0.8 * np.sin(i / 3)  # squeeze
        else:
            price[i] = 65 + 0.8 * (i - 70)  # expansion up
    # Bollinger bands (20-period mean ± 2 std)
    ma = np.convolve(price, np.ones(20) / 20, mode='same')
    std = np.array([np.std(price[max(0, i - 10):i + 10]) for i in range(120)])
    upper = ma + 2 * std
    lower = ma - 2 * std
    ax.plot(x, price, color=ACCENT2, linewidth=1.5, label="Price")
    ax.plot(x, ma, color=YELLOW, linewidth=1.5, alpha=0.8, label="Middle (20 SMA)")
    ax.fill_between(x, lower, upper, color=ACCENT, alpha=0.15)
    ax.plot(x, upper, color=ACCENT, linewidth=1, alpha=0.7)
    ax.plot(x, lower, color=ACCENT, linewidth=1, alpha=0.7)
    # Mark squeeze + expansion
    ax.axvspan(35, 65, alpha=0.1, color=YELLOW)
    ax.text(50, 56, "SQUEEZE\n(low volatility)", color=YELLOW, fontsize=11,
            weight="bold", ha="center")
    ax.annotate("EXPANSION\n(breakout)", xy=(95, 75), xytext=(95, 78),
                color=GREEN, fontsize=11, weight="bold", ha="center",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.5))
    ax.set_title("Bollinger Squeeze → Expansion — Volatility Cycles",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    ax.set_xlabel("Time →", color=MUTED)
    ax.set_ylabel("Price", color=MUTED)
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=TEXT, loc="upper left")
    save(fig, "23-bollinger-squeeze")


def fibonacci_retracement():
    """T3#28 — Fib retracement levels on a swing."""
    import numpy as np
    fig, ax = plt.subplots(figsize=(11, 5.5))
    setup_axes(ax, (0, 80), (50, 70))
    x = np.arange(0, 80)
    # Rally from 55 to 68, then pullback toward 0.5/0.618
    price = np.zeros(80)
    for i in range(80):
        if i < 40:
            price[i] = 55 + (i / 40) * 13  # rally to 68
        else:
            price[i] = 68 - ((i - 40) / 40) * 6  # pullback to ~62
    price += 0.5 * np.sin(x / 3)
    ax.plot(x, price, color=ACCENT2, linewidth=2)

    # Fib levels from low (55) to high (68) — range = 13
    swing_low = 55
    swing_high = 68
    fib_ratios = [0.236, 0.382, 0.5, 0.618, 0.786]
    fib_labels = ["0.236", "0.382", "0.500", "0.618", "0.786"]
    fib_colors = [MUTED, ACCENT, GREEN, YELLOW, RED]
    for ratio, label, col in zip(fib_ratios, fib_labels, fib_colors):
        y = swing_high - ratio * (swing_high - swing_low)
        ax.axhline(y=y, color=col, linestyle="--", linewidth=1.2, alpha=0.7)
        ax.text(78, y + 0.1, f"{label}  ({y:.1f})", color=col, fontsize=9, ha="right")
    ax.axhline(y=swing_low, color=GREEN, linewidth=1.5, alpha=0.6)
    ax.text(0.5, swing_low - 0.5, "0%  (swing low)", color=GREEN, fontsize=9)
    ax.axhline(y=swing_high, color=RED, linewidth=1.5, alpha=0.6)
    ax.text(0.5, swing_high + 0.3, "100%  (swing high)", color=RED, fontsize=9)

    ax.set_title("Fibonacci Retracement — 0.5 and 0.618 Are the Most Watched",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    ax.set_xlabel("Time →", color=MUTED)
    ax.set_ylabel("Price", color=MUTED)
    save(fig, "24-fibonacci-retracement")


# ═══════════════════════════════════════════════════════════════════════════
#   TIER 4 — Advanced diagrams
# ═══════════════════════════════════════════════════════════════════════════

def cvd_divergence():
    """T4#31 — bearish CVD divergence (price up, CVD down)."""
    import numpy as np
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), gridspec_kw={"height_ratios": [2, 1]})
    fig.patch.set_facecolor(BG)
    x = np.arange(0, 100)
    price = 60 + 0.12 * x + 2 * np.sin(x / 8)
    axes[0].plot(x, price, color=ACCENT2, linewidth=2)
    axes[0].annotate("Price grinding HIGHER", xy=(85, price[85]), xytext=(60, 80),
                     color=GREEN, fontsize=10, weight="bold",
                     arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.2))
    axes[0].set_facecolor(BG)
    axes[0].set_title("Price action", color=TEXT, fontsize=11, weight="bold", pad=8)
    axes[0].set_ylabel("Price", color=MUTED)
    axes[0].tick_params(colors=MUTED)
    for s in axes[0].spines.values(): s.set_color(GRID)
    axes[0].grid(True, color=GRID, alpha=0.3, linestyle="--")

    # CVD flat or falling
    cvd = 100 - 0.3 * x + 5 * np.sin(x / 6)
    axes[1].plot(x, cvd, color=YELLOW, linewidth=2)
    axes[1].fill_between(x, cvd, color=YELLOW, alpha=0.1)
    axes[1].annotate("CVD FALLING\n(aggressive sellers > buyers,\nabsorbed by passive bids)",
                     xy=(85, cvd[85]), xytext=(50, 85),
                     color=RED, fontsize=10, weight="bold",
                     arrowprops=dict(arrowstyle="->", color=RED, lw=1.2))
    axes[1].set_facecolor(BG)
    axes[1].set_title("CVD (Cumulative Volume Delta) — DIVERGING from price",
                      color=RED, fontsize=11, weight="bold", pad=8)
    axes[1].set_ylabel("CVD", color=MUTED)
    axes[1].set_xlabel("Time →", color=MUTED)
    axes[1].tick_params(colors=MUTED)
    for s in axes[1].spines.values(): s.set_color(GRID)
    axes[1].grid(True, color=GRID, alpha=0.3, linestyle="--")

    fig.suptitle("Bearish CVD Divergence — Hollow Trend (Reversal Warning)",
                 color=TEXT, fontsize=13, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.92, hspace=0.4)
    save(fig, "30-cvd-divergence")


def wyckoff_cycle():
    """T4#33 — full Wyckoff phase cycle."""
    import numpy as np
    fig, ax = plt.subplots(figsize=(13, 5.5))
    setup_axes(ax, (0, 200), (40, 90))
    x = np.arange(0, 200)
    price = np.zeros(200)
    for i in range(200):
        if i < 40:  # markdown bottom (residual)
            price[i] = 65 - 0.5 * i + 3 * np.sin(i / 4)
        elif i < 80:  # accumulation (sideways)
            price[i] = 47 + 3 * np.sin(i / 3)
        elif i < 130:  # markup
            price[i] = 47 + 0.8 * (i - 80) + 3 * np.sin(i / 5)
        elif i < 165:  # distribution
            price[i] = 87 + 3 * np.sin(i / 4)
        else:  # markdown
            price[i] = 87 - 1.2 * (i - 165) + 3 * np.sin(i / 4)
    ax.plot(x, price, color=ACCENT2, linewidth=2)

    # Color the phases
    ax.axvspan(40, 80, alpha=0.12, color=GREEN, label="Accumulation")
    ax.axvspan(80, 130, alpha=0.12, color=ACCENT2, label="Markup")
    ax.axvspan(130, 165, alpha=0.12, color=YELLOW, label="Distribution")
    ax.axvspan(165, 200, alpha=0.12, color=RED, label="Markdown")

    # Phase labels
    ax.text(60, 87, "ACCUMULATION", color=GREEN, fontsize=11, weight="bold", ha="center")
    ax.text(105, 87, "MARKUP", color=ACCENT2, fontsize=11, weight="bold", ha="center")
    ax.text(147, 87, "DISTRIBUTION", color=YELLOW, fontsize=11, weight="bold", ha="center")
    ax.text(182, 87, "MARKDOWN", color=RED, fontsize=11, weight="bold", ha="center")

    # Spring marker
    ax.annotate("SPRING\n(stop hunt)", xy=(75, 44), xytext=(75, 50),
                color=GREEN, fontsize=9, weight="bold", ha="center",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.2))
    # Upthrust marker
    ax.annotate("UPTHRUST\n(stop hunt)", xy=(155, 91), xytext=(155, 82),
                color=RED, fontsize=9, weight="bold", ha="center",
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.2))

    ax.set_title("Wyckoff Cycle — Accumulation → Markup → Distribution → Markdown",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    ax.set_xlabel("Time →", color=MUTED)
    ax.set_ylabel("Price", color=MUTED)
    save(fig, "31-wyckoff-cycle")


def spring_upthrust_pattern():
    """T4#34 — Spring and Upthrust visual."""
    import numpy as np
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.patch.set_facecolor(BG)

    # SPRING — bottom of accumulation range
    ax = axes[0]
    setup_axes(ax, (0, 16), (45, 60))
    # Range candles
    candles = [
        (1, 51, 53, 49, 52), (2, 52, 54, 50, 51), (3, 51, 53, 49, 52),
        (4, 52, 53, 50, 51), (5, 51, 53, 49, 52), (6, 52, 54, 50, 51),
        (7, 51, 53, 49, 51), (8, 51, 53, 49, 52), (9, 52, 53, 49, 51),
        (10, 51, 52, 49, 51), (11, 51, 53, 49, 52), (12, 52, 53, 50, 51),
        # The spring: deep wick below, close back above
        (13, 51, 53, 46, 52.5),
        (14, 52.5, 54.5, 52, 54),
        (15, 54, 56, 53.5, 55.5),
    ]
    for c in candles:
        draw_candle(ax, c[0], c[1], c[2], c[3], c[4], width=0.6)
    ax.axhline(y=49, color=GREEN, linewidth=2, alpha=0.5)
    ax.text(0.5, 49.4, "Range support", color=GREEN, fontsize=9)
    ax.annotate("SPRING — wick BELOW support,\nclose BACK ABOVE\n(stop hunt complete)",
                xy=(13, 46), xytext=(8, 56),
                color=GREEN, fontsize=10, weight="bold", ha="center",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.5))
    ax.set_title("Wyckoff Spring — Accumulation Finale",
                 color=GREEN, fontsize=12, weight="bold", pad=10)

    # UPTHRUST — top of distribution range
    ax = axes[1]
    setup_axes(ax, (0, 16), (60, 75))
    candles = [
        (1, 69, 71, 67, 70), (2, 70, 72, 68, 69), (3, 69, 71, 67, 70),
        (4, 70, 71, 68, 69), (5, 69, 71, 67, 70), (6, 70, 72, 68, 69),
        (7, 69, 71, 67, 69), (8, 69, 71, 67, 70), (9, 70, 71, 67, 69),
        (10, 69, 70, 67, 69), (11, 69, 71, 67, 70), (12, 70, 71, 68, 69),
        # The upthrust: spike above, close back below
        (13, 69, 74, 67, 67.5),
        (14, 67.5, 68, 65.5, 66),
        (15, 66, 66.5, 64, 64.5),
    ]
    for c in candles:
        draw_candle(ax, c[0], c[1], c[2], c[3], c[4], width=0.6)
    ax.axhline(y=71, color=RED, linewidth=2, alpha=0.5)
    ax.text(0.5, 71.3, "Range resistance", color=RED, fontsize=9)
    ax.annotate("UPTHRUST — wick ABOVE resistance,\nclose BACK BELOW\n(false breakout, then markdown)",
                xy=(13, 74), xytext=(8, 62),
                color=RED, fontsize=10, weight="bold", ha="center",
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.5))
    ax.set_title("Wyckoff Upthrust — Distribution Finale",
                 color=RED, fontsize=12, weight="bold", pad=10)

    fig.suptitle("Spring & Upthrust — Phase-Transition Patterns",
                 color=TEXT, fontsize=14, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.88)
    save(fig, "32-spring-upthrust")


def order_block_example():
    """T4#35 — Bullish Order Block + impulsive move + retest."""
    fig, ax = plt.subplots(figsize=(11, 5.5))
    setup_axes(ax, (0, 18), (55, 75))
    candles = [
        # Ranging candles
        (1, 60, 62, 59, 61), (2, 61, 62, 60, 61),
        # The OB — last red candle before impulse
        (3, 61, 61, 58, 59),  # bullish OB candle (red)
        # Impulsive move up — creates BoS + FVG
        (4, 59, 67, 59, 66),
        (5, 66, 70, 65, 69),
        (6, 69, 72, 68, 71),
        # Continuation
        (7, 71, 73, 70, 72),
        (8, 72, 73, 70, 71),
        # Pullback BACK to the OB
        (9, 71, 72, 68, 69),
        (10, 69, 70, 64, 65),
        # Touched OB zone — bullish reaction
        (11, 65, 67, 61, 66),  # wick into OB zone
        (12, 66, 68, 65, 67),
        (13, 67, 70, 66, 69),
        # Continued bullish
        (14, 69, 72, 68, 71),
        (15, 71, 74, 70, 73),
    ]
    for c in candles:
        draw_candle(ax, c[0], c[1], c[2], c[3], c[4], width=0.55)
    # Highlight the OB zone (the body of candle 3)
    ax.axhspan(58, 61, xmin=0.05, xmax=0.95, color=GREEN, alpha=0.20)
    ax.text(0.3, 59.5, "Bullish OB\nzone", color=GREEN, fontsize=10,
            weight="bold", va="center")
    ax.annotate("Impulsive move\nbreaks structure", xy=(5, 68), xytext=(6.5, 73),
                color=ACCENT2, fontsize=10, weight="bold",
                arrowprops=dict(arrowstyle="->", color=ACCENT2, lw=1.2))
    ax.annotate("Pullback to OB\n→ bullish reaction", xy=(11, 62), xytext=(13, 57),
                color=GREEN, fontsize=10, weight="bold",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.2))
    ax.set_title("Bullish Order Block — Last Red Candle Before Impulsive Move",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    ax.set_xlabel("Time →", color=MUTED)
    ax.set_ylabel("Price", color=MUTED)
    save(fig, "33-order-block")


# ═══════════════════════════════════════════════════════════════════════════
#   TIER 5 — Macro & Context diagrams
# ═══════════════════════════════════════════════════════════════════════════

def macro_correlation():
    """T5#40 — DXY (inverse) and BTC."""
    import numpy as np
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5))
    fig.patch.set_facecolor(BG)
    x = np.arange(0, 100)
    # DXY rising
    dxy = 100 + 0.06 * x + np.sin(x / 8)
    # BTC INVERSELY moving
    btc = 70 - 0.12 * x - 2 * np.sin(x / 8)

    axes[0].plot(x, dxy, color=YELLOW, linewidth=2)
    axes[0].fill_between(x, dxy, color=YELLOW, alpha=0.1)
    axes[0].set_facecolor(BG)
    axes[0].set_title("DXY (US Dollar Index) — Rising = USD Strengthening",
                      color=YELLOW, fontsize=11, weight="bold", pad=8)
    axes[0].set_ylabel("DXY", color=MUTED)
    axes[0].tick_params(colors=MUTED)
    for s in axes[0].spines.values(): s.set_color(GRID)
    axes[0].grid(True, color=GRID, alpha=0.3, linestyle="--")

    axes[1].plot(x, btc, color=ACCENT2, linewidth=2)
    axes[1].fill_between(x, btc, color=ACCENT2, alpha=0.1)
    axes[1].set_facecolor(BG)
    axes[1].set_title("BTC — Falling (INVERSE to DXY)",
                      color=ACCENT2, fontsize=11, weight="bold", pad=8)
    axes[1].set_ylabel("BTC Price", color=MUTED)
    axes[1].set_xlabel("Time →", color=MUTED)
    axes[1].tick_params(colors=MUTED)
    for s in axes[1].spines.values(): s.set_color(GRID)
    axes[1].grid(True, color=GRID, alpha=0.3, linestyle="--")

    fig.suptitle("DXY vs BTC — Inverse Correlation (Macro Headwind)",
                 color=TEXT, fontsize=13, weight="bold", y=0.98)
    fig.subplots_adjust(top=0.92, hspace=0.4)
    save(fig, "40-macro-correlation")


def fear_greed_zones():
    """T5#42 — F&G index ranges with contrarian zones."""
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.set_facecolor(BG)
    # Color bands
    ax.axhspan(0, 25, color=RED, alpha=0.20)
    ax.axhspan(25, 45, color=YELLOW, alpha=0.10)
    ax.axhspan(45, 55, color=MUTED, alpha=0.10)
    ax.axhspan(55, 75, color=YELLOW, alpha=0.10)
    ax.axhspan(75, 100, color=GREEN, alpha=0.20)

    # Labels
    ax.text(5, 12, "EXTREME FEAR\n(0-25)\n→ LONG bias", color=RED, fontsize=11, weight="bold")
    ax.text(5, 35, "Fear (25-45)", color=YELLOW, fontsize=10)
    ax.text(5, 50, "Neutral (45-55) — trade technicals", color=MUTED, fontsize=10)
    ax.text(5, 65, "Greed (55-75)", color=YELLOW, fontsize=10)
    ax.text(5, 87, "EXTREME GREED\n(75-100)\n→ SHORT bias / take profits",
            color=GREEN, fontsize=11, weight="bold")

    # Annotation
    ax.text(60, 12, "BUY ZONE (contrarian)", color=RED, fontsize=11, weight="bold",
            ha="left", style="italic")
    ax.text(60, 87, "SELL ZONE (contrarian)", color=GREEN, fontsize=11, weight="bold",
            ha="left", style="italic")

    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_xticks([])
    ax.set_yticks([0, 25, 45, 55, 75, 100])
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.set_ylabel("F&G Index", color=MUTED, fontsize=10)
    for s in ax.spines.values(): s.set_color(GRID)
    ax.set_title("Fear & Greed Index — Contrarian Zones",
                 color=TEXT, fontsize=13, weight="bold", pad=15)
    save(fig, "41-fear-greed-zones")


# ═══════════════════════════════════════════════════════════════════════════
#   TIER 6 — Execution diagrams
# ═══════════════════════════════════════════════════════════════════════════

def apgar_scoreboard():
    """T6#46 — Trade Apgar 5-dimension visual."""
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.set_facecolor(BG)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)

    dims = [
        ("Setup Quality", "Pre-planned A-setup? Or impulse?"),
        ("Risk:Reward", "≥ 1:2? Ideally 1:3+?"),
        ("Confluences", "4+ signals aligned? Or just 1?"),
        ("Macro/Regime", "Aligned with HTF + macro?"),
        ("Personal State", "Calm, rested, focused?"),
    ]
    for i, (label, desc) in enumerate(dims):
        y = 0.85 - i * 0.16
        # Label box
        ax.add_patch(Rectangle((0.05, y), 0.28, 0.10, color=ACCENT, alpha=0.3,
                               transform=ax.transAxes))
        ax.text(0.07, y + 0.05, label, color=TEXT, fontsize=11, weight="bold",
                va="center", transform=ax.transAxes)
        # Description
        ax.text(0.36, y + 0.05, desc, color=MUTED, fontsize=10,
                va="center", transform=ax.transAxes)
        # Score circles (0, 1, 2)
        for j, val in enumerate([0, 1, 2]):
            cx = 0.82 + j * 0.05
            color = [RED, YELLOW, GREEN][j]
            ax.add_patch(plt.Circle((cx, y + 0.05), 0.018, color=color,
                                    transform=ax.transAxes, alpha=0.7))
            ax.text(cx, y + 0.05, str(val), color=BG, fontsize=9, weight="bold",
                    ha="center", va="center", transform=ax.transAxes)

    # Verdict box
    ax.add_patch(Rectangle((0.05, 0.02), 0.90, 0.07, color=GREEN, alpha=0.15,
                           transform=ax.transAxes))
    ax.text(0.5, 0.055,
            "PASS: total ≥ 7  AND  no zeros in any dimension  →  TRADE.   Otherwise SKIP.",
            color=GREEN, fontsize=12, weight="bold", ha="center", va="center",
            transform=ax.transAxes)

    ax.text(0.5, 0.97, "Trade Apgar — 5-Dimension Pre-Trade Scorecard",
            color=TEXT, fontsize=14, weight="bold", ha="center",
            transform=ax.transAxes)
    save(fig, "50-apgar-scoreboard")


def readiness_traffic_light():
    """T6#47 — pre-session 4-dimension traffic light."""
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.set_facecolor(BG)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)

    dims = [
        ("Sleep",       "7+ hours, rested",      "5-7h, somewhat tired", "Under 5h, exhausted"),
        ("Mood",        "Calm, focused",         "Stressed but ok",       "Angry, anxious"),
        ("Recent P&L",  "Within normal range",    "Slight DD",            "Down 5%+, tilt risk"),
        ("Preparation", "Reviewed setups + plan", "Some prep, rushed",    "Zero prep, jumping cold"),
    ]
    for i, (label, green, yellow, red) in enumerate(dims):
        y = 0.78 - i * 0.18
        ax.text(0.05, y + 0.02, label, color=TEXT, fontsize=11, weight="bold",
                va="center", transform=ax.transAxes)
        # Green box
        ax.add_patch(Rectangle((0.20, y - 0.04), 0.24, 0.08, color=GREEN, alpha=0.30,
                               transform=ax.transAxes))
        ax.text(0.32, y, green, color=TEXT, fontsize=9, ha="center", va="center",
                transform=ax.transAxes)
        # Yellow box
        ax.add_patch(Rectangle((0.46, y - 0.04), 0.24, 0.08, color=YELLOW, alpha=0.25,
                               transform=ax.transAxes))
        ax.text(0.58, y, yellow, color=TEXT, fontsize=9, ha="center", va="center",
                transform=ax.transAxes)
        # Red box
        ax.add_patch(Rectangle((0.72, y - 0.04), 0.24, 0.08, color=RED, alpha=0.30,
                               transform=ax.transAxes))
        ax.text(0.84, y, red, color=TEXT, fontsize=9, ha="center", va="center",
                transform=ax.transAxes)

    # Verdict
    ax.add_patch(Rectangle((0.05, 0.02), 0.91, 0.06, color=YELLOW, alpha=0.15,
                           transform=ax.transAxes))
    ax.text(0.5, 0.05,
            "ALL GREEN: full mode    Mixed: reduced size + only A+ setups    Any RED: NO TRADING TODAY",
            color=TEXT, fontsize=11, weight="bold", ha="center", va="center",
            transform=ax.transAxes)

    ax.text(0.5, 0.95, "Pre-Session Readiness — 4-Dimension Traffic Light",
            color=TEXT, fontsize=14, weight="bold", ha="center",
            transform=ax.transAxes)
    save(fig, "51-readiness-traffic-light")


# ─── Main runner ───────────────────────────────────────────────────────────
def main():
    print(f"Generating diagrams into {OUT_DIR} ...")
    # Tier 2 (already done)
    candle_anatomy()
    bullish_reversal_patterns()
    bearish_reversal_patterns()
    indecision_candles()
    support_resistance()
    market_structure()
    trendlines()
    fvg_example()
    bos_choch()
    # Tier 1
    drawdown_recovery()
    liquidation_distance()
    # Tier 3
    ma_golden_cross()
    rsi_regimes()
    rsi_divergence()
    bollinger_squeeze()
    fibonacci_retracement()
    # Tier 4
    cvd_divergence()
    wyckoff_cycle()
    spring_upthrust_pattern()
    order_block_example()
    # Tier 5
    macro_correlation()
    fear_greed_zones()
    # Tier 6
    apgar_scoreboard()
    readiness_traffic_light()
    print(f"\nDone. {len(list(OUT_DIR.glob('*.png')))} PNG files in {OUT_DIR}")


if __name__ == "__main__":
    main()
