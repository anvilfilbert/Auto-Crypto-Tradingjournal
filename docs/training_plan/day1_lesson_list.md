# Training Module — Day 1 Lesson List

Sign-off document. Review and mark anything to add / remove / reorder.

**Curriculum**: 45 lessons + 5 tier finals + 1 capstone = 51 graded units
**Per lesson**: 5-10 min read + 2-3 min quiz (10 questions, pass 8/10)
**Tone**: Trader-to-trader, direct, opinionated
**Quiz fail behavior**: Show explanations for missed questions, then retry
**Unlock**: Strict within a tier (must pass to advance), all tiers visible
**Final exam**: 25-question personalized exam after capstone, generated from your weakest topics

Notation: `// widgets: X, Y` lists interactive widgets the lesson embeds. `// tags:` is the topic tag list used for I3 personalized final and spaced repetition.

---

## 🟢 TIER 1 — Foundations (10 lessons + final)

| # | Title | One-line summary | Widgets | Tags |
|---|---|---|---|---|
| 1 | **Spot vs Futures vs Perpetuals** | How crypto derivatives differ from spot. Why most retail trades perps and what funding / expiry / leverage mean for you. | — | market_basics, derivatives |
| 2 | **Picking a broker** | Fees, regulation, withdrawal speed, 2FA security. What to look for, what to avoid. | — | market_basics, security |
| 3 | **Order types: Market / Limit / Stop / Stop-Limit / OCO** | When each fits. Why market orders eat your edge on low-liquidity coins. | — | orders, execution |
| 4 | **Long vs Short — what shorting really is** | Borrowing-then-selling, profiting from falls, the asymmetry of long-short risk in crypto. | — | directionality, derivatives |
| 5 | **Fees, funding rates, slippage** | The hidden costs that erode small wins. How a 2-hour funding payment can kill a tight trade. | — | costs, funding |
| 6 | **Risk per trade — the 1-2% rule** | The single most important number you control. Math behind why 50% loss requires 100% recovery. | — | risk, position_sizing |
| 7 | **Position sizing — SL distance + risk % → contract count** | The arithmetic: `size = (equity × risk%) / SL_distance`. Worked example, then practice. | **Position Size Calculator** | position_sizing, math |
| 8 | **Leverage 1x vs 5x vs 100x — isolated vs cross** | Leverage doesn't add risk; SL does. How cross margin wipes your account when one position blows. Liquidation math. | **Leverage Visualizer** | leverage, risk |
| 9 | **Trading psychology basics: FOMO, revenge, tilt** | The three errors that destroy edge. Why you should never re-enter immediately after a stop-out. | — | psychology |
| 10 | **Paper → small-size → full-size progression** | Why backtest passes ≠ paper passes ≠ live passes. The 50/50/200 progression rule. | — | progression, journaling |

**Tier 1 Final** — 12 cumulative questions covering risk math, leverage, order types, psychology.

---

## 🔵 TIER 2 — Chart Reading (9 lessons + final)

| # | Title | One-line summary | Widgets | Tags |
|---|---|---|---|---|
| 11 | **Candle anatomy + what wicks really say** | Body = settled fight, wick = rejected attempt. Long upper wick at resistance = sellers won the round. | — | candles, basics |
| 12 | **Bullish reversal candles** | Hammer, bullish engulfing, morning star, piercing. What context turns a hammer from "buy signal" to noise. | **Candle Pattern Identifier** | candles, reversal_patterns |
| 13 | **Bearish reversal candles** | Shooting star, bearish engulfing, evening star, dark cloud. Why context (location + volume) outweighs shape. | **Candle Pattern Identifier** | candles, reversal_patterns |
| 14 | **Indecision candles: doji, spinning top** | When the market is undecided. Why a doji at support is bullish, but at the middle of a range is meaningless. | — | candles, indecision |
| 15 | **Gaps and what they mean (FVG primer)** | Why gaps are rare in 24/7 markets but real on thin opens. Fair Value Gap concept introduced. | — | candles, gaps, fvg |
| 16 | **Support & Resistance — drawing and validating** | How to draw, how many touches make a level "valid", why round numbers matter. | **Draw Support/Resistance** | structure, support_resistance |
| 17 | **Trendlines — drawing, validating, when they break** | Connecting swing highs/lows, the 3-touch rule, what break-and-retest means. | **Draw Trendline** | structure, trendlines |
| 18 | **Market structure — HH/HL vs LH/LL, BoS, CHoCH** | The cleanest framework for identifying trend. Break of Structure vs Change of Character. | **Spot the Structure** | structure, trend |
| 19 | **Multi-timeframe analysis: HTF bias → LTF entry** | Trade WITH the higher TF, time the entry on a smaller one. The 1D bias / 4H confirmation / 1H entry pattern. | — | structure, multi_tf |

**Tier 2 Final** — 12 cumulative questions: candle recognition, S/R, trendline drawing, multi-TF logic.

---

## 🟣 TIER 3 — Indicators (8 lessons + final)

| # | Title | One-line summary | Widgets | Tags |
|---|---|---|---|---|
| 20 | **Moving averages: SMA / EMA, golden/death cross, dynamic S/R** | Why EMA reacts faster, why the 200-MA is sacred, how MAs become dynamic support. | — | indicators, moving_averages |
| 21 | **RSI — overbought/oversold, regime-aware reading, failure swings, divergences** | Why "overbought" in a trend means nothing. The regime framework. Failure swings as reversal signals. | **RSI Regime Detector**, **Divergence Spotter** | indicators, rsi, divergence |
| 22 | **MACD — signal cross, histogram, divergence** | The momentum gauge. Why the histogram tells you more than the line cross. | **Divergence Spotter** | indicators, macd, divergence |
| 23 | **Bollinger Bands — squeeze, expansion, mean reversion vs breakout** | Volatility expressed as a chart. Why a squeeze is "the calm before the move." | — | indicators, bollinger, volatility |
| 24 | **Volume + VWAP — "no volume = no signal"** | The truth-teller. How VWAP acts as institutional fair-value pivot. | — | indicators, volume, vwap |
| 25 | **ADX & Stochastic — trend strength and extremes-within-trend** | ADX above 25 = trending. Stoch in an uptrend stays "overbought" forever. | — | indicators, adx, stoch |
| 26 | **Fibonacci retracements + extensions** | Why 0.5 and 0.618 are magnets. How to use 1.272 and 1.618 for profit targets. | — | indicators, fibonacci |
| 27 | **ATR — measuring volatility, sizing SLs by ATR** | The objective stop-distance. Why a fixed % SL kills you on volatile coins. | **ATR Stop Calculator** | indicators, atr, volatility |

**Tier 3 Final** — 12 cumulative questions: indicator regimes, divergence types, fib levels, ATR sizing.

---

## 🔴 TIER 4 — Advanced (8 lessons + final)

| # | Title | One-line summary | Widgets | Tags |
|---|---|---|---|---|
| 28 | **Order flow basics: CVD, delta, why aggression matters** | Beneath the candle: who hits the bid vs lifts the offer. CVD as the truth behind price. | — | advanced, order_flow |
| 29 | **Liquidation maps — stop hunts, why retail SLs cluster** | Where stops sit, why whales hunt them, how to NOT put yours there. | — | advanced, liquidations |
| 30 | **Wyckoff phases: accumulation → markup → distribution → markdown** | The 4-phase cycle. How to spot which phase you're in, why retail enters at the END of markup. | **Wyckoff Phase Tagger** | advanced, wyckoff |
| 31 | **Wyckoff Spring & Upthrust — failed-break reversals** | The most reliable reversal pattern: a fake break that traps the wrong-side participants. | **Wyckoff Phase Tagger** | advanced, wyckoff, reversal_patterns |
| 32 | **Order Blocks, Fair Value Gaps, Liquidity Pools** | The institutional-flow concepts. What an order block actually is (and isn't). | **Order Block Hunter** | advanced, order_blocks, fvg |
| 33 | **Smart Money Concepts overview — what's signal, what's narrative** | A pragmatic look at SMC: parts that work, parts that are repackaged classic TA. | — | advanced, smc |
| 34 | **Multi-exchange divergence (SMT) — why cross-checking matters** | When BTC prints a new high on Binance but Coinbase doesn't = warning. The arbitrage edge. | — | advanced, smt |
| 35 | **Funding rate as sentiment indicator** | Extreme positive funding = longs trapped = imminent flush. Reading funding without overfitting. | — | advanced, funding, sentiment |

**Tier 4 Final** — 12 cumulative questions: order flow, Wyckoff phases, liquidation hunts, SMC vs hype.

---

## 🟡 TIER 5 — Macro & Context (5 lessons + final)

| # | Title | One-line summary | Widgets | Tags |
|---|---|---|---|---|
| 36 | **The macro stack — DXY, VIX, S&P 500 futures, why crypto reacts** | When risk-off hits, all risk assets fall together. The hierarchy: equity → crypto majors → alts. | **Macro Snapshot Reader** | macro, dxy, vix |
| 37 | **BTC dominance & altcoin seasons — when to size up alts** | How to read BTC.D, what "alt season" actually is, and when not to chase it. | **Macro Snapshot Reader** | macro, btc_dominance |
| 38 | **Fear & Greed index — contrarian reading** | Why "extreme fear" = best buying opportunity, "extreme greed" = worst time to add. The 25/75 inflection points. | **Macro Snapshot Reader** | macro, sentiment, contrarian |
| 39 | **News-event trading: FOMC, CPI, NFP — what to do (or not)** | Why most traders should FLATTEN before high-impact macro. The "don't fight the print" rule. | — | macro, news_events |
| 40 | **Correlation regimes — when crypto decouples from equities** | How correlation runs in cycles. Why "BTC is digital gold" only works in some regimes. | — | macro, correlation |

**Tier 5 Final** — 8 cumulative questions: macro stack hierarchy, contrarian indicators, news-event handling.

---

## ⚪ TIER 6 — Execution & Journaling (5 lessons + capstone)

| # | Title | One-line summary | Widgets | Tags |
|---|---|---|---|---|
| 41 | **Pre-trade Trade Apgar — 5-question scorecard** | The "Apgar score" for trades. Pass ≥ 7, no zeros. This single check kills 80% of impulse trades. | — | execution, trade_apgar |
| 42 | **Pre-session readiness — mood / sleep / PnL / prep** | Trading drunk, tired, or after a 4-loss streak = predictable disaster. The red/yellow/green system. | — | execution, readiness |
| 43 | **Trade journaling — what to record, what NOT to** | The fields that matter (setup, R, MAE, MFE, why), the fields that don't ("the market is rigged"). | — | execution, journaling |
| 44 | **Reviewing your trades — finding patterns, weekly post-mortem** | How to review without becoming a self-critic. The "what would I do again / different" frame. | — | execution, review |
| 45 | **Building your own system — turning lessons into rules** | How to convert "I notice I lose more on Mondays" into a hard rule. The system-from-data loop. | — | execution, system_building |

**Capstone** — Full multi-TF walkthrough of one real winning trade + one real losing trade, dissected with every concept covered. Then a **personalized 25-question exam** pulled from YOUR weakest topics (per quiz history per I3).

---

## Widget coverage

12 widgets total. Each is reused across multiple lessons:

| Widget | Lessons that use it |
|---|---|
| Position Size Calculator | 7 |
| Leverage Visualizer | 8 |
| Candle Pattern Identifier | 12, 13 |
| Draw Support/Resistance | 16 |
| Draw Trendline | 17 |
| Spot the Structure | 18 |
| RSI Regime Detector | 21 |
| Divergence Spotter | 21, 22 |
| ATR Stop Calculator | 27 |
| Wyckoff Phase Tagger | 30, 31 |
| Order Block Hunter | 32 |
| Macro Snapshot Reader | 36, 37, 38 |

## Topic tags (for I3 personalized final)

`market_basics`, `derivatives`, `security`, `orders`, `execution`, `directionality`, `costs`, `funding`, `risk`, `position_sizing`, `math`, `leverage`, `psychology`, `progression`, `journaling`, `candles`, `basics`, `reversal_patterns`, `indecision`, `gaps`, `fvg`, `structure`, `support_resistance`, `trendlines`, `trend`, `multi_tf`, `indicators`, `moving_averages`, `rsi`, `divergence`, `macd`, `bollinger`, `volatility`, `volume`, `vwap`, `adx`, `stoch`, `fibonacci`, `atr`, `advanced`, `order_flow`, `liquidations`, `wyckoff`, `order_blocks`, `smc`, `smt`, `sentiment`, `macro`, `dxy`, `vix`, `btc_dominance`, `contrarian`, `news_events`, `correlation`, `trade_apgar`, `readiness`, `review`, `system_building`

55 distinct tags. The personalized final pulls questions weighted toward whichever 5-10 topics you scored worst on across all quizzes.

---

## Your turn

Please review and mark anything to:
- **Add** — a topic I missed
- **Remove** — a topic you think is filler
- **Reorder** — if the sequence feels off
- **Reword** — if a one-liner doesn't read right

When the list is signed off, Day 2 starts scaffolding `training/` as the standalone Flask package + the first lesson (T1 #1 Spot vs Futures vs Perpetuals) as a proof-of-shape.
