"""Flask app factory — works in both standalone and mounted modes.

Standalone: `python -m training` creates a Flask app whose root is the
training blueprint. Mounted: the journal imports `training.blueprint.bp` and
registers it at /training, sharing the journal's Flask process.
"""
from pathlib import Path
from flask import Flask
from .blueprint import bp
from .db import init_db, seed_catalog_if_empty

PKG_DIR = Path(__file__).parent.resolve()


def create_app(db_path=None) -> Flask:
    """Build a standalone Flask app that serves training at the root."""
    app = Flask(
        __name__,
        template_folder=str(PKG_DIR / "templates"),
        static_folder=str(PKG_DIR / "static"),
        static_url_path="/static",
    )
    if db_path is None:
        db_path = PKG_DIR / "training.db"
    app.config["TRAINING_DB_PATH"] = str(db_path)

    # one-time init: schema + content catalog
    init_db(db_path)
    seed_catalog_if_empty(db_path, PKG_DIR / "content")

    # register at root (standalone) — journal mounts the same blueprint at /training
    app.register_blueprint(bp, url_prefix="")
    return app
