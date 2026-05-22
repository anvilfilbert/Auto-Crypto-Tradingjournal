"""
Shared Claude prompt text blocks. Import instead of copy-pasting.
Each token saved here saves it on every single AI call.
"""

SCORING_SCALE = """SCORING SCALE:
5 — Moderate: mixed signals, borderline — not worth entering without improvement
6 — Acceptable: clear bias + valid level, SL structural, R:R ≥ 2:1
7 — Good: multiple aligned signals, structural entry + SL, R:R ≥ 2.5:1
8 — Strong: ≥3 signals aligned, clean S/R entry, structural SL, R:R ≥ 3:1
9 — Excellent: near-ideal — all criteria met, multi-TF alignment, R:R ≥ 3.5:1
10 — Perfect: textbook chart pattern, volume confirmation, ideal entry timing, R:R ≥ 4:1""".strip()

LEVEL_PROXIMITY_RULES = """LEVEL PROXIMITY DEFINITIONS (use when scoring):
- Entry ≤ 0.5× ATR from structural level → strong anchor, no penalty
- Entry 0.5–1.0× ATR from structural level → acceptable, note it
- Entry > 1.0× ATR from nearest level → structural anchor missing → score ≤ 6
- SL < 1.0× ATR from entry → inside noise → score ≤ 6
- R:R < 2:1 → score ≤ 6; R:R ≥ 2.5:1 for score 7+; R:R ≥ 3.5:1 for score 9+
- LONG setup in premium zone (price > midpoint of nearest S/R range) → reduce score by 1
- SHORT setup in discount zone (price < midpoint of nearest S/R range) → reduce score by 1
- Midpoint = (nearest resistance + nearest support) / 2; skip if no S/R levels available""".strip()

MARKET_CONTEXT_RULES = """MARKET CONTEXT WEIGHTING:
- Funding rate > 0.05% in trade direction → reduce score by 1 (crowd on-side, squeeze risk)
- Funding rate > 0.1% in trade direction → reduce score by 2 (extremely crowded)
- Funding rate opposite direction → slight tailwind, can note as positive factor
- Fear & Greed < 20 (Extreme Fear): long bias gets +0.5; short bias gets −0.5
- Fear & Greed > 80 (Extreme Greed): long bias gets −0.5; short bias gets +0.5""".strip()

DRAW_ON_LIQUIDITY_RULES = """TAKE-PROFIT TARGETING:
Prefer TP targets that coincide with visible liquidity pools — equal highs/lows,
prior swing highs/lows, previous ATH/ATL, or untested fair value gaps — rather
than arbitrary R:R multiples. Name the specific level and why liquidity rests there.
A TP at a swing high where stop-losses cluster is higher quality than a round-number TP.

TP1 MINIMUM DISTANCE — HARD RULE:
TP1 must be at LEAST 1.0 × ATR_4H away from entry. A TP1 closer than 1×ATR will
print on noise alone — empirically that pattern produces a 96% hit rate but
average winners ~$10 against average losers ~$21 (1:2 R:R against the trader).
If the nearest valid liquidity level is closer than 1×ATR, either:
  - move TP1 to the NEXT structural level past 1×ATR, or
  - keep TP1 there but explicitly justify why it's still a high-quality target
    in tp1_rationale (e.g. 'pre-event close before macro release').
TP2 should be at least 2.0 × ATR_4H to reward leaving runners on.""".strip()
