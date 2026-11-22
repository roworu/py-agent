"""commands: every slash command's reply, ui action and effect on the agent."""

import json
from pathlib import Path

import pytest

from fakes import FakeLLM
from modules import commands
from modules.agent import Agent
from modules.config import Config


class Silent:
    """Events that ignore everything."""

    def text(self, text: str) -> None: ...
    def reasoning(self, text: str) -> None: ...
    def tool_call(self, id: str, name: str, arguments: str) -> None: ...
    def tool_result(self, id: str, result: str) -> None: ...
    def retry(self, delay: float, error: str) -> None: ...
    def ask(self, question: str, options: list[str], body=None) -> int | None: ...
    def compacted(self) -> None: ...


@pytest.fixture
def agent(config: Config, workdir: Path, fake_llm: FakeLLM) -> Agent:
    """An agent with a scripted model."""
    return Agent(config, workdir)


@pytest.mark.parametrize("line", ["/help", "/nope", "/", "/help me"])
def test_help_and_unknown(agent: Agent, line: str) -> None:
    """/help and unknown commands show the help."""
    assert commands.run(agent, line) == (commands.HELP, None)


def test_help_lists_builtin_commands() -> None:
    """Every built-in name except /help itself appears in the help text."""
    for name in set(commands.NAMES) - {"/help"}:
        assert name in commands.HELP


@pytest.mark.parametrize("line", ["/quit", "/exit"])
def test_quit(agent: Agent, line: str) -> None:
    """Both quit commands ask the ui to quit."""
    assert commands.run(agent, line) == ("", "quit")


def test_clear(agent: Agent) -> None:
    """/clear resets the agent."""
    agent.run("hi", Silent())
    assert commands.run(agent, "/clear") == ("new conversation", "clear")
    assert len(agent.messages) == 1


def test_model_list_marks_current(agent: Agent) -> None:
    """/model lists models with * on the current one."""
    assert commands.run(agent, "/model") == ("* p/m\n  q/r", None)


def test_model_switch(agent: Agent) -> None:
    """/model switches by provider/id or bare id."""
    assert commands.run(agent, "/model q/r") == ("model: q/r · thinking off", None)
    assert agent.model.name == "q/r"
    assert commands.run(agent, "/model  m ") == ("model: p/m · thinking off", None)


def test_model_switch_drops_unsupported_thinking(agent: Agent) -> None:
    """Moving to a model without the current level turns thinking off."""
    commands.run(agent, "/model q/r")
    commands.run(agent, "/think high")
    assert commands.run(agent, "/model p/m") == ("model: p/m · thinking off", None)


def test_model_unknown(agent: Agent) -> None:
    """An unknown model leaves the current one."""
    assert commands.run(agent, "/model zz") == ("unknown model zz", None)
    assert agent.model.name == "p/m"


def test_think(agent: Agent) -> None:
    """/think shows the levels; a supported level is set, others are refused."""
    assert commands.run(agent, "/think") == ("thinking: off (available: off)", None)
    assert commands.run(agent, "/think high") == ("thinking: off (available: off)", None)
    commands.run(agent, "/model q/r")
    assert commands.run(agent, "/think") == ("thinking: off (available: off, low, high)", None)
    assert commands.run(agent, "/think high") == ("thinking: high", None)
    assert agent.thinking == "high"
    assert commands.run(agent, "/think off") == ("thinking: off", None)


def test_resume_empty(agent: Agent) -> None:
    """No sessions yet."""
    assert commands.run(agent, "/resume") == ("no sessions in this folder", None)


def test_resume_list_and_pick(agent: Agent, fake_llm: FakeLLM) -> None:
    """/resume lists newest first with the opening message; /resume n continues one."""
    agent.sessions.mkdir(parents=True)
    for i, stamp in enumerate(["2024-01-01_00-00-00", "2024-01-02_00-00-00"]):
        (agent.sessions / f"{stamp}.jsonl").write_text(
            json.dumps({"role": "user", "content": f"msg {i}"}) + "\n" + json.dumps({"role": "assistant", "content": "a"}) + "\n")
    reply, action = commands.run(agent, "/resume")
    assert action is None
    assert reply == (" 1. 2024-01-02_00-00-00  'msg 1'\n"
                     " 2. 2024-01-01_00-00-00  'msg 0'\n"
                     "/resume <n> to continue one")
    assert commands.run(agent, "/resume 2") == ("resumed 2024-01-01_00-00-00", "replay")
    assert [m["content"] for m in agent.messages[1:]] == ["msg 0", "a"]
    assert agent.session.stem == "2024-01-01_00-00-00"


@pytest.mark.parametrize("arg", ["0", "3", "x", "-1"])
def test_resume_bad_number_lists(agent: Agent, arg: str) -> None:
    """Out of range or non-numbers just list the sessions."""
    agent.sessions.mkdir(parents=True)
    (agent.sessions / "s.jsonl").write_text(json.dumps({"role": "user", "content": "hi"}) + "\n")
    reply, action = commands.run(agent, f"/resume {arg}")
    assert action is None and reply.startswith(" 1. s  'hi'")


def test_resume_lists_at_most_20(agent: Agent) -> None:
    """Only the 20 newest sessions are listed."""
    agent.sessions.mkdir(parents=True)
    for i in range(25):
        (agent.sessions / f"{i:02}.jsonl").write_text(json.dumps({"role": "user", "content": "x" * 100}) + "\n")
    lines = commands.run(agent, "/resume")[0].splitlines()
    assert len(lines) == 21 and lines[0].startswith(" 1. 24") and lines[19].startswith("20. 05")
    # opening message is cut to 60 chars
    assert f"'{'x' * 60}'" in lines[0] and "x" * 61 not in lines[0]


def test_undo_redo(agent: Agent) -> None:
    """/undo returns the message for the input; /redo replays."""
    assert commands.run(agent, "/undo") == ("nothing to undo", None)
    assert commands.run(agent, "/redo") == ("nothing to redo", None)
    agent.run("hello", Silent())
    assert commands.run(agent, "/undo") == ("hello", "undo")
    assert commands.run(agent, "/redo") == ("redone", "replay")
    assert agent.messages[-1]["content"] == "ok"


def test_extra_commands_take_priority(agent: Agent) -> None:
    """Plugin commands get the stripped argument and can replace built-ins."""
    commands.EXTRA["/hi"] = lambda a, arg: (f"hi {arg}", None)
    commands.EXTRA["/help"] = lambda a, arg: ("custom help", None)
    assert commands.run(agent, "/hi   bob  ") == ("hi bob", None)
    assert commands.run(agent, "/help") == ("custom help", None)
