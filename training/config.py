"""Runtime configuration loader for the training module.

Settings live in training/config.yaml. Read per-request (cheap — just a small
YAML file). Edit the YAML and the next request picks up the change without
a restart.
"""
from pathlib import Path
import yaml

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_DEFAULTS = {
    "unlock_mode": "enforce",  # safe default if config is missing
}


def load() -> dict:
    """Read training/config.yaml; fall back to defaults on any error."""
    if not _CONFIG_PATH.exists():
        return dict(_DEFAULTS)
    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
        merged = dict(_DEFAULTS)
        merged.update(data)
        return merged
    except Exception:
        return dict(_DEFAULTS)


def unlock_mode() -> str:
    """'enforce' (production) or 'open' (testing — all lessons unlocked)."""
    val = load().get("unlock_mode", "enforce")
    return "open" if str(val).lower() == "open" else "enforce"
