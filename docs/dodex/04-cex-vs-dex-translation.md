# 04 — Bitget → DODEX translation table

A mapping from the concepts we already use (`bitget_client.py`,
`bitget_trader.py`, the auto-trader chain) to their DODEX equivalents.

When a row is **bold**, that's a place where the integration is *not* a
straight translation — the DEX requires a different mental model and either
new code, new state, or both.

## Auth + identity

| Bitget | DODEX |
|---|---|
| API key + secret + passphrase (3 strings, kept in `.env`) | **Ed25519 keypair** (private seed → public key). Stored as a JSON file or env var. |
| HMAC-SHA256 over canonical request string | **Ed25519 over BoC representation hash** (see `02-acki-nacki-blockchain.md`). Signing also requires the destination address pre-prepended into the BoC root cell before hashing. |
| One identity per subaccount | One wallet contract per Ed25519 keypair. Same key can also own multiple `PrivateNote` contracts (one per shielded deposit). |
| Withdrawal whitelist enforced by Bitget | **No exchange-enforced whitelist** — non-custodial. We own the key; we own the consequences. |

## Balance model

| Bitget | DODEX |
|---|---|
| Single USDT-M futures account; `equity_usdt` lives in the wallet snapshot | **Multiple balances per wallet**: public **NACKL** (main asset), public **SHELL/VMSHELL** (gas), plus any bridged-in assets (USDC etc.) once DODEX cross-chain bridges are live, plus any number of `PrivateNote` contract balances (shielded) |
| Periodic `wallet_snapshots` row from REST poll | GraphQL query on each address; subscribe via WS for changes. **No single number to display** — UI must aggregate public + shielded across token types. |
| Sub-account isolation | One key per subaccount-equivalent. Could keep manual-trade and auto-trade keys separate so `chain` column can route to the right wallet. |

## Order placement

| Bitget (`bitget_trader.place_market_order`) | DODEX |
|---|---|
| HTTPS POST `/api/v2/mix/order/place-order` with `{symbol, side, orderType, size, leverage, marginMode, presetTakeProfit, presetStopLoss}` | **Construct + sign external message** to a trading contract. Today: deposit SHELL into `ShellAccumulatorRootUSDC.receive()` (the deposit IS the order). Future limit-order book: TBD. |
| Synchronous HTTP response with `orderId` | Asynchronous. Message broadcasts to network → ~330 ms block → ~750 ms finality. We watch GraphQL for the matched event. |
| Tick-size + min-size enforced by Bitget on rejection | Enforced by contract — message reverts inside TVM, the seller's SHELL is refunded. **Refund is not free**: the inbound external msg still costs gas. |
| Bitget atomically attaches preset SL/TP to the position | **No documented atomic SL/TP**. Likely either separate trigger contracts or client-side monitor that submits a close order on threshold. Currently must be emulated. |

## Cancel / modify

| Bitget | DODEX |
|---|---|
| `cancel-plan-order` / `modify-position-tpsl` | **No documented cancel method** on the accumulator. The lot self-destructs only on `claim()`. For limit-order book: TBD when docs land. |
| Cheap operation (rate-limited only) | A cancel is itself an on-chain transaction → costs gas + 1 block. Cancelling at high frequency is materially more expensive than on Bitget. |

## Position tracking

| Bitget | DODEX |
|---|---|
| `get_open_positions()` returns positions object with `size, entry_price, mark_price, unrealized_pnl, preset_sl, preset_tp` | **No position primitive exists** in the documented contracts. We'd have to track positions client-side from message history (our deposits, our `claim()` events, mark prices from oracles). |
| `get_position_history()` for last N closed | Query GraphQL for messages from our address to / from trading contracts. Reconcile into our `positions` table the same way `bitget_sync` does today. |

## Margin / leverage

| Bitget | DODEX |
|---|---|
| `MAX_LEVERAGE=10` enforced by config + Bitget caps at symbol level | dex.do landing says *"x1000 margin"*. Mechanism not documented. **Treat the claim with suspicion** — high leverage on a thin shielded book is a blow-up vector. Initial integration should hard-cap at 1× until liquidation flow is documented. |
| Cross / isolated margin toggle | Not documented for DODEX. Assume isolated by default. |

## Market data

| Bitget | DODEX |
|---|---|
| WebSocket ticker, candle streams | **Not the right source for DODEX**. The order book is shielded — no public depth/best-bid-best-ask stream. |
| ccxt for candles + L/S | Keep using Bitget/Binance for candles + indicators. DODEX is **execution-only**; market data still comes from CEXs. |
| Oracles: external (yfinance for VIX etc.) | DODEX has on-chain `RootOracle` — we could read its price as a sanity check on our CEX-derived mark price. |

## Fees + cost model

| Bitget | DODEX |
|---|---|
| Maker / taker fee % | Landing claims *"super low"* — no concrete number yet. |
| Funding rate on perps | Not documented. Perpetual contracts on DODEX appear not yet defined — only spot accumulator visible today. |
| No gas | **Gas for every external message** (rejected ones too). Need a VMSHELL balance and a refill strategy. |

## Reconciliation + state machine

| Bitget | DODEX |
|---|---|
| `bitget_sync` polls every 5 min; reconcile fills against open orders | GraphQL **subscription** could deliver events in real time. Or poll the same way — simpler to start, more expensive in long-poll calls. |
| Settled in fiat-mark USDT inside one account | Each pair has its own settlement asset (USDC for ShellAccumulatorRootUSDC). Multi-currency accounting required. |

## What stays the same

The journal's high-level architecture survives:

- The **scanner** is exchange-agnostic — it analyses chart data + makes setup proposals.
- **`positions.chain`** column already partitions books. Add `'dodex_auto'` and the scanner output can target either book.
- **Hindsight + rulebook + AI advisor** all read positions rows — they work the same on DODEX-sourced rows as Bitget ones.
- The **AI consensus gate** (Opus 4.7) doesn't care which exchange will execute. Pass the venue through `signal_consensus.evaluate()` so the gate can adjust thresholds per venue.

The big new code surface is the `trading/dodex_*` package — analogous to
`trading/bitget_trader.py` but speaking TVM external messages instead of REST.
See `05-integration-blueprint.md` for the proposed layout.
