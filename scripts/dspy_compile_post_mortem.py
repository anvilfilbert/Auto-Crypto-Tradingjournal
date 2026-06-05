"""Compile dspy_modules.post_mortem.PostMortemSignature with BootstrapFewShot.

Reads historical positions WHERE postmortem_done=1 and uses the previously-
saved tag+severity as ground truth. Runs DSPy's search to find good few-shot
demos, then saves the compiled program to dspy_modules/compiled/.

OPERATOR-TRIGGERED. Burns API tokens during compilation:
- BootstrapFewShot makes (max_bootstrapped_demos × ~3) Haiku calls per trial,
  × N trials.  Default: ~30 calls per round. At Haiku $1/$5 per 1M, roughly
  $0.05-0.10 per round depending on prompt size.

Usage:
    python scripts/dspy_compile_post_mortem.py --min-samples 30 --dry-run
    python scripts/dspy_compile_post_mortem.py --min-samples 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dspy
from dspy.teleprompt import BootstrapFewShot

from database import db_conn
from dspy_modules.post_mortem import (
    ALLOWED_TAGS, PostMortemSignature, _build_trade_context, _configure_lm,
)


COMPILED_DIR = ROOT / "dspy_modules" / "compiled"
COMPILED_PATH = COMPILED_DIR / "post_mortem_v1.json"


def load_labeled_trades(min_samples: int) -> list[dspy.Example]:
    """Pull historical post-mortems as DSPy training Examples.

    Ground truth = the `postmortem_tag` / `postmortem_severity` / `postmortem_reason`
    columns previously written by trading.post_mortem.analyze_one. This is
    not human-labeled — it's the model's prior judgement. The compile step
    finds the few-shot demos that best generalize across those prior judgements.
    """
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, symbol, direction, entry_price, close_price, tp_levels, "
            "       realized_pnl, close_reason, archetype_at_open, setup_type, "
            "       ai_score_at_open, bear_phase_at_open, close_time, open_time, "
            "       leverage, "
            "       postmortem_tag, postmortem_severity, postmortem_reason, "
            "       postmortem_evidence "
            "FROM positions "
            "WHERE chain='auto_ai' AND postmortem_done=1 "
            "  AND postmortem_tag IS NOT NULL "
            "  AND postmortem_tag IN ({}) "
            "ORDER BY close_time DESC".format(",".join("?" * len(ALLOWED_TAGS))),
            ALLOWED_TAGS
        ).fetchall()

    if len(rows) < min_samples:
        return []

    examples = []
    for r in rows:
        trade = dict(r) if hasattr(r, "keys") else {}
        # Build the input/output Example
        ctx = _build_trade_context(trade)
        ev = trade.get("postmortem_evidence") or ""
        ex = dspy.Example(
            trade_context=ctx,
            tag=trade.get("postmortem_tag") or "unknown",
            severity=trade.get("postmortem_severity") or "low",
            reason=trade.get("postmortem_reason") or "",
            evidence=ev,
        ).with_inputs("trade_context")
        examples.append(ex)
    return examples


def post_mortem_metric(example, prediction, trace=None) -> float:
    """Score the prediction vs labeled ground truth.

    Weighted: tag match (0.6) + severity match (0.3) + reason non-empty (0.1).
    Range: 0.0 - 1.0.
    """
    score = 0.0
    if (prediction.tag or "").strip().lower() == (example.tag or "").strip().lower():
        score += 0.6
    if (prediction.severity or "").strip().lower() == (example.severity or "").strip().lower():
        score += 0.3
    if (prediction.reason or "").strip():
        score += 0.1
    return score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-samples", type=int, default=30,
                    help="Refuse to compile with fewer labeled trades than this")
    ap.add_argument("--max-bootstrapped-demos", type=int, default=4,
                    help="How many demos to find via bootstrapping")
    ap.add_argument("--max-labeled-demos", type=int, default=8,
                    help="How many labeled demos to consider per candidate")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print plan + sample count, but don't call the LM")
    args = ap.parse_args()

    print(f"Loading labeled trades from positions table (min_samples={args.min_samples}) ...")
    examples = load_labeled_trades(args.min_samples)
    print(f"  found {len(examples)} usable examples")

    if not examples:
        print(f"FAIL: need >= {args.min_samples} labeled post-mortems; aborting.")
        return 1

    if args.dry_run:
        print("\nDry-run: would compile with:")
        print(f"  max_bootstrapped_demos = {args.max_bootstrapped_demos}")
        print(f"  max_labeled_demos      = {args.max_labeled_demos}")
        # Rough cost estimate
        n_calls = len(examples) * args.max_bootstrapped_demos
        est_cost = n_calls * 0.003  # ~$0.003 per Haiku post-mortem call
        print(f"  estimated LM calls    = {n_calls}")
        print(f"  estimated cost (USD)  = ${est_cost:.2f}")
        return 0

    print("Configuring LM (Anthropic Haiku) ...")
    _configure_lm()

    print("Building BootstrapFewShot teleprompter ...")
    teleprompter = BootstrapFewShot(
        metric=post_mortem_metric,
        max_bootstrapped_demos=args.max_bootstrapped_demos,
        max_labeled_demos=args.max_labeled_demos,
    )

    print(f"Compiling against {len(examples)} examples — this burns API tokens ...")
    base = dspy.Predict(PostMortemSignature)
    compiled = teleprompter.compile(base, trainset=examples)

    COMPILED_DIR.mkdir(parents=True, exist_ok=True)
    compiled.save(str(COMPILED_PATH))
    print(f"✓ Saved compiled program to {COMPILED_PATH}")

    # Also write a one-line meta record so we know when this was last compiled
    meta_path = COMPILED_DIR / "post_mortem_v1.meta.json"
    meta = {
        "compiled_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_examples": len(examples),
        "max_bootstrapped_demos": args.max_bootstrapped_demos,
        "max_labeled_demos": args.max_labeled_demos,
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"✓ Meta written to {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
