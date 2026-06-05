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
            status          TEXT    DEFAULT 'open',  -- 'open'|'closed'
            -- 1:1 parity with positions table (added 2026-06-01)
            tp_levels             TEXT,    -- JSON array of TP rungs (mirrors positions.tp_levels)
            be_tier_reached       INTEGER, -- Tiered "cuff" BE state 0..3 (Feature 19)
            trade_grade           REAL,    -- Elder A-trade ATR-normalised grade
            execution_lag_minutes INTEGER, -- mins between scan and fill
            sizing_tier           TEXT,    -- 'full' (Opus≥6) or 'half' (Opus=5)
            consensus_model_used  TEXT,
            bear_phase_at_open    TEXT,
            archetype_at_open     TEXT,
            opus_had_overrides    INTEGER,
            tp_levels_count       INTEGER,
            ai_score_at_open      REAL
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

    1:1 with executor.open_real_trade: same pre-flight drift gate, same
    R:R-viability rescue check, same provenance columns. Only difference:
    no Bitget order goes out — we just write a row.

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

    # ── Pre-flight drift / R:R-viability gate (parity with executor.py) ──────
    # Skip placing the paper row if the trade premise has died between scan
    # and execution. Mirrors executor.py:342-431.
    intended_entry_pre = float(signal.get("entry_price") or 0)
    zone_pre           = signal.get("entry_zone") or {}
    zone_low_pre       = float(zone_pre.get("low")  or 0)
    zone_high_pre      = float(zone_pre.get("high") or 0)
    if zone_low_pre > zone_high_pre:
        zone_low_pre, zone_high_pre = zone_high_pre, zone_low_pre
    live_mark = 0.0
    try:
        import bitget_client
        mp = bitget_client.get_mark_prices([sym]) or {}
        live_mark = float(mp.get(sym) or mp.get(sym.upper()) or 0)
    except Exception:
        live_mark = 0.0
    sl_px_pre  = float(signal.get("sl_price")  or 0)
    tp1_px_pre = float(signal.get("tp1_price") or 0)
    if live_mark > 0 and intended_entry_pre > 0:
        drift_pre = abs(live_mark - intended_entry_pre) / intended_entry_pre
        inside_zone_pre = False
        if zone_low_pre > 0 and zone_high_pre > 0:
            zone_mid_pre = (zone_low_pre + zone_high_pre) / 2.0
            pad_pre = zone_mid_pre * 0.0025
            inside_zone_pre = (zone_low_pre - pad_pre) <= live_mark <= (zone_high_pre + pad_pre)
        if not inside_zone_pre and drift_pre > fa_config.MAX_ENTRY_DRIFT_PCT:
            import os as _os
            MIN_RR_AT_FILL = float(_os.environ.get("FUTURES_AI_MIN_RR_AT_FILL", "1.5"))
            is_long_pre = dir_.lower() == "long"
            if is_long_pre:
                reward = tp1_px_pre - live_mark
                risk   = live_mark - sl_px_pre
            else:
                reward = live_mark - tp1_px_pre
                risk   = sl_px_pre - live_mark
            new_rr = (reward / risk) if (reward > 0 and risk > 0) else None
            viable = new_rr is not None and new_rr >= MIN_RR_AT_FILL
            if not viable:
                _log(conn, "rejected_drift_pre_order", sym, dir_,
                     signal.get("consensus_score"),
                     json.dumps({
                        "intended_entry": intended_entry_pre,
                        "live_mark":      live_mark,
                        "drift_pct":      round(drift_pre * 100, 3),
                        "sl_price":       sl_px_pre,
                        "tp1_price":      tp1_px_pre,
                        "new_rr":         round(new_rr, 2) if new_rr is not None else None,
                        "min_rr_required": MIN_RR_AT_FILL,
                        "reason": "paper: R:R no longer favourable at live mark",
                     }))
                return None
            _log(conn, "drift_allowed_rr_viable", sym, dir_,
                 signal.get("consensus_score"),
                 json.dumps({
                    "drift_pct": round(drift_pre * 100, 3),
                    "new_rr":    round(new_rr, 2),
                    "live_mark": live_mark,
                    "intended_entry": intended_entry_pre,
                 }))

    # ── Serialise TP ladder to JSON (variable-tier support, mirrors real) ────
    tp_levels = signal.get("tp_levels")
    if tp_levels and not isinstance(tp_levels, str):
        tp_levels_json = json.dumps(tp_levels)
    elif isinstance(tp_levels, str):
        tp_levels_json = tp_levels
    else:
        # Build synthetic 2-rung ladder from tp1/tp2 when full ladder absent
        synth = []
        if signal.get("tp1_price"):
            synth.append({"idx": 1, "price": float(signal["tp1_price"]),
                          "pct": 50, "hit": False, "attached": True})
        if signal.get("tp2_price"):
            synth.append({"idx": 2, "price": float(signal["tp2_price"]),
                          "pct": 50, "hit": False, "attached": False})
        tp_levels_json = json.dumps(synth) if synth else None

    cur = conn.execute("""
        INSERT INTO paper_positions
          (symbol, direction, archetype, score_consensus,
           entry_price, sl_price, tp1_price, tp2_price,
           notional_usdt, leverage, risk_usdt, current_sl,
           tp_levels, be_tier_reached,
           sizing_tier, consensus_model_used,
           bear_phase_at_open, archetype_at_open,
           opus_had_overrides, tp_levels_count,
           ai_score_at_open, execution_lag_minutes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        # 1:1 provenance columns (mirror executor.py:117-168)
        tp_levels_json,
        0,                                              # be_tier_reached
        (sizing.get("sizing_tier") or "full"),
        signal.get("consensus_model_used"),
        signal.get("bear_phase_at_open"),
        signal.get("archetype_at_open")
            or (signal.get("scanner") or {}).get("archetype"),
        int(signal.get("opus_had_overrides") or 0),
        int(signal.get("tp_levels_count")
            or (len(json.loads(tp_levels_json)) if tp_levels_json else 0)),
        ((signal.get("ai") or {}).get("score") if isinstance(signal.get("ai"), dict)
         else None) or signal.get("ai_score"),
        _compute_lag_minutes(signal.get("_scan_completed_at")),
    ))
    new_id = cur.lastrowid
    conn.commit()
    _log(conn, "paper_open", sym, dir_, signal.get("consensus_score"),
         json.dumps({
             "entry": signal.get("entry_price"),
             "sl":    signal.get("sl_price"),
             "tp1":   signal.get("tp1_price"),
             "tp2":   signal.get("tp2_price"),
             "tp_levels_count": int(signal.get("tp_levels_count") or 0),
             "notional": sizing.get("notional_usdt"),
             "lev":      sizing.get("leverage"),
             "score":    signal.get("consensus_score"),
             "sizing_tier": sizing.get("sizing_tier") or "full",
         }))
    return new_id


def manage_paper_positions(conn, get_mark_price) -> dict:
    """
    Walk every OPEN paper position and apply lifecycle rules against the
    current mark price. 1:1 with executor.manage_real_positions: same
    triggers (SL/TP ladder/MAE/BE/trail), same tiered-BE rules, same
    CPR-aware trail, same `be_price_for` buffer. Only difference is
    no Bitget API call — we just update the row.
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
        sign    = 1 if is_long else -1
        entry   = float(p["entry_price"])
        cur_sl  = float(p["current_sl"] or p["sl_price"])

        # ── Parse the TP ladder (JSON) — variable-tier support (mirrors real).
        # Falls back to denormalised tp1_price/tp2_price columns when JSON
        # is absent (legacy rows). All TP3+ tiers come from the JSON.
        tp_levels = []
        try:
            tp_levels = json.loads(p.get("tp_levels") or "[]") or []
        except Exception:
            tp_levels = []
        if not tp_levels:
            t1 = float(p.get("tp1_price") or 0)
            t2 = float(p.get("tp2_price") or 0)
            if t1: tp_levels.append({"idx": 1, "price": t1, "pct": 50,
                                     "hit": bool(p.get("tp1_hit"))})
            if t2: tp_levels.append({"idx": 2, "price": t2, "pct": 50,
                                     "hit": bool(p.get("tp2_hit"))})

        # 1. SL hit → full close (priority over everything)
        sl_hit = (is_long and mark <= cur_sl) or (not is_long and mark >= cur_sl)
        if sl_hit:
            ev = _close_position(conn, p, mark, "sl",
                                  reason=f"SL hit at {mark:.6g} (was {cur_sl:.6g})")
            events.append(ev); closed += 1
            continue

        # 2. Walk the TP ladder. Last tier hit → full close;
        #    intermediate tiers → mark hit + force-BE on TP1.
        last_idx = max((t.get("idx") or 0) for t in tp_levels) if tp_levels else 0
        tp_full_close = None
        tp1_just_hit  = False
        for lvl in tp_levels:
            if lvl.get("hit"): continue
            tp_px = float(lvl.get("price") or 0)
            if tp_px <= 0: continue
            crossed = (is_long and mark >= tp_px) or (not is_long and mark <= tp_px)
            if not crossed: continue
            lvl["hit"]    = True
            lvl["hit_at"] = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
            if lvl.get("idx") == 1:
                tp1_just_hit = True
                conn.execute(
                    "UPDATE paper_positions SET tp1_hit=1 WHERE id=?", (p["id"],))
            elif lvl.get("idx") == 2:
                conn.execute(
                    "UPDATE paper_positions SET tp2_hit=1 WHERE id=?", (p["id"],))
            if lvl.get("idx") == last_idx:
                tp_full_close = lvl
            _log(conn, f"paper_tp{lvl.get('idx')}", sym, p["direction"],
                 p["score_consensus"],
                 json.dumps({"tp": tp_px, "mark": mark, "idx": lvl.get("idx"),
                             "pct": lvl.get("pct")}))

        # Persist the updated ladder JSON
        if any(l.get("hit_at") and not p.get("tp_levels", "").count(l.get("hit_at","")) for l in tp_levels):
            conn.execute("UPDATE paper_positions SET tp_levels=? WHERE id=?",
                         (json.dumps(tp_levels), p["id"]))

        if tp_full_close:
            ev = _close_position(conn, p, float(tp_full_close["price"]),
                                  f"tp{tp_full_close.get('idx')}",
                                  reason=f"TP{tp_full_close.get('idx')} (final tier) hit at {tp_full_close['price']:.6g}")
            events.append(ev); closed += 1
            continue

        # 3. TP1-hit → force SL to BE (with buffer). 1:1 with executor.py:817-845.
        if tp1_just_hit:
            be_sl = fa_config.be_price_for(entry, is_long)
            sl_already_protective = (cur_sl >= be_sl) if is_long else (cur_sl <= be_sl)
            gap_pct = abs(be_sl - cur_sl) / entry if entry else 0
            if not sl_already_protective and gap_pct >= 0.0005:
                conn.execute(
                    "UPDATE paper_positions SET current_sl=? WHERE id=?",
                    (be_sl, p["id"]))
                conn.commit()
                ev = {"id": p["id"], "symbol": sym, "kind": "be_move",
                      "msg": f"SL → BE {be_sl:.6g} (entry+{fa_config.BE_BUFFER_PCT*100:.2f}%, trigger=tp1_hit)"}
                events.append(ev)
                _log(conn, "paper_be", sym, p["direction"], p["score_consensus"],
                     json.dumps({"old_sl": cur_sl, "new_sl": be_sl, "entry": entry,
                                 "buffer_pct": fa_config.BE_BUFFER_PCT,
                                 "trigger": "tp1_hit"}))
                cur_sl = be_sl
            conn.commit()
            continue

        # 4. ATR-based triggers (BE / TRAIL / MAE / Tiered BE)
        atr = atr_cache.get(sym)
        if atr is None:
            atr = _atr_4h(sym) or 0.0
            atr_cache[sym] = atr
        if atr <= 0:
            continue
        atr_pct = atr / entry * 100.0
        cur_pct = (mark - entry) / entry * 100.0 * sign

        # MAE breach
        if cur_pct <= -atr_pct * 1.0:
            ev = _close_position(conn, p, mark, "mae",
                                  reason=f"MAE breach at {cur_pct:.2f}% adverse")
            events.append(ev); closed += 1
            continue

        # ── Tiered "cuff" BE (Feature 19, env-toggled via FUTURES_AI_TIERED_BE_ENABLED).
        # 1:1 with executor.py:847-914. Before TP1 hit, progressively tighten
        # SL as price approaches TP1: 33% → BE, 66% → lock 33% gain, 90% → 66%.
        try:
            import os as _os
            if int(_os.environ.get("FUTURES_AI_TIERED_BE_ENABLED", "1")):
                tp1_lvl = next((t for t in tp_levels if t.get("idx") == 1), None)
                tier_done = int(p.get("be_tier_reached") or 0)
                tp1_already_hit = bool(p.get("tp1_hit")) or (
                    tp1_lvl and tp1_lvl.get("hit"))
                if tp1_lvl and tp1_lvl.get("price") and tier_done < 3 and not tp1_already_hit:
                    tp1_px = float(tp1_lvl["price"])
                    dist = abs(tp1_px - entry)
                    if dist > 0:
                        pct_traveled = abs(mark - entry) / dist
                        tiers = [(0.33, 0.0), (0.66, 0.33), (0.90, 0.66)]
                        new_tier = tier_done
                        new_sl_target = cur_sl
                        for ti, (thr, lock_frac) in enumerate(tiers, 1):
                            if ti <= tier_done: continue
                            if pct_traveled >= thr:
                                if lock_frac == 0:
                                    cand = fa_config.be_price_for(entry, is_long)
                                else:
                                    cand = entry + sign * (dist * lock_frac)
                                if (is_long and cand > new_sl_target) or \
                                   (not is_long and cand < new_sl_target):
                                    new_sl_target = cand
                                    new_tier = ti
                        if new_tier > tier_done:
                            gap_pct = abs(new_sl_target - cur_sl) / entry if entry else 0
                            if gap_pct >= 0.0005:
                                conn.execute(
                                    "UPDATE paper_positions SET be_tier_reached=?, current_sl=? WHERE id=?",
                                    (new_tier, new_sl_target, p["id"]))
                                conn.commit()
                                ev = {"id": p["id"], "symbol": sym,
                                      "kind": f"be_tier_{new_tier}",
                                      "msg": f"Tiered BE {new_tier} — SL → {new_sl_target:.6g} ({pct_traveled*100:.0f}% to TP1)"}
                                events.append(ev)
                                _log(conn, "paper_be_tier", sym, p["direction"],
                                     p["score_consensus"],
                                     json.dumps({"tier": new_tier,
                                                 "pct_traveled": round(pct_traveled*100, 1),
                                                 "old_sl": cur_sl,
                                                 "new_sl": new_sl_target}))
                                cur_sl = new_sl_target
        except Exception:
            pass

        # ── CPR-aware trail (Feature 2, env-toggled via FUTURES_AI_CPR_TRAIL_ENABLED).
        # 1:1 with executor.py:944-988. Day-type determines trail distance:
        # range → 0.25× ATR, neutral → 0.375× ATR, trend → 0.5× ATR.
        trail_atr_mult = 0.5
        try:
            import os as _os
            if int(_os.environ.get("FUTURES_AI_CPR_TRAIL_ENABLED", "1")):
                from chart_cpr import compute_cpr_from_df, cpr_day_type
                from chart_context import get_chart_context as _gcc
                ctx_1d = _gcc(sym, ["1D"]) or {}
                df_1d = (ctx_1d.get("1D") or {}).get("df")
                if df_1d is not None and len(df_1d) >= 2:
                    _cpr = compute_cpr_from_df(df_1d)
                    _dt_class = cpr_day_type(_cpr)
                    base = _dt_class.get("trail_atr_mult", 1.5)
                    trail_atr_mult = base / 4.0
        except Exception:
            pass

        # Trail trigger (+2× ATR favor)
        if cur_pct >= atr_pct * 2.0:
            new_sl = entry + sign * (atr * trail_atr_mult)
            gap_pct = abs(new_sl - cur_sl) / entry if entry else 0
            move = ((is_long and new_sl > cur_sl) or
                    (not is_long and new_sl < cur_sl)) and gap_pct >= 0.0005
            if move:
                conn.execute(
                    "UPDATE paper_positions SET current_sl=? WHERE id=?",
                    (new_sl, p["id"]))
                conn.commit()
                ev = {"id": p["id"], "symbol": sym, "kind": "trail",
                      "msg": f"Trail SL → {new_sl:.6g} (+2× ATR, mult={trail_atr_mult:.3f})"}
                events.append(ev)
                _log(conn, "paper_trail", sym, p["direction"], p["score_consensus"],
                     json.dumps({"new_sl": new_sl, "atr_pct": atr_pct,
                                 "current_pct": cur_pct,
                                 "trail_atr_mult": trail_atr_mult}))
                continue

        # BE trigger — +1× ATR favor (entry + BE buffer)
        if cur_pct >= atr_pct * 1.0:
            be_sl = fa_config.be_price_for(entry, is_long)
            gap_pct = abs(be_sl - cur_sl) / entry if entry else 0
            move = ((is_long and be_sl > cur_sl) or
                    (not is_long and be_sl < cur_sl)) and gap_pct >= 0.0005
            if move:
                conn.execute(
                    "UPDATE paper_positions SET current_sl=? WHERE id=?",
                    (be_sl, p["id"]))
                conn.commit()
                ev = {"id": p["id"], "symbol": sym, "kind": "be_move",
                      "msg": f"SL → BE {be_sl:.6g} (entry+{fa_config.BE_BUFFER_PCT*100:.2f}%, at +1× ATR)"}
                events.append(ev)
                _log(conn, "paper_be", sym, p["direction"], p["score_consensus"],
                     json.dumps({"new_sl": be_sl, "entry": entry,
                                 "buffer_pct": fa_config.BE_BUFFER_PCT,
                                 "atr_pct": atr_pct, "trigger": "atr_1x"}))

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

    # Feature 9 — Trade Grade on close (1:1 parity with executor.py:281-294).
    # ATR-normalised P&L distance: (exit - entry) / (4× ATR_4H at open).
    try:
        from trade_utils import compute_trade_grade
        grade = compute_trade_grade(p["symbol"], entry, exit_price, p["direction"])
        if grade is not None:
            conn.execute(
                "UPDATE paper_positions SET trade_grade=? WHERE id=?",
                (round(grade, 4), p["id"]))
            conn.commit()
    except Exception:
        pass

    # Fire-and-forget learner reflection. Failures here mustn't block
    # the close path — wrapped wide on the learner side too.
    try:
        from . import learner
        learner.reflect_on_paper_close(conn, p["id"])
    except Exception:
        pass

    return {"id": p["id"], "symbol": p["symbol"], "kind": "close",
            "reason": close_reason, "pnl": realized}


def _compute_lag_minutes(scan_completed_at) -> Optional[int]:
    """Time between scan completion and now, in minutes. Mirrors
    executor._compute_lag_minutes — same semantics, same caps."""
    if scan_completed_at is None or scan_completed_at == "":
        return None
    try:
        import time as _t
        ts = float(scan_completed_at)
        if ts <= 0:
            return None
        delta_min = (_t.time() - ts) / 60.0
        if abs(delta_min) > 1440:
            return None
        return max(0, int(delta_min))
    except (TypeError, ValueError):
        return None


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
