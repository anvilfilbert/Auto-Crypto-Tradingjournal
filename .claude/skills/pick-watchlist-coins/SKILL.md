---
name: pick-watchlist-coins
description: Use when curating the scanner watchlist — adding/removing symbols, reducing scan breadth, building tier-based focus lists, evaluating whether a token is tradeable. Triggers on "reduce the watchlist", "pick coins for scanner", "which coins should we scan", "tier the watchlist", "remove illiquid coins", "what's tradeable on Bitget", "add COIN to scanner".
---

# Watchlist Construction for USDT-M Crypto Perpetual Futures

The scanner currently fetches ~314 symbols every 30 min (Bitget hand-picked +
Binance dynamic by volume). More symbols ≠ more profit — every symbol added
costs (a) one OHLCV fetch + indicator calc per scan, (b) candidate-funnel
noise where genuinely strong setups compete with marginal ones for the
top-N Stage-3 slots, and (c) more drift-abort and consensus-rejected events.

This skill encodes a research-backed methodology for picking the right
symbols and the right *number* of symbols.

## When to apply this

- Operator says "we scan too many coins" / "reduce the watchlist"
- Bitget rejects orders on a symbol that's in the watchlist but illiquid on Bitget
- Considering adding a new token (especially recently listed)
- Weekly review (recommend Sunday before NY open)
- After a significant Bitget listing/delisting

---

## 1. Watchlist size — what number?

There is no universal answer in the literature; sources punt to "depends
on strategy". Apply the rule by intent:

| Strategy | Symbols | Why |
|---|---:|---|
| Discretionary day-trading focus list | **10-20** | Operator can mentally track levels, narrative, fundamentals |
| Algo scanner — quality-first | **40-80** | Enough surface for selection, few enough that the funnel concentrates on real edges |
| Algo scanner — broad opportunity | **150-250** | Catches sector rotations; risk of low-quality signals dominating |
| Pure liquidity sweep | **300-500** | Same noise issue as 314 today; default state but rarely productive |

**Current system was tuned for broad opportunity (314).** Given:
- Auto-trader keeps ~5 concurrent positions
- Many symbols have ≤ $5M daily volume on Bitget specifically (despite Binance liquidity)
- Operator manual chain only takes 1-3 trades/week
- 99% Long bias in 7-day output (834 Long / 9 Short) suggests breadth isn't
  helping diversification

→ **Recommend reducing to ~80-120 symbols** structured as tiered lists.

---

## 2. Tier methodology (the core framework)

Segment the watchlist by liquidity + role, not market-cap rank alone:

### Tier 1 — Majors (10-15 symbols, ALWAYS scanned)
- BTC, ETH (the only mega-caps)
- Top liquid L1s: SOL, BNB, XRP, ADA, AVAX, TRX
- Top liquid alts by structural importance: DOGE, LINK, LTC
- **Criteria**: $200M+ daily volume on the venue you trade, < 10 bps typical spread
- **Why fixed**: BTC drives all macro correlation; ETH leads alt-cycles; these
  set the regime for every other coin. Skipping them means missing context.

### Tier 2 — Liquid mid-caps (20-40 symbols, rotated weekly)
- $1B-$10B market cap with $50M+ daily perp volume on the venue
- Includes: SUI, APT, INJ, NEAR, SEI, ATOM, DOT, TIA, ICP, TON, ARB, OP, HBAR
- Sector reps: ONDO (RWA), JUP (Solana DEX), LDO (LST), TAO/FET/RENDER (AI),
  WLD (worldcoin), AAVE/MKR (DeFi blue chips), RUNE, PENDLE
- **Criteria**: $50M+ daily volume sustained over 14-day average; OI > $25M;
  bid-ask spread < 25 bps in normal markets
- **Why rotated**: narrative cycles (AI, DePIN, L2s, RWA) come and go;
  refresh weekly to ride active rotations

### Tier 3 — Narrative / momentum (15-30 symbols, refreshed every 1-2 weeks)
- Smaller caps ($100M-$1B mcap) IF they meet liquidity floor on the trading venue
- The "hot" rotation: memecoins in a meme rally, AI coins during AI cycle, etc.
- **Criteria — stricter** because of higher rug/manipulation risk:
  - $20M+ daily perp volume on trade venue
  - Listed at least 60 days on a Tier-1 exchange (Binance, Bybit, OKX, Bitget)
  - OI > $5M
  - Spread < 50 bps
  - At least 200 trades per $100k of volume (anti wash-trading)
- **Why short rotation**: by the time you're chasing a meme, the move is half done.
  Keep this list small and time-bound.

### Tier 4 — Watch-only / catalyst (5-10 symbols, NOT in active scanner)
- Coins you want to monitor for catalysts (unlocks, airdrops, mainnet launches)
- **Do not scan automatically** — set price alerts, not auto-trade signals
- Move into Tier 3 only when liquidity criteria are met AND the catalyst plays out

### Tier 0 — Always-exclude
- See "Hard exclusions" section below

---

## 3. Hard exclusions (always reject, even if a tier "rule" passes)

Apply these as filters when building the watchlist programmatically:

| Filter | Threshold | Why |
|---|---|---|
| Listing age on Tier-1 exchange | < 30 days | First 30 days have wild volatility + thin books + liquidation cascades |
| Daily volume on the trade venue | < $10M | Auto-trader can't fill at intended price; drift-aborts will dominate |
| Bid-ask spread (normal market) | > 200 bps (2%) | Every entry pays ~1% just in spread — destroys edge |
| Order book depth within ±2% | < $20k | Anything bigger than micro-positions moves the price |
| Single-venue exposure | only 1 CEX listing | No price discovery; exit liquidity is the same one venue |
| Top-10 holder concentration | > 50% | A few wallets can dump and crash; not the same risk profile as broad-held assets |
| Sell-tax / buy-tax | > 5% asymmetric | Token economics built to extract from traders |
| Repeated pump-and-dump pattern | 70%+ drawdowns from vertical spikes | Behavioural signature — these don't reform into stable trading instruments |
| Stablecoins on the futures venue | n/a | No volatility = no edge for directional futures |
| Wrapped versions of an asset already in the list | duplicates | WBTC + BTC, stETH + ETH — same exposure |

---

## 4. Bitget-specific considerations

The auto-trader executes on Bitget. The dynamic watchlist fetches volume from
**Binance** (`ccxt_client.get_binance_futures_symbols`) but the trade happens
on Bitget. Liquidity that exists on Binance may not exist on Bitget.

**Always cross-check before adding a symbol**:
- Bitget USDT-M perpetual exists for the symbol
- 24h volume on Bitget itself ≥ $5M (not just Binance)
- If Bitget OI is much smaller than Binance OI for the same symbol, you'll
  see drift-aborts: scanner sees Binance price, Bitget fill drifts because
  Bitget's book is thinner

Practical: before pushing a coin from Tier 3 → Tier 2 → Tier 1, verify on
[Bitget's all-pairs page](https://www.bitget.com/futures/usdt) that volume
is real *on Bitget*.

---

## 5. Sector / narrative grouping (not size)

Correlation literature shows that **diversification by sector is more useful
than diversification by market cap** — during macro risk-off, all market
caps compress toward correlation +1; during sector rotation, sectors
decouple. Build the watchlist with sector caps:

| Sector | Max % of watchlist | Anchor symbols |
|---|---:|---|
| BTC / ETH | always present | BTC, ETH |
| Large L1s (alt monetary) | 15% | SOL, BNB, XRP, ADA, AVAX, TRX, DOT, ATOM |
| Smart-contract L2 / Ethereum ecosystem | 15% | ARB, OP, MATIC, STRK, LDO, ZK |
| DeFi blue chips | 10% | AAVE, UNI, LINK, MKR, CRV, SNX |
| AI / DePIN / compute | 10-15% | TAO, FET, RENDER, GRT, WLD, IO, NEAR |
| RWA / institutional | 5-10% | ONDO, PENDLE, MKR |
| Meme | 10-15% | DOGE, SHIB, PEPE, WIF, BONK |
| Solana ecosystem | 10% | SOL (already in L1), JUP, JTO, PYTH, W |
| BTC ecosystem (ordinals, runes) | 5% | ORDI, SATS — when narrative is hot |
| Gaming / Metaverse | 5% | IMX, GALA, SAND, AXS |
| New narrative bucket | 5% | Reserved for whatever rotates this cycle |

If any sector creeps above its cap, the watchlist is over-betting on that
narrative — when the rotation reverses, ALL of them go to zero together.

---

## 6. How to apply this skill to `scanner_watchlist.py`

The file has three layers:
1. **`_BITGET_WATCHLIST`** — hand-picked, hard-coded (use this for Tiers 1+2)
2. **`_get_dynamic_watchlist()`** — Binance volume-filtered + dedup with Tier 1+2
3. **Env vars**: `SCANNER_MAX_SYMBOLS` (default 500), `SCANNER_MIN_VOL_USD` (3M),
   `SCANNER_MIN_OI_USD` (1.5M)

**To reduce watchlist size**, the cleanest knobs:

| Goal | Action |
|---|---|
| Total ~120 symbols | `SCANNER_MAX_SYMBOLS=120` in `.env` |
| Stricter liquidity | `SCANNER_MIN_VOL_USD=10000000` (10M) instead of 3M |
| Stricter OI | `SCANNER_MIN_OI_USD=5000000` (5M) instead of 1.5M |
| Curate Tier 1+2 manually | Edit `_BITGET_WATCHLIST` list in `scanner_watchlist.py` |
| Disable dynamic Binance feed | Set `SCANNER_MAX_SYMBOLS` equal to the hand-picked list length |

Recommended starting point for "quality > quantity":
```env
SCANNER_MAX_SYMBOLS=100
SCANNER_MIN_VOL_USD=10000000
SCANNER_MIN_OI_USD=3000000
```

This gives ~100 symbols all clearing $10M daily volume and $3M OI — enough
to catch rotations, few enough to feed Stage 3 with quality candidates.

---

## 7. Volatility-based filtering (post-listing)

After liquidity, the secondary filter is ATR%. From the research:

| ATR% per day | Use for | Notes |
|---|---|---|
| < 1.5% | Skip — no edge | Stop distance < typical exchange wick noise |
| 1.5% - 3% | Sweet spot | Standard 2-3× ATR stops work. Most majors live here. |
| 3% - 6% | Tradeable with adjustments | Wider stops, smaller size. Most mid-caps. |
| 6% - 10% | Edge of tradeable | Position size cut in half; only with strong setup |
| > 10% | Skip | Stops become impractical; one normal move = full SL |

Crypto runs hot — BTC commonly 3-7% daily — so the ATR% bands are
strategy-dependent. The scanner's `enforce_sl_floor` already requires SL
distance ≥ 0.5× ATR_4H; if the resulting stop is > 8% from entry, the
setup is implicitly in the "too volatile" bucket and should be downgraded.

---

## 8. Funding-rate signal (use for filtering AND as setup input)

Funding extremes (per-8h):
- **Funding > +0.1%/8h** = crowded longs → fade with caution; potential
  liquidation cascade risk
- **Funding < -0.1%/8h** = panic shorts → potential squeeze setup
- **Funding ±0.01% to ±0.05%** = normal range

When building a watchlist, **prefer coins with normal funding** for trend-
following strategies (extreme funding implies the move is mature). The
extreme-funding coins go to a separate "reversal candidates" watchlist if
you trade contrarian setups — not the main trend scanner.

---

## 9. Maintenance routine

**Weekly review** (Sunday before NY open):
1. Pull Bitget's top-30 by 24h volume → confirm all are in Tier 1 or 2
2. Drop any Tier 3 coin whose 14-day volume average fell below $20M
3. Look at scanner output's direction breakdown over the week — if it's >70%
   Long across many setups, something's structurally Long-biased (could be
   confluence asymmetry, could be the watchlist itself)
4. Drop any coin that hasn't appeared in scanner setups in 30 days AND has
   no narrative catalyst

**Monthly**:
1. Cross-check Tier 1 vs Bitget's actual top-volume table
2. Refresh Tier 3 with currently-rotating narratives
3. Verify any new additions pass the hard exclusions (especially listing age)

**On listing event** (a coin lists on Bitget for the first time):
1. **Wait at least 30 days** before considering it for any tier
2. Verify volume + OI consistently meet Tier 3 floor over 30 days
3. Only add after confirming spread is consistently < 50 bps in normal markets

---

## 10. Anti-patterns to flag

If a future request asks for any of these, push back:

- **"Add this coin because I'm bullish on it"** → bias, not edge. Add only
  if it passes liquidity + sector criteria.
- **"Track 500 coins for more opportunity"** → false. More noise, not more
  signal. Each marginal coin steals a Stage-3 slot from a better candidate.
- **"Add COIN, it just listed yesterday"** → 30-day cooldown is non-negotiable
  outside of a deliberate "new listing watch" strategy with its own risk profile.
- **"Remove BTC/ETH since we trade alts"** → BTC drives correlation; ETH
  drives alt-rotation. Keeping them informs the macro context even if no
  trade ever fires on them.
- **"Add 10 memes to the watchlist"** → respect the sector cap. If memes
  exceed 15%, prune the bottom by volume.

---

## 11. Quick-reference checklist (use when evaluating a single symbol)

```
[ ] Listed > 30 days on a Tier-1 exchange (Binance/Bybit/OKX/Bitget)
[ ] 14-day avg volume on Bitget itself ≥ tier floor ($200M/$50M/$20M)
[ ] Bitget OI ≥ tier floor ($100M/$25M/$5M)
[ ] Bid-ask spread (normal market) < tier ceiling (10/25/50 bps)
[ ] Top-10 holder concentration < 50%
[ ] No asymmetric tax / honeypot pattern
[ ] Has clear sector fit + sector isn't already over-allocated
[ ] Funding rate in normal range (-0.05% to +0.05% per 8h)
[ ] If meme/AI: a current narrative tailwind, not last cycle's
[ ] Not a wrapper / duplicate of something already in the watchlist
```

All ten boxes ticked → add at the appropriate tier. Any unchecked → reject
or defer to the next review.

---

## References

Methodology synthesised from:
- [BingX — Crypto exchanges day-trading 2026](https://bingx.com/en/learn/article/best-crypto-exchanges-day-trading-2026-fees-liquidity-compared)
- [CCN — 7 warning signs token liquidity is too risky](https://www.ccn.com/education/crypto/7-warning-signs-tokens-liquidity-too-risky/)
- [Paybis — 12 tactics for low-liquidity crypto](https://paybis.com/blog/12-tactics-for-trading-low-liquidity-crypto/)
- [TradingRiot — What you should know about perpetuals](https://blog.tradingriot.com/p/what-you-should-know-about-perpetual)
- [Coinalyze — Futures market data](https://coinalyze.net/)
- [Sharpe Terminal — Crypto correlation matrix](https://www.sharpe.ai/correlation)
- [VT Markets — ATR indicator guide](https://www.vtmarkets.com/discover/average-true-range-atr-indicator-guide-master-volatility-trading/)
- [Phemex — Funding rate as trading signal](https://phemex.com/academy/what-is-funding-rate-in-crypto-futures)
- [a16z — Perpetual futures primer](https://a16zcrypto.com/posts/article/what-are-perpetual-futures/)
- [arXiv 2505.24831 — Crypto portfolio clustering by correlation](https://arxiv.org/html/2505.24831v1)

The thresholds are starting points calibrated for a small/medium account
trading the auto-trader chain. For institutional sizing the floors should
go higher; for paper/sandbox work they can go lower.
