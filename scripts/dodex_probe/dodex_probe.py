#!/usr/bin/env python3
"""
dodex_probe.py — Python orchestrator for the DODEX Phase 1 read-only probe.

Spawns scripts/dodex_probe/dodex_probe.js as a child process, pipes
JSON-line requests in, reads JSON-line responses out, and prints a short
PASS/FAIL report.

Designed to be run MANUALLY by the operator (or the assistant on demand).
It is NOT wired into the trading-journal service. It signs no transactions.
It writes no DB rows.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENDPOINTS = json.loads((HERE / "endpoints.json").read_text())


# ── Sidecar ──────────────────────────────────────────────────────────────────

class Sidecar:
    def __init__(self) -> None:
        sidecar_js = HERE / "dodex_probe.js"
        if not (HERE / "node_modules").exists():
            print("⚠  node_modules/ missing — run `npm install` in this directory first")
            print(f"   cd {HERE} && npm install")
            sys.exit(2)
        self.proc = subprocess.Popen(
            ["node", str(sidecar_js)],
            cwd=HERE,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._id = 0

    def call(self, op: str, args: dict | None = None, timeout_s: int = 25) -> dict:
        self._id += 1
        msg = {"id": self._id, "op": op, "args": args or {}}
        line = json.dumps(msg) + "\n"
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

        t0 = time.time()
        while True:
            if time.time() - t0 > timeout_s:
                return {"ok": False, "error": "timeout"}
            resp_line = self.proc.stdout.readline()
            if not resp_line:
                # sidecar died — capture stderr
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                return {"ok": False, "error": "sidecar died", "stderr": stderr.strip()}
            try:
                return json.loads(resp_line)
            except json.JSONDecodeError:
                # garbage from a console.log in node — skip and keep reading
                continue

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass


# ── Steps ────────────────────────────────────────────────────────────────────

def step(name: str, fn) -> bool:
    print(f"  → {name} ...", end=" ", flush=True)
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL ({time.time()-t0:.2f}s)")
        print(f"      exception: {type(e).__name__}: {e}")
        return False
    elapsed = time.time() - t0
    if ok:
        print(f"PASS ({elapsed:.2f}s)")
        if detail:
            print(f"      {detail}")
    else:
        print(f"FAIL ({elapsed:.2f}s)")
        print(f"      {detail}")
    return ok


def run(network: str = "mainnet") -> int:
    net = ENDPOINTS.get(network)
    if not net:
        print(f"✗ unknown network '{network}', use one of: {list(ENDPOINTS)}")
        return 2

    print(f"\nDODEX Phase 1 probe — {net['name']}")
    print(f"endpoint root: {net['root']}\n")

    sc = Sidecar()
    passes = 0
    fails = 0

    # 1. sidecar sanity check
    def ping():
        r = sc.call("ping")
        return (r.get("ok"), f"sdk_version_hint={r.get('result',{}).get('sdk_version_hint')}")
    if step("sidecar ping (Node + TVM-SDK loaded)", ping):
        passes += 1
    else:
        fails += 1
        sc.close()
        print(f"\n  aborting — sidecar not healthy")
        return 1

    # 2. for each GraphQL candidate, try node_info
    candidates = net.get("graphql_candidates") or [net.get("graphql")]
    candidates = [c for c in candidates if c]
    working_endpoint = None
    for endpoint in candidates:
        def query(ep=endpoint):
            r = sc.call("node_info", {"endpoint": ep})
            if r.get("ok"):
                return (True, f"{ep} ← OK · {json.dumps(r.get('result'))[:160]}")
            return (False, f"{ep} ← {r.get('error','?')[:120]}")
        if step(f"GraphQL discovery: {endpoint}", query):
            working_endpoint = endpoint
            passes += 1
            break
        fails += 1

    if not working_endpoint:
        print("\n  ✗ no GraphQL endpoint responded — Phase 1 cannot proceed until")
        print("    we find the live endpoint or DODEX docs publish one.")
        sc.close()
        return 1

    # 3. block height check
    def block():
        r = sc.call("block_height", {"endpoint": working_endpoint})
        if r.get("ok") and r.get("result"):
            top = (r["result"] or [{}])[0]
            return (True, f"top block seq_no={top.get('seq_no')} gen_utime={top.get('gen_utime')}")
        return (False, str(r.get("error") or "no result"))
    if step("query top block", block): passes += 1
    else: fails += 1

    # ── Summary ──────────────────────────────────────────────────────────────
    sc.close()
    print(f"\n{passes} passed · {fails} failed")
    print(f"working endpoint: {working_endpoint}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--network", default="mainnet",
                    choices=list(ENDPOINTS.keys()) if "_comment" not in ENDPOINTS else None,
                    help="endpoint key in endpoints.json")
    args = ap.parse_args()
    sys.exit(run(args.network))
