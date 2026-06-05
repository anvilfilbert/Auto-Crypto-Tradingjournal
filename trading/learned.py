"""
L-0 (Master plan Week 2): the accessor layer for learned_params.

Code holds the algorithm; the database holds the parameters.

Public API:
  get(key, default)                       — simple read with default fallback
  get_or(key, *, symbol=None, archetype=None, default=None)
                                          — composite-key lookup with fallbacks
  set(key, value, *, learner_name, ...)   — write + auto-log to learner_log
  pin(key, value, reason)                 — operator override (sticks until unpin)
  unpin(key)                              — release pin, revert to default
  revert(key, reason)                     — restore to default; increments revert_count
  all_params()                            — dict snapshot for daily report
  recent_log(limit=20)                    — recent learner_log entries

Composite-key format:
  symbol_modifier.{symbol}                — per-symbol score modifier
  consensus_min_score.{archetype}         — per-archetype threshold
  session_modifier.{session}.{direction}  — per-session per-direction modifier
  ...

The accessor returns the most specific match it finds. For example,
get_or("session_modifier", session="NY-AM", direction="Long") tries:
  session_modifier.NY-AM.Long  → if present, return
  session_modifier.NY-AM       → fallback (direction-blind)
  session_modifier             → fallback (global)
  → default

This lets learners write at any granularity they have confidence in.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)


# ─── Read ─────────────────────────────────────────────────────────────────

def _typed_value(row: dict) -> Any:
    """Decode a learned_params row to its Python value type."""
    if not row:
        return None
    raw = row.get("value")
    vt = (row.get("value_type") or "json").lower()
    if raw is None:
        return None
    try:
        if vt == "float":
            return float(raw)
        if vt == "int":
            return int(raw)
        if vt == "bool":
            return str(raw).lower() in ("1", "true", "yes")
        if vt == "str":
            return str(raw)
        # default — JSON
        return json.loads(raw)
    except Exception:
        return raw


def get(conn, key: str, default: Any = None) -> Any:
    """Direct key lookup. No fallback chain. Returns default if not found."""
    try:
        row = conn.execute(
            "SELECT value, value_type FROM learned_params WHERE key = ?",
            (key,),
        ).fetchone()
        if not row:
            return default
        v = _typed_value(dict(row))
        return v if v is not None else default
    except Exception as e:
        _log.warning("learned.get(%s) failed: %s", key, e)
        return default


def get_or(conn, key_base: str, *, symbol: Optional[str] = None,
           archetype: Optional[str] = None,
           session: Optional[str] = None,
           direction: Optional[str] = None,
           dow: Optional[str] = None,
           default: Any = None) -> Any:
    """Composite-key lookup with most-specific-first fallback.

    Tries each combination of the provided dimensions, from most specific
    to least, until one returns a non-None value or all are exhausted.
    """
    # Build candidate keys, most-specific first
    parts: list[str] = []
    if symbol: parts.append(symbol)
    if archetype: parts.append(archetype)
    if session: parts.append(session)
    if direction: parts.append(direction)
    if dow: parts.append(dow)
    candidates: list[str] = []
    while parts:
        candidates.append(key_base + "." + ".".join(parts))
        parts.pop()  # drop least-significant from the end
    candidates.append(key_base)  # bare key as final fallback
    for k in candidates:
        v = get(conn, k, default=None)
        if v is not None:
            return v
    return default


# ─── Write ────────────────────────────────────────────────────────────────

def _encode(value: Any) -> tuple[str, str]:
    """Pick value_type + serialize accordingly."""
    if isinstance(value, bool):
        return ("bool", "1" if value else "0")
    if isinstance(value, int):
        return ("int", str(value))
    if isinstance(value, float):
        return ("float", str(value))
    if isinstance(value, str):
        return ("str", value)
    return ("json", json.dumps(value))


def set(conn, key: str, value: Any, *,
        learner_name: str,
        action: str = "applied",
        gate_reason: Optional[str] = None,
        sample_size: Optional[int] = None,
        ci_low: Optional[float] = None,
        ci_high: Optional[float] = None,
        p_value: Optional[float] = None,
        default_value: Any = None,
        payload: Optional[dict] = None) -> dict:
    """Write a parameter and log to learner_log atomically. Respects pinned
    rows (they're not overwritten — change is logged as 'skipped_pinned')."""
    value_type, serialized = _encode(value)
    # Read existing
    existing = conn.execute(
        "SELECT value, value_type, pinned, pinned_reason FROM learned_params WHERE key = ?",
        (key,),
    ).fetchone()
    old_value = existing["value"] if existing else None
    if existing and existing["pinned"]:
        # Log the skip
        conn.execute(
            "INSERT INTO learner_log (learner_name, param_key, old_value, new_value, "
            "action, gate_reason, sample_size, ci_low, ci_high, p_value, payload_json) "
            "VALUES (?, ?, ?, ?, 'skipped_pinned', ?, ?, ?, ?, ?, ?)",
            (learner_name, key, old_value, serialized,
             f"pinned: {existing['pinned_reason']}",
             sample_size, ci_low, ci_high, p_value,
             json.dumps(payload or {}) if payload else None),
        )
        conn.commit()
        return {"action": "skipped_pinned", "reason": existing["pinned_reason"]}
    # Write the param
    default_serialized = json.dumps(default_value) if default_value is not None else None
    conn.execute(
        "INSERT INTO learned_params (key, value, value_type, default_value, "
        "updated_at, sample_size, ci_low, ci_high, p_value) "
        "VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET "
        "  value = excluded.value, "
        "  value_type = excluded.value_type, "
        "  updated_at = excluded.updated_at, "
        "  sample_size = excluded.sample_size, "
        "  ci_low = excluded.ci_low, ci_high = excluded.ci_high, p_value = excluded.p_value",
        (key, serialized, value_type, default_serialized,
         sample_size, ci_low, ci_high, p_value),
    )
    # Log the write
    conn.execute(
        "INSERT INTO learner_log (learner_name, param_key, old_value, new_value, "
        "action, gate_reason, sample_size, ci_low, ci_high, p_value, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (learner_name, key, old_value, serialized,
         action, gate_reason, sample_size, ci_low, ci_high, p_value,
         json.dumps(payload or {}) if payload else None),
    )
    conn.commit()
    return {"action": action, "old": old_value, "new": serialized}


def log_skip(conn, key: str, learner_name: str, reason: str,
             sample_size: Optional[int] = None, payload: Optional[dict] = None) -> None:
    """Log a skipped decision (gate failed) without touching learned_params."""
    conn.execute(
        "INSERT INTO learner_log (learner_name, param_key, old_value, new_value, "
        "action, gate_reason, sample_size, payload_json) "
        "VALUES (?, ?, NULL, NULL, 'skipped_gate', ?, ?, ?)",
        (learner_name, key, reason, sample_size,
         json.dumps(payload or {}) if payload else None),
    )
    conn.commit()


# ─── Pinning (operator override) ──────────────────────────────────────────

def pin(conn, key: str, value: Any, reason: str) -> dict:
    """Operator override — pinned values are NEVER overwritten by learners."""
    value_type, serialized = _encode(value)
    conn.execute(
        "INSERT INTO learned_params (key, value, value_type, updated_at, pinned, pinned_reason) "
        "VALUES (?, ?, ?, datetime('now'), 1, ?) "
        "ON CONFLICT(key) DO UPDATE SET "
        "  value = excluded.value, value_type = excluded.value_type, "
        "  updated_at = excluded.updated_at, pinned = 1, pinned_reason = excluded.pinned_reason",
        (key, serialized, value_type, reason),
    )
    conn.execute(
        "INSERT INTO learner_log (learner_name, param_key, new_value, action, gate_reason) "
        "VALUES ('operator', ?, ?, 'pinned', ?)",
        (key, serialized, reason),
    )
    conn.commit()
    return {"action": "pinned", "key": key, "value": value, "reason": reason}


def unpin(conn, key: str) -> dict:
    """Release operator pin."""
    conn.execute(
        "UPDATE learned_params SET pinned = 0, pinned_reason = NULL WHERE key = ?",
        (key,),
    )
    conn.execute(
        "INSERT INTO learner_log (learner_name, param_key, action) VALUES ('operator', ?, 'unpinned')",
        (key,),
    )
    conn.commit()
    return {"action": "unpinned", "key": key}


# ─── Revert (safety circuit) ──────────────────────────────────────────────

def revert(conn, key: str, reason: str = "auto_revert") -> dict:
    """Revert a param to its default_value. Increments revert_count.
    The safety circuit calls this when post-change outcome degrades."""
    row = conn.execute(
        "SELECT value, default_value, revert_count FROM learned_params WHERE key = ?",
        (key,),
    ).fetchone()
    if not row:
        return {"action": "noop", "reason": "key not found"}
    old_value = row["value"]
    default_value = row["default_value"]
    new_count = (row["revert_count"] or 0) + 1
    if default_value is not None:
        conn.execute(
            "UPDATE learned_params SET value = ?, last_revert_at = datetime('now'), "
            "revert_count = ? WHERE key = ?",
            (default_value, new_count, key),
        )
    else:
        conn.execute(
            "DELETE FROM learned_params WHERE key = ?", (key,),
        )
    conn.execute(
        "INSERT INTO learner_log (learner_name, param_key, old_value, new_value, "
        "action, gate_reason) VALUES ('safety_circuit', ?, ?, ?, 'reverted', ?)",
        (key, old_value, default_value, reason),
    )
    conn.commit()
    return {"action": "reverted", "key": key, "old": old_value,
            "default": default_value, "revert_count": new_count}


# ─── Read-all (daily report + UI) ─────────────────────────────────────────

def all_params(conn) -> list[dict]:
    """All current learned params, decoded. For UI + daily report."""
    rows = conn.execute(
        "SELECT key, value, value_type, updated_at, sample_size, ci_low, ci_high, "
        "p_value, pinned, pinned_reason, revert_count FROM learned_params ORDER BY key"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["decoded"] = _typed_value(d)
        out.append(d)
    return out


def recent_log(conn, limit: int = 20) -> list[dict]:
    """Recent learner_log entries (for UI + daily report)."""
    rows = conn.execute(
        "SELECT ts, learner_name, param_key, old_value, new_value, action, "
        "gate_reason, sample_size, ci_low, ci_high, p_value FROM learner_log "
        "ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]
