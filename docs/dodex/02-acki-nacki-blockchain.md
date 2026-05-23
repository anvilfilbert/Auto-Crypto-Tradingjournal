# 02 — Acki Nacki: the chain underneath DODEX

## TL;DR for a CEX engineer

Acki Nacki is a TON-family L1. Smart contracts are written in **TVM-Solidity**
(a Solidity dialect compiled to TVM bytecode — NOT EVM-compatible). Clients
interact with contracts by constructing, Ed25519-signing and submitting
**external messages** (the wire-level equivalent of an Ethereum transaction,
but message-based instead of account-based). Reads are served by a **GraphQL**
endpoint. Block time is 330 ms; probabilistic finality lands at ~750 ms.

## Consensus

Probabilistic Proof-of-Stake with three roles:

- **Block Producer** — selected deterministically per slot from a block-hash
  seed (the "leader").
- **Block Keeper (BK)** — attestor; signs blocks.
- **Verifier** — randomly drawn per block; validates and re-signs.

Total per-block message count is `2 * (N - 1) + v` where `N` = block keepers
and `v` = verifiers — sub-quadratic, unlike AptosBFT. The protocol claims
*"higher Byzantine fault tolerance than Nakamoto, BFT, Solana"*.

For the trading journal this matters because the **per-trade latency**
the auto-trader sees is bounded by block time + finality, not by a
single-validator-confirmation. Plan for ~1 s end-to-end as the realistic
floor; the marketing "microsecond" number refers to in-contract execution.

## Tokens

- **SHELL** — the standard transferable token. What users hold and trade.
- **VMSHELL** — gas token. Converted 1:1 from SHELL inside the contract at
  deployment / call time. Contracts pay VMSHELL to execute.
- **NACKL** — separate token used by the Bee Engine client-side mining
  subsystem. Not relevant to DODEX trading.

We need to manage **two balances** for our wallet contract: SHELL for trading
+ value movement, and VMSHELL for paying gas. Conversion happens via a contract
call (deposit some SHELL, the contract mints VMSHELL).

## Smart-contract VM

- Language: **TVM-Solidity** (compiled with `tvm-solidity-compiler`).
- VM: **TVM** (Ton Virtual Machine), descended from Telegram Open Network.
- State storage primitive: **Bag of Cells (BoC)** — a tree of 1024-bit cells
  with up to 4 references each. Messages, contract storage and everything
  else is serialised to BoCs.

Practical implication: we cannot reuse any Ethereum tooling (Hardhat, ethers,
web3.py, foundry, etc.). The toolchain is entirely TVM-native.

## Wire protocol — external messages

An external message body has the layout:

```
Maybe(Signature) + Enc(Header) + Function ID + Enc(Arguments)
```

| Section | Size | Notes |
|---|---|---|
| Signature flag + payload | 1 bit + 512 bits | Ed25519 over representation hash of (src-address-prepended) BoC |
| `time` header | 64 bits | Unix ms |
| `expire` header | 32 bits | Unix s — contract drops the message after this |
| `pubkey` header | up to 257 bits | Optional |
| Function ID | 32 bits | First 32 bits of `SHA256("name(in1,in2,…)(out1,out2,…)v2")`. Highest bit cleared for inbound externals, set for outbound. |
| Arguments | variable | Encoded per type into the root cell or chained cells. |

The signing dance is mildly non-obvious:

1. ABI-serialise header + function ID + args into a BoC. Reserve 591 bits in
   the root cell for the destination address.
2. Prepend the actual destination address data without max-padding.
3. Ed25519-sign the representation hash of that BoC.
4. Remove the address, replace with bit `1` + 512-bit signature.

Source: [ABI Specification](https://dev.ackinacki.com/abi/abi).

## SDKs available

Repo: [github.com/tvmlabs/tvm-sdk](https://github.com/tvmlabs/tvm-sdk).
Version: **v2.24.21** (2026-05-21). Targets Acki-Nacki, Venom, Everscale, TON.

| Language | Status | Package |
|---|---|---|
| **Rust** | First-party core | `tvm_sdk` |
| **JS / TS** | First-party | `@tvmsdk/core`, `@tvmsdk/lib-node`, `@tvmsdk/lib-web` |
| Java | Community | — |
| .NET | Community | — |
| **Python** | **None official** | Custom binding via the `json_interface` C-FFI module — every SDK call is `JSON-in / JSON-out` |

**This is the biggest engineering decision for our integration**: the trading
journal is Python. We will end up with one of:

- **(a) Python ctypes wrapper around `json_interface`** — minimal new code, but every error path needs to be discovered.
- **(b) Node.js sidecar service** — `@tvmsdk/lib-node` runs in a local Node process, our Python app talks to it over localhost HTTP/IPC. New runtime to deploy on the Pi.
- **(c) Pure-Python wire client** — build ABI encode/sign/submit from scratch. Most work, but no external runtime + we own the code.
- **(d) Shell out to `tvm-cli`** — easiest, but slow (process spawn per call) and brittle. Fine for prototype, bad for production.

Tradeoffs covered in `05-integration-blueprint.md`.

## Reading state — GraphQL

Endpoint (testnet): `https://shellnet.ackinacki.org/graphql`.

Capabilities (from the docs):

- Query block / transaction / message lists with filters
- Query account state (code hash, balance, last paid)
- Call get-methods on contracts (read-only, off-chain emulation)
- Subscribe (probably WS) — to be confirmed when we touch the SDK

For our use:

- **Order book state** — read RootOracle prices, query our PrivateNote balance, see lot-contract states.
- **Confirmation of our orders** — subscribe to messages from our wallet, react to the matched event.
- **No on-chain ticker history** — we'd still pull candles from Binance/Bitget for charting. DODEX is for execution, not for market data.

## Keys + wallet model

Wallet primitive is a **multisig contract** (the TON convention). The user
generates an Ed25519 keypair (`tvm-cli genphrase --dump keys.json`), funds
the wallet's address, and then signs and submits external messages from the
key.

For an auto-trader running on a Pi we'd need:

- A hot wallet keypair stored on the Pi (likely in `.env` or a separate
  encrypted file).
- A small budget allocated to it (analogous to the Bitget trader subaccount).
- Manual / cold-storage funding flow to top up.

This is a **larger attack surface** than the current API-key model: a
compromised Pi means a drained wallet, not just rate-limited Bitget calls.

## Mainnet status

Not explicitly confirmed in any doc we've fetched. All examples target
`shellnet.ackinacki.org` (testnet). Plan as if mainnet is months away;
treat any sooner date as a bonus.
