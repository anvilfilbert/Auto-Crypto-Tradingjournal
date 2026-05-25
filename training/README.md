# Trading Journal — Training Module

Interactive crypto-trading curriculum. 51 graded units across 6 tiers
(Foundations → Chart Reading → Indicators → Advanced → Macro → Execution
→ Capstone). ~520 quiz questions. 24 visual diagrams.

Runs in two modes from the same code:
- **Mounted** inside the trading-journal app at `/training`
- **Standalone** as its own Flask server on any port

## Install on a fresh machine (verified)

> ⚠ **Important**: `python -m training` MUST be run from the PARENT directory
> of `training/`, NOT from inside it. If you `cd training && python -m training`,
> Python errors with `No module named training` — it can't see the package from
> within itself.

### Option A — git clone (recommended if internet works)

```bash
# 1. Prerequisites
python3 --version           # 3.10+ required
pip3 --version

# 2. Clone the repo
git clone https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal.git
cd Auto-Crypto-Tradingjournal      # ← parent of training/, NOT cd'd into training/

# 3. Install deps
pip3 install -r training/requirements.txt

# 4. Run the standalone server
python3 -m training --port 5050    # cwd is the parent of training/

# 5. Open in any browser on the LAN
# http://<the-Pi's-IP>:5050/
```

### Option B — tarball transfer (no internet on target Pi)

```bash
# On your machine: create the tarball (excludes noise + any local DB)
tar -czf training.tgz \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
  --exclude='training/training.db' \
  training/

# Copy to the new Pi
scp training.tgz pi@<new-pi-ip>:~/

# On the new Pi
ssh pi@<new-pi-ip>
tar -xzf ~/training.tgz             # creates ./training/ in cwd
pip3 install -r training/requirements.txt
python3 -m training --port 5050     # cwd is the parent of training/
```

That's it. On first request, `training.db` is created automatically next to
the cwd and seeded with the 51-lesson catalog. ~2 MB tarball, ~5 seconds to
unpack + boot.

## Command-line options

```
python3 -m training [--port 5050] [--host 0.0.0.0] [--db ./training.db] [--debug]
```

| Flag | Default | Notes |
|---|---|---|
| `--port` | 5050 | Pick any free port |
| `--host` | 0.0.0.0 | Bind all interfaces (LAN-accessible) |
| `--db` | `./training.db` | Where SQLite file lives |
| `--debug` | off | Flask debug + autoreload |

## Verify it's working

```bash
# Health check — should report 51 lessons
curl -s http://localhost:5050/api/status

# Should print: {"data":{"lessons_passed":0,"lessons_total":51,"lessons_with_content":51,"unlock_mode":"open"},"ok":true}
```

## Configuration

Edit `training/config.yaml`:

```yaml
# 'enforce' = lessons unlock only after passing previous quiz (production)
# 'open'    = all lessons clickable regardless of progress (testing)
unlock_mode: open
```

Setting reloads per-request — no restart needed.

## Running it as a system service (optional, on Linux)

Create `/etc/systemd/system/training.service`:

```ini
[Unit]
Description=Trading Journal — Training Module
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/path/to/training
ExecStart=/usr/bin/python3 -m training --port 5050 --db /path/to/training/training.db
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now training
sudo systemctl status training
```

## File layout

```
training/
├── __init__.py / __main__.py / app.py / blueprint.py / routes.py / db.py
├── config.py / config.yaml          ← runtime settings (unlock_mode etc.)
├── content/                          ← all curriculum (markdown-like JSON + YAML quizzes)
│   ├── catalog.json                  ← 51-lesson index
│   ├── lessons/                      ← one JSON per lesson
│   └── quizzes/                      ← one YAML per quiz
├── static/
│   ├── css/training.css              ← scoped under .training-* (no clash)
│   ├── js/training.js                ← quiz submit + reset logic
│   └── charts/                       ← 24 pre-generated PNG diagrams
├── templates/                        ← Jinja2 — own base.html, scoped to itself
├── scripts/
│   ├── generate_chart_diagrams.py    ← matplotlib chart generator
│   └── insert_diagrams_into_lessons.py
├── requirements.txt                  ← flask>=3.0, pyyaml>=6.0  (only 2 deps)
└── training.db                       ← created on first run (gitignored)
```

## Resetting all progress (e.g., friend wants to start fresh)

Click "**Reset all progress**" on the path view (top right of progress bar) —
double-confirmation dialog. Wipes lesson_progress + quiz_attempts, keeps
lessons catalog and content.

Or via API:
```bash
curl -X POST http://localhost:5050/api/reset-progress \
  -H 'Content-Type: application/json' -d '{"confirm":"RESET"}'
```

## What's NOT included

- **No multi-user support** — single user per database. If two people share
  the install, they share progress. For separate progress: run two instances
  with different `--db` paths.
- **No auth** — anyone with network access can browse and reset. Add a
  reverse proxy with basic auth if you want a barrier.
- **No interactive widgets yet** — the 12 planned interactive components
  (position-size calculator, draw-the-S/R, etc.) are stub placeholders. Text
  + diagrams only for now.
- **Charts are pre-rendered PNGs** — no Python plotting deps needed at
  runtime. Only `pip install matplotlib` if you want to regenerate diagrams
  via `python3 -m training.scripts.generate_chart_diagrams`.

## Coupling rules (for anyone editing this)

- `training/` imports NOTHING from the parent journal codebase
- Own SQLite file, separate from journal's `trading_journal.db`
- CSS namespaced `.training-*` — no global pollution
- JS in own module — no shared globals

Delete the `training/` directory → journal works without it. Remove the
single try/except block in journal's `app.py` → journal works without
mounting. Truly independent.
