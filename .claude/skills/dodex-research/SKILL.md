---
name: dodex-research
description: Use when the user asks about Acki Nacki blockchain, DODEX dark-order DEX, the planned DODEX module integration, or anything in docs/dodex/. Loads the canonical references and locked-in decisions so a session can engage with DODEX work without re-fetching upstream docs. Trigger phrases include "DODEX", "Acki Nacki", "dex.do", "check DODEX upstream", "run Phase 1 probe", "TVM-Solidity", "shielded order", "NACKL", "SHELL token", or any time the conversation pivots to the in-app DODEX page (`/#dodex`).
---

# DODEX Research Skill

You have been invoked because the conversation has pivoted to the **Acki Nacki / DODEX integration**. This skill loads the persistent context — what we already know, what's been decided, what's installed on the Pi, and what's still open. Use it to engage productively without re-fetching upstream docs.

## Three things to know first

1. **DODEX is the upcoming dark-order DEX built on the Acki Nacki blockchain.** The chain is mainnet-live since 2025-10-07. The DEX itself is still in build — contracts and developer docs are dropping incrementally on `github.com/ackinacki/ackinacki` and `dev.ackinacki.com/dex.do`. Operator estimate for public release: a few weeks (not committed).
2. **The trading journal has no live DODEX integration.** Two scripts are installed on the Pi (`scripts/dodex_watch.py` upstream monitor + `scripts/dodex_probe/` Phase 1 read-only probe) but neither runs automatically and neither is wired into the auto-trader.
3. **All decisions are documented in `docs/dodex/` — 7 markdown files, ~815 lines.** Always start there before suggesting changes.

## The canonical references (read these first)

| File | When to consult it |
|---|---|
| `docs/dodex/README.md` | Always — orients to the rest |
| `docs/dodex/01-overview.md` | "What is DODEX? What's the status?" |
| `docs/dodex/02-acki-nacki-blockchain.md` | TVM-Solidity, consensus model, SDK options, message wire format, Ed25519 signing |
| `docs/dodex/03-dodex-mechanics.md` | Contract map, order lifecycle, Tornado-style shielded notes |
| `docs/dodex/04-cex-vs-dex-translation.md` | Bitget → DODEX concept-by-concept (auth, balances, orders, etc.) |
| `docs/dodex/05-integration-blueprint.md` | Proposed `trading/dodex/` package layout, SDK choice, custody, 5-phase rollout |
| `docs/dodex/06-risks-and-decision-memo.md` | For/against, worst-case consequences, open questions list with defaults |
| `docs/dodex/07-upstream-watch.md` | What we monitor + the watch script's manifest |

There's also an in-app reminder page at `/#dodex` (rendered from `templates/index.html`, the `#page-dodex` block) that mirrors the same information in dashboard form.

## Locked-in decisions (operator-confirmed 2026-05-23)

| Decision | Value |
|---|---|
| SDK strategy | **Node.js sidecar** (option B). Python orchestrator spawns Node child process running `@tvmsdk/lib-node` + `@tvmsdk/core`. Communicate via newline-delimited JSON on stdin/stdout. |
| Mainnet endpoint | `https://mainnet.ackinacki.org/` · live since 2025-10-07. The `/graphql` subpath returned 502 on 2026-05-23 — actual GraphQL URL TBD via Phase 1 discovery. |
| Token roles | **NACKL** = main / transferable / traded asset. **SHELL** = gas / transaction-cost token. **VMSHELL** = in-contract form of SHELL (1:1 convertible at execution time). |
| Asset scope | Multi-pair from launch · cross-chain bridges to other L1s are also WIP. |
| Phases approved | 0 (knowledge) + 1 (read-only probe) + 2 (module skeleton, no trading). 3-5 deferred. |
| Upstream monitoring | On-demand only · no cron · no Telegram. Operator says "check DODEX upstream" → assistant SSHs and runs the watcher. ~2× / week expected cadence. |
| Privacy primitives | Tornado-style: ZK deposit proof → Nullifier records the hash → deterministic PrivateNote deploys → balance shielded inside. |

## What's already installed on the Pi (passive)

```
~/trading-journal/
├── docs/dodex/                    # 7 knowledge files
├── scripts/
│   ├── dodex_watch.py             # upstream change monitor (Python)
│   └── dodex_probe/               # Phase 1 read-only probe
│       ├── package.json           # @tvmsdk/core + @tvmsdk/lib-node
│       ├── dodex_probe.js         # Node sidecar (read-only ops)
│       ├── dodex_probe.py         # Python orchestrator
│       ├── endpoints.json         # known mainnet + testnet URLs
│       ├── README.md
│       └── node_modules/          # installed; gitignored
└── data/dodex_watch/              # hash baselines (gitignored)
```

Pi runtime: Node v22.22.2 + npm 10.9.7 (pre-existing) · TVM-SDK v2.24.21.

## Quick-action vocabulary (operator → assistant)

| Operator says | You do |
|---|---|
| "check DODEX upstream" | SSH the Pi → `cd /home/<user>/trading-journal && python3 scripts/dodex_watch.py` → report any CHANGED targets. The watcher hashes 10 sources; only changes are reported. |
| "run Phase 1 probe" | SSH the Pi → `python3 scripts/dodex_probe/dodex_probe.py --network mainnet` → report PASS/FAIL per step. First-time run will discover the live GraphQL endpoint among the candidates in `endpoints.json`. |
| "start Phase 2" | Implement the module skeleton per `docs/dodex/05-integration-blueprint.md` — `trading/dodex/` package, `FUTURES_AI_DODEX_ENABLED=0` flag, read-only `/api/dodex/state` route, UI page tied to live balances. No writes. |
| "freeze DODEX work" | `git revert 722ebc6 64cd697` cleanly removes all DODEX scaffolding (knowledge files + nav page + scripts). |

## Open questions (still pending answers)

These gate Phase 3+ decisions and have documented defaults in `docs/dodex/06-risks-and-decision-memo.md`:

- Whitelist application on dex.do
- Capital allocation to DODEX (default $10 hard-cap)
- Trading style on DODEX (default: swing + position, scalp deferred — block-time latency makes scalp marginal)
- Manual UI first or auto-trader first (default: manual)
- Key custody approach (default: single hot key on Pi · chmod 600 · hard tx caps)
- Privacy default — PrivateNote-mediated trading or public (default: public)

Don't act on these defaults silently — surface the question when it matters.

## Critical "don't"s

- **Don't** assume the marketing claims on dex.do match the dev docs. The landing page advertises an order-book DEX with x1000 leverage; the docs currently only document a fixed-rate SHELL↔USDC accumulator. The general limit-order primitives are **not yet published**.
- **Don't** activate the probe without operator approval — it's installed but deliberately not running.
- **Don't** confuse SHELL (gas) with NACKL (main) — older Acki Nacki dev docs called SHELL "the standard token" but the operator has clarified that NACKL is the main asset on DODEX.
- **Don't** confuse the testnet (`shellnet.ackinacki.org`) with mainnet (`mainnet.ackinacki.org`). DODEX in-build contracts still live on testnet; production chain is mainnet.
- **Don't** try to use EVM tooling (Hardhat, ethers.js, web3.py, foundry). Acki Nacki is TVM-family — TVM-Solidity compiles to TVM bytecode, not EVM. The toolchain is `@tvmsdk/*` and `tvm-cli`.

## When the docs/code might be stale

If the conversation involves DODEX work that didn't move recently, run the upstream watcher first to refresh the picture. The script's last_run summary lives at `data/dodex_watch/last_run.json` on the Pi.

After a confirmed CHANGED target in the watcher output, re-read the relevant `docs/dodex/0X-*.md` file to see if anything in the doc is now wrong, and propose an update before continuing implementation.
