# 07 — Upstream watch (DODEX repo + docs change monitoring)

*Goal: notice when DODEX contracts or developer docs change so we don't
build against a stale snapshot.*

## What to watch

| Surface | URL | What we care about |
|---|---|---|
| Acki Nacki node monorepo | `github.com/ackinacki/ackinacki` | New releases, contract changes in `tvm_contracts/` or `contracts/`, mainnet-related commits |
| TVM-SDK | `github.com/tvmlabs/tvm-sdk` | New SDK releases (npm `@tvmsdk/*` versions), Node bindings updates |
| DODEX dev docs root | `dev.ackinacki.com/dex.do` | Removal of `(Work in progress)` tags, new contracts published, order-book / leverage / liquidation primitives appearing |
| DODEX dev docs full corpus | `dev.ackinacki.com/llms-full.txt` | Easiest one-shot diff target — single text file containing all dev docs |
| Acki Nacki docs root | `docs.ackinacki.com` | Tokenomics / SHELL vs NACKL clarifications, mainnet announcements |
| dex.do landing | `dex.do` | Whitelist status, asset listings, fee model numbers |

## Cadence

**On-demand only.** The operator pings the assistant when they want a
check ("check DODEX upstream"). The assistant SSH's into the Pi and runs
the watch script. ~Two checks per week is the operator's expected cadence
for this phase. No cron, no Telegram push.

Rationale: low-frequency upstream changes don't warrant automated
notifications. The operator is closer to the Acki Nacki community than
the assistant is — they'll often know about a release before the script
sees it.

## Watch-script outline (NOT yet implemented)

A single Python script — `scripts/dodex_watch.py` — that:

1. Reads a manifest of (url, name) pairs.
2. Fetches each URL (or for GitHub repos, the last commit SHA + release tag
   via `gh api repos/{owner}/{repo}/commits/main` and `…/releases/latest`).
3. Hashes the response body + writes to `data/dodex_watch/<name>.sha`.
4. Compares against the previous hash. If changed, writes the new content
   to `data/dodex_watch/<name>.txt` and emits a one-liner summary listing
   what moved.
5. Optionally — when wired to Telegram via the existing `telegram_notify`
   module — pushes the summary so we don't have to remember to check.

No code yet; this file just documents the plan.

## Concrete URLs to put in the manifest

```python
WATCH_TARGETS = [
    # GitHub repos — use commits API + releases
    ("repo:ackinacki", "https://api.github.com/repos/ackinacki/ackinacki/commits/main"),
    ("repo:ackinacki:releases", "https://api.github.com/repos/ackinacki/ackinacki/releases?per_page=5"),
    ("repo:tvm-sdk", "https://api.github.com/repos/tvmlabs/tvm-sdk/commits/main"),
    ("repo:tvm-sdk:releases", "https://api.github.com/repos/tvmlabs/tvm-sdk/releases?per_page=5"),

    # Doc surfaces — pull the .md / .txt variants since they diff cleanly
    ("docs:dex.do",   "https://dev.ackinacki.com/dex.do.md"),
    ("docs:llms-full","https://dev.ackinacki.com/llms-full.txt"),
    ("docs:sdk",      "https://dev.ackinacki.com/acki-nacki-sdk/untitled.md"),
    ("docs:abi",      "https://dev.ackinacki.com/abi/abi.md"),
    ("docs:ackinacki-getting-started",
                      "https://docs.ackinacki.com/for-developers/getting-started-with-acki-nacki.md"),

    # dex.do landing — HTML, hash whole body
    ("landing:dex.do", "https://www.dex.do"),
]
```

## What we'd act on

| Change observed | Action |
|---|---|
| `(Work in progress)` removed from any DODEX contract | Re-read that contract's spec, update `03-dodex-mechanics.md`. |
| New contract appearing in dev docs | Append to contract map in `03-dodex-mechanics.md`. |
| Limit-order or leverage spec published | This is the gate for Phase 3+. Decide whether to start that phase. |
| TVM-SDK major version bump | Re-evaluate Node sidecar dependency, check breaking changes. |
| `ackinacki/ackinacki` release tagged | Check release notes; cross-reference with our integration. |
| dex.do whitelist mechanism changes | Apply / re-apply if needed. |

## Open: who runs the script

Once we go to Phase 1 (read-only probe), the watch script becomes worth
automating. Until then I'll re-run the WebFetches above when you ask me to
"check upstream" or when we touch DODEX work again.
