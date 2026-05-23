# 03 — DODEX mechanics: contracts, orders, privacy

## The contract map (testnet, partial — many marked WIP)

| Contract | Role |
|---|---|
| `RootPrivateNote` (`RootPN`) | Verifies a ZK deposit proof. Deterministically deploys a `PrivateNote` for that deposit. Mints ECC Shell into it. |
| `PrivateNote` | Per-deposit shielded wallet. Holds balance, stakes, coupons, withdrawals — all private at the wallet layer. |
| `Nullifier` | Stores static nullifier hashes. Prevents a deposit ZK proof from being redeemed twice (double-spend prevention). |
| `RootOracle` / `Oracle` / `OracleEventList` | Permissionless price oracles. Anyone can list an oracle; the protocol picks aggregation. |
| `ShellAccumulatorRootUSDC` | The (only currently documented) matching contract. Receives seller SHELL deposits, deploys `ShellSellOrderLot`, matches buyer USDC deposits FIFO largest-denomination-first. |
| `ShellSellOrderLot` | Per-order child contract. Holds the seller's locked SHELL. Self-destructs on `claim()`. |
| `Exchange.mintAndSendAccumulator(buyer, value, nonce)` | Buy-side entry — credits the accumulator on the buyer's behalf. |
| `Pari Mutuel Pool` (PMP) | Pool primitive. Role for trading is not documented. (Possibly used for liquidations / insurance / prediction markets — unclear.) |

## Order lifecycle (documented portion only)

Today the docs only show a **fixed-rate SHELL↔USDC accumulator**. There is
no documented limit-order book yet. The flow is:

```
SELLER                                       BUYER
  │                                             │
  │── send SHELL ──▶ ShellAccumulatorRootUSDC.receive()
  │                                             │
  │      (Root validates denomination,          │
  │       assigns orderId, deploys              │
  │       ShellSellOrderLot)                    │
  │                                             │
  │                                  ── send USDC ──▶ Exchange.mintAndSendAccumulator()
  │                                             │
  │                  (Root matches orders       │
  │                   FIFO, largest             │
  │                   denomination first)       │
  │                                             │
  │── seller.claim() ─▶ ShellSellOrderLot       │
  │                                             │
  │◀── Root sends USDC ─────────────────────────│
  │                                             │
  │      (lot self-destructs)                   │
  ▼                                             ▼
```

Implications:

- **Sellers initiate orders by sending tokens**, not by submitting a "place order" payload. The deposit IS the order.
- **Order IDs are assigned on-chain** by the Root.
- **No documented cancel method** — to be confirmed. Without one, a partially-filled lot would either need to wait for completion or to be cancelled via the lot contract directly (TBD).
- **Buyers don't pick a counterparty**; they deposit USDC and the Root applies its matching algorithm.
- **Settlement is `claim()`** — the seller (or a script monitoring the lot) pulls USDC after match.

The general limit-order book described on dex.do is presumably layered on top
of this — but it is **not yet documented**. We should not assume the public
flow until docs land.

## Privacy model (Tornado-style shielded notes)

The privacy half is a **separate primitive** from the trading flow above.
You opt into it by going through `RootPrivateNote` instead of trading public
SHELL directly.

```
PUBLIC SIDE                                SHIELDED SIDE
  ECC USDC ──┐
             │
             ▼
       RootPN.deposit(zk_proof)
             │
             ├──▶ verifies ZK deposit proof
             ├──▶ checks Nullifier(nullifier_hash)
             │     reject if already seen
             ├──▶ records nullifier_hash
             ├──▶ deterministically deploys PrivateNote(owner_data)
             └──▶ mints ECC Shell → PrivateNote
                                              │
                                  (user trades, stakes, etc.
                                   from inside the PrivateNote
                                   — balances stay hidden)
                                              │
                                              ▼
                                       (eventual exit)
                                              │
              public address ◀────────────────┘
              (balance becomes visible at this point)
```

The Nullifier blocks replay: each deposit proof contains a `nullifier_hash`
derived from the secret. After the first redemption, the hash is recorded,
so the same proof can't be replayed.

What the docs **do not** explain (gap we should ask about):

- Does the matching engine work over shielded notes, or do you have to
  un-shield before placing a public order?
- The dex.do landing says *"all individual orders are hidden"* — that
  implies matching IS over shielded state, but no docs we found describe
  the matching mechanics.
- "Trades automatically revealed when users exit" — what specifically reveals?
  The exit transaction is by definition public, but is the linkage from
  exit-tx back to historical orders made explicit, or do you simply lose
  forward-looking privacy from the exit point on?

## What "no MEV by design" means here

The marketing claim is plausible if:

1. Orders are **shielded** (no public mempool to front-run from).
2. Matching is **deterministic FIFO** at the contract level (no validator
   ordering advantage).
3. Block production is **deterministically leader-selected** at the consensus
   layer (no validator can prioritise their own trade).

This holds for the documented accumulator. Once the general order book lands
the analysis needs to be redone.

## What's missing from the public docs (open questions)

1. Limit-order placement and cancellation API (general order book).
2. Margin/leverage contract design — the *"x1000 margin"* claim.
3. Stop-loss / take-profit primitives — are they on-chain triggers, or
   client-side monitors that submit close orders?
4. Liquidation engine — who liquidates, what insurance fund.
5. Order-book read API — can we query depth, or only our own orders?
6. Asset listing flow — *"permissionless listing"* — what does a new pair
   require contractually?
7. Withdrawal latency from PrivateNote → public address.
8. Fee model — *"super low trading fees"* but no number.

Until at least (1) and (2) are documented, **we cannot fully scope the
auto-trader integration**. We can scope a read-only / public-order-book
prototype, and a key-management plan — that's what 04 and 05 cover.
