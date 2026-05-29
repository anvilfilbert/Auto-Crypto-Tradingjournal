"""
Static PNG renderer for VMC Cipher B.

Produces either:
  - A standalone oscillator pane (just the indicator)
  - A combined price + VMC dual-pane chart

Lives separately from agent_chart_draw.py so trade-card rendering stays
simple. Both consume compute_vmc_cipher() from chart_vmc_cipher.py.
"""
from __future__ import annotations

import base64
import io
from typing import Optional

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import chart_vmc_cipher
import chart_vmc_cipher_a


# Colours pulled from the TradingView original
_C_WT1       = "#90caf9"   # light blue (Pine #90caf9, alpha 10)
_C_WT2       = "#0d47a1"   # dark blue  (Pine #0d47a1, alpha 30)
_C_WT_FILL   = "#1f5bd6"   # area between WT1 and WT2
_C_VWAP      = "#ffd54f"   # yellow VWAP (wt1-wt2)
_C_MFI_POS   = "#3ee145"   # GREEN when positive (Pine #3ee145 alpha 40)
_C_MFI_NEG   = "#ff3d2e"   # RED when negative (Pine #ff3d2e alpha 40)
_C_GOLD_BUY  = "#26d96b"   # green dot (Cipher B buy)
_C_GOLD_SELL = "#ef4444"   # red dot (Cipher B sell)
_C_BULL_DIV  = "#26d96b"   # green divergence ring
_C_BEAR_DIV  = "#ef4444"   # red divergence ring
_C_REF       = "#5b6b85"   # dashed reference lines
_C_TEXT      = "#cbd5e1"
_C_BG        = "#0c1322"

# Cipher A marker colours
_C_LONG_EMA      = "#00ff00"   # green circle
_C_SHORT_EMA     = "#ff0000"   # red circle
_C_RED_CROSS     = "#ff0000"   # red ×
_C_BLUE_TRI      = "#0064ff"   # blue ▲
_C_RED_DIAMOND   = "#ff4444"   # red ◆
_C_BLOOD_DIAMOND = "#cc0000"   # dark red ◆ (larger)
_C_YELLOW_X      = "#ffd60a"   # yellow ×
_C_BULL_CANDLE   = "#ffcc00"   # yellow ◆


def draw_vmc_only(
    candles: pd.DataFrame,
    symbol: str = "",
    width_px: int = 1100,
    height_px: int = 360,
    n_bars: int = 120,
) -> str:
    """Render JUST the VMC pane as a base64 PNG (no price subplot)."""
    if candles is None or candles.empty:
        return _placeholder("no candles")

    df = candles.tail(n_bars).copy()
    vmc = chart_vmc_cipher.compute_vmc_cipher(df)
    if vmc["wt1"].empty:
        return _placeholder("insufficient data")

    fig, ax = plt.subplots(
        figsize=(width_px / 100, height_px / 100),
        dpi=100,
        facecolor=_C_BG,
    )
    _render_pane(ax, df, vmc, symbol)
    fig.tight_layout(pad=0.6)
    return _fig_to_b64(fig)


def draw_price_and_vmc(
    candles: pd.DataFrame,
    symbol: str = "",
    width_px: int = 1100,
    height_px: int = 700,
    n_bars: int = 120,
    with_cipher_a: bool = True,
) -> str:
    """Render price chart (with Cipher A overlay) on top, Cipher B pane below."""
    if candles is None or candles.empty:
        return _placeholder("no candles")

    df = candles.tail(n_bars).copy()
    vmc_b   = chart_vmc_cipher.compute_vmc_cipher(df)
    cipher_a = chart_vmc_cipher_a.compute_cipher_a(df) if with_cipher_a else None
    if vmc_b["wt1"].empty:
        return _placeholder("insufficient data")

    fig, (ax_price, ax_vmc) = plt.subplots(
        2, 1,
        figsize=(width_px / 100, height_px / 100),
        dpi=100,
        gridspec_kw={"height_ratios": [2.4, 1]},
        sharex=True,
        facecolor=_C_BG,
    )
    _render_price(ax_price, df, symbol, cipher_a=cipher_a)
    _render_pane(ax_vmc, df, vmc_b, symbol="")  # header only on top
    fig.tight_layout(pad=0.6)
    return _fig_to_b64(fig)


# ── Internals ───────────────────────────────────────────────────────────────────

def _x_axis(df: pd.DataFrame):
    """Build a datetime x-axis from the candles DataFrame.
    chart_candles returns a RangeIndex with timestamps in a 'timestamp' column
    (milliseconds since epoch). Convert to pandas datetime for matplotlib."""
    if "timestamp" in df.columns:
        return pd.to_datetime(df["timestamp"].astype("int64"), unit="ms")
    return pd.to_datetime(df.index)


def _render_pane(ax, df: pd.DataFrame, vmc: dict, symbol: str) -> None:
    """Draw the VMC oscillator into a single matplotlib Axes."""
    ax.set_facecolor(_C_BG)
    for sp in ax.spines.values():
        sp.set_color("#1f2937")
    ax.tick_params(colors=_C_TEXT, labelsize=7)
    ax.grid(True, color="#1f2937", linestyle="--", linewidth=0.4, alpha=0.5)

    x = _x_axis(df)
    wt1 = vmc["wt1"]; wt2 = vmc["wt2"]; mfi = vmc["mfi"]
    p = vmc["params"]

    # Reference lines
    ax.axhline(0,           color=_C_REF, linewidth=0.7, linestyle="-",  alpha=0.4)
    ax.axhline(p["ob"],     color=_C_REF, linewidth=0.5, linestyle="--", alpha=0.4)
    ax.axhline(p["os"],     color=_C_REF, linewidth=0.5, linestyle="--", alpha=0.4)
    ax.axhline(p["ob_alert"], color=_C_REF, linewidth=0.4, linestyle=":",  alpha=0.3)
    ax.axhline(p["os_alert"], color=_C_REF, linewidth=0.4, linestyle=":",  alpha=0.3)

    # Money flow ribbon — split colour by sign (Pine V6 uses green/red, not yellow)
    ax.fill_between(x, mfi, 0, where=(mfi >= 0),
                    facecolor=_C_MFI_POS, alpha=0.50, linewidth=0)
    ax.fill_between(x, mfi, 0, where=(mfi < 0),
                    facecolor=_C_MFI_NEG, alpha=0.50, linewidth=0)

    # WaveTrend area: wt2 area first, then wt1 area on top (Pine style)
    ax.fill_between(x, wt2, 0, facecolor=_C_WT2, alpha=0.30, linewidth=0)
    ax.fill_between(x, wt1, 0, facecolor=_C_WT1, alpha=0.45, linewidth=0)

    # VWAP (wt1 - wt2) — yellow signed area
    vwap = vmc.get("vwap")
    if vwap is not None and not vwap.empty:
        ax.fill_between(x, vwap, 0,
                        facecolor=_C_VWAP, alpha=0.35, linewidth=0)
        ax.plot(x, vwap, color=_C_VWAP, linewidth=1.0, alpha=0.85)

    # Gold-dot signals — Pine uses location.absolute (plot AT wt2 value)
    gb_mask = vmc["gold_buy"]
    gs_mask = vmc["gold_sell"]
    if gb_mask.any():
        ax.scatter(x[gb_mask], wt2[gb_mask], s=28, color=_C_GOLD_BUY,
                   edgecolors="#0c5132", linewidths=0.6, zorder=6, marker="o")
    if gs_mask.any():
        ax.scatter(x[gs_mask], wt2[gs_mask], s=28, color=_C_GOLD_SELL,
                   edgecolors="#5b1411", linewidths=0.6, zorder=6, marker="o")

    # Divergence rings — open circles at the pivot bar's wt2 value
    bull_div = vmc.get("bull_div")
    bear_div = vmc.get("bear_div")
    if bull_div is not None and bull_div.any():
        ax.scatter(x[bull_div], wt2[bull_div], s=70, facecolors="none",
                   edgecolors=_C_BULL_DIV, linewidths=1.8, zorder=7, marker="o")
    if bear_div is not None and bear_div.any():
        ax.scatter(x[bear_div], wt2[bear_div], s=70, facecolors="none",
                   edgecolors=_C_BEAR_DIV, linewidths=1.8, zorder=7, marker="o")

    # Header line — symbol + params + latest values
    last_wt1 = float(wt1.iloc[-1]) if not pd.isna(wt1.iloc[-1]) else 0.0
    last_wt2 = float(wt2.iloc[-1]) if not pd.isna(wt2.iloc[-1]) else 0.0
    last_mfi = float(mfi.iloc[-1]) if not pd.isna(mfi.iloc[-1]) else 0.0
    header = (
        f"{symbol + '  ' if symbol else ''}"
        f"VMC Cipher B  {p['wt_n1']}/{p['wt_n2']}/{p['wt_ma']} · "
        f"MFI {p['mfi_length']}×{p['mfi_mult']}     "
        f"WT1={last_wt1:+.2f}  WT2={last_wt2:+.2f}  MFI={last_mfi:+.2f}"
    )
    ax.text(0.005, 0.97, header, transform=ax.transAxes,
            color=_C_TEXT, fontsize=8, fontweight="bold",
            verticalalignment="top", fontfamily="monospace")

    # Y-axis range with a small pad
    y_all = pd.concat([wt1, wt2, mfi]).dropna()
    if not y_all.empty:
        lo, hi = float(y_all.min()), float(y_all.max())
        pad = max(5.0, (hi - lo) * 0.08)
        ax.set_ylim(min(lo - pad, -85), max(hi + pad, 85))

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha="center")


def _render_price(ax, df: pd.DataFrame, symbol: str,
                   cipher_a: dict = None) -> None:
    """Price line + Cipher A overlay (EMA ribbon + signal markers)."""
    ax.set_facecolor(_C_BG)
    for sp in ax.spines.values():
        sp.set_color("#1f2937")
    ax.tick_params(colors=_C_TEXT, labelsize=7)
    ax.grid(True, color="#1f2937", linestyle="--", linewidth=0.4, alpha=0.5)

    x = _x_axis(df); c = df["close"]; h = df["high"]
    ax.plot(x, c, color="#cbd5e1", linewidth=1.0, zorder=3)
    # subtle base fill
    ax.fill_between(x, c, c.min() - (c.max() - c.min()) * 0.2,
                    color="#cbd5e1", alpha=0.04, linewidth=0)

    if cipher_a:
        # 8-EMA ribbon — render lighter to darker by EMA index
        ribbon_palette_bull = ["#1573d4", "#3096ff", "#57abff", "#85c2ff",
                                "#9bcdff", "#b3d9ff", "#c9e5ff", "#dfecfb"]
        for i, col in enumerate(ribbon_palette_bull, start=1):
            ema = cipher_a.get(f"ema{i}")
            if ema is not None:
                ax.plot(x, ema, color=col, linewidth=0.9, alpha=0.75, zorder=2)

        # Signal markers (positioned slightly above the bar's high so they
        # don't collide with the price line)
        y_offset = (h.max() - h.min()) * 0.012 if not h.empty else 0
        def _plot_marker(mask, marker, color, size, label, edge=None):
            if mask is None or not mask.any(): return
            y = h[mask] + y_offset
            ax.scatter(x[mask], y, marker=marker, color=color, s=size,
                       edgecolors=edge or color, linewidths=0.4, zorder=6)

        _plot_marker(cipher_a.get("long_ema"),       "o", _C_LONG_EMA,     22, "Long EMA")
        _plot_marker(cipher_a.get("short_ema"),      "o", _C_SHORT_EMA,    22, "Short EMA")
        _plot_marker(cipher_a.get("red_cross"),      "X", _C_RED_CROSS,    24, "Red ×")
        _plot_marker(cipher_a.get("blue_triangle"),  "^", _C_BLUE_TRI,     34, "Blue ▲")
        _plot_marker(cipher_a.get("red_diamond"),    "D", _C_RED_DIAMOND,  18, "Red ◆")
        _plot_marker(cipher_a.get("blood_diamond"),  "D", _C_BLOOD_DIAMOND, 42, "Blood ◆",
                     edge="#000000")
        _plot_marker(cipher_a.get("yellow_x"),       "X", _C_YELLOW_X,     50, "Yellow ×",
                     edge="#000000")
        _plot_marker(cipher_a.get("bull_candle"),    "D", _C_BULL_CANDLE,  12, "Bull ◆")

    # Header
    last = float(c.iloc[-1])
    header = f"{symbol}  last={last:.6g}"
    if cipher_a:
        header += "   Cipher A: EMA ribbon + markers"
    ax.text(0.005, 0.97, header,
            transform=ax.transAxes, color=_C_TEXT,
            fontsize=8, fontweight="bold",
            verticalalignment="top", fontfamily="monospace")


def _fig_to_b64(fig) -> str:
    """Encode a matplotlib figure as a base64 PNG data string."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(),
                bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _placeholder(msg: str) -> str:
    """Render a tiny placeholder PNG when computation isn't possible."""
    fig, ax = plt.subplots(figsize=(8, 2), dpi=100, facecolor=_C_BG)
    ax.set_facecolor(_C_BG)
    ax.text(0.5, 0.5, f"VMC Cipher B — {msg}",
            transform=ax.transAxes, color=_C_TEXT,
            fontsize=10, horizontalalignment="center",
            verticalalignment="center", fontfamily="monospace")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values(): sp.set_visible(False)
    return _fig_to_b64(fig)
