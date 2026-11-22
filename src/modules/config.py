"""Config: create default files on first run, load and check them, expose one Config object."""

import json
import os
import sys
from dataclasses import dataclass, field
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
    # tokens the model accepts, None when not set
    context_window: int | None = None

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
    plugins: list[Path]
    # "plugins" section of settings.json: folder name -> that plugin's settings
    plugin_settings: dict[str, dict] = field(default_factory=dict)

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


# json names for the python types a config value can have
JSON_TYPES: dict[type, str] = {dict: "object", list: "array", str: "string", bool: "boolean",
                               int: "number", float: "number", type(None): "null"}


def _check(value, kind: type, where: str):
    """Return value if it has the expected type, else exit saying what is wrong."""
    if value is None:
        sys.exit(f"{where} is missing")
    if not isinstance(value, kind):
        sys.exit(f"{where} must be {JSON_TYPES[kind]}, not {JSON_TYPES.get(type(value), type(value).__name__)}")
    return value


def _read(path: Path) -> dict:
    """Read a json file, writing its default first if missing."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_FILES[path.name], indent=2) + "\n")
        path.chmod(0o600)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"{path}: {e}")
    return _check(data, dict, str(path))


def _efforts(model: dict, where: str) -> dict[str, str]:
    """reasoning_effort per thinking level; levels map to themselves, thinkingLevelMap overrides, null disables."""
    if not model.get("reasoning"):
        return {}
    overrides = _check(model.get("thinkingLevelMap", {}), dict, f"{where}.thinkingLevelMap")
    return {level: effort for level in THINKING_LEVELS[1:] if (effort := overrides.get(level, level))}


def load() -> Config:
    """Load settings.json and auth.json into a Config; exits on bad config."""
    path = _config_dir()
    file = path / "settings.json"
    settings = _read(file)
    auth = _read(path / "auth.json")

    # one Model per provider model, in file order
    models = []
    for provider, p in _check(settings.get("providers", {}), dict, f"{file}: providers").items():
        where = f"{file}: providers.{provider}"
        p = _check(p, dict, where)
        base_url = _check(p.get("baseUrl"), str, f"{where}.baseUrl")
        key = _check(auth.get(provider, ""), str, f"{path / 'auth.json'}: {provider}")
        for i, m in enumerate(_check(p.get("models", []), list, f"{where}.models")):
            m = _check(m, dict, f"{where}.models[{i}]")
            model_id = _check(m.get("id"), str, f"{where}.models[{i}].id")
            window = m.get("contextWindow")
            if window is not None:
                _check(window, int, f"{where}.models[{i}].contextWindow")
            models.append(Model(provider, model_id, base_url, key, _efforts(m, f"{where}.models[{i}]"), window))
    if not models:
        sys.exit(f"no models: add providers to {file}")

    # defaultModel, else the first one
    name = settings.get("defaultModel")
    model = _find(models, _check(name, str, f"{file}: defaultModel")) if name else models[0]
    if not model:
        sys.exit(f"unknown defaultModel {name}")

    thinking = settings.get("defaultThinkingLevel", "off")
    if thinking not in THINKING_LEVELS:
        sys.exit(f"unknown defaultThinkingLevel {thinking}, use one of {', '.join(THINKING_LEVELS)}")

    prompt_file = None
    if override := settings.get("overrideSystemPrompt"):
        prompt_file = path / Path(_check(override, str, f"{file}: overrideSystemPrompt")).expanduser()
        if not prompt_file.is_file():
            sys.exit(f"overrideSystemPrompt: no such file {prompt_file}")

    plugin_settings = _check(settings.get("plugins", {}), dict, f"{file}: plugins")
    for name, value in plugin_settings.items():
        _check(value, dict, f"{file}: plugins.{name}")
    # every folder in plugins/ with a main.py not turned off, sorted so load order is stable
    plugins = [p for p in sorted((path / "plugins").glob("*/main.py"))
               if plugin_settings.get(p.parent.name, {}).get("enabled", True)]

    return Config(path, models, model, thinking, prompt_file, plugins, plugin_settings)

