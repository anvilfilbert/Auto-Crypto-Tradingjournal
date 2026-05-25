"""Trading Journal — Training Module (standalone-capable).

Imports nothing from the journal codebase. Can run mounted inside the journal
(via the blueprint) or standalone via `python -m training`.
"""
from .app import create_app

__version__ = "0.1.0"
__all__ = ["create_app"]
