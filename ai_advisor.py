"""
ai_advisor.py — Claude-powered trading improvement recommendations.

Builds a rich prompt from aggregated trading stats and sends it to
claude-sonnet-4-6 via the Anthropic SDK. Returns structured JSON
with sections for strengths, weaknesses, and specific recommendations.

The full stats dict (from analytics.py) is serialized into the prompt so
Claude can reference real numbers (e.g., "Your BOME win rate is 72% but
your ZEN positions lost $45 on average — consider tighter stops there").
"""

import json
import os
from constants import MODEL, FAST_MODEL

from ai_client import send as ai_send
from analytics import get_dashboard_kpis, get_deep_stats
from database  import db_conn
from helpers   import strip_fence, build_cached_messages
import market_context
import prompt_builder




def _prune_stats(deep: dict) -> dict:
    """
    Strip empty arrays and low-signal data before feeding to Claude.
    Caps by_symbol to top 10 and by_hour to top 8 most-differentiated hours.
    Skill-provenance slices stay as-is — they're already small (≤7 buckets).
    """
    out = {k: v for k, v in deep.items() if not (isinstance(v, list) and not v)}
    if "by_symbol" in out and isinstance(out["by_symbol"], list):
        out["by_symbol"] = sorted(out["by_symbol"], key=lambda x: -x.get("trade_count", 0))[:10]
    if "by_hour" in out and isinstance(out["by_hour"], list):
        filtered = [h for h in out["by_hour"] if h.get("trade_count", 0) >= 3]
        out["by_hour"] = sorted(filtered, key=lambda x: -abs(x.get("win_rate", 50) - 50))[:8]
    return out


def _build_prompt(kpis: dict, deep: dict, mkt_ctx: str = "",
                  filters: dict = None) -> str:
    """Serialize the key stats into a structured prompt for Claude."""

    pruned = _prune_stats(deep)

    # Pull only the data that's most useful for analysis (skip raw chart arrays)
    summary = {
        "overview": {
            "total_trades":   kpis["total_trades"],
            "win_rate_pct":   kpis["win_rate"],
            "total_pnl_usdt": kpis["total_pnl"],
            "total_fees_usdt": kpis["total_fees"],
            "best_trade_usdt":  kpis["best_trade"],
            "worst_trade_usdt": kpis["worst_trade"],
            "avg_win_usdt":     kpis["avg_win"],
            "avg_loss_usdt":    kpis["avg_loss"],
            "profit_factor":    kpis["profit_factor"],
            "max_drawdown_usdt": kpis["max_drawdown"],
        },
        "by_symbol":    pruned.get("by_symbol", []),
        "by_month":     pruned.get("by_month", []),
        "by_weekday":   pruned.get("by_weekday", []),
        "by_hour":      pruned.get("by_hour", []),
        "by_direction": pruned.get("by_direction", []),
        "duration_buckets": pruned.get("duration_buckets", []),
        "streaks":      pruned.get("streaks", {}),
        "fee_analysis": pruned.get("fee_analysis", {}),
        "worst_symbols": pruned.get("worst_symbols", []),
        # ── Skill-provenance slices (per-trade tagging from 2026-05-23) ─────
        # These are the input the advisor needs to comment on whether the
        # *trading skills themselves* are working. Without them the advisor
        # can only attribute performance to symbols/hours.
        "skill_provenance": {
            "by_consensus_model": pruned.get("by_consensus_model", []),
            "by_bear_phase":      pruned.get("by_bear_phase", []),
            "by_archetype":       pruned.get("by_archetype", []),
            "by_po3_bucket":      pruned.get("by_po3_bucket", []),
            "by_opus_overrides":  pruned.get("by_opus_overrides", []),
            "by_tp_count":        pruned.get("by_tp_count", []),
        },
    }

    stats_json  = json.dumps(summary)
    exch        = ((filters or {}).get("exchange") or "").capitalize()
    exch_label  = f"{exch} USDT-M Futures" if exch else "Multi-exchange USDT-M Futures (Bitget + Blofin)"

    mkt_block = f"\nCURRENT MARKET CONTEXT:\n{mkt_ctx}\n" if mkt_ctx else ""

    return f"""You are a crypto futures trading coach. Analyze this trader's 6-month {exch_label} data and respond with ONLY a valid JSON object — no markdown, no code fences, no explanation outside the JSON.

TRADING STATISTICS:
{stats_json}
{mkt_block}

The `skill_provenance` section is new and important: it breaks down performance by the
auto-trader's *trading skills* (consensus model used, bear-phase alignment at entry,
setup archetype, PO3 modifier stacking, Opus override usage, TP-ladder depth). If any
skill cohort has ≥5 trades AND a clearly different win rate / PnL from the overall
average, surface it in `skill_insights`. Skip cohorts with <5 trades — too noisy.

Respond with this exact JSON structure. Keep each text field concise (2-3 sentences max). Include 3-5 items in strengths, weaknesses, recommendations, and symbol_insights:

{{"overall_status":"2-3 sentence honest summary referencing actual numbers","score":{{"value":1-10,"label":"Poor|Developing|Competent|Good|Excellent"}},"strengths":[{{"title":"short title","detail":"2 sentences with specific numbers"}}],"weaknesses":[{{"title":"short title","detail":"2 sentences with specific numbers"}}],"recommendations":[{{"priority":"High|Medium|Low","title":"short title","action":"one specific action","expected_impact":"one expected result"}}],"symbol_insights":[{{"symbol":"XYZUSDT","insight":"one sentence"}}],"skill_insights":[{{"skill":"consensus_model|bear_phase|archetype|po3_bucket|opus_overrides|tp_count","cohort":"specific bucket value","verdict":"working|hurting|inconclusive","detail":"one sentence with the win rate / pnl gap vs overall"}}],"risk_management":"2-3 sentences on position sizing and stops","mindset_note":"one honest encouraging sentence"}}

Reference real numbers (win rates, PnL figures, symbols). No generic advice. Skill_insights is empty list if no cohort has ≥5 trades yet."""


def analyze(filters: dict = None) -> dict:
    """
    Run the full AI analysis. Returns a dict with Claude's assessment.
    Raises on API error.
    """
    if filters is None:
        filters = {}

    with db_conn() as conn:
        # Check whether the exchange column exists; fall back to unfiltered if not
        # (handles DBs that pre-date the v2.5 multi-exchange migration).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
        safe_filters = filters if "exchange" in cols else {
            k: v for k, v in filters.items() if k != "exchange"
        }
        kpis   = get_dashboard_kpis(filters=safe_filters, conn=conn)
        deep   = get_deep_stats(filters=safe_filters, conn=conn)
        stable = prompt_builder.build_stable_prefix(conn)

    ctx     = market_context.get_market_context(["BTCUSDT"])
    mkt_str = market_context.format_for_prompt(ctx)
    prompt  = _build_prompt(kpis, deep, mkt_str, filters=safe_filters)

    raw_text, _cached = ai_send(
        "advisor", MODEL,
        build_cached_messages("", prompt, stable_prefix=stable),
        max_tokens=4096,
    )
    raw = strip_fence(raw_text.strip())

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "overall_status": raw,
            "score":          {"value": 0, "label": "N/A"},
            "strengths":      [],
            "weaknesses":     [],
            "recommendations": [],
            "symbol_insights": [],
            "risk_management": "",
            "mindset_note":    "",
        }

    result["_model"] = MODEL
    return result
