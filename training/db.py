"""SQLite schema + data helpers for the training module.

Schema is idempotent — safe to run init_db() on every boot.
Catalog seed reads JSON lesson files at first run and inserts rows into
`lessons`. After that, lesson content lives on disk; only progress data
lives in the DB.
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
  id              INTEGER PRIMARY KEY,
  slug            TEXT    UNIQUE NOT NULL,
  title           TEXT    NOT NULL,
  tier            INTEGER NOT NULL,
  order_in_tier   INTEGER NOT NULL,
  topic_tags_json TEXT    DEFAULT '[]',
  estimated_minutes INTEGER DEFAULT 8,
  is_final        INTEGER DEFAULT 0,
  is_capstone     INTEGER DEFAULT 0,
  has_content     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lesson_progress (
  lesson_id            INTEGER PRIMARY KEY,
  status               TEXT    DEFAULT 'locked',  -- locked|unlocked|in_progress|passed
  attempts             INTEGER DEFAULT 0,
  best_score           INTEGER,
  passed_at            TEXT,
  total_seconds_spent  INTEGER DEFAULT 0,
  FOREIGN KEY (lesson_id) REFERENCES lessons(id)
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  lesson_id    INTEGER NOT NULL,
  question_id  TEXT    NOT NULL,
  topic_tag    TEXT,
  correct      INTEGER NOT NULL,
  user_answer  TEXT,
  ts           TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_lesson ON quiz_attempts(lesson_id);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_topic ON quiz_attempts(topic_tag);

CREATE TABLE IF NOT EXISTS widget_attempts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  lesson_id   INTEGER NOT NULL,
  widget_id   TEXT    NOT NULL,
  score       REAL,
  payload_json TEXT,
  ts          TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_queue (
  question_id          TEXT PRIMARY KEY,
  topic_tag            TEXT,
  due_at               TEXT,
  consecutive_correct  INTEGER DEFAULT 0
);
"""


@contextmanager
def conn_ctx(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path) -> None:
    """Apply schema. Idempotent."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with conn_ctx(db_path) as c:
        c.executescript(SCHEMA)


def seed_catalog_if_empty(db_path, content_dir: Path) -> None:
    """First-run seed: load catalog.json and populate the lessons table.

    Marks each row's has_content flag based on whether the lesson JSON
    file exists on disk. Idempotent — re-runs are no-ops once seeded.
    """
    with conn_ctx(db_path) as c:
        existing = c.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        if existing > 0:
            return
        catalog_path = content_dir / "catalog.json"
        if not catalog_path.exists():
            return
        catalog = json.loads(catalog_path.read_text())
        lessons_dir = content_dir / "lessons"
        for entry in catalog:
            slug = entry["slug"]
            has_content = (lessons_dir / f"{slug}.json").exists()
            c.execute("""
                INSERT INTO lessons (id, slug, title, tier, order_in_tier,
                                     topic_tags_json, estimated_minutes,
                                     is_final, is_capstone, has_content)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                entry["id"], slug, entry["title"], entry["tier"], entry["order_in_tier"],
                json.dumps(entry.get("topic_tags", [])),
                entry.get("estimated_minutes", 8),
                int(entry.get("is_final", False)),
                int(entry.get("is_capstone", False)),
                int(has_content),
            ))
            # Mark the first lesson of Tier 1 as unlocked by default
            if entry["tier"] == 1 and entry["order_in_tier"] == 1:
                c.execute("""
                    INSERT OR IGNORE INTO lesson_progress (lesson_id, status)
                    VALUES (?, 'unlocked')
                """, (entry["id"],))


def list_lessons_with_progress(db_path):
    """Return all lessons joined with progress (one row per lesson)."""
    with conn_ctx(db_path) as c:
        return [dict(r) for r in c.execute("""
            SELECT l.*, COALESCE(p.status, 'locked') AS status,
                   p.attempts, p.best_score, p.passed_at
            FROM lessons l
            LEFT JOIN lesson_progress p ON p.lesson_id = l.id
            ORDER BY l.tier, l.order_in_tier
        """)]


def get_lesson(db_path, slug: str) -> Optional[dict]:
    with conn_ctx(db_path) as c:
        row = c.execute("""
            SELECT l.*, COALESCE(p.status,'locked') AS status,
                   p.best_score, p.attempts, p.passed_at
            FROM lessons l LEFT JOIN lesson_progress p ON p.lesson_id = l.id
            WHERE l.slug = ?
        """, (slug,)).fetchone()
        return dict(row) if row else None


def mark_lesson_unlocked(db_path, lesson_id: int) -> None:
    with conn_ctx(db_path) as c:
        c.execute("""
            INSERT INTO lesson_progress (lesson_id, status)
            VALUES (?, 'unlocked')
            ON CONFLICT(lesson_id) DO UPDATE SET status='unlocked'
            WHERE lesson_progress.status='locked'
        """, (lesson_id,))


def record_quiz_attempts(db_path, lesson_id: int, answers: list) -> dict:
    """Insert per-question rows + roll up lesson_progress.

    answers: [{question_id, topic_tag, correct (bool), user_answer}, ...]
    Returns: {score, total, passed, attempts}
    """
    score = sum(1 for a in answers if a["correct"])
    total = len(answers)
    with conn_ctx(db_path) as c:
        for a in answers:
            c.execute("""
                INSERT INTO quiz_attempts (lesson_id, question_id, topic_tag, correct, user_answer)
                VALUES (?,?,?,?,?)
            """, (lesson_id, a["question_id"], a.get("topic_tag"), int(a["correct"]), a.get("user_answer")))
        # roll up
        row = c.execute("SELECT attempts, best_score, status FROM lesson_progress WHERE lesson_id=?",
                        (lesson_id,)).fetchone()
        prev_attempts = (row["attempts"] if row else 0) or 0
        prev_best = (row["best_score"] if row else None)
        new_best = max(prev_best or 0, score)
        passed = score >= 8  # pass threshold
        new_status = "passed" if passed else ("in_progress" if row else "in_progress")
        c.execute("""
            INSERT INTO lesson_progress (lesson_id, status, attempts, best_score, passed_at)
            VALUES (?, ?, ?, ?, CASE WHEN ? THEN datetime('now') ELSE NULL END)
            ON CONFLICT(lesson_id) DO UPDATE SET
                attempts   = attempts + 1,
                best_score = MAX(COALESCE(best_score,0), excluded.best_score),
                status     = CASE WHEN excluded.status='passed' THEN 'passed' ELSE lesson_progress.status END,
                passed_at  = CASE WHEN excluded.status='passed' AND lesson_progress.passed_at IS NULL
                                  THEN datetime('now') ELSE lesson_progress.passed_at END
        """, (lesson_id, new_status, prev_attempts + 1, new_best, passed))
        # If passed, unlock the next lesson in the same tier
        if passed:
            nxt = c.execute("""
                SELECT id FROM lessons
                WHERE tier = (SELECT tier FROM lessons WHERE id=?)
                  AND order_in_tier = (SELECT order_in_tier FROM lessons WHERE id=?) + 1
            """, (lesson_id, lesson_id)).fetchone()
            if nxt:
                c.execute("""
                    INSERT INTO lesson_progress (lesson_id, status)
                    VALUES (?, 'unlocked')
                    ON CONFLICT(lesson_id) DO UPDATE SET status='unlocked'
                    WHERE lesson_progress.status='locked'
                """, (nxt["id"],))
    return {"score": score, "total": total, "passed": passed, "best_score": new_best}
