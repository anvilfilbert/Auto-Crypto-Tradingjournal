# Deep Research — Quant Additions for Crypto-Futures Auto-Trading

Saved 2026-05-31. Spawned via research agent; full text below.

> **Item code in this doc:** `R-1` through `R-10` (Research top-10).
> Cross-doc references use the prefix codes: `L-N` (Learning), `A-X` (Agents), `N-N` (Noise).

## 0. Cross-doc dependencies

This document is one of four in the auto_ai concept set:
- `AUTO_AI_LEARNING_ARCHITECTURE.md` — the learner consumes R-N metrics
- `AUTO_AI_SPECIALIZED_AGENTS.md` — agents synthesize R-N signals
- `AUTO_AI_NOISE_DETECTION.md` — noise gates use R-2 (microstructure) and R-7 (VPIN)

**Hard dependencies to other docs:**
- `R-7` (VPIN pipeline) is the **single build** consumed by `N-4` (VPIN gate), `A-E` (Cascade Predictor), and `L-6` (Pattern learners)
- `R-1` (quantstats KPIs) feed the Stats UI panel and `L-5` (Risk learners) as a measurement layer
- `R-4` (CUSUM/Page-Hinkley) feed the `L-N` learner's edge-decay detection
- `R-9` (Fractional Kelly) is consumed by `L-5` (Risk learners) as the sizing formula
- `R-5` (Bayesian credible intervals) gates ALL `L-N` learner updates

## Top-10 priority (highest ROI first)

1. **R-1 quantstats integration** — trivial install, gives DSR + K-ratio + Ulcer + Omega + Tail + GPR + Information Ratio in one afternoon. Best ROI item.
2. **R-2 Volatility targeting position size** — 1 day, no new deps. Expected +20-30% Sharpe at zero return cost.
3. **R-3 Funding-rate-adjusted P&L + liquidation-distance score** — 1 day combined. Uses Coinalyze + Bitget data already pulled.
4. **R-4 CUSUM + Page-Hinkley on per-setup expectancy** — 1 day. Catches strategy degradation weeks earlier than rulebook does.
5. **R-5 Bayesian posterior expectancy with credible interval** — 1 day. Stops decisions on 12-trade samples.
6. **R-6 arch + GARCH-driven dynamic SL distance** — 1 week. Forward-looking vol > lookback ATR.
7. **R-7 VPIN from Binance aggTrades** — 1 week. Empirically validated 2025 crypto signal; free data. **(Shared build with N-4 and A-E.)**
8. **R-8 vectorbt + Optuna walk-forward sweep** — 1 week. Statistically sound parameter search.
9. **R-9 Fractional Kelly per archetype** — 1 week. Dynamic 0.5R-1.5R sizing per archetype.
10. **R-10 lifelines survival model of trade hold time** — 1 week. Conditional median-time-to-SL.

Ranked LOWER: NautilusTrader full port (1 month), FinRL RL agent (overfitting traps), paid Glassnode/CryptoQuant/Hyblock until free-tier exhausted, Amberdata (institutional pricing).

## Full 7-section report

(Sections 1-7 covering Advanced KPIs · Order-flow signals · Software libraries · Data sources · Risk management · Behavioural / meta · Recent academic papers — see chat history for full text with URLs to source papers, libraries, and pricing pages.)

Key citations:
- Deflated Sharpe Ratio (Bailey & Lopez de Prado): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- VPIN for crypto (Buildix + Github): https://www.buildix.trade/blog/what-is-vpin-flow-toxicity-crypto-trading
- Oct-2025 cascade SaR paper: https://arxiv.org/pdf/2603.09164
- quantstats library: https://github.com/ranaroussi/quantstats
- arch (GARCH) library: https://arch.readthedocs.io/
- River (online ML / drift detectors): https://riverml.xyz/
- NautilusTrader: https://nautilustrader.io/
- vectorbt: https://vectorbt.dev/
- Optuna: https://optuna.org/
- FinRL: https://github.com/AI4Finance-Foundation/FinRL
