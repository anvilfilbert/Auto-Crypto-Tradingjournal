---
name: schema-migration-checklist
description: Use when adding a positions column, a new SQLite table, or modifying schema. Triggers on "add column X to positions", "create table Y", "schema migration N".
---

# Schema Migration Checklist

All schema changes go through `database.py::init_db()` via the `_apply(version, name, sql)` idempotent migration system. Migrations run at app startup and persist their version in `schema_version` table.

## Current state (as of 2026-05-24)

- Latest migration: **v58** (positions.ai_score_at_open)
- Pattern file: `database.py` lines 228-491
- All migrations must be IDEMPOTENT — they run on every startup; `_apply()` skips if already recorded.

## When NOT to add a column

Before adding a column to `positions`, check:
- Can it be computed from existing fields? → Don't store it; compute on read.
- Is it sparse (most rows NULL)? → Consider a separate table with FK to positions.id.
- Is it write-once and bounded? → Column is fine.

Adding to `positions` is a one-way door (rarely worth removing later). Be deliberate.

## Checklist

### 1. Choose the migration version
- Find the highest `_apply(N, ...)` call. New migration = N+1.
- Pattern: `grep -n "_apply([0-9]" database.py | tail -3` to confirm.

### 2. Choose the right column type
- INTEGER: counts, booleans (0/1), enums (cast keyword to int)
- REAL: prices, scores, percentages
- TEXT: keyword strings, JSON blobs, ISO timestamps
- DEFAULT clauses: explicit defaults reduce NULL ambiguity downstream

### 3. Add the migration call
- File: `database.py`, in the appropriate section (positions columns clustered around lines 228-490)
- Pattern:
  ```python
  _apply(N, "positions.<column>",
         "ALTER TABLE positions ADD COLUMN <column> <TYPE> DEFAULT <value>")
  ```
- Group with related migrations (e.g., skill provenance lives at v52-57; calibration at v58).

### 4. Update CLAUDE.md
- File: `CLAUDE.md`, "Database" section (currently lists migrations 47-58).
- Add a line: `**<Feature>:** \`positions.<column> <TYPE>\` (migration N, added YYYY-MM-DD). <One-sentence purpose>. Populated by <where>.`

### 5. Populate the column on new entries
- If populated at trade-open: edit `trading/executor.py::_insert_open_position` INSERT statement + add to parameter list.
- If populated at trade-close: edit `executor._mark_closed()` or wherever close happens.
- If populated post-hoc by analytics: no insert change needed.
- ALWAYS confirm the column value flows from the signal/orchestrator level into `_insert_open_position`. Check `trading/orchestrator.py` for the signal dict construction.

### 6. Backfill historical data (if applicable)
- For columns where historical population is needed: write a one-shot script in `scripts/backfill_<column>.py`.
- Must be idempotent (safe to re-run).
- Run on Pi via scp + python3.

### 7. Update analytics/reporting
- File: `analytics.py`, `ai_rulebook.py`, etc.
- If the column is meant to be in cohort aggregations: add a slice query.
- If meant for a UI page: surface via a route + JS render.

### 8. Tests
- File: `tests/test_database_migrations.py`
- Add a test confirming the column exists after `init_db()`.
- Pattern:
  ```python
  def test_migration_<N>_<column>(in_memory_db):
      cur = in_memory_db.execute("PRAGMA table_info(positions)")
      cols = {r[1]: r[2] for r in cur.fetchall()}
      assert "<column>" in cols
      assert cols["<column>"] == "<TYPE>"
  ```

### 9. **Deploy — bytecode cache nuke is REQUIRED**
- Per `feedback_pi_bytecode_cache.md` memory: deploys touching `database.py::init_db()` or `config.py::snapshot()` MUST nuke __pycache__ before restart.
- Sync via `/tmp/deploy_audit.exp`
- Restart via `/tmp/nuke_cache.exp` (NOT `restart_pi.exp` — the cached `init_db()` won't pick up the new migration without cache wipe).
- Verify: `journalctl -u trading-journal | grep "Applied migration <N>"` should appear once.

### 10. Post-deploy verification
- Query the schema directly:
  ```sql
  SELECT version, name FROM schema_version WHERE version = <N>;
  ```
- If empty: migration didn't run. Check journalctl for errors.
- If present: confirm a new INSERT populates the column with the right value.

### 11. Backup AFTER verification (per deploy routine)
- `bash ~/trading-journal/scripts/backup_db.sh`

## Red flags

- "I'll skip the cache nuke" → stop. The cached `init_db()` bytecode WILL skip your new migration silently. Lesson from 2026-05-22 incident.
- "I'll mutate the column type later" → stop. SQLite ALTER COLUMN is painful. Choose the type carefully now.
- "I'll let the migration run a non-idempotent operation" → stop. Every migration must be safe to re-run.
- "I'll backfill with a non-idempotent script" → stop. Backfill scripts often need re-runs; write them idempotent (`UPDATE ... WHERE <column> IS NULL`).
- "I'll add a column that 95% of rows will be NULL on" → reconsider. A side table with FK is better for sparse data.
