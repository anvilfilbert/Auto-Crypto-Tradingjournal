# DODEX Integration — Knowledge Base

*Preparation phase, 2026-05-23. No app code touched.*

This folder collects everything needed to think clearly about integrating
[DODEX (dex.do)](https://www.dex.do/) — the dark-order DEX on the
[Acki Nacki](https://docs.ackinacki.com/) blockchain — as an optional module
of the trading journal.

DODEX is **not yet on mainnet**. The Acki Nacki SDK has no Python binding.
Several DODEX contracts are marked *(Work in progress)* in the official docs.
The purpose of this folder is to be ready to move fast when the protocol
stabilises, not to ship code now.

## Files

| File | Purpose |
|---|---|
| [`01-overview.md`](01-overview.md) | One-page summary — what DODEX is, status, claims, why it matters to us |
| [`02-acki-nacki-blockchain.md`](02-acki-nacki-blockchain.md) | The chain underneath: TVM, consensus, message model, signing, GraphQL |
| [`03-dodex-mechanics.md`](03-dodex-mechanics.md) | Contracts, order lifecycle, shielded notes, exit flow |
| [`04-cex-vs-dex-translation.md`](04-cex-vs-dex-translation.md) | Bitget → DODEX translation table, what changes for the auto-trader |
| [`05-integration-blueprint.md`](05-integration-blueprint.md) | Proposed module layout, SDK choices, key custody, on/off switch |
| [`06-risks-and-decision-memo.md`](06-risks-and-decision-memo.md) | For/against integration, worst-case consequences, open questions |
| [`07-upstream-watch.md`](07-upstream-watch.md) | How we monitor DODEX repo + docs for changes, schedule + change-detection script outline |

## Key terms you'll see repeatedly

- **TVM** — Ton Virtual Machine. Smart-contract VM used by Acki Nacki, descended from Telegram Open Network. Not EVM-compatible.
- **TVM-Solidity** — Solidity dialect that compiles to TVM bytecode. Different from Ethereum Solidity.
- **BoC** — Bag of Cells. The serialisation format for TVM messages and contract state.
- **External message** — a signed message from a client to a contract. The equivalent of a "transaction" in Ethereum, but more general.
- **SHELL / VMSHELL** — two token denominations on Acki Nacki. SHELL is the standard transferable token; VMSHELL is what contracts spend on gas. 1:1 convertible.
- **NACKL** — separate token used by the Bee Engine mining subsystem (unrelated to DODEX trading).
- **Shielded note** — a contract instance (`PrivateNote`) that holds a hidden balance. Created via a ZK deposit proof. Spent via a `Nullifier` to prevent double-spend. Tornado-style cryptography.
- **Dark order** — privacy classification: prices, balances and individual orders in the on-chain book are shielded. Trades become public when a user exits to a transparent address. *Not* dark in the "hidden venue" CEX sense.

## Status as of 2026-05-23 (operator-confirmed)

- **Acki Nacki mainnet: live since 2025-10-07.** Testnet `shellnet.ackinacki.org` still used for the latest in-development tooling, including DODEX preview contracts.
- **DODEX: in active build.** Contracts and docs are dropping incrementally on `github.com/ackinacki/ackinacki` and `dev.ackinacki.com/dex.do`. Operator estimate: public release within the next few weeks.
- **TVM-SDK** at `v2.24.21` (released 2026-05-21). Rust core + JS/TS bindings, no Python.
- DODEX docs reference contracts `Nullifier`, `RootPrivateNote`, `PrivateNote`, `RootOracle`, `Oracle`, `OracleEventList`, `Pari Mutuel Pool`, `ShellAccumulatorRootUSDC`, `ShellSellOrderLot`, `Exchange`. Several marked *(Work in progress)*.
- The dex.do landing page advertises an order-book DEX with x1000 margin, no MEV, microsecond execution, sub-second settlement. **These are marketing claims; the docs only currently expose a fixed-rate SHELL↔USDC accumulator** — the general limit-order primitives are not yet documented.

## Operator-confirmed decisions (2026-05-23)

- **SDK strategy:** Node.js sidecar (option B in `02-acki-nacki-blockchain.md`).
- **Token roles:** `$NACKL` is the *main user/transferable token* on Acki Nacki and the asset that will be traded on DODEX. `SHELL` is the *gas / transaction-cost token* (and `VMSHELL` is its in-contract form for paying execution). This is operator-stated; the older Acki Nacki dev docs use `SHELL` as the "standard" token — reconcile during Phase 1 when we touch a live wallet.
- **Asset scope:** DODEX will support multiple trading pairs at launch; cross-chain bridges to other L1s are also WIP. Plan the integration as multi-pair from the start, not SHELL/USDC-only.
- **Approved phases:** **Phase 1 (read-only probe)** + **Phase 2 (module skeleton, toggleable, no trading)**. Phases 3-5 deferred until those land cleanly.
- **Monitoring:** track upstream changes on `github.com/ackinacki/ackinacki` + the `dev.ackinacki.com` docs so we stay current as the protocol stabilises. See `07-upstream-watch.md`.

## How to refresh this knowledge later

External docs we sourced from:
- https://www.dex.do/ — landing
- https://docs.ackinacki.com/ — chain overview
- https://docs.ackinacki.com/for-developers/developer-tools-and-sdk
- https://dev.ackinacki.com/dex.do — DODEX dev docs (use `?ask=<q>` to query a section)
- https://dev.ackinacki.com/abi/abi — ABI spec
- https://dev.ackinacki.com/llms-full.txt — full corpus
- https://github.com/tvmlabs/tvm-sdk — official SDK (Rust + JS/TS)
- https://github.com/ackinacki/ackinacki — node implementation
