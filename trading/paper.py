"""
trading.paper — full-lifecycle paper-trade simulator.

Mirrors the real-trader chain end-to-end EXCEPT no orders go to Bitget.
Every event (entry decision, SL move, partial close, full close) is
logged to futures_ai_log and the open simulated position is tracked in
the paper_positions table.

State machine:
  CANDIDATE → CONSENSUS_REJECTED   (logged, no row created)
            → OPEN                  (paper_position row created)
                  ↓
              [monitor every 10 min]
                  ↓
  OPEN      → BE_MOVED              (SL → entry once +1× ATR)
            → TRAIL_MOVED           (SL → entry + 0.5× ATR once +2× ATR)
            → TP1_HIT (50% close)   (still partly OPEN with remainder)
            → TP2_HIT (full close)
            → MAE_BREACH (full close, marked as "cut")
            → INVALIDATED (1H close past entry + MAE)

Defaults:
  TP1 closes 50% of position; remainder runs to TP2 with trail SL.

Result rows feed:
  - futures_ai_log (every state transition)
  - paper_positions table (current state + lifecycle timestamps)
  - Aggregated into /api/futures-ai/state runtime stats

After 50 closed paper trades, the rule promotion (see memory:
'feedback_rulebook_no_bias') kicks in: paper data becomes eligible for
the rulebook miner. Until then, paper is sandboxed.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
from typing import Optional

from trading import config as fa_config


# ── DB schema (lazy-create on first use) ────────────────────────────────────

def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            archetype       TEXT,
            score_consensus INTEGER,
            opened_at       TEXT    DEFAULT (datetime('now')),
            closed_at       TEXT,
            entry_price     REAL    NOT NULL,
            sl_price        REAL    NOT NULL,
            tp1_price       REAL,
            tp2_price       REAL,
            notional_usdt   REAL,
            leverage        INTEGER,
            risk_usdt       REAL,
            current_sl      REAL,   -- moves on BE / TRAIL events
            tp1_hit         INTEGER DEFAULT 0,
            tp2_hit         INTEGER DEFAULT 0,
            sl_hit          INTEGER DEFAULT 0,
            mae_breach      INTEGER DEFAULT 0,
            invalidated     INTEGER DEFAULT 0,
            close_reason    TEXT,   -- 'tp2'|'sl'|'mae'|'invalid'|'manual'
            realized_pnl    REAL,
            mfe_pct         REAL,
            mae_pct         REAL,
            status          TEXT    DEFAULT 'open'   -- 'open'|'closed'
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_positions_status_opened "
        "ON paper_positions(status, opened_at DESC)"
    )
    conn.commit()


# ── Public API ───────────────────────────────────────────────────────────────

def open_paper_trade(conn, signal: dict, sizing: dict) -> Optional[int]:
    """
    Given an approved consensus signal + a sizing dict from risk_budget,
    record a paper position. Returns the new paper_positions.id or None
    on failure.

    `signal` shape:
      {scanner:{score,direction,archetype}, ai:{...}, consensus_score, symbol,
       entry_price, sl_price, tp1_price, tp2_price}

    Idempotency: if a symbol already has an OPEN paper position with the
    same direction, refuse to open a duplicate.
    """
    _ensure_table(conn)
    sym  = signal.get("symbol")
    dir_ = signal.get("direction")
    if not sym or not dir_:
        return None
    # Dedup guard
    dupe = conn.execute(
        "SELECT id FROM paper_positions WHERE symbol=? AND direction=? "
        "AND status='open' LIMIT 1",
        (sym, dir_),
    ).fetchone()
    if dupe:
        return None

    cur = conn.execute("""
        INSERT INTO paper_positions
          (symbol, direction, archetype, score_consensus,
           entry_price, sl_price, tp1_price, tp2_price,
           notional_usdt, leverage, risk_usdt, current_sl)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        sym, dir_,
        (signal.get("scanner") or {}).get("archetype"),
        signal.get("consensus_score"),
        signal.get("entry_price"),
        signal.get("sl_price"),
        signal.get("tp1_price"),
        signal.get("tp2_price"),
        sizing.get("notional_usdt"),
        sizing.get("leverage"),
        sizing.get("risk_usdt"),
        signal.get("sl_price"),   # current_sl starts at sl_price
    ))
    new_id = cur.lastrowid
    conn.commit()
    _log(conn, "paper_open", sym, dir_, signal.get("consensus_score"),
         json.dumps({
             "entry": signal.get("entry_price"),
             "sl":    signal.get("sl_price"),
             "tp1":   signal.get("tp1_price"),
             "tp2":   signal.get("tp2_price"),
             "notional": sizing.get("notional_usdt"),
             "lev":      sizing.get("leverage"),
             "score":    signal.get("consensus_score"),
         }))
    return new_id


def manage_paper_positions(conn, get_mark_price) -> dict:
    """
    Walk every OPEN paper position and apply lifecycle rules against the
    current mark price. Caller provides `get_mark_price(symbol) -> float`
    so we don't couple this module to a specific exchange client.

    Rules (mirror the live-trade alerts):
      - SL hit          → close at SL
      - TP1 hit         → mark 50% closed, move SL to BE if not yet moved
      - TP2 hit         → close remainder at TP2
      - +1× ATR favor   → move SL to entry (BE)            [needs ATR_4H]
      - +2× ATR favor   → move SL to entry + 0.5× ATR (TRAIL)
      - -1× ATR adverse → close at mark (MAE breach)

    ATR_4H is fetched per-symbol via the existing chart_context. We cache
    per-symbol to keep cycle-cost bounded.
    """
    _ensure_table(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM paper_positions WHERE status='open' "
        "ORDER BY opened_at DESC"
    ).fetchall()]
    if not rows:
        return {"checked": 0, "closed": 0, "events": []}

    events: list[dict] = []
    closed = 0
    atr_cache: dict[str, float] = {}

    for p in rows:
        sym  = p["symbol"]
        try:
            mark = float(get_mark_price(sym) or 0)
        except Exception:
            continue
        if mark <= 0:
            continue

        is_long = (p["direction"] or "").lower() == "long"
        entry   = float(p["entry_price"])
        cur_sl  = float(p["current_sl"] or p["sl_price"])
        tp1     = float(p["tp1_price"] or 0)
        tp2     = float(p["tp2_price"] or 0)

        # 1. SL hit?
        sl_hit = (is_long and mark <= cur_sl) or (not is_long and mark >= cur_sl)
        if sl_hit:
            ev = _close_position(conn, p, mark, "sl",
                                  reason=f"SL hit at {mark:.6g} (was {cur_sl:.6g})")
            events.append(ev); closed += 1
            continue

        # 2. TP2 → full close
        tp2_hit = (is_long and tp2 and mark >= tp2) or (not is_long and tp2 and mark <= tp2)
        if tp2_hit:
            ev = _close_position(conn, p, tp2, "tp2",
                                  reason=f"TP2 hit at {tp2:.6g}")
            events.append(ev); closed += 1
            continue

        # 3. TP1 — partial close + move SL to BE if not yet
        tp1_hit = (not p["tp1_hit"]) and (
            (is_long and tp1 and mark >= tp1) or
            (not is_long and tp1 and mark <= tp1)
        )
        if tp1_hit:
            conn.execute(
                "UPDATE paper_positions SET tp1_hit=1, current_sl=? WHERE id=?",
                (entry, p["id"]),
            )
            conn.commit()
            ev = {"id": p["id"], "symbol": sym, "kind": "tp1_partial",
                  "msg": f"TP1 hit at {tp1:.6g} — 50% closed, SL moved to BE"}
            events.append(ev)
            _log(conn, "paper_tp1", sym, p["direction"], p["score_consensus"],
                 json.dumps({"tp1": tp1, "mark": mark}))
            continue

        # 4. BE / TRAIL / MAE — need ATR
        atr = atr_cache.get(sym)
        if atr is None:
            atr = _atr_4h(sym) or 0.0
            atr_cache[sym] = atr
        if atr <= 0:
            continue
        atr_pct = atr / entry * 100.0
        sign = 1 if is_long else -1
        cur_pct = (mark - entry) / entry * 100.0 * sign

        # MAE breach
        if cur_pct <= -atr_pct * 1.0:
            ev = _close_position(conn, p, mark, "mae",
                                  reason=f"MAE breach at {cur_pct:.2f}% adverse")
            events.append(ev); closed += 1
            continue

        # Trail trigger
        if cur_pct >= atr_pct * 2.0:
            new_sl = entry + sign * (atr * 0.5)
            # Only widen the SL in the favorable direction
            move = (is_long and new_sl > cur_sl) or (not is_long and new_sl < cur_sl)
            if move:
                conn.execute(
                    "UPDATE paper_positions SET current_sl=? WHERE id=?",
                    (new_sl, p["id"]),
                )
                conn.commit()
                ev = {"id": p["id"], "symbol": sym, "kind": "trail",
                      "msg": f"Trail SL → {new_sl:.6g} (at +2× ATR)"}
                events.append(ev)
                _log(conn, "paper_trail", sym, p["direction"], p["score_consensus"],
                     json.dumps({"new_sl": new_sl, "atr_pct": atr_pct,
                                 "current_pct": cur_pct}))
                continue

        # BE trigger — SL placed slightly past entry (fee+slippage buffer) so
        # a fill at that level nets the trader ≥ $0 instead of locking a small
        # taker-fee loss. See trading/config.py::be_price_for.
        if cur_pct >= atr_pct * 1.0:
            be_sl = fa_config.be_price_for(entry, is_long)
            move = (is_long and be_sl > cur_sl) or (not is_long and be_sl < cur_sl)
            if move:
                conn.execute(
                    "UPDATE paper_positions SET current_sl=? WHERE id=?",
                    (be_sl, p["id"]),
                )
                conn.commit()
                ev = {"id": p["id"], "symbol": sym, "kind": "be_move",
                      "msg": f"SL → BE {be_sl:.6g} (entry+{fa_config.BE_BUFFER_PCT*100:.2f}%, at +1× ATR)"}
                events.append(ev)
                _log(conn, "paper_be", sym, p["direction"], p["score_consensus"],
                     json.dumps({"new_sl": be_sl, "entry": entry,
                                 "buffer_pct": fa_config.BE_BUFFER_PCT,
                                 "atr_pct": atr_pct}))

    return {"checked": len(rows), "closed": closed, "events": events}


def force_close_all(conn, get_mark_price, reason: str = "manual") -> int:
    """Close every OPEN paper position at the current mark — used by
    Pause Now."""
    _ensure_table(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM paper_positions WHERE status='open'"
    ).fetchall()]
    n = 0
    for p in rows:
        try:
            mark = float(get_mark_price(p["symbol"]) or 0)
        except Exception:
            continue
        if mark <= 0:
            continue
        _close_position(conn, p, mark, reason, reason=f"Forced close: {reason}")
        n += 1
    return n


# ── Internals ────────────────────────────────────────────────────────────────

def _close_position(conn, p: dict, exit_price: float, close_reason: str,
                     reason: str = "") -> dict:
    """Common close-out: compute realized P&L, mark closed, log,
    then trigger the learner reflection."""
    is_long = (p["direction"] or "").lower() == "long"
    entry = float(p["entry_price"])
    notional = float(p["notional_usdt"] or 0)
    sign = 1 if is_long else -1
    move_pct = (exit_price - entry) / entry * sign
    realized = round(notional * move_pct, 4)

    conn.execute("""
        UPDATE paper_positions SET
          status='closed', closed_at=datetime('now'),
          realized_pnl=?, close_reason=?,
          sl_hit=?, mae_breach=?, tp2_hit=?
        WHERE id=?
    """, (
        realized, close_reason,
        1 if close_reason == "sl"  else 0,
        1 if close_reason == "mae" else 0,
        1 if close_reason == "tp2" else 0,
        p["id"],
    ))
    conn.commit()
    _log(conn, "paper_close", p["symbol"], p["direction"],
         p["score_consensus"],
         json.dumps({"exit": exit_price, "pnl": realized,
                     "reason": close_reason, "note": reason}))

    # Fire-and-forget learner reflection. Failures here mustn't block
    # the close path — wrapped wide on the learner side too.
    try:
        from . import learner
        learner.reflect_on_paper_close(conn, p["id"])
    except Exception:
        pass

    return {"id": p["id"], "symbol": p["symbol"], "kind": "close",
            "reason": close_reason, "pnl": realized}


def _atr_4h(symbol: str) -> Optional[float]:
    """Best-effort ATR_4H lookup."""
    try:
        import chart_context
        ctx = chart_context.get_chart_context(symbol, ["4H"]) or {}
        return float(((ctx.get("4H", {}).get("indicators", {})
                        .get("atr") or {}).get("value") or 0)) or None
    except Exception:
        return None


def _log(conn, event: str, sym: str, direction: str, score: Optional[int],
         payload: str) -> None:
    try:
        conn.execute("""
            INSERT INTO futures_ai_log(ts, event, symbol, direction, score, payload_json)
            VALUES (datetime('now'), ?, ?, ?, ?, ?)
        """, (event, sym or "", direction or "", int(score or 0), payload))
        conn.commit()
    except Exception:
        pass
