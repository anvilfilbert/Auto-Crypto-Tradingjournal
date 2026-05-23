# 01 — DODEX Overview

## What it is

DODEX (`dex.do`) is a non-custodial dark-order DEX built on the
[Acki Nacki](https://docs.ackinacki.com/) blockchain. "Dark" here means
**privacy**, not a CEX-style hidden venue:

> All user balances are shielded at funding. All Prices in the On Chain Order
> Book are shielded. All individual orders are hidden.
> — dex.do landing

The protocol's stated goal is preventing market manipulation rather than
enabling transfer privacy. Trades **become public when a user exits** to a
transparent address — there is no KYC/AML, but cash-out reveals identity.

## Architecture claims (from the landing page, verbatim)

- *"Each trading pair matching engine runs in its own WASM engine in parallel scalable to thousands of Nodes."*
- *"The Matching Engine written in RUST runs at native speeds inside WASM."*
- *"Average block finality: 750 ms. Block time: 330 ms."*
- *"Microsecond execution"* + *"settlement under 1 second"*
- *"Order Book — no liquidity pools, no slippage"*
- *"No MEV by design"*
- *"x1000 margin"*
- *"No gas fees on a single Order Book, trading fees are super low"*
- *"Fully permissionless Listing"* + *"Fully permissionless Oracles"*

Important: "microsecond execution" is contract-internal speed. End-to-end
latency a programmatic trader sees is **block-bound** — ~330 ms per block,
~750 ms to finality. That's still very fast for a blockchain, but it is
*not* CEX-style microsecond fill latency.

## What's actually deployed today (testnet)

The documented contracts on testnet (`shellnet.ackinacki.org`):

- **`ShellAccumulatorRootUSDC`** — receives seller SHELL deposits, deploys per-order lots, runs FIFO matching against buyer USDC deposits, sends USDC to seller on `claim()`. *Fixed-rate, not a limit-order book*.
- **`ShellSellOrderLot`** — per-order contract instance; self-destructs on `claim()`.
- **`Exchange.mintAndSendAccumulator(buyer, value, nonce)`** — buy-side entry.
- **`RootPrivateNote`** — verifies a ZK deposit proof, deterministically deploys a `PrivateNote`, mints ECC Shell into it.
- **`PrivateNote`** — per-wallet shielded balance contract.
- **`Nullifier`** — stores a static nullifier hash, blocks double-spend of a shielded note.
- **`RootOracle` / `Oracle` / `OracleEventList`** — permissionless price oracles.
- **`Pari Mutuel Pool` (PMP)** — pool primitive; exact role for trading unclear from docs.

Several of these are explicitly marked *(Work in progress)* on
[dev.ackinacki.com/dex.do](https://dev.ackinacki.com/dex.do).

The **general limit-order book** that the marketing page describes does NOT
appear to be documented yet. What is documented is a fixed-rate accumulator
for SHELL/USDC. This is the largest gap between the website's claims and
the developer corpus.

## Whitelist

The dex.do landing has an *"Apply to Whitelist"* link. We do not currently
have whitelist access. Whether this is required for testnet, mainnet, or
only for permissionless listing is unclear from public docs.

## Why this matters to the trading journal

The journal currently routes everything through one centralised exchange
(Bitget). A working DEX integration would give us:

1. **Counterparty diversification** — Bitget account ban, withdrawal freeze, or API outage no longer halts the auto-trader.
2. **Self-custody** — funds live in a wallet contract we control, not on an exchange.
3. **Pure-data privacy** — even if DODEX trades become public on exit, *during* the trade the size and price are shielded — useful for larger positions that would otherwise move the Bitget tape.

Against this, see [`06-risks-and-decision-memo.md`](06-risks-and-decision-memo.md) for what could go wrong.
