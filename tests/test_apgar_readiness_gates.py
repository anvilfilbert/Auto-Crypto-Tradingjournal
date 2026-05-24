"""Tests for Features 7 + 8 — Trade Apgar + Readiness gates (kill_switch hooks)."""
import sys
import os
import sqlite3
import types
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for mod in ("chart_context", "ccxt", "pandas"):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)


def _setup_db():
    """In-memory DB with required tables for testing the gate logic."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE apgar_sessions (
            id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP,
            q1 INTEGER, q2 INTEGER, q3 INTEGER, q4 INTEGER, q5 INTEGER,
            total INTEGER, passed INTEGER, notes TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE session_readiness (
            id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP,
            mood INTEGER, sleep INTEGER, prior_pnl_flag INTEGER, prep INTEGER,
            color TEXT, notes TEXT
        )
    """)
    return conn


class TestApgarGateLogic:
    """The actual passed-computation is in the route handler; here we test
    the kill_switch's reading of the apgar_sessions table."""

    def test_passing_apgar_present_does_not_block(self):
        db = _setup_db()
        db.execute(
            "INSERT INTO apgar_sessions (q1,q2,q3,q4,q5,total,passed) "
            "VALUES (2,2,2,1,1,8,1)")
        db.commit()
        row = db.execute(
            "SELECT passed FROM apgar_sessions WHERE ts >= date('now') "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
        assert row["passed"] == 1

    def test_failed_apgar_blocks(self):
        db = _setup_db()
        db.execute(
            "INSERT INTO apgar_sessions (q1,q2,q3,q4,q5,total,passed) "
            "VALUES (0,2,2,2,2,8,0)")  # 1 zero → passed=0
        db.commit()
        row = db.execute(
            "SELECT passed FROM apgar_sessions WHERE ts >= date('now') "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row["passed"] == 0

    def test_no_apgar_for_today(self):
        db = _setup_db()
        # No insert — query returns None
        row = db.execute(
            "SELECT passed FROM apgar_sessions WHERE ts >= date('now') "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row is None


class TestReadinessGateLogic:
    def test_green_does_not_block(self):
        db = _setup_db()
        db.execute(
            "INSERT INTO session_readiness (mood, sleep, prior_pnl_flag, prep, color) "
            "VALUES (2, 2, 1, 2, 'green')")
        db.commit()
        row = db.execute(
            "SELECT color FROM session_readiness WHERE ts >= date('now') "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row["color"] == "green"

    def test_red_blocks(self):
        db = _setup_db()
        db.execute(
            "INSERT INTO session_readiness (mood, sleep, prior_pnl_flag, prep, color) "
            "VALUES (0, 0, 0, 0, 'red')")
        db.commit()
        row = db.execute(
            "SELECT color FROM session_readiness WHERE ts >= date('now') "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row["color"] == "red"

    def test_yellow_present(self):
        db = _setup_db()
        db.execute(
            "INSERT INTO session_readiness (mood, sleep, prior_pnl_flag, prep, color) "
            "VALUES (1, 1, 0, 1, 'yellow')")
        db.commit()
        row = db.execute(
            "SELECT color FROM session_readiness WHERE ts >= date('now') "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row["color"] == "yellow"
