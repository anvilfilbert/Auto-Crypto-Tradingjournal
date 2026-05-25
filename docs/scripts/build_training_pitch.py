"""Generate a 4-page A4 PDF brochure for the training module.

Renders inline HTML via playwright. Output: /tmp/training-brochure.pdf
"""
from playwright.sync_api import sync_playwright
from pathlib import Path
import base64

OUT_PDF = Path("/tmp/training-brochure.pdf")
SHOTS = Path("/tmp/pdf_screenshots")


def img_b64(name):
    data = (SHOTS / name).read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode()


HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  @page {{
    size: A4;
    margin: 0;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif;
    color: #1f2937;
    background: white;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  .page {{
    width: 210mm;
    height: 297mm;
    padding: 16mm 18mm;
    page-break-after: always;
    position: relative;
    overflow: hidden;
  }}
  .page:last-child {{ page-break-after: auto; }}

  /* Header / brand */
  .brand {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 8mm;
    border-bottom: 1px solid #e5e7eb;
    margin-bottom: 8mm;
  }}
  .brand-logo {{
    font-size: 11pt;
    font-weight: 700;
    color: #6c63ff;
    letter-spacing: -0.01em;
  }}
  .brand-logo span {{ color: #1f2937; font-weight: 400; }}
  .brand-meta {{
    font-size: 8pt;
    color: #6b7280;
  }}

  /* Typography */
  h1 {{
    font-size: 32pt;
    font-weight: 800;
    letter-spacing: -0.025em;
    line-height: 1.1;
    color: #111827;
  }}
  h2 {{
    font-size: 22pt;
    font-weight: 700;
    letter-spacing: -0.015em;
    line-height: 1.15;
    color: #111827;
    margin-bottom: 4mm;
  }}
  h3 {{
    font-size: 13pt;
    font-weight: 700;
    color: #1f2937;
    margin-bottom: 2mm;
  }}
  .tagline {{
    font-size: 13pt;
    color: #6b7280;
    line-height: 1.5;
    margin-top: 3mm;
  }}
  p {{
    font-size: 10pt;
    line-height: 1.6;
    color: #374151;
    margin-bottom: 3mm;
  }}
  .lead {{ font-size: 11pt; }}
  strong {{ color: #111827; font-weight: 700; }}
  code {{
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 9pt;
    background: #f3f4f6;
    padding: 1px 5px;
    border-radius: 3px;
    color: #6c63ff;
  }}

  /* Hero section */
  .hero-img {{
    width: 100%;
    border-radius: 8px;
    border: 1px solid #e5e7eb;
    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
    margin-top: 6mm;
  }}

  /* Stats grid */
  .stats {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 4mm;
    margin-top: 6mm;
  }}
  .stat {{
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    padding: 4mm;
    text-align: center;
  }}
  .stat-number {{
    font-size: 22pt;
    font-weight: 800;
    color: #6c63ff;
    line-height: 1;
    margin-bottom: 1.5mm;
  }}
  .stat-label {{
    font-size: 8pt;
    color: #6b7280;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }}

  /* Tier ladder */
  .tier-ladder {{
    margin: 5mm 0;
  }}
  .tier-row {{
    display: flex;
    align-items: center;
    gap: 4mm;
    padding: 3mm 0;
    border-bottom: 1px dotted #e5e7eb;
  }}
  .tier-row:last-child {{ border-bottom: none; }}
  .tier-num {{
    width: 8mm;
    height: 8mm;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    color: white;
    font-size: 10pt;
    flex-shrink: 0;
  }}
  .tier-info {{ flex: 1; }}
  .tier-title {{
    font-size: 10pt;
    font-weight: 700;
    color: #111827;
  }}
  .tier-desc {{
    font-size: 8pt;
    color: #6b7280;
    margin-top: 0.5mm;
  }}
  .tier-count {{
    font-size: 9pt;
    color: #6b7280;
    font-weight: 600;
    flex-shrink: 0;
  }}

  /* Two-column layout */
  .row {{
    display: flex;
    gap: 6mm;
    margin-bottom: 5mm;
  }}
  .col {{ flex: 1; }}
  .col-2 {{ flex: 2; }}

  /* Feature box */
  .feature-box {{
    background: #f9fafb;
    border-left: 3px solid #6c63ff;
    padding: 4mm;
    border-radius: 0 4px 4px 0;
    margin-bottom: 4mm;
  }}
  .feature-box h3 {{ font-size: 10pt; margin-bottom: 2mm; color: #6c63ff; }}
  .feature-list {{
    list-style: none;
    font-size: 9pt;
    line-height: 1.7;
    color: #374151;
  }}
  .feature-list li {{
    padding-left: 4mm;
    position: relative;
  }}
  .feature-list li::before {{
    content: "✓";
    position: absolute;
    left: 0;
    color: #26d96b;
    font-weight: 700;
  }}

  /* Inline screenshot */
  .shot {{
    width: 100%;
    border-radius: 6px;
    border: 1px solid #e5e7eb;
    box-shadow: 0 2px 12px rgba(0,0,0,0.06);
  }}
  .shot-cap {{
    font-size: 8pt;
    color: #6b7280;
    text-align: center;
    margin-top: 2mm;
    font-style: italic;
  }}

  /* Install code block */
  .code-block {{
    background: #0d1117;
    color: #e7eaf3;
    padding: 5mm;
    border-radius: 6px;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 9pt;
    line-height: 1.6;
    margin: 4mm 0;
    overflow: hidden;
  }}
  .code-block .cmt {{ color: #8a93a6; }}
  .code-block .cmd {{ color: #4fc3f7; }}
  .code-block .arg {{ color: #ffb300; }}

  /* Callout / highlight */
  .callout {{
    background: linear-gradient(135deg, #6c63ff15, #4fc3f715);
    border: 1px solid #6c63ff40;
    border-radius: 8px;
    padding: 5mm;
    margin: 5mm 0;
  }}
  .callout-title {{
    font-size: 10pt;
    font-weight: 700;
    color: #6c63ff;
    margin-bottom: 2mm;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .callout p {{ font-size: 9.5pt; margin-bottom: 2mm; color: #1f2937; }}
  .callout p:last-child {{ margin-bottom: 0; }}

  /* CTA section */
  .cta {{
    background: #111827;
    color: white;
    padding: 8mm;
    border-radius: 8px;
    text-align: center;
    margin-top: 6mm;
  }}
  .cta h3 {{
    font-size: 16pt;
    color: white;
    margin-bottom: 3mm;
  }}
  .cta p {{
    font-size: 10pt;
    color: #d1d5db;
    margin-bottom: 4mm;
  }}
  .cta-link {{
    display: inline-block;
    background: #6c63ff;
    color: white;
    padding: 3mm 8mm;
    border-radius: 4px;
    font-size: 10pt;
    font-weight: 600;
    text-decoration: none;
    font-family: "SF Mono", Menlo, monospace;
  }}

  /* Footer */
  .footer {{
    position: absolute;
    bottom: 8mm;
    left: 18mm;
    right: 18mm;
    font-size: 7.5pt;
    color: #9ca3af;
    display: flex;
    justify-content: space-between;
    border-top: 1px solid #e5e7eb;
    padding-top: 3mm;
  }}

  /* Inline tier colors */
  .t1 {{ background: #26d96b; }}
  .t2 {{ background: #4fc3f7; }}
  .t3 {{ background: #b388ff; }}
  .t4 {{ background: #ef5350; }}
  .t5 {{ background: #ffb300; }}
  .t6 {{ background: #6b7280; }}
</style>
</head>
<body>

<!-- ═══════════════════════ PAGE 1 — HERO ═══════════════════════ -->
<section class="page">
  <header class="brand">
    <div class="brand-logo">🎓 Trading Journal <span>— Training Module</span></div>
    <div class="brand-meta">Open-source · Self-hosted · Standalone</div>
  </header>

  <h1>Crypto Trading,<br>Taught Like It Matters.</h1>
  <p class="tagline">
    A 51-lesson interactive course that takes a complete beginner from
    "what is a futures contract?" to a working personal trading system —
    with visual diagrams, scored quizzes, and a tone that respects your time.
  </p>

  <img class="hero-img" src="{img_b64('01-path-hero.png')}" alt="Path view with tier tree">

  <div class="stats">
    <div class="stat">
      <div class="stat-number">51</div>
      <div class="stat-label">Graded Units</div>
    </div>
    <div class="stat">
      <div class="stat-number">~520</div>
      <div class="stat-label">Quiz Questions</div>
    </div>
    <div class="stat">
      <div class="stat-number">24</div>
      <div class="stat-label">Visual Diagrams</div>
    </div>
    <div class="stat">
      <div class="stat-number">6</div>
      <div class="stat-label">Tiers · Beginner→Pro</div>
    </div>
  </div>

  <div class="footer">
    <span>Page 1 of 4 — Overview</span>
    <span>github.com/anvilfilbert/Auto-Crypto-Tradingjournal</span>
  </div>
</section>

<!-- ═══════════════════════ PAGE 2 — CURRICULUM ═══════════════════════ -->
<section class="page">
  <header class="brand">
    <div class="brand-logo">🎓 Trading Journal <span>— Training Module</span></div>
    <div class="brand-meta">Six tiers · 51 graded units</div>
  </header>

  <h2>Structure: from survival math to professional discipline.</h2>
  <p class="lead">
    Each tier builds on the previous. You can't pass Tier 3 (Indicators) without
    first mastering Tier 2 (Chart Reading) — because indicators on top of
    chart-reading <em>blindness</em> just adds noise, not signal.
    The unlock chain isn't a gimmick; it mirrors how actual trader competence develops.
  </p>

  <div class="tier-ladder">
    <div class="tier-row">
      <div class="tier-num t1">1</div>
      <div class="tier-info">
        <div class="tier-title">Foundations</div>
        <div class="tier-desc">Spot vs perp, leverage, position sizing, risk %, psychology — the survival math that prevents account-killing mistakes.</div>
      </div>
      <div class="tier-count">11 units</div>
    </div>
    <div class="tier-row">
      <div class="tier-num t2">2</div>
      <div class="tier-info">
        <div class="tier-title">Chart Reading</div>
        <div class="tier-desc">Candle anatomy, reversal patterns, support & resistance, trendlines, market structure, multi-timeframe analysis.</div>
      </div>
      <div class="tier-count">10 units</div>
    </div>
    <div class="tier-row">
      <div class="tier-num t3">3</div>
      <div class="tier-info">
        <div class="tier-title">Indicators</div>
        <div class="tier-desc">RSI (regime-aware), MACD, Bollinger Bands, volume, ADX, Stochastic, Fibonacci, ATR-based stops — used right, not as black boxes.</div>
      </div>
      <div class="tier-count">9 units</div>
    </div>
    <div class="tier-row">
      <div class="tier-num t4">4</div>
      <div class="tier-info">
        <div class="tier-title">Advanced</div>
        <div class="tier-desc">Order flow / CVD, liquidation maps, Wyckoff phases, Spring & Upthrust, Order Blocks, FVGs, SMC (with honest commentary on what's marketing).</div>
      </div>
      <div class="tier-count">9 units</div>
    </div>
    <div class="tier-row">
      <div class="tier-num t5">5</div>
      <div class="tier-info">
        <div class="tier-title">Macro &amp; Context</div>
        <div class="tier-desc">DXY, VIX, S&amp;P futures, BTC dominance, Fear &amp; Greed, news events, correlation regimes — crypto doesn't trade in a vacuum.</div>
      </div>
      <div class="tier-count">6 units</div>
    </div>
    <div class="tier-row">
      <div class="tier-num t6">6</div>
      <div class="tier-info">
        <div class="tier-title">Execution &amp; Journaling + Capstone</div>
        <div class="tier-desc">Pre-trade Apgar scorecard, pre-session readiness, journaling, weekly review, building your own system. Full multi-TF walkthroughs.</div>
      </div>
      <div class="tier-count">6 units</div>
    </div>
  </div>

  <div class="row">
    <div class="col">
      <h3>Every lesson includes:</h3>
      <ul class="feature-list">
        <li>5-10 minute trader-to-trader read</li>
        <li>Visual diagrams where they help (24 across the course)</li>
        <li>Comparison tables for trade-offs</li>
        <li>Worked examples with real numbers</li>
        <li>Common-mistakes callouts</li>
        <li>10-question quiz with explanations on wrong answers</li>
      </ul>
    </div>
    <div class="col">
      <img class="shot" src="{img_b64('02-lesson-with-diagram.png')}" alt="Lesson with diagram">
      <div class="shot-cap">Lesson 12 — candle anatomy with labeled diagram</div>
    </div>
  </div>

  <div class="footer">
    <span>Page 2 of 4 — Curriculum</span>
    <span>github.com/anvilfilbert/Auto-Crypto-Tradingjournal</span>
  </div>
</section>

<!-- ═══════════════════════ PAGE 3 — ACTIVE LEARNING ═══════════════════════ -->
<section class="page">
  <header class="brand">
    <div class="brand-logo">🎓 Trading Journal <span>— Training Module</span></div>
    <div class="brand-meta">Active learning, not passive consumption</div>
  </header>

  <h2>You don't pass by reading. You pass by understanding.</h2>
  <p class="lead">
    Quizzes are the gate. 10 multiple-choice questions per lesson, written to
    test concept understanding — not memorization of trivia. Pass at 8/10 to
    unlock the next lesson. Tier finals raise the bar (10/12). The capstone exam
    is 25 questions pulled from across the curriculum.
  </p>

  <div class="row">
    <div class="col">
      <img class="shot" src="{img_b64('04-quiz-view.png')}" alt="Quiz view">
      <div class="shot-cap">Quiz interface — instant feedback, explanations on every wrong answer</div>
    </div>
    <div class="col">
      <h3>How learning actually sticks:</h3>
      <ul class="feature-list">
        <li><strong>Forced articulation</strong> — multiple choice forces you to commit to a specific interpretation</li>
        <li><strong>Explanations on miss</strong> — every wrong answer shows you why, with reference to the lesson concept</li>
        <li><strong>Tier finals at 10/12</strong> — higher bar than per-lesson 8/10 because Tier-level concepts are load-bearing</li>
        <li><strong>Retry without time penalty</strong> — review the explanations, take it again, learn the gap</li>
        <li><strong>Progress saved server-side</strong> — pick up where you left off on any device</li>
        <li><strong>One-click reset</strong> — wipe progress for a fresh start or to hand off to a friend</li>
      </ul>
    </div>
  </div>

  <div class="callout">
    <div class="callout-title">Configurable for any audience</div>
    <p>
      <strong>Strict mode</strong> (<code>unlock_mode: enforce</code>): the production
      flow — lessons unlock only after the previous quiz is passed. The intended
      sequential learner experience.
    </p>
    <p>
      <strong>Open mode</strong> (<code>unlock_mode: open</code>): all lessons clickable
      regardless of progress. Useful for review-mode, content authoring, or just
      letting a curious visitor browse freely. Progress badges still show real
      state. One-line change in <code>training/config.yaml</code>; no restart needed.
    </p>
  </div>

  <div class="row">
    <div class="col">
      <img class="shot" src="{img_b64('05-wyckoff-diagram.png')}" alt="Wyckoff cycle diagram">
      <div class="shot-cap">Tier 4 — Wyckoff cycle visualization</div>
    </div>
    <div class="col">
      <img class="shot" src="{img_b64('06-apgar-scoreboard.png')}" alt="Trade Apgar scoreboard">
      <div class="shot-cap">Tier 6 — Trade Apgar pre-trade scorecard</div>
    </div>
  </div>

  <div class="footer">
    <span>Page 3 of 4 — Active Learning</span>
    <span>github.com/anvilfilbert/Auto-Crypto-Tradingjournal</span>
  </div>
</section>

<!-- ═══════════════════════ PAGE 4 — INSTALL & EXTEND ═══════════════════════ -->
<section class="page">
  <header class="brand">
    <div class="brand-logo">🎓 Trading Journal <span>— Training Module</span></div>
    <div class="brand-meta">For builders, extenders, and forkers</div>
  </header>

  <h2>Stand-alone. Two dependencies. Five-line install.</h2>
  <p class="lead">
    The training module is a self-contained Flask package. It mounts inside our
    full trading journal at <code>/training</code>, OR runs on its own Pi/laptop/server
    as an independent app. Zero shared state with the parent project.
  </p>

  <div class="code-block">
<span class="cmt"># On a fresh machine (Raspberry Pi, Ubuntu, Mac, anything with Python 3.10+):</span>
<span class="cmd">git clone</span> <span class="arg">https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal.git</span>
<span class="cmd">cd</span> Auto-Crypto-Tradingjournal       <span class="cmt"># ← parent of training/</span>
<span class="cmd">pip3 install</span> -r training/requirements.txt   <span class="cmt"># flask + pyyaml, ~10s</span>
<span class="cmd">python3 -m</span> training --port 5050         <span class="cmt"># http://your-pi:5050/</span>
  </div>

  <div class="row">
    <div class="col">
      <h3>Architecture for collaborators:</h3>
      <ul class="feature-list">
        <li><strong>Zero imports from the parent journal</strong> — verifiable by grep</li>
        <li><strong>Own SQLite</strong> (<code>training.db</code>), separate from journal data</li>
        <li><strong>CSS scoped under <code>.training-*</code></strong> — no global pollution if embedded elsewhere</li>
        <li><strong>JS in its own module</strong> — no shared globals</li>
        <li><strong>No external API calls</strong>, no env vars, no credentials, no telemetry</li>
        <li><strong>Idempotent diagram generator</strong> + wiring script — add new visuals in minutes</li>
        <li><strong>2 MB tarball</strong> — copy the folder anywhere and it works</li>
      </ul>
    </div>
    <div class="col">
      <h3>Easy to extend:</h3>
      <ul class="feature-list">
        <li><strong>Lesson content</strong> in plain JSON — markdown-like blocks</li>
        <li><strong>Quizzes</strong> in plain YAML — questions, answers, explanations</li>
        <li><strong>Diagrams</strong> generated by matplotlib script (one function = one diagram)</li>
        <li><strong>Catalog</strong> is one JSON file — add a lesson, append an entry</li>
        <li><strong>Two reusable scripts</strong>: <code>generate_chart_diagrams.py</code> + <code>insert_diagrams_into_lessons.py</code> handle the boilerplate</li>
      </ul>
    </div>
  </div>

  <div class="callout">
    <div class="callout-title">Fork-friendly by design</div>
    <p>
      We built this to be the curriculum BASE for whoever wants to teach
      trading their way. Fork it. Rewrite Tier 4 with your own SMC takes.
      Add a Tier 7 on options. Swap the dark theme. Translate every lesson.
      Embed inside your community's Discord bot via the JSON API. The
      coupling rules and content/code separation make all of this easy.
    </p>
  </div>

  <div class="cta">
    <h3>Want to see it live? Spin it up in 5 minutes.</h3>
    <p>Tested on Raspberry Pi 5 (Bookworm), Ubuntu 24, macOS Sonoma. Python 3.10+ and a free port is all you need.</p>
    <a class="cta-link">github.com/anvilfilbert/Auto-Crypto-Tradingjournal</a>
  </div>

  <div class="footer">
    <span>Page 4 of 4 — Install &amp; Extend</span>
    <span>Open source · No API keys · No registration · 2 deps</span>
  </div>
</section>

</body>
</html>
"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(HTML, wait_until="networkidle")
        page.pdf(
            path=str(OUT_PDF),
            format="A4",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        browser.close()
    print(f"  ✓ {OUT_PDF}  ({OUT_PDF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
