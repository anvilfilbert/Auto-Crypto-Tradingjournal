"""
Daily Telegram Report — operator status loop.

Runs once at 09:00 UTC. Pulls from all the auto_ai observability tables
(positions, futures_ai_log, learner_log, learned_params, ...) and
constructs a single-message status report. Sent via the existing
telegram_notify pipeline.

Public:
  generate_report(conn) -> str    # the formatted message
  send_daily_report(conn) -> bool # generate + send
"""
from __future__ import annotations

import datetime as _dt
import logging

_log = logging.getLogger(__name__)

# Constants used in report countdowns
TODAY_REF = "2026-05-31"  # option-a ship date — reminders count from here


def _days_since(date_str: str) -> int:
    try:
        ref = _dt.datetime.fromisoformat(date_str)
        now = _dt.datetime.now()
        return (now - ref).days
    except Exception:
        return 0


def _format_section(title: str, body: str) -> str:
    return f"\n*{title}*\n{body}"


def _perf_section(conn) -> str:
    """24h / 7d / 30d P&L + WR."""
    try:
        from trading import kill_switch
        rt = kill_switch.evaluate(conn)
    except Exception:
        return "  (snapshot unavailable)"
    lines = [
        f"  24h: {rt.get('daily_pnl_pct'):+.2f}%",
        f"  7d:  ${rt.get('pnl_7d_usd', 0):+.2f} ({rt.get('pnl_7d_pct', 0):+.2f}%)  WR {rt.get('winrate_7d_pct', 0):.0f}% ({rt.get('winrate_7d_wins', 0)}/{rt.get('winrate_7d_total', 0)})",
        f"  30d: ${rt.get('pnl_30d_usd', 0):+.2f} ({rt.get('pnl_30d_pct', 0):+.2f}%)  WR {rt.get('winrate_30d_pct', 0):.0f}% ({rt.get('winrate_30d_wins', 0)}/{rt.get('winrate_30d_total', 0)})",
    ]
    return "\n".join(lines)


def _reminders_section(conn) -> str:
    """Active operator countdowns."""
    days = _days_since(TODAY_REF)
    lines: list[str] = []
    # Red-Team soft→hard review at +14d
    rt_remaining = 14 - days
    if rt_remaining > 0:
        lines.append(f"  • Red-Team soft→hard review in {rt_remaining}d")
    elif rt_remaining == 0:
        lines.append("  • Red-Team soft→hard review TODAY — read A-A log + decide")
    elif rt_remaining > -7:
        lines.append(f"  • Red-Team soft→hard review OVERDUE by {-rt_remaining}d")
    # Strategy Selector revisit at +30d
    ss_remaining = 30 - days
    if ss_remaining > 0:
        lines.append(f"  • Strategy Selector revisit in {ss_remaining}d (post L-3)")
    elif ss_remaining == 0:
        lines.append("  • Strategy Selector revisit TODAY")
    return "\n".join(lines) if lines else "  (none active)"


def _learner_activity_section(conn) -> str:
    """Last 24h of learner_log entries."""
    try:
        rows = conn.execute(
            "SELECT learner_name, param_key, action, gate_reason, new_value, sample_size "
            "FROM learner_log WHERE ts >= datetime('now', '-24 hours') "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()
    except Exception:
        return "  (table unavailable)"
    if not rows:
        return "  (no learner activity in 24h)"
    out = []
    for r in rows:
        action = r["action"]
        key = r["param_key"]
        if action == "applied":
            out.append(f"  ✓ {key}: → {r['new_value']} (n={r['sample_size']})")
        elif action == "skipped_gate":
            reason = (r["gate_reason"] or "")[:50]
            out.append(f"  • {key}: skipped — {reason}")
        elif action == "reverted":
            out.append(f"  ⏪ {key}: REVERTED — {(r['gate_reason'] or '')[:50]}")
        elif action == "skipped_pinned":
            out.append(f"  📌 {key}: pinned — skipped")
    return "\n".join(out)


def _noise_gates_section(conn) -> str:
    """Rejection counts per noise-gate category in last 24h."""
    try:
        rows = conn.execute(
            "SELECT event, COUNT(*) AS n FROM futures_ai_log "
            "WHERE ts >= datetime('now', '-24 hours') "
            "AND event LIKE 'rejected_%' "
            "GROUP BY event ORDER BY n DESC"
        ).fetchall()
    except Exception:
        return "  (table unavailable)"
    if not rows:
        return "  (no rejections in 24h — either gates idle or scanner idle)"
    return "\n".join(f"  {r['event']}: {r['n']}" for r in rows)


def _edge_decay_alerts_section(conn) -> str:
    """Any archetype in watch or alert state."""
    try:
        from trading import edge_decay
        alerts = edge_decay.alerts_only(conn, window_days=30)
    except Exception:
        return "  (module unavailable)"
    if not alerts:
        return "  ✓ no archetypes in watch/alert state"
    out = []
    for a in alerts:
        out.append(f"  ⚠ {a['archetype']} ({a['severity']}): "
                    f"CUSUM={a['cusum_value']:.2f}{'!' if a['cusum_alert'] else ''}, "
                    f"PH={a['ph_value']:.2f}{'!' if a['ph_alert'] else ''} (n={a['n']})")
    return "\n".join(out)


def _postmortem_section(conn) -> str:
    """Top recurring loss patterns (last 7d) — emitted by A-C Post-Mortem."""
    try:
        from trading import post_mortem
        tags = post_mortem.top_recurring_tags(conn, window_days=7, limit=3)
    except Exception:
        return "  (module unavailable)"
    if not tags:
        return "  ✓ no analyzed losses in 7d"
    out = []
    for t in tags:
        marker = " ⚠" if t.get("high_count", 0) > 0 else ""
        out.append(f"  {t['tag']}: {t['count']}× (high-sev: {t.get('high_count', 0)}){marker}")
    return "\n".join(out)


def generate_report(conn) -> str:
    """Compose the full daily report."""
    today = _dt.date.today().isoformat()
    parts = [f"🤖 *Auto-AI Daily Status* — {today}"]
    parts.append(_format_section("📊 Performance", _perf_section(conn)))
    parts.append(_format_section("⏰ Reminders", _reminders_section(conn)))
    parts.append(_format_section("🔄 Learner activity (24h)", _learner_activity_section(conn)))
    parts.append(_format_section("🚫 Noise gates (24h rejections)", _noise_gates_section(conn)))
    parts.append(_format_section("📉 Edge-decay watch", _edge_decay_alerts_section(conn)))
    parts.append(_format_section("🔍 Top loss patterns (7d)", _postmortem_section(conn)))
    parts.append(_format_section("⚡ Execution quality (7d)", _exec_quality_section(conn)))
    return "\n".join(parts)


def _exec_quality_section(conn) -> str:
    try:
        from trading import exec_quality
        return exec_quality.daily_report_line(conn)
    except Exception:
        return "  (module unavailable)"


def send_daily_report(conn) -> bool:
    """Generate + send via Telegram."""
    try:
        msg = generate_report(conn)
        import telegram_notify
        return telegram_notify.send_message(msg)
    except Exception as e:
        _log.warning("daily_report send failed: %s", e)
        return False
