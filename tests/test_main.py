"""main: argument parsing, plugin-first startup and -c resume, with the ui's run stubbed out."""

import json
import sys
from pathlib import Path

import pytest

import main
from modules import tui


@pytest.fixture
def start(monkeypatch: pytest.MonkeyPatch, workdir: Path, write_settings):
    """Run main() in workdir with the given args, without opening the ui; returns the Chat it ran."""
    write_settings({"providers": {"p": {"baseUrl": "u", "models": [{"id": "m"}]}}})
    monkeypatch.chdir(workdir)
    runs: list[tui.Chat] = []
    monkeypatch.setattr(tui.Chat, "run", lambda self: runs.append(self))

    def run(*args: str) -> tui.Chat:
        monkeypatch.setattr(sys, "argv", ["py-agent", *args])
        main.main()
        return runs[-1]
    return run


def write_session(sessions: Path, name: str, content: str) -> None:
    """A one-message session file."""
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{name}.jsonl").write_text(json.dumps({"role": "user", "content": content}) + "\n")


def test_starts_fresh(start, workdir: Path) -> None:
    """Without -c the app runs with an empty conversation in the cwd."""
    chat = start()
    assert chat.agent.cwd == workdir
    assert len(chat.agent.messages) == 1


def test_continue_resumes_latest(start, config_dir: Path, workdir: Path) -> None:
    """-c continues the newest session of this folder."""
    sessions = config_dir / "sessions" / str(workdir).replace("/", "-")
    write_session(sessions, "2024-01-01_00-00-00", "old")
    write_session(sessions, "2024-02-01_00-00-00", "new")
    agent = start("--continue").agent
    assert agent.messages[1]["content"] == "new"
    assert agent.session.stem == "2024-02-01_00-00-00"


def test_continue_without_sessions(start) -> None:
    """-c with no sessions just starts fresh."""
    assert len(start("-c").agent.messages) == 1


def test_plugins_replace_classes_before_creation(start, config_dir: Path) -> None:
    """Plugins run before the agent and ui are created, so replaced classes are used."""
    folder = config_dir / "plugins" / "swap"
    folder.mkdir(parents=True)
    (folder / "main.py").write_text(
        "def setup(api):\n"
        "    assert api.agent is None and api.chat is None\n"
        "    class A(api.Agent):\n        tagged = True\n"
        "    class C(api.Chat):\n        tagged = True\n"
        "    api.Agent, api.Chat = A, C\n")
    chat = start()
    assert chat.tagged and chat.agent.tagged


def test_unknown_argument(start, capsys: pytest.CaptureFixture) -> None:
    """Unknown flags exit with usage."""
    with pytest.raises(SystemExit) as e:
        start("--nope")
    assert e.value.code == 2
    assert "usage: py-agent" in capsys.readouterr().err
