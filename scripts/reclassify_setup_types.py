"""
Reclassify all historical positions with both classifiers and compare.

Steps per position:
  1. classify_rules — fast multi-signal heuristic (B+C+D)
  2. classify_ai    — Haiku ground truth (A)
  3. Write the AI label to positions.setup_type (it's the more reliable
     of the two for analytics)
  4. Track agreement / disagreement / confidence stats

This serves three purposes:
  - Final setup_type labels are AI-validated.
  - Rule-based classifier gets cross-checked against ground truth so we
    know which heuristics are off and by how much.
  - Going forward, the live scanner uses classify_rules() for free; this
    backfill is the periodic sanity check.

Idempotent: rerunning re-classifies everything. Cost ~110 Haiku calls.
"""
import datetime as _dt
import sys

sys.path.insert(0, "/home/fbauer/trading-journal")

from collections import Counter

from database import db_conn
import chart_candles
import chart_context
import setup_classifier


_GARBAGE = ("claude-", "haiku", "sonnet", "opus", "gpt-", "Quick score only")


def _is_garbage(s: str | None) -> bool:
    if not s:
        return True
    sl = s.lower()
    return any(tok.lower() in sl for tok in _GARBAGE)


def _to_ms(iso: str) -> int | None:
    try:
        dt = _dt.datetime.fromisoformat((iso or "")[:19]).replace(tzinfo=_dt.timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def main():
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT id, symbol, direction, open_time, setup_type
            FROM positions
            WHERE open_time IS NOT NULL AND open_time != ''
            ORDER BY open_time DESC
        """).fetchall()]

    targets = rows   # always reclassify all rows — backfill is idempotent
    print(f"Reclassifying {len(targets)} positions with rules + AI…")

    rule_counts: Counter = Counter()
    ai_counts:   Counter = Counter()
    agree = 0
    disagree = 0
    rule_failed = 0
    ai_failed   = 0
    confusion: dict[tuple[str, str], int] = {}

    with db_conn() as conn:
        for i, r in enumerate(targets, 1):
            ms = _to_ms(r["open_time"])
            if ms is None:
                rule_failed += 1
                continue

            # 4H candles fed to both classifiers
            df_4h = chart_candles.get_candles_at_time(r["symbol"], "4H", ms, limit=200)
            if df_4h is None or df_4h.empty:
                rule_failed += 1
                ai_failed   += 1
                continue

            rule = setup_classifier.classify_rules(
                r["symbol"], r["direction"], r["open_time"], candles_df=df_4h
            )
            rule_label = rule["archetype"]
            rule_counts[rule_label] += 1

            # For AI: pull pre-formatted prompt text via chart_context
            try:
                ctx_full = chart_context.get_historical_context(
                    r["symbol"], ["4H", "1H"], ms
                )
                pt_4h = (ctx_full.get("4H") or {}).get("prompt_text", "")
                pt_1h = (ctx_full.get("1H") or {}).get("prompt_text", "")
            except Exception:
                pt_4h = pt_1h = ""

            ai = setup_classifier.classify_ai(
                r["symbol"], r["direction"], r["open_time"],
                prompt_text_4h=pt_4h, prompt_text_1h=pt_1h,
            )
            ai_label = ai["archetype"]
            if "AI classification failed" in (ai.get("reasoning") or ""):
                ai_failed += 1
            ai_counts[ai_label] += 1

            # Write AI label (more trustworthy) to setup_type
            conn.execute(
                "UPDATE positions SET setup_type=? WHERE id=?",
                (ai_label, r["id"]),
            )

            if rule_label == ai_label:
                agree += 1
            else:
                disagree += 1
            confusion[(rule_label, ai_label)] = confusion.get((rule_label, ai_label), 0) + 1

            if i % 10 == 0:
                conn.commit()
                print(f"  …{i}/{len(targets)} agree={agree} disagree={disagree}")
        conn.commit()

    print()
    print("=== Rule-based distribution ===")
    for k, n in rule_counts.most_common():
        print(f"  {k:18s} {n:4d}")
    print()
    print("=== AI distribution (final) ===")
    for k, n in ai_counts.most_common():
        print(f"  {k:18s} {n:4d}")
    print()
    print(f"Agreement: {agree}/{agree + disagree}  "
          f"({100*agree/max(1, agree+disagree):.0f}%)")
    if rule_failed:
        print(f"Rule classifier failed: {rule_failed}")
    if ai_failed:
        print(f"AI classifier failed:   {ai_failed}")

    print()
    print("=== Confusion matrix (rule → AI) ===")
    print("rule_label          → ai_label          n")
    print("-" * 56)
    for (r_lbl, a_lbl), n in sorted(confusion.items(), key=lambda x: -x[1])[:15]:
        marker = "  " if r_lbl == a_lbl else "✗ "
        print(f"{marker}{r_lbl:18s} → {a_lbl:18s} {n:3d}")


if __name__ == "__main__":
    main()
