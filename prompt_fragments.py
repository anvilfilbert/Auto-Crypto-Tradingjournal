"""
Shared Claude prompt text blocks. Import instead of copy-pasting.
Each token saved here saves it on every single AI call.
"""

SCORING_SCALE = """SCORING SCALE:
5 — Moderate: mixed signals, borderline — not worth entering without improvement
6 — Acceptable: clear bias + valid level, SL structural, R:R ≥ 2:1
7 — Good: multiple aligned signals, structural entry + SL, R:R ≥ 2.5:1
8 — Strong: ≥3 signals aligned, clean S/R entry, structural SL, R:R ≥ 3:1
9 — Excellent: strong setup — ≥3 signals aligned across 2+ timeframes, R:R ≥ 3:1, no rulebook conflict
10 — Conviction: same as 9 plus volume confirmation OR a clean break of a multi-month level
USE THE FULL RANGE — score 9-10 was never used in our 111-trade history because the previous rubric required 'textbook' / 'near-ideal' which is unfalsifiable. The new bar is achievable: if the setup has multi-TF alignment + structural anchor + R:R ≥ 3, that's a 9.""".strip()

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
- BTC 24h direction matters: BTC down >2% AND BTC.D rising → broad risk-off, favor Short on alts
- BTC 24h direction matters: BTC up >2% AND OTHERS.D rising → alt rotation, favor Long on alts
- Read VIX, BTC.D, OTHERS.D, ES1! as raw structural facts — they describe the regime; do not invent
  a "risk-on" or "buy the dip" narrative from any single indicator.
- Fear & Greed is shown for context but is NOT a scoring input (paused 2026-06-01).""".strip()

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
