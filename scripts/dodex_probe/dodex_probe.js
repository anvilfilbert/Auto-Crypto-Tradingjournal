/**
 * dodex_probe.js — Node sidecar for the DODEX Phase 1 read-only probe.
 *
 * Talks to Acki Nacki via the official TVM-SDK (@tvmsdk/lib-node).
 * Communicates with the Python orchestrator via newline-delimited JSON
 * on stdin / stdout. NEVER signs or submits a transaction.
 *
 * Protocol:
 *   stdin  ← {"id": <int>, "op": <op>, "args": {...}}
 *   stdout → {"id": <int>, "ok": true/false, "result": ..., "error": ...}
 *
 * Supported ops (all read-only):
 *   "ping"          — sanity check, returns {pong: true, sdk_version: <str>}
 *   "node_info"     — query node version / lastBlockTime via GraphQL
 *   "block_height"  — current last seen block
 *   "account_state" — fetch a public address's state (balance, code hash)
 *   "run_local"     — emulate a get-method call on a contract (off-chain)
 *
 * To add a write capability later, declare it explicitly here AND require
 * a separate "write_unlock" op so it's hard to ship by accident.
 */
'use strict';

const readline = require('node:readline');

// Lazy-load the SDK so the script can fail with a friendly message if
// `npm install` hasn't been run yet.
let TonClient, libNode;
try {
    ({ TonClient } = require('@tvmsdk/core'));
    libNode = require('@tvmsdk/lib-node');
} catch (e) {
    process.stdout.write(JSON.stringify({
        id: 0, ok: false,
        error: 'tvm-sdk not installed — run `npm install` in scripts/dodex_probe/',
        detail: String(e),
    }) + '\n');
    process.exit(1);
}

// SDK bootstrap
TonClient.useBinaryLibrary(libNode.libNode);

// One client per endpoint, cached
const clients = new Map();
function getClient(endpoint) {
    if (clients.has(endpoint)) return clients.get(endpoint);
    const c = new TonClient({ network: { endpoints: [endpoint] } });
    clients.set(endpoint, c);
    return c;
}

// ── Handlers ────────────────────────────────────────────────────────────────

async function handle(msg) {
    const { id, op, args = {} } = msg;
    try {
        switch (op) {
            case 'ping':
                return { id, ok: true, result: { pong: true, sdk_version_hint: TonClient.version || 'unknown' } };

            case 'node_info': {
                const client = getClient(args.endpoint);
                const versionRes = await client.net.query({
                    query: '{ info { version time lastBlockTime } }',
                });
                return { id, ok: true, result: versionRes };
            }

            case 'block_height': {
                const client = getClient(args.endpoint);
                const res = await client.net.query_collection({
                    collection: 'blocks',
                    filter: { workchain_id: { eq: 0 } },
                    order: [{ path: 'seq_no', direction: 'DESC' }],
                    limit: 1,
                    result: 'id seq_no gen_utime',
                });
                return { id, ok: true, result: res.result };
            }

            case 'account_state': {
                const client = getClient(args.endpoint);
                if (!args.address) throw new Error('account_state requires `address`');
                const res = await client.net.query_collection({
                    collection: 'accounts',
                    filter: { id: { eq: args.address } },
                    result: 'id acc_type balance code_hash last_paid',
                });
                return { id, ok: true, result: res.result };
            }

            case 'run_local': {
                const client = getClient(args.endpoint);
                if (!args.address) throw new Error('run_local requires `address`');
                if (!args.abi) throw new Error('run_local requires `abi`');
                if (!args.method) throw new Error('run_local requires `method`');
                const acc = await client.net.query_collection({
                    collection: 'accounts',
                    filter: { id: { eq: args.address } },
                    result: 'boc',
                });
                if (!acc.result || acc.result.length === 0) {
                    return { id, ok: false, error: 'account not found' };
                }
                const res = await client.tvm.run_tvm({
                    account: acc.result[0].boc,
                    message: {
                        abi: { type: 'Contract', value: args.abi },
                        address: args.address,
                        call_set: { function_name: args.method, input: args.input || {} },
                        signer: { type: 'None' },
                        is_internal: false,
                    },
                });
                return { id, ok: true, result: res.decoded };
            }

            default:
                return { id, ok: false, error: `unknown op ${op}` };
        }
    } catch (e) {
        return { id, ok: false, error: String(e && e.message || e) };
    }
}

// ── Main I/O loop ───────────────────────────────────────────────────────────

const rl = readline.createInterface({ input: process.stdin });

rl.on('line', async (line) => {
    line = line.trim();
    if (!line) return;
    let msg;
    try {
        msg = JSON.parse(line);
    } catch (e) {
        process.stdout.write(JSON.stringify({ id: 0, ok: false, error: 'invalid json on stdin', detail: String(e) }) + '\n');
        return;
    }
    const reply = await handle(msg);
    process.stdout.write(JSON.stringify(reply) + '\n');
});

rl.on('close', () => process.exit(0));
