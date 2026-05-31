"""
database.py — SQLite schema definition and connection helpers.

The database has four tables:
  positions       — one row per closed trade (core data, from position_history CSV)
  orders          — individual order fills linked to a position
  wallet_snapshots — wallet balance history (for equity curve chart)
  import_log      — tracks which CSV files have been imported and when
"""

import logging
import os
import sqlite3
from contextlib import contextmanager

_log = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "trading_journal.db"))


def get_conn():
    """Return a sqlite3 connection with row_factory set to dict-like Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent reads
    conn.execute("PRAGMA wal_autocheckpoint=100")  # checkpoint every 100 pages (~400KB), keeps WAL small
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_conn():
    """Context manager that opens a connection and guarantees close on exit."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create all tables if they do not exist yet. Safe to call on every startup."""
    conn = get_conn()
    cur = conn.cursor()

    # ── schema_version ─────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL,
            applied_at TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

    def _applied(ver: int) -> bool:
        return conn.execute(
            "SELECT 1 FROM schema_version WHERE version=?", (ver,)
        ).fetchone() is not None

    def _apply(ver: int, name: str, sql: str):
        if _applied(ver):
            return
        try:
            conn.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                _log.error("Migration %d (%s) failed: %s", ver, name, e, exc_info=True)
                raise
            _log.debug("Migration %d: column already exists (%s)", ver, name)
        conn.execute("INSERT INTO schema_version (version, name) VALUES (?,?)", (ver, name))
        conn.commit()
        _log.info("Applied migration %d: %s", ver, name)

    # ── positions ──────────────────────────────────────────────────────────────
    # Primary trade table. One row = one closed futures position.
    # Fields map directly to Bitget's "position history" export columns,
    # plus three user-editable fields (notes, tags) and two calculated ones
    # (duration_minutes, leverage_guess).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT    NOT NULL,        -- e.g. 'BOMEUSDT'
            base_asset       TEXT    NOT NULL,        -- e.g. 'BOME'
            direction        TEXT    NOT NULL,        -- 'Long' or 'Short'
            margin_mode      TEXT,                    -- 'Cross' or 'Isolated'
            open_time        TEXT    NOT NULL,        -- ISO datetime string
            close_time       TEXT    NOT NULL,        -- ISO datetime string
            duration_minutes INTEGER,                 -- calculated: close - open in minutes
            entry_price      REAL,
            close_price      REAL,
            size_contracts   TEXT,                    -- raw: '400000BOME'
            size_usdt        REAL,                    -- closed value in USDT
            position_pnl     REAL,                    -- gross PnL before fees
            realized_pnl     REAL,                    -- net PnL after fees
            opening_fee      REAL,
            closing_fee      REAL,
            total_fees       REAL,
            notes            TEXT    DEFAULT '',      -- user-editable freetext
            tags             TEXT    DEFAULT '',      -- comma-separated tags
            is_manual        INTEGER DEFAULT 0,       -- 1 if entered by hand (not imported)
            created_at       TEXT    DEFAULT (datetime('now')),
            updated_at       TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ── orders ─────────────────────────────────────────────────────────────────
    # Individual order records from Bitget's "order history" export.
    # Linked to positions by symbol + time proximity (position_id set during import).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id         TEXT    UNIQUE,
            date             TEXT,
            direction        TEXT,
            symbol           TEXT,
            order_source     TEXT,
            transaction_type TEXT,
            price            REAL,
            avg_price        REAL,
            order_amount     REAL,
            executed         REAL,
            trading_volume   REAL,
            realized_pnl     REAL,
            net_profits      REAL,
            status           TEXT,
            position_id      INTEGER REFERENCES positions(id)
        )
    """)

    # ── wallet_snapshots ───────────────────────────────────────────────────────
    # Every row from Bitget's "transactions" export. Wallet balance at each event
    # lets us draw an equity curve and calculate max drawdown.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wallet_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref      TEXT,
            date           TEXT,
            symbol         TEXT,
            futures        TEXT,
            margin_mode    TEXT,
            type           TEXT,
            amount         REAL,
            fee            REAL,
            wallet_balance REAL
        )
    """)

    # ── analyzed_calls ─────────────────────────────────────────────────────────
    # Saved trade call analyses. One row per call the user analyzed and saved.
    # status: 'saved' → 'matched' (confirmed link to live position) → 'closed'
    cur.execute("""
        CREATE TABLE IF NOT EXISTS analyzed_calls (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT NOT NULL,
            direction        TEXT NOT NULL,
            call_text        TEXT,
            entry_price      REAL,
            dca_price        REAL,
            sl_price         REAL,
            tp1_price        REAL,
            tp2_price        REAL,
            avg_entry        REAL,
            total_notional   REAL,
            margin_needed    REAL,
            risk_pct         REAL,
            risk_amount      REAL,
            leverage         INTEGER,
            has_dca          INTEGER DEFAULT 0,
            has_candle_close_sl INTEGER DEFAULT 0,
            setup_score      INTEGER,
            setup_label      TEXT,
            rr_ratio         TEXT,
            trade_type       TEXT,
            sl_warning       TEXT,
            entry_timing     TEXT,
            analysis_json    TEXT,
            status           TEXT DEFAULT 'saved',
            matched_at       TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── analyzed_calls column migrations ──────────────────────────────────────────
    _apply(1,  "analyzed_calls.exchange",      "ALTER TABLE analyzed_calls ADD COLUMN exchange TEXT DEFAULT 'bitget'")
    _apply(2,  "analyzed_calls.cot_reasoning", "ALTER TABLE analyzed_calls ADD COLUMN cot_reasoning TEXT DEFAULT NULL")
    _apply(17, "analyzed_calls.analyst",       "ALTER TABLE analyzed_calls ADD COLUMN analyst TEXT DEFAULT ''")
    _apply(18, "analyzed_calls.notes",         "ALTER TABLE analyzed_calls ADD COLUMN notes TEXT DEFAULT ''")
    _apply(19, "analyzed_calls.outcome",       "ALTER TABLE analyzed_calls ADD COLUMN outcome TEXT DEFAULT NULL")
    _apply(20, "analyzed_calls.outcome_pnl",   "ALTER TABLE analyzed_calls ADD COLUMN outcome_pnl REAL DEFAULT NULL")
    _apply(21, "analyzed_calls.hit_tp1",       "ALTER TABLE analyzed_calls ADD COLUMN hit_tp1 INTEGER DEFAULT 0")
    _apply(22, "analyzed_calls.hit_tp2",       "ALTER TABLE analyzed_calls ADD COLUMN hit_tp2 INTEGER DEFAULT 0")
    _apply(23, "analyzed_calls.hit_sl",        "ALTER TABLE analyzed_calls ADD COLUMN hit_sl INTEGER DEFAULT 0")
    _apply(24, "analyzed_calls.outcome_at",    "ALTER TABLE analyzed_calls ADD COLUMN outcome_at TEXT DEFAULT NULL")
    _apply(26, "analyzed_calls.gemini_score",   "ALTER TABLE analyzed_calls ADD COLUMN gemini_score INTEGER DEFAULT NULL")
    _apply(27, "analyzed_calls.consensus_score","ALTER TABLE analyzed_calls ADD COLUMN consensus_score REAL DEFAULT NULL")
    _apply(28, "analyzed_calls.consensus_flag", "ALTER TABLE analyzed_calls ADD COLUMN consensus_flag TEXT DEFAULT NULL")
    _apply(29, "analyzed_calls.risk_verdict_json", "ALTER TABLE analyzed_calls ADD COLUMN risk_verdict_json TEXT DEFAULT NULL")
    _apply(30, "analyzed_calls.monitor_alert",     "ALTER TABLE analyzed_calls ADD COLUMN monitor_alert INTEGER DEFAULT 0")
    _apply(31, "analyzed_calls.chart_png_b64",     "ALTER TABLE analyzed_calls ADD COLUMN chart_png_b64 TEXT DEFAULT NULL")

    # ── pending_limits ─────────────────────────────────────────────────────────
    # Limit orders the user has placed on exchange but not yet triggered.
    # "Shadow trades" — tracked for risk and correlation analysis before they fill.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_limits (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id         INTEGER REFERENCES analyzed_calls(id) ON DELETE SET NULL,
            symbol          TEXT NOT NULL,
            direction       TEXT NOT NULL,
            limit_price     REAL NOT NULL,
            size_usdt       REAL,
            leverage        INTEGER DEFAULT 10,
            sl_price        REAL,
            tp1_price       REAL,
            tp2_price       REAL,
            analyst         TEXT DEFAULT '',
            status          TEXT DEFAULT 'waiting',
            triggered_at    TEXT,
            analysis_json   TEXT,
            notes           TEXT DEFAULT '',
            bitget_order_id TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)
    _apply(3, "pending_limits.bitget_order_id", "ALTER TABLE pending_limits ADD COLUMN bitget_order_id TEXT")

    # ── positions column migrations ────────────────────────────────────────────
    _apply(4,  "positions.analyst",                "ALTER TABLE positions ADD COLUMN analyst TEXT DEFAULT ''")
    _apply(5,  "positions.execution_grade",        "ALTER TABLE positions ADD COLUMN execution_grade TEXT DEFAULT NULL")
    _apply(6,  "positions.execution_grade_reason", "ALTER TABLE positions ADD COLUMN execution_grade_reason TEXT DEFAULT NULL")
    _apply(7,  "positions.setup_type",             "ALTER TABLE positions ADD COLUMN setup_type TEXT DEFAULT ''")
    _apply(8,  "positions.call_id",                "ALTER TABLE positions ADD COLUMN call_id INTEGER DEFAULT NULL")
    _apply(9,  "positions.external_id",            "ALTER TABLE positions ADD COLUMN external_id TEXT DEFAULT NULL")
    _apply(10, "positions.exchange",               "ALTER TABLE positions ADD COLUMN exchange TEXT DEFAULT 'bitget'")
    _apply(11, "positions.leverage",               "ALTER TABLE positions ADD COLUMN leverage INTEGER DEFAULT NULL")
    _apply(12, "positions.market_regime",          "ALTER TABLE positions ADD COLUMN market_regime TEXT DEFAULT NULL")
    _apply(13, "positions.mfe_price",              "ALTER TABLE positions ADD COLUMN mfe_price REAL DEFAULT NULL")
    _apply(14, "positions.mae_price",              "ALTER TABLE positions ADD COLUMN mae_price REAL DEFAULT NULL")
    _apply(15, "positions.mfe_pct",                "ALTER TABLE positions ADD COLUMN mfe_pct REAL DEFAULT NULL")
    _apply(16, "positions.mae_pct",                "ALTER TABLE positions ADD COLUMN mae_pct REAL DEFAULT NULL")

    # ── trader_rulebook ────────────────────────────────────────────────────────
    # Personalised rules synthesised by Claude from trade history.
    # Cleared and regenerated on each rulebook update.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trader_rulebook (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type    TEXT NOT NULL,   -- 'warning', 'strength', 'habit', 'calibration'
            title        TEXT NOT NULL,
            rule         TEXT NOT NULL,
            confidence   TEXT DEFAULT 'medium',
            data_points  INTEGER DEFAULT 0,
            generated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── trade_hindsight ────────────────────────────────────────────────────────
    # Retroactive AI analysis: what would Claude have recommended before each trade?
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_hindsight (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id      INTEGER UNIQUE REFERENCES positions(id),
            analyzed_at      TEXT DEFAULT (datetime('now')),

            -- Recommendation (blind — Claude didn't know the actual outcome)
            setup_score      INTEGER,
            setup_label      TEXT,
            would_enter      INTEGER,  -- 1=ENTER, 0=SKIP
            rec_direction    TEXT,     -- Long/Short Claude recommended
            direction_match  INTEGER,  -- 1 if rec matches actual direction
            rec_entry_low    REAL,
            rec_entry_high   REAL,
            rec_sl           REAL,
            rec_tp1          REAL,
            rec_tp2          REAL,
            rec_rr           TEXT,
            key_conditions   TEXT,     -- JSON array
            risks            TEXT,     -- JSON array
            skip_reason      TEXT,

            -- Comparison
            actual_pnl       REAL,
            hypothetical_pnl REAL,     -- P&L if recommendation had been followed
            verdict          TEXT,     -- TP|TN|FP|FN|NEUTRAL (signal accuracy category)

            -- Raw
            analysis_json    TEXT,
            input_tokens     INTEGER,
            output_tokens    INTEGER
        )
    """)

    # ── trader_rulebook_history ────────────────────────────────────────────────
    # Keeps the last 3 rulebook versions so we can compare rule evolution.
    _apply(25, "trader_rulebook_history", """
        CREATE TABLE IF NOT EXISTS trader_rulebook_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            version     INTEGER NOT NULL,
            rules_json  TEXT    NOT NULL,
            trade_count INTEGER,
            saved_at    TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ── optimizer_runs ────────────────────────────────────────────────────────
    _apply(32, "optimizer_runs", """
        CREATE TABLE IF NOT EXISTS optimizer_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL DEFAULT (datetime('now')),
            symbol      TEXT    NOT NULL,
            timeframe   TEXT    NOT NULL,
            days        INTEGER NOT NULL,
            n_trials    INTEGER NOT NULL,
            best_sharpe REAL,
            best_params TEXT,
            duration_sec REAL
        )
    """)

    # ── entry_watcher_recs ────────────────────────────────────────────────────
    _apply(33, "entry_watcher_recs", """
        CREATE TABLE IF NOT EXISTS entry_watcher_recs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol            TEXT NOT NULL,
            direction         TEXT NOT NULL,
            alert_type        TEXT NOT NULL,
            entry_low         REAL,
            entry_high        REAL,
            sl_price          REAL,
            tp1_price         REAL,
            tp2_price         REAL,
            score             REAL,
            archetype         TEXT,
            rationale         TEXT,
            key_conditions    TEXT,
            status            TEXT DEFAULT 'active',
            invalidation_reason TEXT,
            replaced_by       TEXT,
            created_at        TEXT DEFAULT (datetime('now')),
            expires_at        TEXT,
            invalidated_at    TEXT,
            analysis_json     TEXT
        )
    """)

    _apply(34, "positions.setup_score", "ALTER TABLE positions ADD COLUMN setup_score INTEGER DEFAULT NULL")

    _apply(35, "positions.funding_pnl",
           "ALTER TABLE positions ADD COLUMN funding_pnl REAL DEFAULT NULL")
    _apply(36, "positions.signal_price",
           "ALTER TABLE positions ADD COLUMN signal_price REAL DEFAULT NULL")
    _apply(37, "positions.execution_lag_minutes",
           "ALTER TABLE positions ADD COLUMN execution_lag_minutes INTEGER DEFAULT NULL")
    _apply(38, "analyzed_calls.regime_label",
           "ALTER TABLE analyzed_calls ADD COLUMN regime_label TEXT DEFAULT NULL")
    _apply(39, "analyzed_calls.ml_win_prob",
           "ALTER TABLE analyzed_calls ADD COLUMN ml_win_prob REAL DEFAULT NULL")

    # ── volume_baseline ──────────────────────────────────────────────────────
    # Per-(symbol, timeframe) rolling volume samples. Used by volume_baseline.py
    # to measure surges against each coin's own median pace rather than a flat
    # 20-bar trailing window. Ported from Kaizen Tools (2026-05-21 audit).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS volume_baseline (
            symbol      TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            samples     TEXT NOT NULL,   -- JSON-encoded float[]
            last_ts     REAL NOT NULL,
            PRIMARY KEY (symbol, timeframe)
        )
    """)
    _apply(40, "volume_baseline", "SELECT 1")  # mark migration applied

    # ── ai_self_review ──────────────────────────────────────────────────────
    # Stores AI retrospective on closed trades where prediction disagreed with
    # outcome. Used to surface recurring "missed signal" suggestions back into
    # the prompt as an AI wishlist. See ai_self_review.py.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_self_review (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id       INTEGER NOT NULL UNIQUE,
            missed_signal TEXT,
            threshold     TEXT,
            timeframe     TEXT,
            weight        TEXT,
            why           TEXT,
            raw_response  TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    _apply(41, "ai_self_review", "SELECT 1")

    # ── tps_json + ladder R:R columns ────────────────────────────────────────
    # Multi-TP ladder (Task B, 2026-05-21). Existing tp1_price/tp2_price stays
    # as the AI's STRATEGIC baseline. tps_json holds the full exchange-set
    # ladder (up to ~7 TPs) as JSON: [{"price": float, "size_pct": float,
    # "hit": bool, "hit_at": str|None}, ...]. first_tp_rr / last_tp_rr are
    # derived but cached so the UI doesn't recompute on every render.
    _apply(42, "positions.tps_json",
           "ALTER TABLE positions ADD COLUMN tps_json TEXT DEFAULT NULL")
    _apply(43, "positions.first_tp_rr",
           "ALTER TABLE positions ADD COLUMN first_tp_rr REAL DEFAULT NULL")
    _apply(44, "positions.last_tp_rr",
           "ALTER TABLE positions ADD COLUMN last_tp_rr REAL DEFAULT NULL")
    _apply(45, "pending_limits.tps_json",
           "ALTER TABLE pending_limits ADD COLUMN tps_json TEXT DEFAULT NULL")

    # Futures-AI auto-trader audit log
    cur.execute("""
        CREATE TABLE IF NOT EXISTS futures_ai_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT    DEFAULT (datetime('now')),
            event        TEXT    NOT NULL,
            symbol       TEXT,
            direction    TEXT,
            score        INTEGER,
            payload_json TEXT
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_futures_ai_log_ts "
        "ON futures_ai_log(ts DESC)"
    )
    _apply(46, "futures_ai_log", "SELECT 1")

    # Chain separation: every position belongs to exactly one trading
    # 'chain' — manual (operator-executed) or auto_ai (Futures-AI bot).
    # All existing rows default to 'manual' since that's all that exists
    # pre-Futures-AI. AutoAI inserts use 'auto_ai'. This is the
    # foundation for chain-scoped rulebook, hindsight, and analytics.
    _apply(47, "positions.chain",
           "ALTER TABLE positions ADD COLUMN chain TEXT DEFAULT 'manual'")

    # 'is_hedge' — flags catastrophe-hedge positions opened by the
    # auto-trader's hedge_manager. Hedges are defensive insurance against
    # rapid downside moves and MUST NOT count toward MAX_CONCURRENT_POSITIONS,
    # consecutive-loss breakers, or the win-streak progression. Their P&L
    # still affects equity (real Bitget money) but they're excluded from
    # all "trade quality" metrics.
    _apply(48, "positions.is_hedge",
           "ALTER TABLE positions ADD COLUMN is_hedge INTEGER DEFAULT 0")

    # 'close_reason' — short categorical tag explaining why a position
    # closed. Used by the Futures-AI page "Recent closed" table and by
    # hindsight categorisation. Values:
    #   SL                — stop loss hit
    #   TP1 / TP2         — take profit hit
    #   BE                — break-even trigger fired
    #   MAE_cut           — max adverse excursion auto-close
    #   trail_stop        — trailing stop hit
    #   manual_close      — operator force-closed via UI
    #   hedge_unwind: <r> — catastrophe hedge unwound (reason in suffix)
    #   pending_reconcile — Bitget history not yet available, retry next cycle
    _apply(49, "positions.close_reason",
           "ALTER TABLE positions ADD COLUMN close_reason TEXT DEFAULT NULL")

    # 'tp_levels' — JSON array of {idx, price, pct, hit, hit_at} per TP level.
    # Replaces the binary tp1_price/tp2_price model when Opus (consensus model)
    # emits multi-TP overrides. Phase 1 (2026-05-23): DB stores the full ladder,
    # charts render all levels, but the auto-trader still places ONLY TP1 as a
    # plan order. Operator handles partial closes manually until Phase 2 wires
    # plan-order-per-level execution. tp1_price / tp2_price remain populated
    # (= levels[0].price and levels[1].price respectively) for backward compat
    # with existing chart code, scanner output, and analyzed_calls consumers.
    _apply(50, "positions.tp_levels",
           "ALTER TABLE positions ADD COLUMN tp_levels TEXT DEFAULT NULL")
    _apply(51, "analyzed_calls.tp_levels",
           "ALTER TABLE analyzed_calls ADD COLUMN tp_levels TEXT DEFAULT NULL")

    # ── Skill provenance (migrations 52-57) ──────────────────────────────────
    # Six columns that tag a position with the "trading skill" provenance that
    # produced it. Lets AI Advisor + analytics aggregate by *skill* (not just
    # by symbol/hour) so we can answer "is Opus consensus actually winning?",
    # "are bear-phase-aligned trades better?", "do PO3 modifiers correlate
    # with outcome?". Populated at trade open by trading/executor.py and
    # backfilled from futures_ai_log shadow logs by
    # scripts/backfill_position_skills.py.
    _apply(52, "positions.consensus_model_used",
           "ALTER TABLE positions ADD COLUMN consensus_model_used TEXT DEFAULT NULL")
    _apply(53, "positions.bear_phase_at_open",
           "ALTER TABLE positions ADD COLUMN bear_phase_at_open TEXT DEFAULT NULL")
    _apply(54, "positions.archetype_at_open",
           "ALTER TABLE positions ADD COLUMN archetype_at_open TEXT DEFAULT NULL")
    _apply(55, "positions.po3_total",
           "ALTER TABLE positions ADD COLUMN po3_total REAL DEFAULT NULL")
    _apply(56, "positions.opus_had_overrides",
           "ALTER TABLE positions ADD COLUMN opus_had_overrides INTEGER DEFAULT 0")
    _apply(57, "positions.tp_levels_count",
           "ALTER TABLE positions ADD COLUMN tp_levels_count INTEGER DEFAULT 0")
    _apply(58, "positions.ai_score_at_open",
           "ALTER TABLE positions ADD COLUMN ai_score_at_open REAL DEFAULT NULL")
    _apply(59, "positions.be_tier_reached",
           "ALTER TABLE positions ADD COLUMN be_tier_reached INTEGER DEFAULT 0")
    _apply(60, "positions.trade_grade",
           "ALTER TABLE positions ADD COLUMN trade_grade REAL DEFAULT NULL")

    # Feature 13 — Supply/Demand zones with order-absorption decay.
    # Each zone has a price band [bottom, top], a touch counter, and a
    # valid flag (auto-invalidated at 3+ touches). Per-symbol per-timeframe.
    _apply(61, "sd_zones",
           """CREATE TABLE IF NOT EXISTS sd_zones (
                  id          INTEGER PRIMARY KEY AUTOINCREMENT,
                  symbol      TEXT NOT NULL,
                  timeframe   TEXT NOT NULL,
                  zone_type   TEXT NOT NULL,   -- 'demand' (bullish) or 'supply' (bearish)
                  top         REAL NOT NULL,
                  bottom      REAL NOT NULL,
                  touches     INTEGER DEFAULT 0,
                  valid       INTEGER DEFAULT 1,
                  created_at  TEXT DEFAULT (datetime('now')),
                  last_seen   TEXT
              )""")
    # Feature 7 — Trade Apgar pre-trade scorecard (Elder). 5-question checklist;
    # each answer 0/1/2. Total ≥7 AND no zeros = passed (allow new trades today).
    _apply(62, "apgar_sessions",
           """CREATE TABLE IF NOT EXISTS apgar_sessions (
                  id        INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts        TEXT DEFAULT (datetime('now')),
                  q1        INTEGER,
                  q2        INTEGER,
                  q3        INTEGER,
                  q4        INTEGER,
                  q5        INTEGER,
                  total     INTEGER,
                  passed    INTEGER,
                  notes     TEXT
              )""")
    # Feature 8 — Pre-session operator readiness (Elder + Douglas).
    # Mood / sleep / prior_pnl_flag / prep — yields red/yellow/green.
    _apply(63, "session_readiness",
           """CREATE TABLE IF NOT EXISTS session_readiness (
                  id              INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts              TEXT DEFAULT (datetime('now')),
                  mood            INTEGER,
                  sleep           INTEGER,
                  prior_pnl_flag  INTEGER,
                  prep            INTEGER,
                  color           TEXT,
                  notes           TEXT
              )""")

    # Migration 64 — chain isolation for trader_rulebook (2026-05-25).
    # Existing rules become chain='manual' (they were generated from the
    # main operator chain). auto_ai rules will be generated separately.
    _apply(64, "trader_rulebook.chain",
           "ALTER TABLE trader_rulebook ADD COLUMN chain TEXT DEFAULT 'manual'")

    # Migration 65 — chain isolation for trade_hindsight (2026-05-25).
    # Same schema gap as migration 64. Existing rows backfilled to 'manual'.
    _apply(65, "trade_hindsight.chain",
           "ALTER TABLE trade_hindsight ADD COLUMN chain TEXT DEFAULT 'manual'")

    # Migration 66 — sizing tier on positions (2026-05-26).
    # Records WHICH sizing rule produced this position so calibration can
    # bucket outcomes by tier. Tiers:
    #   "full"          — Opus ≥ 6 → normal 2% risk per trade
    #   "half"          — Opus = 5 → half-size, ~1% risk per trade
    #   (future) "half_dca_initial" / "half_dca_add" — when DCA mechanic ships (Phase 2)
    _apply(66, "positions.sizing_tier",
           "ALTER TABLE positions ADD COLUMN sizing_tier TEXT DEFAULT 'full'")

    # ── SL persistence (added 2026-05-31) ─────────────────────────────────────
    # Snapshot of the INITIAL stop-loss price at trade open. Defines 1R risk
    # for downstream realized-R computation in the stats page. Written by
    # executor._insert_open_position and paper.open_paper_trade. Never moved
    # — even if SL gets trailed or bumped to BE during the trade, this column
    # holds the original SL so R-multiples stay anchored to the entry-time
    # plan. NULL for historical positions opened before this migration.
    _apply(67, "positions.sl_price",
           "ALTER TABLE positions ADD COLUMN sl_price REAL DEFAULT NULL")

    # ── R-3 funding cost + liquidation distance (2026-05-31, Master plan R-3)
    # funding_paid_usd: total funding paid (positive) or received (negative)
    #   across the hold period. Pulled from Bitget position history
    #   (totalFunding field) at reconcile time. Default NULL for historical
    #   positions; populated by funding_backfill job going forward.
    # liq_distance_atr: at trade open, distance from entry to liquidation
    #   measured in 4H ATR units. >5 = healthy, <2 = structurally fragile
    #   (SL is likely inside liquidation zone, exchange forces close before
    #   our SL fires). Written by executor._insert_open_position.
    _apply(68, "positions.funding_paid_usd",
           "ALTER TABLE positions ADD COLUMN funding_paid_usd REAL DEFAULT NULL")
    _apply(69, "positions.liq_distance_atr",
           "ALTER TABLE positions ADD COLUMN liq_distance_atr REAL DEFAULT NULL")

    # ── L-0 (Master plan): self-learning foundation ──────────────────────────
    # learned_params: versioned key-value store. The scanner / orchestrator /
    # executor read tunables from here via trading.learned.get(). Pinned rows
    # are operator overrides — the learner respects them.
    _apply(70, "learned_params",
           "CREATE TABLE IF NOT EXISTS learned_params ("
           "key TEXT PRIMARY KEY, "
           "value TEXT NOT NULL, "
           "value_type TEXT NOT NULL DEFAULT 'json', "
           "default_value TEXT, "
           "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
           "sample_size INTEGER DEFAULT 0, "
           "ci_low REAL, ci_high REAL, p_value REAL, "
           "pinned INTEGER NOT NULL DEFAULT 0, "
           "pinned_reason TEXT, "
           "last_revert_at TEXT, "
           "revert_count INTEGER NOT NULL DEFAULT 0)")

    # learner_log: every learner decision (apply / skip / revert) logged here.
    # Read by daily Telegram report + Stats UI "Recent auto-adjustments" panel.
    _apply(71, "learner_log",
           "CREATE TABLE IF NOT EXISTS learner_log ("
           "id INTEGER PRIMARY KEY AUTOINCREMENT, "
           "ts TEXT NOT NULL DEFAULT (datetime('now')), "
           "learner_name TEXT NOT NULL, "
           "param_key TEXT NOT NULL, "
           "old_value TEXT, new_value TEXT, "
           "action TEXT NOT NULL, "
           "gate_reason TEXT, "
           "sample_size INTEGER, "
           "ci_low REAL, ci_high REAL, p_value REAL, "
           "payload_json TEXT)")

    # ── settings ──────────────────────────────────────────────────────────────
    # Key-value store: last sync time, account equity, rulebook timestamps.
    # Also created by bitget_sync._ensure_settings_table() but must exist here
    # so ai_rulebook works even if a sync has never run.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # ── import_log ─────────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS import_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            filename       TEXT,
            file_type      TEXT,     -- 'positions', 'orders', 'order_details', 'transactions'
            rows_imported  INTEGER,
            imported_at    TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── token_usage ────────────────────────────────────────────────────────────
    # One row per Claude API call. Provides cost visibility per module.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             TEXT    DEFAULT (datetime('now')),
            module         TEXT    NOT NULL,   -- 'call_analyzer', 'scanner', 'rulebook', 'hindsight', 'advisor'
            model          TEXT    NOT NULL,
            input_tokens   INTEGER NOT NULL,
            output_tokens  INTEGER NOT NULL,
            cached_tokens  INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    _log.info("DB initialized at %s", DB_PATH)
    conn.close()


if __name__ == "__main__":
    init_db()
