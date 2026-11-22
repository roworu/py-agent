"""Shared fixtures: src on sys.path, an isolated config dir, fakes, and module globals restored after each test."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fakes import FakeLLM, FakeServer  # noqa: E402
from modules import commands, llm, prompt, tools  # noqa: E402
from modules.config import Config, Model  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep each test away from the real config dir and from globals other tests or plugins changed."""
    monkeypatch.setenv("PY_AGENT_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # plugins mutate these tables in place, so give each test its own copy
    monkeypatch.setattr(tools, "TOOLS", dict(tools.TOOLS))
    monkeypatch.setattr(tools, "SPECS", list(tools.SPECS))
    monkeypatch.setattr(commands, "EXTRA", {})
    monkeypatch.setattr(commands, "NAMES", list(commands.NAMES))
    monkeypatch.setattr(commands, "DESCRIPTIONS", dict(commands.DESCRIPTIONS))
    # setting a value to itself makes monkeypatch put it back if a plugin replaces it
    monkeypatch.setattr(llm, "complete", llm.complete)
    monkeypatch.setattr(prompt, "build", prompt.build)
    monkeypatch.setattr(llm, "RETRY_DELAYS", [0, 0, 0, None])


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """The PY_AGENT_DIR every test runs with."""
    return tmp_path / "config"


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """An empty folder for the agent to work in."""
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def git_workdir(workdir: Path) -> Path:
    """workdir inside a git repo, so snapshots and /undo are active."""
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    return workdir


@pytest.fixture
def model() -> Model:
    """A non-reasoning model."""
    return Model("p", "m", "http://127.0.0.1:9/v1", "", {})


@pytest.fixture
def reasoning_model() -> Model:
    """A reasoning model supporting low and high."""
    return Model("q", "r", "http://127.0.0.1:9/v1", "key", {"low": "low", "high": "max"})


@pytest.fixture
def config(config_dir: Path, model: Model, reasoning_model: Model) -> Config:
    """A Config with both models, the first being the default; its dir exists, as after config.load."""
    config_dir.mkdir()
    return Config(config_dir, [model, reasoning_model], model, "off", None, [])


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """Scripted llm.complete installed for the agent."""
    fake = FakeLLM()
    monkeypatch.setattr(llm, "complete", fake)
    return fake


@pytest.fixture
def server() -> FakeServer:
    """A running fake OpenAI-compatible server."""
    with FakeServer() as s:
        yield s


@pytest.fixture
def write_settings(config_dir: Path):
    """Write settings.json (and optionally auth.json) into the config dir."""
    def write(settings: dict | str, auth: dict | None = None) -> None:
        config_dir.mkdir(parents=True, exist_ok=True)
        text = settings if isinstance(settings, str) else json.dumps(settings)
        (config_dir / "settings.json").write_text(text)
        (config_dir / "auth.json").write_text(json.dumps(auth or {}))
    return write
