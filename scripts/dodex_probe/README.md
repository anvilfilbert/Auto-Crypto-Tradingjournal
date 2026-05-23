# DODEX Phase 1 — Read-Only Probe

*Installed on the Pi but **NOT** activated. Run manually only when you want
to test connectivity to Acki Nacki + the TVM-SDK Node sidecar.*

## What this probe does

A read-only check that we can:

1. Reach the Acki Nacki **mainnet** endpoint (`https://mainnet.ackinacki.org/`).
2. Talk to the TVM-SDK via Node sidecar (`@tvmsdk/lib-node`) without errors.
3. Query basic chain state — node version, last block, current time.
4. Read an account's state (any public address — by default a well-known
   contract or our future wallet).
5. Optionally call a get-method on a contract.

**It does NOT** sign or submit any transaction. It holds no private keys.
It writes no DB rows. It is safe to run.

## Why a Node sidecar

The trading journal is Python. The official `@tvmsdk/lib-node` package is
Node.js. Rather than reinvent BoC serialisation + Ed25519 signing in
Python, we spawn a small Node process that exposes the SDK's capabilities
as a JSON-line protocol over stdin/stdout. The Python orchestrator pipes
requests in and reads JSON responses.

This pattern keeps the Python code clean and lets us upgrade the SDK by
bumping a single npm version.

## Files

| File | Purpose |
|---|---|
| `package.json` | Node deps — `@tvmsdk/core`, `@tvmsdk/lib-node` |
| `dodex_probe.js` | Node sidecar — reads JSON commands on stdin, writes JSON results on stdout |
| `dodex_probe.py` | Python orchestrator — spawns the sidecar, sends a handful of test commands, prints a human report |
| `endpoints.json` | Known endpoints (mainnet + testnet). Editable, not committed-private. |

## Setup (Pi)

```bash
cd /home/<user>/trading-journal/scripts/dodex_probe
npm install
```

This drops a `node_modules/` next to the script. The trading-journal repo's
`.gitignore` (and the rsync deploy script) should keep `node_modules/` out of
sync — they're rebuilt locally on the Pi.

## Running the probe (manual only)

```bash
cd /home/<user>/trading-journal
python3 scripts/dodex_probe/dodex_probe.py --network mainnet
```

Expected output: a short report listing each probe step (endpoint reach,
SDK init, version query, get-method call) with PASS / FAIL per step.

## Switching to testnet

DODEX itself is still on testnet (`shellnet.ackinacki.org`). To probe DODEX
contracts (when their addresses become public) use:

```bash
python3 scripts/dodex_probe/dodex_probe.py --network testnet
```

## What this is NOT

- **Not** an auto-trader. It cannot place orders.
- **Not** part of `trading-journal` service. The systemd unit doesn't
  invoke it. The scanner doesn't trigger it. Nothing in the running app
  knows it exists.
- **Not** in cron. (The upstream-watch script IS in cron; this probe is
  not.)
- **Not** holding keys. We add key management only when we move to
  Phase 3.

## When to actually run it

Per operator decision 2026-05-23:
- **NOT yet.** The probe is installed for the day DODEX needs verification.
- Re-run when DODEX docs/contracts move (the watch script will surface
  that), or when we move to Phase 2 (UI page wired to read-only state).
