# 06 — Risks, decision memo, open questions

## Arguments FOR integrating DODEX

1. **Counterparty diversification.** Today, Bitget is a single point of
   failure. A ban, withdrawal freeze, API outage, or insolvency event halts
   the auto-trader and may strand funds. A working DODEX integration would
   let us route to a non-custodial venue at runtime.

2. **Self-custody.** Funds live in a wallet contract we control. No
   exchange can freeze, seize, or close the account.

3. **Privacy of working orders.** The dex.do landing promises shielded
   prices, balances and individual orders. For a small account this is
   minor; for a larger book it reduces information leakage that lets
   counterparties price against you.

4. **No MEV / no front-running.** The combination of deterministic FIFO
   matching + shielded order book closes off most MEV vectors that exist on
   public order books (Uniswap-style AMMs in particular).

5. **Sub-second finality.** 330 ms blocks + ~750 ms finality is competitive
   with CEX latency for the swing-trade timescales the journal targets.

6. **Existing architecture absorbs it cheaply.** `positions.chain` already
   partitions books. AI consensus is venue-agnostic. The scanner is
   exchange-agnostic. The new code is concentrated in one package
   (`trading/dodex/`), one Flask route, two UI pages.

7. **Educational value.** Building a working DEX client teaches us the TON
   / TVM stack — which is broader than just DODEX. If the chain itself
   grows, the same skills carry to other Acki Nacki dapps.

## Arguments AGAINST integrating DODEX

1. **Maturity risk.** DODEX is explicitly **work in progress**. Several
   core contracts are tagged WIP in the dev docs. The marketing claims on
   dex.do (x1000 leverage, microsecond execution, full order book) are
   ahead of the developer documentation. Building against moving APIs
   wastes effort.

2. **No Python SDK.** The trading journal is Python. The official TVM-SDK
   has Rust + JS/TS bindings only. We will either ship a Node sidecar (new
   runtime to maintain), write a ctypes wrapper around the JSON-FFI (new
   binding to maintain), or build a pure-Python client (new wire encoder /
   signer to maintain). All are non-trivial.

3. **Mainnet not confirmed live.** All examples target
   `shellnet.ackinacki.org` (testnet). A few weeks could become a few
   months. Effort spent now may sit idle.

4. **Documented trading flow is minimal.** Today the only documented
   matching contract is a fixed-rate SHELL↔USDC accumulator. The general
   limit-order book, cancel semantics, stop-loss / take-profit primitives,
   liquidation engine — none are documented. We cannot scope the
   auto-trader path until they are.

5. **Privacy adds operational complexity.** Generating + verifying ZK
   deposit proofs, managing per-deposit PrivateNote contracts, tracking
   nullifier hashes — meaningfully more state than the Bitget REST model.

6. **Key custody is a new attack surface.** Compromised Bitget API key →
   damage capped by withdrawal whitelist. Compromised DODEX wallet key →
   full balance drain. The Pi is reasonably secure but not hardened
   against a determined attacker.

7. **Gas + dual-token complexity.** Every external message costs VMSHELL.
   Failed transactions still cost gas. The journal needs a VMSHELL
   accounting layer, a top-up flow, and an emergency-low-gas alert.

8. **Liquidity at launch.** Bitget has hundreds of USDT-M perpetuals.
   DODEX may launch with a handful of pairs. Our scanner watches ~300
   symbols — most won't be tradeable on DODEX.

9. **Two systems to learn AND keep current.** If DODEX changes its API
   we're chasing two protocols. Twice the breakage budget.

10. **Regulatory ambiguity.** Shielded order books are a grey area in
    several jurisdictions. The "trades become public on exit" mechanism is
    not enough by itself to prevent chain-analysis linkage. Worth at least
    a thought before opening a meaningful wallet.

## Concrete worst-case consequences

| Scenario | Damage |
|---|---|
| Pi key compromise | Attacker drains the DODEX hot wallet. No exchange recourse. Recovery = the money is gone. |
| Bug in our signing code | Wallet locks, or worse — every tx silently signed with wrong nonce, all funds routed to wrong destination. Testnet first is mandatory. |
| Stuck shielded deposit | PrivateNote contract has a bug; we cannot withdraw. Funds locked until protocol patches (or never). |
| DODEX rugpull / centralised admin upgrade | An upgrade key takes ownership of the trading contracts. Our funds re-routed. Mitigation: only deposit working capital, never the full bankroll. |
| x1000 leverage liquidation in thin shielded book | Permissionlessly-listed coin with shielded depth → we can't read liquidity → over-leverage → blowup. Cap at 1× until liquidation flow is documented. |
| DODEX delays mainnet 6+ months | Phase-1 prep wasted. Mitigation: every phase delivers value standalone, all phases gated by feature flag. We stop where we stop. |
| Confused operator | Two execution paths in the UI → wrong button pressed → trade on wrong venue. Mitigation: explicit confirmation step + per-page venue badge. |

## My recommendation

**Yes to Phase 0 (this folder). Yes to Phase 1 (read-only probe) when DODEX
testnet whitelist access is available.** Phases 2+ get re-evaluated when
the limit-order book and liquidation engine are documented and DODEX has a
public mainnet date.

The trading journal absorbs the architecture extension cheaply. The risk
is concentrated in **(a) the maturity of the protocol itself** and **(b)
key custody**. Both are addressable by phasing slowly and capping the
DODEX-side bankroll until we have weeks of clean testnet operation.

## Open questions for the operator

Numbered for ease of reply. Anything you skip, I'll assume the default in
brackets.

### Timing + status

1. **DODEX mainnet target** — do you have a confirmed launch window, or
   is "in a few weeks" community-sourced? [Default: assume unknown,
   plan for testnet-only for the next 3 months.]
2. **Whitelist access** — do you (or will you) have whitelist access on
   the testnet? If not, do we need to apply through dex.do? [Default:
   apply when we move to Phase 1.]

### Scope + capital

3. **Capital allocation** — what fraction of the auto-trader bankroll
   would you allocate to DODEX initially? E.g., 10% of equity, hard
   cap at $25? [Default: $10 hard-cap until Phase 4.]
4. **Asset scope** — which pairs do you care about? Only SHELL/USDC (the
   only documented one), or are you waiting for specific listings? [Default:
   SHELL/USDC only at Phase 1, evaluate adding pairs at Phase 4.]
5. **Trading style** — block-time latency makes scalp marginal. Are you
   imagining DODEX as a swing / position venue, or do you want scalp
   capability? [Default: swing + position only.]
6. **Manual or auto?** — operator-only UI, auto-trader only, or both?
   [Default: manual UI first (Phase 3), auto-trader second (Phase 4).]

### Architecture

7. **SDK choice** — strong preference for Python ctypes vs Node sidecar
   vs other? [Default: Node sidecar in Phase 1, re-evaluate for Phase 4.]
8. **Key custody** — hot wallet on the Pi (convenient) vs hardware
   wallet (safe, much harder to script) vs multisig with off-Pi
   co-signer? [Default: single hot key on the Pi, hard tx caps, small
   working balance.]
9. **Privacy default** — when shielded operations land, do you want the
   auto-trader to default to PrivateNote-mediated trading, or stay public
   for simplicity? [Default: public until Phase 5.]

### Failure handling

10. **If DODEX delays 6+ months** — do we deprecate the integration
    effort or maintain partial scaffolding? [Default: maintain Phase 0+1
    indefinitely as cheap-to-keep prep; abandon higher phases if no signal
    of imminent launch.]
11. **If DODEX changes contract shape** — do we keep chasing, or freeze
    integration and wait for stability? [Default: freeze + wait, with a
    quarterly check.]

You don't have to answer all of these right now. Even partial answers
shape Phase 1's design. Anything you don't answer, I'll proceed with the
defaults above when we get to the next phase.
