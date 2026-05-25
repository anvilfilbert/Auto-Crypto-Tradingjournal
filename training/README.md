# Trading Journal — Training Module

Interactive crypto trading curriculum. Standalone-capable: runs inside the
journal at `/training`, OR as its own Flask app.

## Run standalone

```bash
cd training
pip install -r requirements.txt
python -m training --port 5050
# open http://localhost:5050
```

## Run inside the journal

The journal mounts this package's blueprint at `/training`. Nothing extra to do
on this side — just keep `training/` next to the journal sources.

## What's in here

- `app.py` — Flask app factory (standalone)
- `blueprint.py` — Flask Blueprint (mountable)
- `routes.py` — view + API handlers (used by both modes)
- `db.py` — SQLite schema + queries (own DB, separate from journal's)
- `content/` — lesson JSONs, quiz YAMLs, diagrams, catalog
- `templates/` — Jinja2 views (own base.html, scoped CSS)
- `static/` — CSS, JS, generated chart PNGs

## Curriculum

51 graded units in 6 tiers. See `content/catalog.json` for the index.
Each lesson lives at `content/lessons/<slug>.json` and its quiz at
`content/quizzes/<id>.yaml`.

## Adding a lesson

1. Add an entry to `content/catalog.json`.
2. Create `content/lessons/NN-slug.json` with typed blocks (`text`,
   `callout`, `image`, `widget`).
3. Create `content/quizzes/NN.yaml` with 10 MCQ questions.
4. Restart — `seed_catalog_if_empty` only seeds the catalog on a fresh DB.
   For existing DBs, manually insert via SQL or recreate the DB.

## Why standalone

Zero imports from the journal. If something breaks in the journal, training
keeps running. If you want to ship the curriculum publicly later, you copy
the `training/` folder and run.
