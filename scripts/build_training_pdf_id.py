"""Build a complete Indonesian translation of the training module as a single PDF.

Pipeline:
  1. Translate each lesson JSON → Indonesian (Claude Sonnet, glossary-cached system).
  2. Render translated lessons + diagrams → one big standalone HTML.
  3. Convert HTML → A4 PDF via Playwright.

Trading terminology (Long/Short/TP/SL/RSI/etc.) stays English. Prose translated
to natural Indonesian. Quizzes are NOT included. Already-translated lessons are
cached to disk so re-runs only retry failures.

Run from repo root:  python3 scripts/build_training_pdf_id.py
Output:              docs/training_id.pdf
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from pathlib import Path
from html import escape as h

# Make repo root importable regardless of cwd
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load ANTHROPIC_API_KEY from .env if not already set
if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = _REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip("\"'")
                break

import ai_client  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LESSON_SRC = REPO / "training" / "content" / "lessons"
CATALOG = REPO / "training" / "content" / "catalog.json"
CHARTS = REPO / "training" / "static" / "charts"
OUT_DIR = REPO / "docs" / "training_id"
OUT_HTML = REPO / "docs" / "training_id.html"
OUT_PDF = REPO / "docs" / "training_id.pdf"

OUT_DIR.mkdir(parents=True, exist_ok=True)

TIER_META = {
    1: ("Fondasi", "Foundations — apa itu pasar, leverage, manajemen risiko dasar"),
    2: ("Membaca Chart", "Chart Reading — candlestick, level, struktur pasar"),
    3: ("Indikator", "Indicators — RSI, MACD, EMA, Bollinger Bands"),
    4: ("Lanjutan", "Advanced — Wyckoff, FVG, Order Block, SMC"),
    5: ("Makro & Konteks", "Macro & Context — VIX, DXY, BTC dominance, korelasi"),
    6: ("Eksekusi & Jurnal", "Execution & Journaling — disiplin, Apgar, readiness"),
}

# ── Translation glossary ────────────────────────────────────────────────────
# Terms that MUST stay English (or use the English form even when Indonesian
# is technically possible — these are universally used in Indonesian crypto
# trading and translating them would harm clarity).
GLOSSARY = """KEEP THESE TERMS IN ENGLISH (never translate):

Positions / orders:
  Long, Short, Take Profit (TP), Stop Loss (SL), Limit, Market, Stop,
  Entry, Exit, Position, Order, Leverage, Margin, Equity, Liquidation,
  Funding Rate, Open Interest (OI), Hedge

Indicators:
  RSI, MACD, EMA, SMA, ATR, Bollinger Bands, BB, MFI, ADX, Stochastic,
  WaveTrend, CVD, OBV, VWAP, Pivot, CPR

Patterns / SMC:
  Support, Resistance, Bullish, Bearish, Breakout, Reversal, Continuation,
  Candle, Candlestick, Doji, Hammer, Shooting Star, Engulfing, Pin Bar,
  Inside Bar, Outside Bar, FVG, Fair Value Gap, Order Block, Liquidity,
  BoS, Break of Structure, CHoCH, Change of Character, SMT, Wyckoff,
  Accumulation, Distribution, Spring, Upthrust, Premium, Discount,
  Equilibrium, Range, Trend, Higher High, Higher Low, Lower High, Lower Low,
  Mitigation, Imbalance, Inducement, Sweep, Reclaim, Retest

Risk / stats:
  Risk:Reward, R:R, R-multiple, Drawdown, Win Rate, Expectancy, Sharpe,
  Calmar, Position Sizing, Kelly, Expectancy, Edge, MFE, MAE

Macro:
  VIX, DXY, BTC dominance, Bull market, Bear market, Pump, Dump, FOMO,
  FUD, HODL, Risk-on, Risk-off, Capitulation

Timeframes:
  1D, 4H, 1H, 15m, 5m, 1m, Daily, 4-hour, 1-hour, weekly, monthly (English form)

Markets:
  Spot, Futures, Perpetual, Perp, Cross margin, Isolated margin,
  Bid, Ask, Spread, Slippage

Sessions (kept English):
  Asia session, London session, NY session, Silver Bullet, Kill Zone

Misc:
  Setup, Confluence, Backtest, Forward test, Paper trade, Live trade,
  Journal, Apgar, Readiness, Tier, Capstone

ALSO PRESERVE:
- All HTML tags exactly: <p>, <strong>, <em>, <ul>, <ol>, <li>, <br>, <code>, <a>
- All numbers, percentages, prices ($60,000, 5%, etc.)
- All ticker symbols (BTCUSDT, ETHUSDT, etc.)
- All formulas, code, URLs, file paths
"""

SYSTEM_PROMPT = (
    "You are a professional Indonesian translator specialising in crypto futures trading "
    "education. You translate prose to natural, fluent Indonesian while preserving every "
    "technical term, HTML tag, number, and code identifier exactly as given.\n\n"
    + GLOSSARY
    + "\n\nTone: trader-to-trader, clear, no-nonsense. Use 'kamu' (informal you) — this is "
      "casual peer education, not formal writing. Indonesian sentence flow should feel "
      "natural to a native reader, not literal word-for-word.\n\n"
      "You receive a lesson JSON. You return ONLY the translated JSON (no preamble, no code "
      "fence). You translate ONLY these fields:\n"
      "  - subtitle (string)\n"
      "  - block.content for blocks of type text/heading/ol\n"
      "  - block.title and block.content for blocks of type callout\n"
      "  - block.headers (every list element) for blocks of type table\n"
      "  - block.rows (every cell, but keep numbers/symbols/code unchanged)\n"
      "  - block.alt and block.caption for blocks of type image\n"
      "All other fields (slug, type, kind, src, etc.) stay byte-identical."
)


def strip_code_fence(text: str) -> str:
    """Best-effort strip of ```json … ``` fences."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def translate_lesson(lesson: dict, attempts: int = 3) -> dict:
    user_prompt = (
        "Translate this lesson JSON into Indonesian per the rules. Return ONLY the JSON:\n\n"
        + json.dumps(lesson, ensure_ascii=False)
    )
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            text, _ = ai_client.send(
                module="training_pdf_id",
                model="claude-sonnet-4-6",
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=16000,
                system=SYSTEM_PROMPT,
            )
            return json.loads(strip_code_fence(text))
        except Exception as e:
            last_err = e
            print(f"    attempt {attempt} failed: {type(e).__name__}: {e}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(2 * attempt)
    raise RuntimeError(f"translation failed after {attempts} attempts: {last_err}")


def translate_all(catalog: list[dict]) -> None:
    """Translate every lesson. Idempotent — skips files already on disk."""
    n_translated = 0
    n_skipped = 0
    n_failed = 0
    for entry in catalog:
        slug = entry["slug"]
        src = LESSON_SRC / f"{slug}.json"
        dst = OUT_DIR / f"{slug}.json"
        if not src.exists():
            print(f"  ! missing source: {slug}")
            n_failed += 1
            continue
        if dst.exists():
            n_skipped += 1
            continue
        print(f"  → {slug}")
        try:
            lesson = json.loads(src.read_text())
            translated = translate_lesson(lesson)
            dst.write_text(json.dumps(translated, ensure_ascii=False, indent=2))
            n_translated += 1
        except Exception as e:
            print(f"    ✗ {e}", file=sys.stderr)
            n_failed += 1
    print(f"\n  translated={n_translated}  skipped(cached)={n_skipped}  failed={n_failed}")
    if n_failed:
        raise RuntimeError(f"{n_failed} lesson(s) failed to translate — re-run to retry")


# ── HTML rendering ──────────────────────────────────────────────────────────
# Dark theme — matches the live training module (training/static/css/training.css)
#   --training-bg:      #0d1117
#   --training-bg2:     #131922
#   --training-bg3:     #1a2030
#   --training-text:    #e7eaf3
#   --training-muted:   #8a93a6
#   --training-border:  #2a3140
#   --training-accent:  #6c63ff
#   --training-accent2: #4fc3f7
#   --training-green:   #26d96b
#   --training-red:     #ef5350
#   --training-yellow:  #ffb300

CSS = """
@page {
    size: A4;
    margin: 18mm 16mm;
    background: #0d1117;
    @bottom-center {
        content: counter(page);
        font-family: -apple-system, sans-serif;
        font-size: 9pt;
        color: #8a93a6;
    }
}
* { box-sizing: border-box; }
html, body {
    background: #0d1117 !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.7;
    color: #e7eaf3;
    margin: 0;
    padding: 0;
}
h1, h2, h3, h4 { color: #e7eaf3; }
h1 { font-size: 28pt; font-weight: 800; letter-spacing: -0.02em; margin: 0 0 6mm 0; }
h2 { font-size: 18pt; font-weight: 700; margin: 8mm 0 4mm 0; letter-spacing: -0.01em; }
h3 { font-size: 13pt; font-weight: 600; margin: 5mm 0 2mm 0; }
h4 { font-size: 11pt; font-weight: 600; color: #b8c0d5; margin: 4mm 0 2mm 0; }
p { margin: 0 0 3mm 0; color: #e7eaf3; }
ul, ol { margin: 0 0 3mm 0; padding-left: 6mm; color: #e7eaf3; }
li { margin-bottom: 1mm; }
strong { font-weight: 600; color: #ffffff; }
em { font-style: italic; }
a { color: #4fc3f7; text-decoration: none; }
code {
    font-family: 'SF Mono', 'Consolas', monospace;
    font-size: 9.5pt;
    background: #1a2030;
    color: #4fc3f7;
    padding: 0.4mm 1.4mm;
    border-radius: 1mm;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 4mm 0 5mm 0;
    font-size: 9.5pt;
    color: #e7eaf3;
}
th {
    text-align: left;
    background: #131922;
    color: #8a93a6;
    padding: 2.5mm 3mm;
    border-bottom: 1px solid #2a3140;
    font-weight: 600;
    font-size: 9pt;
    text-transform: uppercase;
    letter-spacing: 0.03em;
}
td {
    padding: 2.5mm 3mm;
    border-bottom: 1px solid #2a3140;
    vertical-align: top;
}
tr:last-child td { border-bottom: none; }

.callout {
    margin: 5mm 0;
    padding: 4mm 5mm;
    border-radius: 2mm;
    border-left: 3px solid #6c63ff;
    background: rgba(108, 99, 255, 0.08);
}
.callout-title {
    font-weight: 600;
    margin-bottom: 2mm;
    color: #e7eaf3;
    font-size: 9pt;
    letter-spacing: 0.03em;
    text-transform: uppercase;
}
/* Variants matching live: info / key / warn / danger / tip / example */
.callout.kind-info    { border-color: #4fc3f7; background: rgba(79, 195, 247, 0.08); }
.callout.kind-key     { border-color: #26d96b; background: rgba(38, 217, 107, 0.08); }
.callout.kind-tip     { border-color: #26d96b; background: rgba(38, 217, 107, 0.08); }
.callout.kind-warn    { border-color: #ffb300; background: rgba(255, 179, 0, 0.08); }
.callout.kind-warning { border-color: #ffb300; background: rgba(255, 179, 0, 0.08); }
.callout.kind-danger  { border-color: #ef5350; background: rgba(239, 83, 80, 0.08); }
.callout.kind-example { border-color: #ffb300; background: rgba(255, 179, 0, 0.08); }

.lesson { page-break-before: always; }
.lesson:first-of-type { page-break-before: auto; }
.lesson-header {
    border-bottom: 1px solid #2a3140;
    padding-bottom: 4mm;
    margin-bottom: 6mm;
}
.lesson-meta {
    font-size: 9pt;
    color: #8a93a6;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 2mm;
}
.lesson-title {
    font-size: 22pt;
    font-weight: 700;
    letter-spacing: -0.015em;
    color: #e7eaf3;
    margin: 0 0 2mm 0;
}
.lesson-subtitle {
    font-size: 11pt;
    color: #8a93a6;
    margin: 0;
}

.lesson-img {
    display: block;
    max-width: 100%;
    margin: 5mm auto 2mm;
    border-radius: 2mm;
    border: 1px solid #2a3140;
}
.lesson-img-caption {
    font-size: 9pt;
    color: #8a93a6;
    text-align: center;
    margin-bottom: 5mm;
    font-style: italic;
}

.tier-cover {
    page-break-before: always;
    page-break-after: always;
    text-align: center;
    padding-top: 80mm;
}
.tier-cover .tier-num {
    font-size: 12pt;
    color: #6c63ff;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-weight: 600;
    margin-bottom: 4mm;
}
.tier-cover .tier-name {
    font-size: 36pt;
    font-weight: 800;
    color: #e7eaf3;
    margin: 0 0 6mm 0;
    letter-spacing: -0.02em;
}
.tier-cover .tier-desc {
    font-size: 12pt;
    color: #8a93a6;
    max-width: 120mm;
    margin: 0 auto;
}

.cover { text-align: center; padding-top: 55mm; }
.cover .brand {
    font-size: 14pt;
    color: #6c63ff;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 10mm;
}
.cover .title {
    font-size: 40pt;
    font-weight: 800;
    color: #e7eaf3;
    line-height: 1.1;
    margin-bottom: 6mm;
    letter-spacing: -0.025em;
}
.cover .subtitle {
    font-size: 13pt;
    color: #8a93a6;
    max-width: 130mm;
    margin: 0 auto 12mm auto;
    line-height: 1.6;
}
.cover .meta {
    font-size: 10pt;
    color: #8a93a6;
    margin-top: 35mm;
}

.toc {
    page-break-after: always;
    padding-top: 15mm;
}
.toc h2 {
    margin-top: 0;
    margin-bottom: 6mm;
    color: #e7eaf3;
}
.toc-tier {
    margin: 5mm 0 3mm 0;
    font-size: 11pt;
    font-weight: 700;
    color: #6c63ff;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.toc-lesson {
    padding: 0.8mm 0;
    font-size: 10pt;
    color: #e7eaf3;
}
.toc-lesson .num {
    color: #8a93a6;
    margin-right: 3mm;
    display: inline-block;
    min-width: 8mm;
}
"""


def render_block(block: dict) -> str:
    t = block.get("type")
    if t == "heading":
        return f"<h3>{block.get('content', '')}</h3>"
    if t == "text":
        return block.get("content", "")
    if t == "ol":
        content = block.get("content", "")
        # content may contain prose; if empty, skip
        return content if content else ""
    if t == "callout":
        kind = block.get("kind", "info")
        title = block.get("title", "")
        content = block.get("content", "")
        title_html = f'<div class="callout-title">{title}</div>' if title else ""
        return f'<div class="callout kind-{h(kind)}">{title_html}{content}</div>'
    if t == "table":
        headers = block.get("headers", [])
        rows = block.get("rows", [])
        thead = "<thead><tr>" + "".join(f"<th>{c}</th>" for c in headers) + "</tr></thead>"
        tbody = "<tbody>" + "".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
        ) + "</tbody>"
        return f"<table>{thead}{tbody}</table>"
    if t == "image":
        src = block.get("src", "")
        # src is relative to training/static/, e.g. "charts/12-candle-anatomy.png"
        abs_path = (REPO / "training" / "static" / src).resolve()
        alt = block.get("alt", "")
        caption = block.get("caption", "")
        if not abs_path.exists():
            return f'<p><em>[image missing: {src}]</em></p>'
        cap_html = f'<div class="lesson-img-caption">{caption}</div>' if caption else ""
        # Embed as base64 data URI — file:// URLs are blocked by Playwright
        # when using set_content() (no same-origin parent for security).
        import base64
        b64 = base64.b64encode(abs_path.read_bytes()).decode("ascii")
        ext = abs_path.suffix.lstrip(".").lower() or "png"
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml"}.get(ext, "image/png")
        return f'<img class="lesson-img" src="data:{mime};base64,{b64}" alt="{h(alt)}" />{cap_html}'
    return f'<p><em>[unknown block type: {t}]</em></p>'


def render_lesson(entry: dict, lesson: dict) -> str:
    tier_name_id = TIER_META[entry["tier"]][0]
    meta = f"Tier {entry['tier']} · {tier_name_id} · Lesson {entry['order_in_tier']}"
    subtitle = lesson.get("subtitle", "")
    blocks_html = "".join(render_block(b) for b in lesson.get("blocks", []))
    return f"""<section class="lesson" id="lesson-{entry['slug']}">
      <div class="lesson-header">
        <div class="lesson-meta">{meta}</div>
        <h2 class="lesson-title">{h(entry['title'])}</h2>
        {f'<p class="lesson-subtitle">{h(subtitle)}</p>' if subtitle else ''}
      </div>
      {blocks_html}
    </section>"""


def render_tier_cover(tier_num: int) -> str:
    name, desc = TIER_META[tier_num]
    return f"""<section class="tier-cover">
      <div class="tier-num">Tier {tier_num}</div>
      <h2 class="tier-name">{name}</h2>
      <p class="tier-desc">{desc}</p>
    </section>"""


def render_cover() -> str:
    return """<section class="cover">
      <div class="brand">Crypto Trading Academy</div>
      <div class="title">Pelatihan Lengkap Trading Crypto Futures</div>
      <div class="subtitle">Dari pemula sampai mahir — 58 lesson terbagi dalam 6 tier.
        Foundations, Chart Reading, Indicators, Advanced (Wyckoff/SMC),
        Macro &amp; Context, Execution &amp; Journaling.</div>
      <div class="meta">Versi Bahasa Indonesia · Tanpa kuis · Untuk dibaca offline</div>
    </section>"""


def render_toc(catalog: list[dict]) -> str:
    lines = ['<section class="toc"><h2>Daftar Isi</h2>']
    current_tier = 0
    for entry in catalog:
        if entry["tier"] != current_tier:
            current_tier = entry["tier"]
            name, _ = TIER_META[current_tier]
            lines.append(f'<div class="toc-tier">Tier {current_tier} — {name}</div>')
        lines.append(
            f'<div class="toc-lesson">'
            f'<span class="num">{entry["order_in_tier"]:02d}</span>'
            f'{h(entry["title"])}'
            f'</div>'
        )
    lines.append("</section>")
    return "".join(lines)


def build_html(catalog: list[dict]) -> str:
    sections = [render_cover(), render_toc(catalog)]
    current_tier = 0
    for entry in catalog:
        if entry["tier"] != current_tier:
            current_tier = entry["tier"]
            sections.append(render_tier_cover(current_tier))
        translated = json.loads((OUT_DIR / f"{entry['slug']}.json").read_text())
        sections.append(render_lesson(entry, translated))
    body = "\n".join(sections)
    return f"""<!doctype html>
<html lang="id"><head>
<meta charset="utf-8">
<title>Pelatihan Trading Crypto Futures — Bahasa Indonesia</title>
<style>{CSS}</style>
</head><body>
{body}
</body></html>"""


def build_pdf() -> None:
    from playwright.sync_api import sync_playwright
    html_str = OUT_HTML.read_text()
    print(f"  rendering HTML → PDF ({len(html_str):,} chars)")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html_str, wait_until="networkidle")
        # Give base64 images a beat to decode before snapshotting
        page.wait_for_timeout(1000)
        page.pdf(
            path=str(OUT_PDF),
            format="A4",
            margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
            print_background=True,   # critical for dark backgrounds to render
            display_header_footer=False,
            prefer_css_page_size=True,
        )
        browser.close()
    size_mb = OUT_PDF.stat().st_size / 1024 / 1024
    print(f"  ✓ PDF written: {OUT_PDF}  ({size_mb:.1f} MB)")


def main() -> int:
    catalog = json.loads(CATALOG.read_text())
    print(f"=== translating {len(catalog)} lessons into Indonesian ===")
    translate_all(catalog)
    print(f"\n=== building HTML ===")
    html_doc = build_html(catalog)
    OUT_HTML.write_text(html_doc)
    print(f"  wrote: {OUT_HTML}  ({len(html_doc):,} chars)")
    print(f"\n=== rendering PDF ===")
    build_pdf()
    return 0


if __name__ == "__main__":
    sys.exit(main())
