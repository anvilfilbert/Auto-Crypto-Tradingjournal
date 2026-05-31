"""
trading.executor — real-money lifecycle (Bitget subaccount).

Mirrors trading.paper's lifecycle 1:1, but every order goes through
bitget_trader to Bitget's REST API on the auto-trader subaccount.

State machine (same as paper):
  open  -> entry placed (market, with preset SL+TP1 attached by Bitget)
        -> [monitor every 10 min]
        -> BE_MOVED  (modify SL to entry)
        -> TRAIL_MOVED  (modify SL to entry + 0.5x ATR)
        -> TP2_HIT  (Bitget's preset TP1 already partially closed; we close remainder at TP2)
        -> MAE_BREACH (full market close)
        -> SL_FILLED (Bitget closed it itself; we detect via missing position)

Positions are persisted to the existing `positions` table with
chain='auto_ai' AND exchange='bitget_trader' so analytics queries scoped
to the manual chain ignore them entirely.

Reconciliation:
  Every monitor cycle we list Bitget's open positions for this subaccount
  and compare to what we think is open. Positions that VANISHED were
  closed by Bitget (preset SL or TP fire) — we look up the recent fills
  to find the realized P&L and write a closed-position row.

Safety:
  - Refuses every call unless fa_config.is_real_mode() == True
  - Every Bitget API error is caught + logged; no retries (operator
    intervenes if a write fails)
  - Idempotent: open_real_trade checks for an existing OPEN position on
    the same (symbol, direction) before placing
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from . import config as fa_config
from . import bitget_trader


def _compute_lag_minutes(scan_completed_at) -> Optional[int]:
    """Time between scan completion and now, in minutes. Returns None when
    the input is missing/unparseable so the DB column stays NULL.
    Used to populate positions.execution_lag_minutes for auto_ai trades —
    feeds the alpha-decay panel in the risk dashboard."""
    if scan_completed_at is None or scan_completed_at == "":
        return None
    try:
        import time as _t
        ts = float(scan_completed_at)
        if ts <= 0:
            return None
        delta_min = (_t.time() - ts) / 60.0
        # Sanity: cap absurd values (e.g. clock skew) at +/- 1 day.
        if abs(delta_min) > 1440:
            return None
        return max(0, int(delta_min))
    except (TypeError, ValueError):
        return None


# ── DB helpers ────────────────────────────────────────────────────────────────

def _open_auto_positions(conn) -> list[dict]:
    """Our internal record of what we *think* is currently open on the
    Bitget trader subaccount."""
    return [dict(r) for r in conn.execute("""
        SELECT * FROM positions
        WHERE chain='auto_ai'
          AND (close_time IS NULL OR close_time = '')
        ORDER BY open_time DESC
    """).fetchall()]


def _insert_open_position(conn, signal: dict, sizing: dict,
                            order_result: dict) -> int:
    """Write the position row immediately after order placement. Sets
    close_time=NULL so the reconciler treats it as open. Also persists the
    skill-provenance fields (consensus_model_used, bear_phase_at_open,
    archetype_at_open, po3_total, opus_had_overrides, tp_levels_count) so
    later analytics can aggregate by *skill* not just by symbol/hour."""
    sym  = signal.get("symbol")
    dir_ = signal.get("direction")
    # Merge Bitget's per-tier attach outcomes into the stored ladder so the
    # Phase-2 fill-detector knows which tiers were actually placed (vs
    # being silently dropped by Bitget for size-floor or precision reasons).
    # Without this, `_detect_tp_fills` would false-positive every tier as
    # "filled" simply because Bitget never knew about them.
    tp_levels = signal.get("tp_levels") or []
    attach_results = (order_result or {}).get("tp_attach_results") or []
    if tp_levels and attach_results:
        attach_by_idx = {a.get("idx"): a for a in attach_results}
        for lvl in tp_levels:
            ar = attach_by_idx.get(lvl.get("idx"))
            lvl["attached"] = bool(ar and ar.get("ok"))
    elif tp_levels:
        # Legacy / partial-data path — assume ONLY TP1 attached so the
        # detector ignores TP2+ instead of falsely marking them filled.
        for i, lvl in enumerate(tp_levels):
            lvl["attached"] = (i == 0)
    tp_levels_json = json.dumps(tp_levels) if tp_levels else None
    cur = conn.execute("""
        INSERT INTO positions(
            symbol, base_asset, direction,
            margin_mode, open_time, close_time,
            entry_price, close_price,
            size_usdt, size_contracts,
            realized_pnl, position_pnl,
            opening_fee, closing_fee, total_fees,
            is_manual, exchange, leverage,
            chain, setup_type, setup_score, signal_price, tp_levels,
            consensus_model_used, bear_phase_at_open, archetype_at_open,
            po3_total, opus_had_overrides, tp_levels_count,
            ai_score_at_open, sizing_tier, execution_lag_minutes,
            sl_price
        ) VALUES (
            ?, ?, ?,
            'isolated', datetime('now'), '',
            ?, NULL,
            ?, ?,
            NULL, NULL,
            NULL, NULL, NULL,
            0, 'bitget_trader', ?,
            'auto_ai', ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?
        )
    """, (
        sym,
        (sym or "").replace("USDT", ""),
        dir_,
        order_result.get("mark_at_entry") or signal.get("entry_price"),
        order_result.get("size_usdt"),
        str(order_result.get("size_contracts") or "") + ((sym or "").replace("USDT", "")),
        order_result.get("leverage"),
        (signal.get("scanner") or {}).get("archetype") or "auto_ai",
        signal.get("consensus_score"),
        signal.get("entry_price"),
        tp_levels_json,
        # Skill provenance
        signal.get("consensus_model_used"),
        signal.get("bear_phase_at_open"),
        signal.get("archetype_at_open"),
        signal.get("po3_total"),
        int(signal.get("opus_had_overrides") or 0),
        int(signal.get("tp_levels_count") or 0),
        # AI score at open — added 2026-05-24 for Opus calibration analysis.
        # Read from the nested consensus location (signal["ai"]["score"]),
        # with a flat-key fallback in case paper-mode passes it differently.
        ((signal.get("ai") or {}).get("score") if isinstance(signal.get("ai"), dict)
         else None) or signal.get("ai_score"),
        # Sizing tier (2026-05-26): "full" (Opus≥6) or "half" (Opus=5).
        (sizing.get("sizing_tier") or "full"),
        # Execution lag in minutes (2026-05-26): time between the scan that
        # produced this setup and the actual fill. Useful for the risk
        # dashboard's alpha-decay analysis. Computed at insert time.
        _compute_lag_minutes(signal.get("_scan_completed_at")),
        # Initial SL price (migration 67, 2026-05-31) — defines 1R for the
        # stats-page realized-R computation. Snapshotted once at open and
        # never updated even if SL is moved (BE / trail) during the trade.
        signal.get("sl_price"),
    ))
    conn.commit()
    return cur.lastrowid


def _had_be_move(conn, position_id) -> bool:
    """Did this position ever have its SL moved to break-even? Reads the
    decision log. Returns False on any error (best-effort detection)."""
    if not conn or not position_id:
        return False
    try:
        row = conn.execute("""
            SELECT 1 FROM futures_ai_log
            WHERE event='real_be'
              AND json_extract(payload_json, '$.position_id') = ?
            LIMIT 1
        """, (position_id,)).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def _categorize_close_reason(pnl: float, entry_px: float, close_px: float,
                                direction: str, raw_reason: str = "",
                                conn=None, position_id=None) -> str:
    """Turn a raw close-detection string into a short categorical tag the
    UI can render compactly. Examples returned: 'TP', 'SL', 'BE_stop',
    'manual_close'. Falls back to pending_reconcile / the raw string when
    uncategorizable.

    `conn` + `position_id` enable a richer detection path: if the SL was
    previously moved to break-even (`real_be` event in `futures_ai_log`)
    AND the actual close happened near the entry price, we tag the close
    as `BE_stop` — "stop-loss fired at the break-even level" — instead of
    misreporting as early_close / manual_close.
    """
    # Lifecycle reasons pass through as-is when they're already short
    raw_lower = (raw_reason or "").lower()
    if "be_trigger" in raw_lower or "break-even" in raw_lower:
        return "BE_stop"
    if "trail" in raw_lower:
        return "trail_stop"
    if "mae" in raw_lower:
        return "MAE_cut"
    if "hedge" in raw_lower:
        # Already prefixed by hedge_manager — keep the suffix verbatim
        return raw_reason if raw_reason.startswith("hedge_unwind") else "hedge_unwind"
    if "manual" in raw_lower or "operator" in raw_lower:
        return "manual_close"
    if "history not yet available" in raw_lower or "pending" in raw_lower:
        return "pending_reconcile"

    # Bitget-closed path — categorise by direction of close vs entry
    if entry_px and close_px and direction:
        is_long = direction.strip().lower() == "long"
        move_pct = (close_px - entry_px) / entry_px * (1 if is_long else -1)

        # ── BE-stop detection ────────────────────────────────────────────
        # If this position had a prior `real_be` event AND the close landed
        # near the entry (within ~0.6% — covers the be_buffer + a bit of
        # slippage), it's a stop-out at the BE-moved level. Without this
        # check, a +0.15% close on a Long with ~$0 pnl falls into
        # 'early_close' which is the wrong story — it WAS a stop, just at
        # the safer level we'd moved it to.
        if abs(move_pct) <= 0.006 and _had_be_move(conn, position_id):
            return "BE_stop"

        # Big positive move with positive pnl → TP-style close
        # Big negative move with negative pnl → SL-style close
        # Small absolute move → manual/early close
        if abs(move_pct) < 0.005:    # <0.5% — not a TP/SL fire
            return "early_close"
        if pnl > 0 and move_pct > 0:
            return "TP"
        if pnl <= 0 and move_pct < 0:
            return "SL"
        # Mixed signal (e.g. pnl positive but tiny move) — call it manual
        return "manual_close"

    # Nothing to categorise from — store the raw reason or a marker
    return raw_reason[:40] if raw_reason else "unknown"


def _mark_closed(conn, position_id: int, close_price: float,
                  realized_pnl: float, reason: str) -> None:
    # Look up entry + direction so we can categorise the close reason
    try:
        row = conn.execute(
            "SELECT entry_price, direction FROM positions WHERE id=?",
            (position_id,),
        ).fetchone()
        entry_px = float(row["entry_price"] or 0) if row else 0
        direction = (row["direction"] if row else "") or ""
    except Exception:
        entry_px, direction = 0, ""
    short_reason = _categorize_close_reason(
        realized_pnl, entry_px, close_price, direction, reason,
        conn=conn, position_id=position_id)
    conn.execute("""
        UPDATE positions
        SET close_time   = datetime('now'),
            close_price  = ?,
            realized_pnl = ?,
            close_reason = ?
        WHERE id = ?
    """, (close_price, realized_pnl, short_reason, position_id))
    conn.commit()

    # Feature 9 — Trade Grade (Elder A-trade normalization, 2026-05-24).
    # ATR-normalized P&L distance: (exit - entry) / (4× ATR_4H at open).
    # 4× ATR is roughly the "expected daily channel" for the asset; an
    # A-trade closes for ≥30% of channel = ≥1.2× ATR_4H.
    try:
        from trade_utils import compute_trade_grade
        if row:
            symbol = conn.execute(
                "SELECT symbol FROM positions WHERE id=?", (position_id,)
            ).fetchone()[0]
            grade = compute_trade_grade(symbol, entry_px, close_price, direction)
            if grade is not None:
                conn.execute(
                    "UPDATE positions SET trade_grade = ? WHERE id = ?",
                    (round(grade, 4), position_id))
                conn.commit()
    except Exception:
        pass

    _log(conn, "auto_close", position_id,
         {"close_price": close_price, "pnl": realized_pnl,
          "reason": short_reason, "raw_reason": reason})


def _log(conn, event: str, position_id: Optional[int], payload: dict) -> None:
    try:
        conn.execute("""
            INSERT INTO futures_ai_log(ts, event, symbol, direction, score, payload_json)
            VALUES (datetime('now'), ?, ?, ?, ?, ?)
        """, (
            event,
            payload.get("symbol") or "",
            payload.get("direction") or "",
            int(payload.get("score") or 0),
            json.dumps({**payload, "position_id": position_id})[:500],
        ))
        conn.commit()
    except Exception:
        pass


# ── Public surface ────────────────────────────────────────────────────────────

def open_real_trade(conn, signal: dict, sizing: dict) -> Optional[int]:
    """
    Place a market entry on the Bitget trader subaccount and record the
    resulting position. Returns the inserted positions.id, or None if
    refused/failed.
    """
    if not fa_config.is_real_mode():
        _log(conn, "real_refused", None,
             {"reason": "not in real mode", "symbol": signal.get("symbol")})
        return None

    sym  = signal.get("symbol")
    dir_ = signal.get("direction") or ""

    # Idempotency — never open a duplicate position
    for p in _open_auto_positions(conn):
        if p["symbol"] == sym and (p["direction"] or "").lower() == dir_.lower():
            _log(conn, "real_dedup", None,
                 {"symbol": sym, "direction": dir_,
                  "reason": "auto-position already open"})
            return None

    # ── Pre-flight drift check (added 2026-05-26) ────────────────────────────
    # Previously we placed the market order FIRST, then checked drift on the
    # fill price, then closed if drift > tolerance. That produced ~15 trades
    # per day that opened and closed within 5-6 seconds on Bitget when fast
    # altcoins pumped 8-33% between the scan and execution. Now we check the
    # live mark price BEFORE placing the order and skip the round-trip if the
    # entry premise is already gone.
    #
    # The fill may still drift between this check and the order fill, so the
    # post-fill guard further down stays in place as a second line of defence.
    intended_entry_pre = float(signal.get("entry_price") or 0)
    zone_pre           = signal.get("entry_zone") or {}
    zone_low_pre       = float(zone_pre.get("low")  or 0)
    zone_high_pre      = float(zone_pre.get("high") or 0)
    if zone_low_pre > zone_high_pre:
        zone_low_pre, zone_high_pre = zone_high_pre, zone_low_pre
    try:
        live_mark = float(bitget_trader.get_mark_price(sym) or 0)
    except Exception:
        live_mark = 0.0

    # ── Pre-flight viability check (Path 3, 2026-05-26) ─────────────────────
    # Old gate: "is fill within 2% of planned entry?" — that blocked every
    # setup where price moved meaningfully (the XAN/TIA pumping-alts pattern).
    # The right question isn't "did price drift" but "does the trade still
    # have favorable math at the live price?". Compute R:R using live mark
    # and the unchanged TP1/SL. If R:R is still ≥ MIN_RR_AT_FILL the trade
    # is still tradeable at market — allow it. If R:R has flipped against
    # us (TP1 already passed, or reward < 1× the risk) reject.
    sl_px_pre  = float(signal.get("sl_price")  or 0)
    tp1_px_pre = float(signal.get("tp1_price") or 0)
    inside_zone_pre = False
    if zone_low_pre > 0 and zone_high_pre > 0 and live_mark > 0:
        zone_mid_pre = (zone_low_pre + zone_high_pre) / 2.0
        pad_pre = zone_mid_pre * 0.0025
        inside_zone_pre = (zone_low_pre - pad_pre) <= live_mark <= (zone_high_pre + pad_pre)

    if live_mark > 0 and intended_entry_pre > 0:
        drift_pre = abs(live_mark - intended_entry_pre) / intended_entry_pre

        # If fill is INSIDE the scanner's entry_zone OR within tolerance,
        # the original analysis still holds → skip the R:R check entirely.
        if inside_zone_pre or drift_pre <= fa_config.MAX_ENTRY_DRIFT_PCT:
            pass  # original behaviour — allow trade

        else:
            # Outside zone AND outside tolerance — compute R:R at live mark.
            # MIN_RR_AT_FILL: minimum reward/risk ratio for a "rescued" entry
            # to still be worth taking. Default 1.5 (reward must be ≥1.5× the
            # risk). Env-tunable via FUTURES_AI_MIN_RR_AT_FILL.
            import os as _os
            MIN_RR_AT_FILL = float(_os.environ.get("FUTURES_AI_MIN_RR_AT_FILL", "1.5"))
            is_long = dir_.lower() == "long"
            if is_long:
                reward = tp1_px_pre - live_mark
                risk   = live_mark - sl_px_pre
            else:  # Short
                reward = live_mark - tp1_px_pre
                risk   = sl_px_pre - live_mark

            new_rr = (reward / risk) if (reward > 0 and risk > 0) else None
            viable_at_live = (new_rr is not None and new_rr >= MIN_RR_AT_FILL)

            if not viable_at_live:
                # Either TP1 already passed (reward ≤ 0) or SL already passed
                # (risk ≤ 0) or new R:R < threshold. The trade premise died.
                _log(conn, "rejected_drift_pre_order", None, {
                    "symbol":         sym,
                    "direction":      dir_,
                    "intended_entry": intended_entry_pre,
                    "live_mark":      live_mark,
                    "drift_pct":      round(drift_pre * 100, 3),
                    "sl_price":       sl_px_pre,
                    "tp1_price":      tp1_px_pre,
                    "new_rr":         round(new_rr, 2) if new_rr is not None else None,
                    "min_rr_required": MIN_RR_AT_FILL,
                    "zone_low":       zone_low_pre or None,
                    "zone_high":      zone_high_pre or None,
                    "reason":         "R:R math no longer favourable at live mark — TP1 passed or reward < 1.5× risk",
                })
                return None
            # Otherwise: drift is large but R:R still works → allow trade
            # at market. Log this for visibility — it's a "rescued" entry.
            _log(conn, "drift_allowed_rr_viable", None, {
                "symbol":   sym, "direction": dir_,
                "drift_pct": round(drift_pre * 100, 3),
                "new_rr":    round(new_rr, 2),
                "live_mark": live_mark, "intended_entry": intended_entry_pre,
                "reason": "drift > tolerance but R:R still ≥ 1.5 — trade still viable",
            })

    try:
        client_oid = f"fa-{uuid.uuid4().hex[:16]}"
        result = bitget_trader.place_market_order(
            symbol      = sym,
            side        = dir_,
            size_usdt   = float(sizing.get("notional_usdt") or 0),
            leverage    = int(sizing.get("leverage") or 1),
            sl_price    = signal.get("sl_price"),
            tp1_price   = signal.get("tp1_price"),
            tp2_price   = signal.get("tp2_price"),
            # Phase 2: full ladder. trader attaches one plan order per tier.
            tp_levels   = signal.get("tp_levels"),
            client_oid  = client_oid,
        )
    except Exception as e:
        _log(conn, "real_place_failed", None,
             {"symbol": sym, "direction": dir_, "error": str(e)[:200]})
        return None

    # ── Entry-drift guard ────────────────────────────────────────────────────
    # The scanner produces both a point-estimate `signal["entry_price"]` (the
    # ideal entry) AND an `signal["entry_zone"] = {low, high}` (the band the
    # operator's analysis blessed). The TP ladder is anchored to the scanner
    # entry, so when fill drifts far enough that the trade no longer makes
    # sense (TP1 ends up below entry on a Long, etc.) we abort.
    #
    # Two-tier check (updated 2026-05-26):
    #   1. If `entry_zone` is set, the fill is OK as long as it sits inside
    #      the zone — that's the band the scanner already accepted. This is
    #      the primary check; respects the scanner's intent rather than
    #      arbitrary ±2% around a single point.
    #   2. Outside the zone we fall back to a tolerance around the point
    #      estimate (MAX_ENTRY_DRIFT_PCT). When no zone is available the
    #      tolerance check is the only gate.
    #
    # Either failure → close immediately + log `real_entry_drift_aborted`.
    fill_px = float(result.get("mark_at_entry") or 0)
    intended_entry = float(signal.get("entry_price") or 0)
    zone = signal.get("entry_zone") or {}
    zone_low  = float(zone.get("low")  or 0)
    zone_high = float(zone.get("high") or 0)
    if zone_low > zone_high:
        zone_low, zone_high = zone_high, zone_low

    drift_pct = None
    inside_zone = False
    if fill_px and intended_entry:
        drift_pct = abs(fill_px - intended_entry) / intended_entry
    if fill_px and zone_low > 0 and zone_high > 0:
        # Small (0.25% of mid) tolerance lets a fill right at the edge pass.
        zone_mid = (zone_low + zone_high) / 2.0
        pad = zone_mid * 0.0025
        inside_zone = (zone_low - pad) <= fill_px <= (zone_high + pad)

    drift_violation = (
        fill_px and intended_entry and
        fa_config.MAX_ENTRY_DRIFT_PCT > 0 and
        drift_pct is not None and drift_pct > fa_config.MAX_ENTRY_DRIFT_PCT and
        not inside_zone
    )
    if drift_violation:
        try:
            bitget_trader.close_position(sym, dir_.lower(), percentage=100.0)
        except Exception as e:
            _log(conn, "real_entry_drift_close_failed", None, {
                "symbol": sym, "direction": dir_,
                "intended_entry": intended_entry, "fill_price": fill_px,
                "drift_pct": round(drift_pct * 100, 3),
                "zone_low": zone_low, "zone_high": zone_high,
                "error": str(e)[:200],
            })
            return None
        _log(conn, "real_entry_drift_aborted", None, {
            "symbol":         sym,
            "direction":      dir_,
            "intended_entry": intended_entry,
            "fill_price":     fill_px,
            "drift_pct":      round(drift_pct * 100, 3),
            "tolerance_pct":  fa_config.MAX_ENTRY_DRIFT_PCT * 100,
            "zone_low":       zone_low or None,
            "zone_high":      zone_high or None,
            "inside_zone":    inside_zone,
            "tp1":            signal.get("tp1_price"),
            "tp2":            signal.get("tp2_price"),
            "sl":             signal.get("sl_price"),
            "order_id":       result.get("order_id"),
            "client_oid":     client_oid,
        })
        return None

    pos_id = _insert_open_position(conn, signal, sizing, result)

    # A-D (Master plan Week 11) — record slippage on every fill so the
    # Execution Quality Monitor can spot deteriorating fills before they
    # erode the edge.
    try:
        from trading import exec_quality
        actual_fill = float(result.get("mark_at_entry") or 0)
        if intended_entry and actual_fill:
            exec_quality.record_slippage(
                conn, pos_id,
                intended_entry=intended_entry,
                actual_entry=actual_fill,
                direction=dir_,
            )
    except Exception as _eq_err:
        logger.debug("A-D slippage record failed for pos %s: %s", pos_id, _eq_err)

    _log(conn, "real_open", pos_id, {
        "symbol":         sym,
        "direction":      dir_,
        "score":          signal.get("consensus_score"),
        "entry":          result.get("mark_at_entry"),
        "sl":             signal.get("sl_price"),
        "tp1":            signal.get("tp1_price"),
        "tp2":            signal.get("tp2_price"),
        "notional":       result.get("size_usdt"),
        "lev_req":        result.get("leverage_requested"),
        "lev_actual":     result.get("leverage_actual"),
        "set_lev_result": result.get("set_leverage_result"),
        "attached_sl":    result.get("attached_sl"),
        "attached_tp1":   result.get("attached_tp1"),
        "order_id":       result.get("order_id"),
        "client_oid":     client_oid,
    })
    # Surface leverage mismatch as its own log event so it shows up
    # prominently in the UI decision feed.
    if (result.get("leverage_actual") != result.get("leverage_requested")
            and result.get("leverage_requested")):
        _log(conn, "lev_mismatch", pos_id, {
            "symbol":     sym,
            "requested":  result.get("leverage_requested"),
            "actual":     result.get("leverage_actual"),
            "set_result": result.get("set_leverage_result"),
        })
    return pos_id


def manage_real_positions(conn) -> dict:
    """
    Reconcile our DB state with Bitget's actual open positions, then
    apply lifecycle rules (BE/TRAIL/MAE) on positions still open.

    Lifecycle uses position_risk_monitor's thresholds (1× ATR for BE,
    2× ATR for trail, 1× ATR adverse for MAE) — same as paper.
    """
    if not fa_config.is_real_mode():
        return {"skipped": "not real mode"}

    # Pull what's *actually* open at Bitget
    try:
        live_positions = bitget_trader.get_open_positions()
    except Exception as e:
        _log(conn, "real_reconcile_error", None,
             {"error": str(e)[:200]})
        return {"error": str(e)[:200]}

    live_keys = {(p["symbol"], p["direction"].lower()) for p in live_positions}
    db_open   = _open_auto_positions(conn)

    summary = {"checked": 0, "closed_via_reconcile": 0,
               "be_moved": 0, "trail_moved": 0, "mae_cut": 0}

    # Reconciliation: anything in db_open that's NOT in live_positions
    # was closed by Bitget itself (preset SL/TP fired). Pull the actual
    # close price + net P&L (fees included) from Bitget's
    # /history-position endpoint so realized_pnl is accurate immediately.
    if db_open and any((p["symbol"], (p["direction"] or "").lower()) not in live_keys
                       for p in db_open):
        try:
            import time
            now_ms = int(time.time() * 1000)
            history = bitget_trader.get_position_history(
                start_ms=now_ms - 86400_000,   # last 24h
                end_ms=now_ms,
                limit=50,
            )
        except Exception as e:
            history = []
            _log(conn, "real_history_fetch_failed", None,
                 {"error": str(e)[:200]})
    else:
        history = []

    for p in db_open:
        key = (p["symbol"], (p["direction"] or "").lower())
        if key not in live_keys:
            # Match against history by symbol + direction + open_time
            # (positionId is the truly unique key but we don't store it
            # — close time within 24h of our open is a good proxy).
            match = None
            for h in history:
                if h["symbol"] == p["symbol"] and \
                   h["direction"].lower() == (p["direction"] or "").lower():
                    # Closest by close_ms (most recent close wins)
                    match = h
                    break
            if match:
                close_px  = match["close_price"]
                realized  = match["net_profit"]   # after fees + funding
                reason    = f"Bitget close · open {match['open_price']} → close {close_px}"
            else:
                # History not yet available — fall back to mark + 0 pnl,
                # but log so we can retry on next cycle.
                close_px  = float(bitget_trader.get_mark_price(p["symbol"]) or 0)
                realized  = 0.0
                reason    = "reconcile (history not yet available)"
            _mark_closed(conn, p["id"], close_px,
                          realized_pnl=realized, reason=reason)
            summary["closed_via_reconcile"] += 1

    # For positions STILL open, apply lifecycle rules
    db_open_after = [p for p in db_open
                     if (p["symbol"], (p["direction"] or "").lower())
                     in live_keys]

    for p in db_open_after:
        summary["checked"] += 1
        live = next(lp for lp in live_positions
                    if lp["symbol"] == p["symbol"]
                    and lp["direction"].lower() == (p["direction"] or "").lower())
        try:
            actions = _apply_lifecycle_rules(conn, p, live)
            for a in actions:
                summary[a] = summary.get(a, 0) + 1
        except Exception as e:
            _log(conn, "real_lifecycle_error", p["id"],
                 {"symbol": p["symbol"], "error": str(e)[:200]})

    return summary


def force_close_all(conn) -> int:
    """Close every auto_ai open position at market. Returns count closed."""
    if not fa_config.is_real_mode():
        return 0
    n = 0
    for p in _open_auto_positions(conn):
        try:
            bitget_trader.close_position(p["symbol"],
                                          (p["direction"] or "long").lower(),
                                          percentage=100.0)
            _log(conn, "real_force_close", p["id"],
                 {"symbol": p["symbol"], "reason": "pause_now / force"})
            n += 1
        except Exception as e:
            _log(conn, "real_force_close_error", p["id"],
                 {"symbol": p["symbol"], "error": str(e)[:200]})
    return n


# ── TP-fill detector (Phase 2 of multi-TP) ──────────────────────────────────

def _detect_tp_fills(conn, db_pos: dict, live: dict) -> list[dict]:
    """Compare the ORIGINALLY-placed tp_levels (stored in db_pos.tp_levels
    JSON at trade-open) against what's STILL pending on Bitget side (live.
    tp_levels — populated from /orders-plan-pending).

    A tier whose price is no longer in the pending list but is also not
    yet marked .hit must have FIRED. Update its .hit=True / .hit_at=<utc>
    and persist back to the JSON column.

    Returns the list of newly-detected fills (empty list = no change).
    The CALLER decides what to do with each fill (log + BE-on-TP1 trigger).
    """
    raw = db_pos.get("tp_levels")
    if not raw:
        return []
    try:
        db_tps = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(db_tps, list) or len(db_tps) <= 1:
        # Single-TP positions don't need Phase-2 reconciliation —
        # the existing /position-history path handles their close.
        return []

    # BUG-014 fix (2026-05-27): use a TOLERANCE-based match between DB and
    # live TP prices. Exact rounding to 6 decimals caused a false-positive
    # on INJUSDT: DB tier prices were stored at 4 decimals (5.9157, 6.1724,
    # 6.4291) but Bitget snapped them to its 3-decimal tick grid (5.916,
    # 6.172, 6.429). Exact comparison treated all 3 as "not in pending"
    # and marked them hit. AZTEC + TIA happened to have matching precision
    # so they weren't affected. Tolerance-match (0.05%) covers any normal
    # tick-rounding difference while still detecting real fills (which
    # always move price by orders of magnitude more).
    live_tps = live.get("tp_levels") or []
    live_prices = [float(t.get("price") or 0) for t in live_tps if t.get("price")]

    def _has_pending_match(target: float) -> bool:
        """True if any live TP price is within 0.05% of target."""
        if target <= 0:
            return False
        return any(abs(lp - target) / target < 0.0005 for lp in live_prices)

    newly_filled: list[dict] = []
    for tp in db_tps:
        if tp.get("hit"):
            continue
        # Phase-1 positions (opened before 2026-05-24 10:36 CEST) have
        # tp_levels JSON in the DB but only TP1 was ever actually attached
        # to Bitget as a plan order. The detector must NOT infer "filled"
        # from absence-in-pending for those tiers — they were never there.
        # Trade-open writes `attached=True` per tier when Bitget accepted
        # the place-tpsl-order. Skip any tier without that flag.
        if not tp.get("attached"):
            continue
        try:
            target_price = float(tp.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if target_price and not _has_pending_match(target_price):
            tp["hit"]    = True
            tp["hit_at"] = _utc_iso_now()
            newly_filled.append(tp)

    if newly_filled:
        try:
            conn.execute(
                "UPDATE positions SET tp_levels = ? WHERE id = ?",
                (json.dumps(db_tps), db_pos["id"]),
            )
            conn.commit()
        except Exception:
            pass
    return newly_filled


def _utc_iso_now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ── Lifecycle rules — mirror position_risk_monitor's thresholds ─────────────

def _apply_lifecycle_rules(conn, db_pos: dict, live: dict) -> list[str]:
    """Returns list of action labels for the summary."""
    actions: list[str] = []
    try:
        import chart_context
        ctx = chart_context.get_chart_context(live["symbol"], ["4H"]) or {}
        atr = float(((ctx.get("4H", {}).get("indicators", {})
                       .get("atr") or {}).get("value") or 0))
    except Exception:
        atr = 0.0
    if atr <= 0:
        return actions

    # Use Bitget's reported entry as the authoritative price — it's the actual
    # averaged fill, vs db_pos.entry_price which is the signal/target price
    # the executor *intended* to fill at. The two can drift by ~0.05-0.10% on
    # market entries due to slippage. The BE buffer must be applied to the
    # REAL fill so the buffer math (entry × 1.0015 for Long) actually nets
    # the trader ≥ $0 on an SL fill.
    entry   = float(live.get("entry_price") or db_pos.get("entry_price") or 0)
    mark    = float(live.get("mark_price") or 0)
    is_long = live["direction"].lower() == "long"
    if entry <= 0 or mark <= 0:
        return actions

    sign = 1 if is_long else -1
    current_pct = (mark - entry) / entry * 100.0 * sign
    atr_pct     = atr / entry * 100.0
    current_sl  = float(live.get("preset_sl") or entry)   # bitget side-of-truth

    # ── Phase 2: TP-fill detection ──────────────────────────────────────────
    # Compare ORIGINALLY-placed tp_levels (stored in DB at trade open) vs
    # what's still PENDING on Bitget. Any tier that disappeared from
    # pending = filled. Log it; if TP1 just fired, also fire the BE move
    # (operator default 2026-05-24: yes, first profit = capital protection).
    filled_tps = _detect_tp_fills(conn, db_pos, live)
    for tp in filled_tps:
        idx = tp.get("idx") or "?"
        _log(conn, "real_tp_hit", db_pos["id"], {
            "symbol":        live["symbol"],
            "tp_idx":        idx,
            "trigger_price": tp.get("price"),
            "size_pct":      tp.get("pct"),
            "hit_at":        tp.get("hit_at"),
        })
        actions.append(f"tp{idx}_hit")
    # If TP1 just fired, force-move SL to BE+buffer regardless of ATR position.
    # Standard playbook: first partial = capital protection. Skip if SL is
    # already at/past BE (current_sl already favourable).
    if any((t.get("idx") == 1) for t in filled_tps):
        be_sl = fa_config.be_price_for(entry, is_long)
        gap_pct = abs(be_sl - current_sl) / entry if entry else 0
        sl_already_protective = (current_sl >= be_sl) if is_long else (current_sl <= be_sl)
        if not sl_already_protective and gap_pct >= 0.0005:
            try:
                result = bitget_trader.modify_position_sl(
                    live["symbol"], live["direction"].lower(), be_sl
                )
                if result.get("ok"):
                    _log(conn, "real_be", db_pos["id"], {
                        "symbol": live["symbol"], "old_sl": current_sl,
                        "new_sl": be_sl, "entry": entry,
                        "buffer_pct": fa_config.BE_BUFFER_PCT,
                        "trigger":    "tp1_hit",
                        "result":     result,
                    })
                    actions.append("be_moved_on_tp1")
                    current_sl = be_sl   # update local for downstream checks
                else:
                    _log(conn, "real_be_failed", db_pos["id"], {
                        "symbol": live["symbol"], "trigger": "tp1_hit",
                        "reason": result.get("reason", "unknown"),
                    })
            except Exception as e:
                _log(conn, "real_be_failed", db_pos["id"], {
                    "symbol": live["symbol"], "trigger": "tp1_hit",
                    "error":  str(e)[:200],
                })

    # ── Feature 19 — Tiered "cuff the trade" BE move (Elder, 2026-05-24) ──
    # Before TP1 hit, progressively tighten SL as price moves toward TP1.
    # Three tiers: at 33% of distance to TP1 → move to BE; at 66% → lock
    # 33% of gain; at 90% → lock 66% of gain. Tracked in positions.be_tier_reached
    # so we don't re-apply or regress. Env-toggle FUTURES_AI_TIERED_BE_ENABLED.
    try:
        import os as _os
        if int(_os.environ.get("FUTURES_AI_TIERED_BE_ENABLED", "1")):
            tp_levels = json.loads(db_pos.get("tp_levels") or "[]")
            tp1 = next((t for t in tp_levels if t.get("idx") == 1), None)
            tier_done = int(db_pos.get("be_tier_reached") or 0)
            if tp1 and tp1.get("price") and tier_done < 3 and not (
                any((t.get("idx") == 1) for t in filled_tps)):
                # Compute pct of distance traveled toward TP1
                tp1_price = float(tp1["price"])
                distance_to_tp1 = abs(tp1_price - entry)
                if distance_to_tp1 > 0:
                    pct_traveled = abs(mark - entry) / distance_to_tp1
                    # Tier definitions: (threshold, new_sl_lock_fraction_of_distance)
                    # tier 1: 33% → SL at entry (BE)
                    # tier 2: 66% → SL at entry + 33% of distance
                    # tier 3: 90% → SL at entry + 66% of distance
                    tiers = [(0.33, 0.0), (0.66, 0.33), (0.90, 0.66)]
                    new_tier = tier_done
                    new_sl_target = current_sl
                    for tier_idx, (threshold, lock_frac) in enumerate(tiers, 1):
                        if tier_idx <= tier_done:
                            continue
                        if pct_traveled >= threshold:
                            # Compute SL at entry + lock_frac × distance (direction-aware)
                            lock_offset = distance_to_tp1 * lock_frac
                            candidate_sl = entry + sign * lock_offset
                            # Apply BE buffer for tier 1 (lock_frac=0)
                            if lock_frac == 0:
                                candidate_sl = fa_config.be_price_for(entry, is_long)
                            # Only adopt if candidate is more protective
                            if (is_long and candidate_sl > new_sl_target) or \
                               (not is_long and candidate_sl < new_sl_target):
                                new_sl_target = candidate_sl
                                new_tier = tier_idx
                    if new_tier > tier_done:
                        gap_pct = abs(new_sl_target - current_sl) / entry if entry else 0
                        if gap_pct >= 0.0005:
                            try:
                                r2 = bitget_trader.modify_position_sl(
                                    live["symbol"], live["direction"].lower(), new_sl_target)
                                if r2.get("ok"):
                                    conn.execute(
                                        "UPDATE positions SET be_tier_reached=? WHERE id=?",
                                        (new_tier, db_pos["id"]))
                                    conn.commit()
                                    _log(conn, "real_be_tier", db_pos["id"], {
                                        "symbol":     live["symbol"],
                                        "tier":       new_tier,
                                        "pct_traveled": round(pct_traveled * 100, 1),
                                        "old_sl":     current_sl,
                                        "new_sl":     new_sl_target,
                                        "result":     r2,
                                    })
                                    actions.append(f"be_tier_{new_tier}")
                                    current_sl = new_sl_target
                            except Exception as _e:
                                _log(conn, "real_be_tier_failed", db_pos["id"], {
                                    "symbol": live["symbol"], "tier": new_tier,
                                    "error":  str(_e)[:200],
                                })
    except Exception as _e:
        logger.debug("tiered BE check error: %s", _e)

    # MAE breach — -1× ATR (matches position_risk_monitor)
    if current_pct <= -atr_pct * 1.0:
        try:
            bitget_trader.close_position(live["symbol"],
                                          live["direction"].lower(),
                                          percentage=100.0)
            # Compute gross realized P&L from price diff × size × direction.
            # Fees (~0.12% round-trip) are NOT subtracted here — the next
            # reconcile cycle pulls the fee-adjusted net from Bitget's
            # position history and overwrites. But this gross approximation
            # is right to within ~1% and ends the "$0 reported for every
            # MAE_cut close" bug.
            # BUG 2026-05-31: get_open_positions() normalises the field as
            # `size_contracts`, NOT `total`. Reading the wrong key gave 0
            # and produced gross_pnl=0 — every MAE_cut close logged as $0
            # P&L (MMT, GRASS, etc.). Fall through both names for safety.
            size = float(live.get("size_contracts")
                          or live.get("total")
                          or 0)
            gross_pnl = (mark - entry) * size * sign
            _mark_closed(conn, db_pos["id"], mark, realized_pnl=gross_pnl,
                          reason="MAE breach auto-cut")
            actions.append("mae_cut")
        except Exception as e:
            _log(conn, "real_mae_cut_failed", db_pos["id"],
                 {"symbol": live["symbol"], "error": str(e)[:200]})
        return actions

    # Trail — by default +2× ATR triggers, with new SL at entry + 0.5× ATR.
    # CPR width forecasting (Feature 2, 2026-05-24): if the day-type is
    # "trend" (narrow CPR), widen the trail to give the position room to
    # run; if "range" (wide CPR), tighten the trail.
    trail_atr_mult = 0.5   # default — distance of new SL beyond entry
    try:
        import os as _os
        if int(_os.environ.get("FUTURES_AI_CPR_TRAIL_ENABLED", "1")):
            from chart_cpr import compute_cpr_from_df, cpr_day_type
            from chart_context import get_chart_context as _gcc
            ctx_1d = _gcc(live["symbol"], ["1D"]) or {}
            df_1d = (ctx_1d.get("1D") or {}).get("df")
            if df_1d is not None and len(df_1d) >= 2:
                _cpr = compute_cpr_from_df(df_1d)
                _dt  = cpr_day_type(_cpr)
                # trail_atr_mult fields from cpr_day_type are 1.0/1.5/2.0 representing
                # the trail-distance multiplier. Map to "new SL beyond entry" by /4.
                # (default 0.5 was for 2.0 trail mult, so factor is /4)
                base = _dt.get("trail_atr_mult", 1.5)
                trail_atr_mult = base / 4.0   # → 0.25 (range), 0.375 (neutral), 0.5 (trend)
    except Exception:
        pass

    if current_pct >= atr_pct * 2.0:
        new_sl = entry + sign * (atr * trail_atr_mult)
        gap_pct = abs(new_sl - current_sl) / entry if entry else 0
        if ((is_long and new_sl > current_sl) or (not is_long and new_sl < current_sl)) \
                and gap_pct >= 0.0005:
            try:
                result = bitget_trader.modify_position_sl(
                    live["symbol"], live["direction"].lower(), new_sl
                )
                if result.get("ok"):
                    _log(conn, "real_trail", db_pos["id"], {
                        "symbol": live["symbol"], "old_sl": current_sl, "new_sl": new_sl,
                        "result": result,
                    })
                    actions.append("trail_moved")
                else:
                    _log(conn, "real_trail_failed", db_pos["id"],
                         {"symbol": live["symbol"],
                          "reason": result.get("reason", "unknown")})
            except Exception as e:
                _log(conn, "real_trail_failed", db_pos["id"],
                     {"symbol": live["symbol"], "error": str(e)[:200]})
        return actions

    # BE move — +1× ATR
    # SL must NOT be placed at raw entry: hitting it would still cost the
    # round-trip taker fee (~0.12% on Bitget) + a slippage allowance, locking
    # in a small loss. fa_config.be_price_for() applies the buffer.
    #
    # `current_sl` is Bitget's tick-rounded reported value (e.g. 134.55 for
    # an internally-computed 134.5515). A naive `be_sl > current_sl` compare
    # in full precision will spuriously fire a redundant move every monitor
    # cycle. Compare with a 0.05% epsilon — that's well below the BE buffer
    # itself (0.15%) and well above any tick-rounding noise.
    if current_pct >= atr_pct * 1.0:
        be_sl = fa_config.be_price_for(entry, is_long)
        gap_pct = abs(be_sl - current_sl) / entry if entry else 0
        # "tighter" means the be_sl moves toward favorable side AND is
        # materially different from current_sl (more than tick noise).
        if is_long:
            be_is_tighter = (be_sl > current_sl) and (gap_pct >= 0.0005)
        else:
            be_is_tighter = (be_sl < current_sl) and (gap_pct >= 0.0005)
        if be_is_tighter:
            try:
                result = bitget_trader.modify_position_sl(
                    live["symbol"], live["direction"].lower(), be_sl
                )
                if result.get("ok"):
                    _log(conn, "real_be", db_pos["id"], {
                        "symbol": live["symbol"], "old_sl": current_sl,
                        "new_sl": be_sl, "entry": entry,
                        "buffer_pct": fa_config.BE_BUFFER_PCT,
                        "result": result,
                    })
                    actions.append("be_moved")
                else:
                    _log(conn, "real_be_failed", db_pos["id"],
                         {"symbol": live["symbol"],
                          "reason": result.get("reason", "unknown")})
            except Exception as e:
                _log(conn, "real_be_failed", db_pos["id"],
                     {"symbol": live["symbol"], "error": str(e)[:200]})

    return actions
