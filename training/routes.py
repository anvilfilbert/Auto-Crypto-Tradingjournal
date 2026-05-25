"""Route handlers for the training blueprint.

All routes are registered on `bp` (imported here from blueprint.py).
"""
import json
from pathlib import Path
import yaml
from flask import current_app, render_template, request, jsonify, abort

from .blueprint import bp
from .db import (
    list_lessons_with_progress, get_lesson, mark_lesson_unlocked,
    record_quiz_attempts,
)


def _db_path() -> Path:
    return Path(current_app.config["TRAINING_DB_PATH"])


def _content_dir() -> Path:
    return Path(__file__).parent / "content"


def _load_lesson_content(slug: str) -> dict:
    path = _content_dir() / "lessons" / f"{slug}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _load_quiz(quiz_id: str) -> dict:
    path = _content_dir() / "quizzes" / f"{quiz_id}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


# ── Views ───────────────────────────────────────────────────────────────────

@bp.route("/")
def path_view():
    """Top-level path view — tier tree with lock/unlock state."""
    lessons = list_lessons_with_progress(_db_path())
    # Group by tier
    tiers = {}
    for l in lessons:
        tiers.setdefault(l["tier"], []).append(l)
    tier_meta = {
        1: {"name": "Foundations", "color": "#26d96b", "icon": "🟢"},
        2: {"name": "Chart Reading", "color": "#4fc3f7", "icon": "🔵"},
        3: {"name": "Indicators", "color": "#b388ff", "icon": "🟣"},
        4: {"name": "Advanced", "color": "#ef5350", "icon": "🔴"},
        5: {"name": "Macro & Context", "color": "#ffb300", "icon": "🟡"},
        6: {"name": "Execution & Journaling", "color": "#cfd8dc", "icon": "⚪"},
    }
    # Stats
    total = sum(1 for l in lessons if not l["is_final"] and not l["is_capstone"])
    passed = sum(1 for l in lessons if l["status"] == "passed")
    return render_template("path.html", tiers=tiers, tier_meta=tier_meta,
                           total=total, passed=passed)


@bp.route("/lesson/<slug>")
def lesson_view(slug):
    meta = get_lesson(_db_path(), slug)
    if not meta:
        abort(404)
    content = _load_lesson_content(slug)
    if not content:
        return render_template("lesson_pending.html", lesson=meta)
    # mark in_progress on first view
    if meta["status"] == "unlocked":
        # tracked by virtue of attempts; status moves to in_progress on first quiz attempt
        pass
    return render_template("lesson.html", lesson=meta, content=content)


@bp.route("/quiz/<slug>")
def quiz_view(slug):
    meta = get_lesson(_db_path(), slug)
    if not meta:
        abort(404)
    quiz_id = slug.split("-")[0]  # e.g. "01-spot..." → "01"
    quiz = _load_quiz(quiz_id)
    if not quiz:
        return render_template("quiz_pending.html", lesson=meta)
    # Strip the answers from what gets sent to the page —
    # otherwise users could just inspect DOM
    safe_questions = []
    for q in quiz.get("questions", []):
        sq = {k: v for k, v in q.items() if k not in ("correct", "explanation")}
        safe_questions.append(sq)
    return render_template("quiz.html", lesson=meta, quiz=quiz,
                           safe_questions=safe_questions)


# ── API ─────────────────────────────────────────────────────────────────────

@bp.route("/api/quiz/<slug>/submit", methods=["POST"])
def quiz_submit(slug):
    meta = get_lesson(_db_path(), slug)
    if not meta:
        return jsonify({"ok": False, "error": "lesson not found"}), 404
    quiz_id = slug.split("-")[0]
    quiz = _load_quiz(quiz_id)
    if not quiz:
        return jsonify({"ok": False, "error": "quiz not found"}), 404
    body = request.get_json() or {}
    user_answers = body.get("answers") or {}
    # Grade
    graded = []
    detailed = []
    for q in quiz["questions"]:
        qid = q["id"]
        user_idx = user_answers.get(qid)
        correct_idx = q["correct"]
        is_correct = (user_idx == correct_idx)
        graded.append({
            "question_id": qid,
            "topic_tag": q.get("topic_tag"),
            "correct": is_correct,
            "user_answer": str(user_idx),
        })
        detailed.append({
            "question_id": qid,
            "correct": is_correct,
            "correct_idx": correct_idx,
            "user_idx": user_idx,
            "explanation": q.get("explanation", ""),
        })
    result = record_quiz_attempts(_db_path(), meta["id"], graded)
    result["detailed"] = detailed
    return jsonify({"ok": True, "data": result})


@bp.route("/api/status")
def api_status():
    """Simple health check + counts."""
    lessons = list_lessons_with_progress(_db_path())
    return jsonify({"ok": True, "data": {
        "lessons_total": len(lessons),
        "lessons_with_content": sum(1 for l in lessons if l["has_content"]),
        "lessons_passed": sum(1 for l in lessons if l["status"] == "passed"),
    }})
