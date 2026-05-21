"""
self_review_scheduler.py — Background thread that fires AI self-review on
alpha-leak trades once per day.

Cadence: every SELF_REVIEW_INTERVAL seconds (default 24h). Each run picks
at most SELF_REVIEW_LIMIT pending candidates (high-score losers + low-score
winners not yet reviewed) and processes them. Skips entirely when:
  - journal_paused.is_paused() is True
  - There are fewer than SELF_REVIEW_MIN_PENDING candidates (avoid empty runs)

Provider routing: each review goes through ai_client.send() which means it
automatically uses the Anthropic→Groq→Cerebras→OpenRouter→Gemini cascade we
built earlier. So even when Anthropic credit is depleted, reviews continue
to flow via the free backup providers — no special handling needed here.

Cost when Anthropic is funded: ~$0.02 per review × default 10 reviews/day
= ~$0.20/day = ~$6/month. Cost on cascade fallback: $0.
"""
import os
import threading
import time
from datetime import datetime, timezone

from database import db_conn
import journal_paused

SELF_REVIEW_INTERVAL    = int(os.environ.get("SELF_REVIEW_INTERVAL", str(24 * 3600)))
SELF_REVIEW_LIMIT       = int(os.environ.get("SELF_REVIEW_LIMIT", "10"))
SELF_REVIEW_MIN_PENDING = int(os.environ.get("SELF_REVIEW_MIN_PENDING", "3"))
SELF_REVIEW_FIRST_DELAY = int(os.environ.get("SELF_REVIEW_FIRST_DELAY", "300"))  # 5 min after boot


def _count_pending() -> int:
    """How many alpha-leak trades are unreviewed and waiting? See ai_self_review."""
    try:
        with db_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) AS n
                FROM analyzed_calls a
                LEFT JOIN ai_self_review r ON r.call_id = a.id
                WHERE r.id IS NULL
                  AND a.outcome IN ('won','lost')
                  AND a.setup_score IS NOT NULL
                  AND (
                        (a.setup_score >= 7 AND a.outcome = 'lost')
                     OR (a.setup_score <= 5 AND a.outcome = 'won')
                  )
            """).fetchone()
        return int(row["n"] or 0) if row else 0
    except Exception:
        return 0


def _run_once() -> None:
    """Single cycle. Imports inside so a fresh start picks up code changes."""
    pending = _count_pending()
    if pending < SELF_REVIEW_MIN_PENDING:
        print(f"[SelfReview] {pending} pending — below threshold "
              f"({SELF_REVIEW_MIN_PENDING}); skipping cycle", flush=True)
        return
    print(f"[SelfReview] {pending} pending — processing up to {SELF_REVIEW_LIMIT}",
          flush=True)
    import ai_self_review
    with db_conn() as conn:
        result = ai_self_review.run_pending_reviews(conn, limit=SELF_REVIEW_LIMIT)
    ok    = sum(1 for r in result.get("results", []) if r.get("status") == "reviewed")
    errs  = sum(1 for r in result.get("results", []) if r.get("status") == "error")
    print(f"[SelfReview] cycle done — {ok} reviewed, {errs} errors", flush=True)


def start() -> None:
    """Spawn the background loop. Idempotent — no-op if already started."""
    if os.environ.get("SELF_REVIEW_SCHEDULER", "").lower() == "off":
        print("[SelfReview] Disabled via SELF_REVIEW_SCHEDULER=off", flush=True)
        return

    def _loop():
        time.sleep(SELF_REVIEW_FIRST_DELAY)
        while True:
            try:
                if journal_paused.is_paused():
                    print("[SelfReview] paused — skipping cycle", flush=True)
                else:
                    _run_once()
            except Exception as e:
                print(f"[SelfReview] Unexpected error: {e}", flush=True)
            time.sleep(SELF_REVIEW_INTERVAL)

    t = threading.Thread(target=_loop, name="self-review-scheduler", daemon=True)
    t.start()
    print(f"[SelfReview] Background scheduler started "
          f"(every {SELF_REVIEW_INTERVAL // 3600}h, first run in "
          f"{SELF_REVIEW_FIRST_DELAY // 60} min)", flush=True)
