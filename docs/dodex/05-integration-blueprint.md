# 05 — Integration blueprint

*Hypothetical module layout for the day we decide to ship. NO code exists
yet. This file is a thinking document, not a spec.*

## Design constraints from the existing codebase

1. **Modular + toggleable.** The user explicitly asked for the integration to
   be a module that can be turned off. Today's analogues: `FUTURES_AI_ENABLED`
   env flag, the `chain` column on `positions`.
2. **Two-chain architecture extends to three.** `positions.chain` already
   takes `'manual'` and `'auto_ai'`. Adding `'dodex_auto'` (and possibly
   `'dodex_manual'`) is a one-column-value change, no migration needed
   structurally.
3. **AI consensus gate stays venue-agnostic.** `signal_consensus.evaluate()`
   doesn't know which exchange executes — it just produces a verdict. We pass
   the verdict + venue to the orchestrator, which picks the executor.
4. **Scanner stays exchange-agnostic.** Setups are derived from CEX market
   data. DODEX is execution-only, not a data source.

## Proposed package layout

```
trading/
├── bitget_trader.py            (existing)
├── executor.py                 (existing — has open_real_trade)
├── orchestrator.py             (existing — process_setups)
├── ...
├── dodex/                      ← NEW package
│   ├── __init__.py
│   ├── config.py               # env-driven enable flag, RPC URLs, contract addresses
│   ├── client.py               # the thin SDK wrapper — see "SDK choices" below
│   ├── wallet.py               # Ed25519 keypair load + sign helpers
│   ├── contracts.py            # contract addresses + ABI loaders for each one
│   ├── trader.py               # the analogue of bitget_trader: place / cancel / query
│   ├── sync.py                 # GraphQL poll loop, reconciliation into positions table
│   └── README.md               # operator-facing notes
data/
└── dodex/                      ← NEW
    ├── abi/                    # JSON ABIs for each contract we call
    └── keys/                   # NOT in git — ed25519 keypair lives here, chmod 600
routes/
└── dodex.py                    ← NEW Flask blueprint /api/dodex/*
templates/
└── index.html                  ← additions: page-dodex, page-dodex-docs, nav items
```

## Database extensions (when we eventually ship)

No schema changes are strictly required because `positions.chain` already
exists. But we'll likely want:

- `positions.venue TEXT DEFAULT 'bitget'` — distinguishes Bitget from DODEX
  even when both could be `chain='auto_ai'`. (Or fold into `chain`:
  `'dodex_auto'` etc. — TBD.)
- `dodex_wallet_snapshots` table — periodic snapshot of public + shielded
  balances (analogue of `wallet_snapshots` for the Bitget account).
- `dodex_log` table — like `futures_ai_log` but for DODEX-specific events
  (tx submitted, tx confirmed, tx reverted, gas spent).

Defer these decisions until the executor code is real.

## SDK choices

Recapped from `02-acki-nacki-blockchain.md`. The biggest decision is:

| Option | Pro | Con | Pick when |
|---|---|---|---|
| **(a) Python ctypes around `json_interface`** | One process. No new runtime. Full SDK power. | We bind every call manually; SDK upgrades require re-binding. | Long-term, if we're confident DODEX is going live and worth the effort. |
| **(b) Node.js sidecar (`@tvmsdk/lib-node`)** | Official SDK, fully maintained. Talk to it via local HTTP/IPC. | Two runtimes to maintain on the Pi. Inter-process latency ~ms-ish. | Pragmatic short-term — fastest to a working prototype. |
| **(c) Pure-Python wire client** | Full control, no FFI. Tiny dep tree. | We re-implement ABI encoding, BoC serialisation, Ed25519 signing dance. Error-prone. | Only if (a) and (b) prove unworkable. |
| **(d) `tvm-cli` subprocess** | Trivial to wire. | One process spawn per call. Bad for production, fine for tests. | Phase-0 read-only experimentation only. |

**My recommendation when we move:** start with **(b) Node sidecar** for the
first working integration, then evaluate switching to **(a) ctypes** once
the wire shape is fully understood. Skip (c) unless we're forced to.

## On / off switch

```
.env
FUTURES_AI_DODEX_ENABLED=0   # default OFF
FUTURES_AI_DODEX_MODE=paper  # paper | real
DODEX_RPC_URL=https://shellnet.ackinacki.org/graphql
DODEX_KEYFILE=/home/<user>/trading-journal/data/dodex/keys/trader.json
DODEX_MAX_NOTIONAL_USDT=10   # very low while we validate
DODEX_MAX_LEVERAGE=1         # hard cap until liquidation flow understood
```

`trading/dodex/config.py` reads these. `orchestrator.process_setups()` only
dispatches DODEX execution when the flag is on. If the flag is off, the rest
of the system runs identically to today — no risk of accidental DEX trades.

## UI surface

A separate **DODEX page** + dedicated **DODEX docs page**, in the existing
`#page-*` left-nav scheme. Following the existing nav grouping:

```
Docs section adds:
  💱 DODEX            → showPage('dodex')          → live state + actions
  📖 DODEX Docs       → showPage('dodex-docs')     → in-app reference
```

The DODEX page mirrors the Futures-AI page layout but with DEX-specific
fields:

- Wallet status: public SHELL balance · VMSHELL gas · USDC · # PrivateNotes · last topup
- Open orders (= our seller-lot contracts that haven't claimed)
- Recent fills
- Decision log (analogue of `futures_ai_log`)
- Operator buttons: pause / resume / cancel all / withdraw to cold address
- Health: GraphQL endpoint reachability, last block seen, gas budget remaining

The DODEX Docs page is in-app reference content (a curated subset of this
`docs/dodex/` folder) so operators can sanity-check without leaving the UI.

## Phasing

Because DODEX itself is WIP, we should not build it as one big PR. Suggested
phases:

| Phase | Scope | Approval gate |
|---|---|---|
| **0 — Knowledge (you are here)** | This folder. No code. | Implicit — we're not shipping. |
| **1 — Read-only probe** | A standalone Python script that connects to `shellnet.ackinacki.org/graphql`, fetches block height + a contract's get-method. Validates the SDK choice and our environment. Not wired into the app. | After we have whitelist / testnet access. |
| **2 — Module skeleton, toggleable, no trading** | `trading/dodex/` skeleton, `.env` flag, `/api/dodex/state` route returning balances + health. Page exists in UI but only shows state. NO writes. | After Phase 1 works end-to-end. |
| **3 — Manual trade from UI** | Operator clicks a button → wallet signs a deposit-as-sell-order tx → reconciler picks up the fill. Single pair, hard-capped notional. | After Phase 2 + a wallet funding cycle on testnet. |
| **4 — Auto-trader DODEX execution** | Orchestrator can route consensus-approved setups to DODEX when scanner picks a DODEX-supported pair. New `chain='dodex_auto'`. | After Phase 3 stability + DODEX mainnet live + at least one week of testnet observations. |
| **5 — Shielded operations** | Optional. Use `RootPN` + `PrivateNote` for privacy. | After Phase 4 + understanding the shielded order matching flow (not yet documented). |

Each phase is independently turnable-off. If DODEX mainnet slips or the
protocol changes shape, we stop at whichever phase we're in without
disrupting the Bitget side.

## Key custody — the part to take seriously

The Pi already holds Bitget API credentials. Adding an Ed25519 key changes
the threat model:

- **Compromised API key:** attacker can place trades, can NOT withdraw to
  external addresses (if withdrawal whitelist is set). Damage capped.
- **Compromised Ed25519 key:** attacker can drain the wallet to any address.
  Damage = full wallet balance.

Mitigations for the integration:

1. Keep the DODEX wallet **small** — fund only the working budget, top up from
   cold storage manually.
2. Store key in a **chmod 600** file outside the repo, not in `.env`.
3. **Two-tier keys**: a "spending" key on the Pi with the active budget;
   ownership of the wallet retained by a "vault" key kept off-Pi. (Multisig
   contract supports this.)
4. **Per-tx caps** inside `dodex_trader` — refuse to sign any external
   message moving more than `DODEX_MAX_NOTIONAL_USDT` regardless of the
   signal that produced it.
5. **Logged + reviewable**: every signed external message body hash goes to
   `dodex_log` before submission. Operator can audit.
6. Consider a **second confirmation channel** for high-value movements
   (Telegram approval before signing? Two-of-three multisig?).

None of this exists yet. It's the bar we'd hold ourselves to before
flipping `FUTURES_AI_DODEX_ENABLED=1` in production.

## What I'd need from the user before Phase 1

See the questions list in [`06-risks-and-decision-memo.md`](06-risks-and-decision-memo.md).
The blocking ones are: timing, capital allocation, key custody preference,
asset scope.
