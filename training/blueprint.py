"""The single Flask Blueprint that powers both standalone and mounted modes.

Routes are defined here so the same code paths serve / (standalone) and
/training (mounted in journal). State is stored in the Flask app's
TRAINING_DB_PATH config.
"""
from pathlib import Path
from flask import Blueprint

PKG_DIR = Path(__file__).parent.resolve()

bp = Blueprint(
    "training",
    __name__,
    template_folder=str(PKG_DIR / "templates"),
    static_folder=str(PKG_DIR / "static"),
    static_url_path="/static-training",  # used when mounted to avoid colliding with host /static
)

# import the route handlers (which decorate bp)
from . import routes  # noqa: E402,F401
