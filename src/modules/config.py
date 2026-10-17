"""Config: create default files on first run, load and check them, expose one Config object."""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

VERSION = "replaced_during_build"
THINKING_LEVELS: list[str] = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
DEFAULT_FILES: dict[str, dict] = {
    "settings.json": {
        "version": VERSION,
        "defaultThinkingLevel": "off",
        "providers": {
            "llama.cpp": {
                "baseUrl": "http://localhost:8080/v1",
                "models": [
                    {"id": "qwen3.6-35b"},
                    {
                        "id": "qwen3.8-27b",
                        "reasoning": True,
                        "thinkingLevelMap": {"minimal": None, "xhigh": None, "max": None},
                    },
                ],
            }
        },
    },
    "auth.json": {"llama.cpp": "123"},
}


@dataclass(frozen=True)
class Model:
    provider: str
    id: str
    base_url: str
    api_key: str
    efforts: dict[str, str]

    @property
    def name(self) -> str:
        """provider/id, as accepted by /model."""
        return f"{self.provider}/{self.id}"

    def supports(self, thinking: str) -> bool:
        """Whether this model accepts the thinking level."""
        return thinking == "off" or thinking in self.efforts


@dataclass(frozen=True)
class Config:
    dir: Path
    models: list[Model]
    default_model: Model
    default_thinking: str
    prompt_file: Path | None

    @property
    def history(self) -> Path:
        """Input history file."""
        return self.dir / "history"

    def find_model(self, name: str) -> Model | None:
        """Match "provider/id" exactly, or a bare id against the first provider having it."""
        return _find(self.models, name)


def _find(models: list[Model], name: str) -> Model | None:
    return next((m for m in models if name in (m.name, m.id)), None)


def _config_dir() -> Path:
    """$PY_AGENT_DIR, else $XDG_CONFIG_HOME/py-agent, else ~/.config/py-agent."""
    if override := os.environ.get("PY_AGENT_DIR"):
        return Path(override).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "py-agent"


def _read(path: Path) -> dict:
    """Read a json file, writing its default first if missing."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_FILES[path.name], indent=2) + "\n")
        path.chmod(0o600)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"{path}: {e}")


def _efforts(model: dict) -> dict[str, str]:
    """reasoning_effort per thinking level; levels map to themselves, thinkingLevelMap overrides, null disables."""
    if not model.get("reasoning"):
        return {}
    overrides = model.get("thinkingLevelMap", {})
    return {level: effort for level in THINKING_LEVELS[1:] if (effort := overrides.get(level, level))}


def load() -> Config:
    """Load settings.json and auth.json into a Config; exits on bad config."""
    path = _config_dir()
    settings = _read(path / "settings.json")
    auth = _read(path / "auth.json")

    # one Model per provider model, in file order
    try:
        models = [
            Model(provider, m["id"], p["baseUrl"], auth.get(provider, ""), _efforts(m))
            for provider, p in settings.get("providers", {}).items()
            for m in p.get("models", [])
        ]
    except KeyError as e:
        sys.exit(f"{path / 'settings.json'}: missing {e}")
    if not models:
        sys.exit(f"no models: add providers to {path / 'settings.json'}")

    # defaultModel, else the first one
    name = settings.get("defaultModel")
    model = _find(models, name) if name else models[0]
    if not model:
        sys.exit(f"unknown defaultModel {name}")

    thinking = settings.get("defaultThinkingLevel", "off")
    if thinking not in THINKING_LEVELS:
        sys.exit(f"unknown defaultThinkingLevel {thinking}, use one of {', '.join(THINKING_LEVELS)}")

    prompt_file = None
    if override := settings.get("overrideSystemPrompt"):
        prompt_file = path / Path(override).expanduser()
        if not prompt_file.is_file():
            sys.exit(f"overrideSystemPrompt: no such file {prompt_file}")

    return Config(path, models, model, thinking, prompt_file)
