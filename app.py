import logging
import os
import signal
import sys
import atexit

from flask import Flask, render_template, make_response

# Configure root logger to write to stderr so _log.info reaches journalctl.
# Without this, only print() calls show up in systemd logs; logger.info is
# silently dropped. force=True overrides any default handler installed by
# imports that ran before this point.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
    force=True,
)

_log = logging.getLogger(__name__)

from database import init_db, get_conn
from importer import import_folder

import bitget_sync
import blofin_sync
import scanner_scheduler
import monitor_scheduler

from routes.journal   import bp as journal_bp
from routes.analytics import bp as analytics_bp
from routes.market    import bp as market_bp
from routes.calls     import bp as calls_bp
from routes.limits    import bp as limits_bp
from routes.live      import bp as live_bp
from routes.sync      import bp as sync_bp
from routes.scanner   import bp as scanner_bp
from routes.hindsight import bp as hindsight_bp
from routes.settings  import bp as settings_bp
from routes.backtest  import bp as backtest_bp
from routes.risk      import bp as risk_bp
from routes.futures_ai import bp as futures_ai_bp

# ── app setup ──────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB upload limit

# NaN / Infinity are valid Python but invalid JSON — Flask's default encoder
# silently emits `NaN` which causes `response.json()` to throw in browsers.
# This encoder converts them to null.
import json as _json, math as _math
class _SafeEncoder(_json.JSONEncoder):
    def iterencode(self, o, _one_shot=False):
        return super().iterencode(self._sanitize(o), _one_shot)
    def _sanitize(self, obj):
        if isinstance(obj, float):
            if _math.isnan(obj) or _math.isinf(obj):
                return None
            return obj
        if isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._sanitize(v) for v in obj]
        return obj
app.json_encoder = _SafeEncoder

app.register_blueprint(journal_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(market_bp)
app.register_blueprint(calls_bp)
app.register_blueprint(limits_bp)
app.register_blueprint(live_bp)
app.register_blueprint(sync_bp)
app.register_blueprint(scanner_bp)
app.register_blueprint(hindsight_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(backtest_bp)
app.register_blueprint(risk_bp)
app.register_blueprint(futures_ai_bp)

# Training module — standalone-capable package mounted at /training.
# The single line of coupling between journal and training. Removing this
# block leaves the journal fully functional without training.
try:
    from training.blueprint import bp as training_bp
    from training.db import init_db as _t_init_db, seed_catalog_if_empty as _t_seed
    from pathlib import Path as _PathT
    _TRAINING_DIR = _PathT(__file__).parent / "training"
    _TRAINING_DB = _TRAINING_DIR / "training.db"
    app.config["TRAINING_DB_PATH"] = str(_TRAINING_DB)
    _t_init_db(_TRAINING_DB)
    _t_seed(_TRAINING_DB, _TRAINING_DIR / "content")
    app.register_blueprint(training_bp, url_prefix="/training")
except Exception as _training_err:
    import logging as _log
    _log.warning("training module failed to mount: %s", _training_err)


@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


# ── startup ────────────────────────────────────────────────────────────────────
# init_db() runs at import time so WSGI servers (gunicorn) initialise the schema.
init_db()

def _checkpoint_on_exit(*_):
    """Flush WAL to the main DB file before process exit (SIGTERM from systemd)."""
    try:
        from database import get_conn
        conn = get_conn()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass

signal.signal(signal.SIGTERM, _checkpoint_on_exit)
atexit.register(_checkpoint_on_exit)

if __name__ == "__main__":

    # Auto-import CSV data if DB is empty
    conn = get_conn()
    if conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0:
        csv_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".csv")]
        if csv_files:
            _log.info("[Startup] DB empty, auto-importing %d CSV files from data/", len(csv_files))
            import_folder(DATA_DIR, conn)
    conn.close()

    bitget_sync.start_background_sync()
    blofin_sync.start_background_sync()
    scanner_scheduler.start()
    monitor_scheduler.start()
    import self_review_scheduler
    self_review_scheduler.start()
    # Event-driven mini-scan trigger — kicks off an out-of-cycle scan when
    # BTC/ETH posts a sharp 15m move. Cheap insurance for the 30-min
    # scheduled cadence. Toggleable via SCANNER_EVENT_TRIGGER=off.
    try:
        import scanner_event_trigger
        scanner_event_trigger.start()
    except Exception as e:
        print(f"[boot] scanner_event_trigger failed to start: {e}", flush=True)

    # Futures-AI orchestrator hook — fires after EVERY scan completion
    # (forced API scans AND periodic scheduler scans). Registered here
    # unconditionally so it works even when scanner_scheduler.start()
    # short-circuits because Telegram isn't configured.
    try:
        import ai_scanner
        from trading import orchestrator as _fa_orch
        def _on_scan_completed_fa(setups):
            try:
                scan_state = ai_scanner.get_state()
                summary = _fa_orch.on_scan_completed(scan_state)
                if summary and not summary.get("skipped"):
                    _log.info("[Futures-AI] orchestrator: %s", summary)
            except Exception as e:
                _log.exception("[Futures-AI] orchestrator hook failed: %s", e)
        ai_scanner.register_completion_hook(_on_scan_completed_fa)
        _log.info("[Futures-AI] orchestrator hook registered on ai_scanner")
    except Exception as e:
        _log.warning("[Futures-AI] could not register orchestrator hook: %s", e)

    port = int(os.environ.get("PORT", 8082))
    _log.info("[App] Trading Journal running on http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
