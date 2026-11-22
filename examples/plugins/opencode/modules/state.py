"""What the plugin remembers between runs: theme, recent models and session titles, in <config dir>/opencode/state.json."""

import json
from pathlib import Path


def _path(config) -> Path:
    return config.dir / "opencode" / "state.json"


def load(config) -> dict:
    """The saved state, empty when missing or unreadable."""
    try:
        state = json.loads(_path(config).read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save(config, state: dict) -> None:
    """Write the state back."""
    path = _path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))
