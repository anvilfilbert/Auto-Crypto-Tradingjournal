"""
ai_blindspots.py — Pattern miner over closed-trade analyses.

Two complementary loops, both purely analytical (no AI calls):

(a) Phrase mining
    For every closed trade with analysis_json, parse the AI's key_conditions
    and cot_reasoning text. Tokenize into 2–4 word phrases. Group by phrase
    and compute the empirical win rate of trades that cited it. Surface
    phrases the AI uses confidently but that don't predict winners.

(b) Structured feature calibration
    Extract concrete features from analysis_json (score bucket, direction,
    setup_label, R:R bucket, key phrases present/absent). For each feature
    value, compute trade count + win rate. The AI then sees its own
    historical edge per feature instead of relying on textbook intuition.

Both outputs converge into mine_and_format_for_prompt() which produces a
single text block to inject into the cached stable_prefix. The block
deliberately fits in ~600 tokens so it doesn't blow up the cached prompt.

Hindsight verdicts (TP/FP/TN/FN) — when present in `outcome` column — are
used to label samples beyond just realised P&L.
"""
import json
import re
from collections import Counter, defaultdict

from database import db_conn

# Phrases we explicitly extract as boolean features (case-insensitive substring)
_FEATURE_TAGS = [
    "ema fully bullish",
    "ema fully bearish",
    "adx",                   # broad — refined to bucketed value below
    "rsi overbought",
    "rsi oversold",
    "macd bullish",
    "macd bearish",
    "support cluster",
    "resistance cluster",
    "liquidation",
    "smart money",
    "fully bullish",         # may overlap with ema; intentional
    "fully bearish",
    "weak",                  # signals weak 4H/1D
    "strong trend",
    "counter-trend",
    "above ema",
    "below ema",
    "asia session",
]

# Score buckets — match Kelly buckets so trader's mental model stays consistent
def _score_bucket(score) -> str:
    try:
        s = int(score)
    except (TypeError, ValueError):
        return "unknown"
    if s <= 5: return "≤5"
    if s == 6: return "6"
    if s <= 8: return "7-8"
    return "9-10"


def _rr_bucket(rr) -> str:
    try:
        r = float(rr)
    except (TypeError, ValueError):
        return "unknown"
    if r < 1.5: return "<1.5"
    if r < 2.0: return "1.5–2"
    if r < 3.0: return "2–3"
    return "≥3"


def _load_closed_trades(min_rows: int = 10) -> list[dict]:
    """Pull closed trades joined with their analyzed_calls reasoning."""
    rows: list[dict] = []
    with db_conn() as conn:
        cur = conn.execute("""
            SELECT id, symbol, direction, setup_score, setup_label,
                   rr_ratio, analysis_json, outcome, outcome_pnl,
                   hit_tp1, hit_tp2, hit_sl, cot_reasoning
            FROM analyzed_calls
            WHERE outcome IS NOT NULL
              AND outcome IN ('won','lost')
              AND analysis_json IS NOT NULL
        """)
        for r in cur:
            try:
                aj = json.loads(r["analysis_json"]) if r["analysis_json"] else {}
            except Exception:
                aj = {}
            rows.append({
                "id":           r["id"],
                "symbol":       r["symbol"],
                "direction":    r["direction"],
                "setup_score":  r["setup_score"],
                "setup_label":  r["setup_label"],
                "rr_ratio":     r["rr_ratio"],
                "outcome":      r["outcome"],
                "won":          r["outcome"] == "won",
                "key_conditions": aj.get("key_conditions") or [],
                "cot":          (r["cot_reasoning"] or aj.get("cot_reasoning") or ""),
            })
    return rows if len(rows) >= min_rows else []


# ── (a) Phrase mining ────────────────────────────────────────────────────────

def _tokenize_phrases(text: str, min_words: int = 2, max_words: int = 4) -> list[str]:
    """Generate lowercase n-grams (2-4 words) from a reasoning string."""
    if not text:
        return []
    # Cheap normalization: lower, strip punctuation except space/dash, collapse ws
    clean = re.sub(r"[^a-z0-9\s\-]", " ", text.lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    words = clean.split()
    out = []
    for n in range(min_words, max_words + 1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            # Skip phrases that are all stopwords-ish
            if len(phrase) >= 6:
                out.append(phrase)
    return out


_STOP_PHRASE_RE = re.compile(
    r"^(the |a |to |of |and |is |with |on |in |at |for |as |by |this )",
    re.I,
)


def mine_phrase_blindspots(trades: list[dict] = None, min_count: int = 5,
                            limit: int = 8) -> list[dict]:
    """For phrases that appear in >= min_count closed trades, compute win rate
    + lift vs baseline. Surface the ones where the AI was wrong most often."""
    if trades is None:
        trades = _load_closed_trades()
    if not trades:
        return []
    baseline_wr = sum(1 for t in trades if t["won"]) / len(trades)
    # Phrase → list[won_bool]
    by_phrase: dict[str, list[bool]] = defaultdict(list)
    for t in trades:
        # Combine reasoning + each key_condition into one text blob per trade
        blob = " ".join([*(t["key_conditions"] or []), t["cot"]])
        seen_in_trade = set()
        for phrase in _tokenize_phrases(blob):
            if _STOP_PHRASE_RE.match(phrase):
                continue
            if phrase in seen_in_trade:
                continue  # one mention per trade — avoids inflating from repetition
            seen_in_trade.add(phrase)
            by_phrase[phrase].append(t["won"])

    blindspots = []
    for phrase, results in by_phrase.items():
        if len(results) < min_count:
            continue
        wr = sum(results) / len(results)
        lift = wr - baseline_wr
        blindspots.append({
            "phrase":   phrase,
            "n":        len(results),
            "win_rate": round(wr * 100, 1),
            "lift":     round(lift * 100, 1),   # vs baseline WR
            "baseline_wr": round(baseline_wr * 100, 1),
        })
    # Sort by absolute lift × sqrt(n) so we get stable, meaningful patterns
    blindspots.sort(
        key=lambda b: abs(b["lift"]) * (b["n"] ** 0.5),
        reverse=True,
    )
    return blindspots[:limit]


# ── (b) Structured feature calibration ──────────────────────────────────────

def _features_of(trade: dict) -> dict:
    """Extract structured boolean/categorical features from one trade."""
    kc_text = " ".join(trade["key_conditions"] or []).lower()
    cot     = (trade["cot"] or "").lower()
    blob    = kc_text + " " + cot

    feats = {
        "score":       _score_bucket(trade["setup_score"]),
        "direction":   trade["direction"] or "Long",
        "setup_type":  trade["setup_label"] or "unspecified",
        "rr":          _rr_bucket(trade["rr_ratio"]),
    }
    for tag in _FEATURE_TAGS:
        feats[f"has[{tag}]"] = tag in blob
    return feats


def compute_feature_calibration(trades: list[dict] = None,
                                 min_count: int = 5) -> dict:
    """Returns {feature: [{value, n, win_rate}]} for each structured feature
    where at least one value has min_count samples."""
    if trades is None:
        trades = _load_closed_trades()
    if not trades:
        return {}
    # feature → value → list[won]
    grouped: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for t in trades:
        f = _features_of(t)
        for fname, fval in f.items():
            grouped[fname][str(fval)].append(t["won"])

    out: dict[str, list[dict]] = {}
    for fname, by_val in grouped.items():
        entries = []
        for val, results in by_val.items():
            if len(results) < min_count:
                continue
            wr = sum(results) / len(results)
            entries.append({"value": val, "n": len(results),
                            "win_rate": round(wr * 100, 1)})
        if entries:
            entries.sort(key=lambda e: e["win_rate"])
            out[fname] = entries
    return out


# ── Prompt-ready block ──────────────────────────────────────────────────────

def mine_and_format_for_prompt() -> str:
    """Produce the cached-prefix block. Empty string if too little data."""
    trades = _load_closed_trades()
    if not trades:
        return ""
    n_total = len(trades)
    n_won   = sum(1 for t in trades if t["won"])
    baseline = round(n_won / n_total * 100, 1)

    phrases = mine_phrase_blindspots(trades)
    feats   = compute_feature_calibration(trades)

    lines = [
        f"AI BLIND-SPOT ANALYSIS — empirical learnings from {n_total} closed trades "
        f"(baseline WR {baseline}%):",
    ]
    if phrases:
        lines.append("Phrases the AI cited and their actual win rates:")
        for p in phrases[:6]:
            arrow = "↓" if p["lift"] < 0 else "↑"
            lines.append(
                f"  • \"{p['phrase']}\" — used in {p['n']} trades, WR {p['win_rate']}% "
                f"({arrow}{abs(p['lift']):.1f}pp vs baseline)"
            )
    if feats:
        lines.append("Empirical win rate by feature value:")
        # Show only the feature groups with widest WR spread (most informative)
        spread = []
        for fname, entries in feats.items():
            if len(entries) >= 2:
                wrs = [e["win_rate"] for e in entries]
                spread.append((max(wrs) - min(wrs), fname, entries))
        spread.sort(reverse=True)
        for _, fname, entries in spread[:4]:
            top = entries[-1]   # highest WR
            bot = entries[0]    # lowest WR
            lines.append(
                f"  • {fname}: best={top['value']} ({top['win_rate']}%, n={top['n']}) · "
                f"worst={bot['value']} ({bot['win_rate']}%, n={bot['n']})"
            )
    if len(lines) == 1:
        return ""
    lines.append("Treat these as priors, not rules — they reflect your own "
                 "trade history, not market truth.")
    return "\n".join(lines)
