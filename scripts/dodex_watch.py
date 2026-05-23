#!/usr/bin/env python3
"""
dodex_watch.py — monitor upstream DODEX + Acki Nacki sources for changes.

Designed to run unattended via cron, twice per week. On each run:

1. Fetches each target URL (GitHub APIs return JSON; docs return text).
2. Computes a content hash and compares it against the previous hash on disk.
3. If anything moved, writes the new content + emits a short summary.
4. Optionally posts the summary to Telegram (when TELEGRAM_BOT_TOKEN is set
   and the journal's `telegram_notify` module is available).

State lives in `data/dodex_watch/` (excluded from rsync deploy).

Outputs:
- data/dodex_watch/<name>.hash      — last seen content hash
- data/dodex_watch/<name>.body      — last seen body (for offline diff)
- data/dodex_watch/last_run.json    — one-line per target status
- stdout                            — human-readable summary

NOT in scope: it does NOT pull contract code or trigger any DODEX
interaction. It is read-only over public HTTPS.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# ── Targets ──────────────────────────────────────────────────────────────────

# (name, url, kind) — kind is just metadata for the summary line.
WATCH_TARGETS: list[tuple[str, str, str]] = [
    # GitHub APIs — JSON, easy diffs on commit SHA + release tag
    ("repo:ackinacki:commits",
     "https://api.github.com/repos/ackinacki/ackinacki/commits/main",
     "github-commit"),
    ("repo:ackinacki:releases",
     "https://api.github.com/repos/ackinacki/ackinacki/releases?per_page=5",
     "github-releases"),
    ("repo:tvm-sdk:commits",
     "https://api.github.com/repos/tvmlabs/tvm-sdk/commits/main",
     "github-commit"),
    ("repo:tvm-sdk:releases",
     "https://api.github.com/repos/tvmlabs/tvm-sdk/releases?per_page=5",
     "github-releases"),

    # Doc surfaces — pull the .md / .txt variants since they diff cleanly
    ("docs:dex.do",
     "https://dev.ackinacki.com/dex.do.md",
     "docs"),
    ("docs:llms-full",
     "https://dev.ackinacki.com/llms-full.txt",
     "docs"),
    ("docs:sdk",
     "https://dev.ackinacki.com/acki-nacki-sdk/untitled.md",
     "docs"),
    ("docs:abi",
     "https://dev.ackinacki.com/abi/abi.md",
     "docs"),
    ("docs:getting-started",
     "https://docs.ackinacki.com/for-developers/getting-started-with-acki-nacki.md",
     "docs"),

    # dex.do landing — HTML, hash the whole body
    ("landing:dex.do",
     "https://www.dex.do",
     "landing"),
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _state_dir() -> Path:
    """Where we persist hashes + last-seen bodies. Outside the repo so it
    survives rsync (which excludes `data/`)."""
    here = Path(__file__).resolve().parent
    root = here.parent  # repo root
    d = root / "data" / "dodex_watch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _fetch(url: str, timeout: int = 20) -> Optional[bytes]:
    """HTTP GET. Returns body bytes or None on error. Sends a UA so GitHub
    won't reject us."""
    req = Request(url, headers={
        "User-Agent": "trading-journal-dodex-watch/1.0",
        "Accept": "application/json, text/plain, text/markdown, */*",
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as e:
        # 304 / 404 etc. — surface as "no body"
        print(f"  http error {e.code} on {url}", file=sys.stderr)
        return None
    except URLError as e:
        print(f"  network error on {url}: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  unexpected error on {url}: {e!r}", file=sys.stderr)
        return None


def _short_summary_line(name: str, kind: str, body: bytes) -> str:
    """Per-target one-liner with the most informative slice of the response.
    GitHub APIs → first SHA / first release tag. Docs → first 80 chars."""
    if kind == "github-commit":
        try:
            obj = json.loads(body)
            sha = (obj.get("sha") or "")[:12]
            msg = (obj.get("commit") or {}).get("message", "").split("\n", 1)[0][:80]
            return f"latest commit {sha} — {msg}"
        except Exception:
            return body[:80].decode("utf-8", "replace")
    if kind == "github-releases":
        try:
            arr = json.loads(body)
            if not arr:
                return "no releases"
            top = arr[0]
            return f"latest release {top.get('tag_name','?')} ({top.get('published_at','?')})"
        except Exception:
            return body[:80].decode("utf-8", "replace")
    # docs / landing
    text = body.decode("utf-8", "replace").strip()
    head = " ".join(text.split())[:120]
    return head


# ── Main ─────────────────────────────────────────────────────────────────────

def run(quiet: bool = False, telegram: bool = False) -> int:
    state = _state_dir()
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    changes: list[tuple[str, str]] = []   # (name, summary_line)
    unchanged_count = 0
    error_count = 0
    last_run_log: list[dict] = []

    for name, url, kind in WATCH_TARGETS:
        if not quiet:
            print(f"[{now}] checking {name} ({url})")
        body = _fetch(url)
        target_state = {"name": name, "url": url, "kind": kind, "ts": now}
        if body is None:
            target_state["status"] = "error"
            error_count += 1
            last_run_log.append(target_state)
            continue

        new_hash = _hash_body(body)
        hash_file = state / f"{name.replace(':', '_').replace('/', '_')}.hash"
        body_file = state / f"{name.replace(':', '_').replace('/', '_')}.body"

        prev_hash = hash_file.read_text().strip() if hash_file.exists() else None
        target_state["hash"] = new_hash[:16]

        if prev_hash == new_hash:
            target_state["status"] = "unchanged"
            unchanged_count += 1
        else:
            target_state["status"] = "first-seen" if prev_hash is None else "changed"
            summary = _short_summary_line(name, kind, body)
            changes.append((name, summary))
            hash_file.write_text(new_hash)
            body_file.write_bytes(body)

        last_run_log.append(target_state)

    (state / "last_run.json").write_text(json.dumps({
        "ts": now,
        "targets": last_run_log,
        "summary": {
            "checked": len(WATCH_TARGETS),
            "changed": len(changes),
            "unchanged": unchanged_count,
            "errors": error_count,
        },
    }, indent=2))

    # Stdout report
    if changes:
        print(f"\n=== DODEX UPSTREAM CHANGES @ {now} ===")
        for name, summary in changes:
            print(f"  CHANGED  {name}")
            print(f"           {summary}")
        print(f"\n({len(changes)} changed · {unchanged_count} unchanged · {error_count} errors)")
    elif not quiet:
        print(f"\nno upstream changes ({unchanged_count} targets quiet, {error_count} errors)")

    # Optional Telegram push — best-effort, ignore failures
    if telegram and changes:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            import telegram_notify  # type: ignore
            msg_lines = [f"📡 DODEX upstream changes ({len(changes)}) @ {now}"]
            for name, summary in changes:
                msg_lines.append(f"\n• {name}\n  {summary[:160]}")
            telegram_notify.send_message("\n".join(msg_lines))
        except Exception as e:  # noqa: BLE001
            print(f"telegram push failed: {e!r}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quiet", action="store_true",
                    help="suppress per-target status; only show changes")
    ap.add_argument("--telegram", action="store_true",
                    help="push the change summary to Telegram if anything moved")
    args = ap.parse_args()
    sys.exit(run(quiet=args.quiet, telegram=args.telegram))
