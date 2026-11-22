"""agent: the tool loop, events, sessions, resume, stop, undo and redo."""

import json
import os
from pathlib import Path
from threading import Event

import pytest

from fakes import FakeLLM, assistant, call
from modules import llm, tools
from modules.agent import Agent
from modules.config import Config


class Recorder:
    """Events implementation that remembers what it was told."""

    def __init__(self) -> None:
        self.log: list[tuple] = []

    def text(self, text: str) -> None:
        self.log.append(("text", text))

    def reasoning(self, text: str) -> None:
        self.log.append(("reasoning", text))

    def tool_call(self, id: str, name: str, arguments: str) -> None:
        self.log.append(("tool_call", name, json.loads(arguments)))

    def tool_result(self, id: str, result: str) -> None:
        self.log.append(("tool_result", result))

    def retry(self, delay: float, error: str) -> None:
        self.log.append(("retry", delay))

    def ask(self, question: str, options: list[str], body=None) -> int | None:
        return None

    def compacted(self) -> None:
        self.log.append(("compacted",))


def saved(agent: Agent) -> list[dict]:
    """Messages in the session file."""
    return [json.loads(line) for line in agent.session.read_text().splitlines()]


def roles(messages: list[dict]) -> list[str]:
    """Just the roles, to check conversation shape."""
    return [m["role"] for m in messages]


@pytest.fixture
def agent(config: Config, workdir: Path, fake_llm: FakeLLM) -> Agent:
    """An agent working in workdir with a scripted model."""
    return Agent(config, workdir)


def test_init(agent: Agent, config: Config, workdir: Path) -> None:
    """Starts with only the system prompt, default model and per-project folders."""
    project = str(workdir).replace(os.sep, "-")
    assert agent.sessions == config.dir / "sessions" / project
    assert agent.snapshots == config.dir / "snapshots" / project
    assert roles(agent.messages) == ["system"]
    assert str(workdir) in agent.messages[0]["content"]
    assert agent.model is config.default_model and agent.thinking == "off"
    assert agent.usage is None and agent.turns == [] and agent.undone == []


def test_unsupported_default_thinking_falls_back(config: Config, workdir: Path) -> None:
    """A thinking level the default model can't do becomes off."""
    from dataclasses import replace
    assert Agent(replace(config, default_thinking="high"), workdir).thinking == "off"
    assert Agent(replace(config, default_thinking="high", default_model=config.models[1]), workdir).thinking == "high"


def test_set_model(agent: Agent, config: Config) -> None:
    """Switching keeps a supported level and drops an unsupported one."""
    plain, smart = config.models
    agent.set_model(smart)
    agent.thinking = "low"
    agent.set_model(smart)
    assert agent.thinking == "low"
    agent.set_model(plain)
    assert agent.model is plain and agent.thinking == "off"


def test_plain_answer(agent: Agent, fake_llm: FakeLLM) -> None:
    """A reply without tool calls ends the turn and is saved."""
    fake_llm.replies.append(assistant("hello", reasoning="hm", usage={"prompt_tokens": 3}))
    events = Recorder()
    agent.run("hi", events)
    assert events.log == [("reasoning", "hm"), ("text", "hello")]
    assert agent.messages[1:] == [{"role": "user", "content": "hi"},
                                  {"role": "assistant", "content": "hello", "reasoning_content": "hm"}]
    assert agent.usage == {"prompt_tokens": 3}
    assert saved(agent) == agent.messages[1:]
    req = fake_llm.requests[0]
    assert req["messages"] == agent.messages[:2]
    assert req["tools"] is tools.SPECS and req["thinking"] == "off" and req["model"] is agent.model


def test_tool_loop(agent: Agent, fake_llm: FakeLLM, workdir: Path) -> None:
    """Tool calls run in order, each result is sent back, and the loop ends on plain text."""
    fake_llm.replies += [
        assistant("writing", [call("1", "write", path="f.txt", content="x"), call("2", "read", path="f.txt")]),
        assistant("", [call("3", "bash", command="cat f.txt")]),
        assistant("done"),
    ]
    events = Recorder()
    agent.run("go", events)
    assert (workdir / "f.txt").read_text() == "x"
    assert roles(agent.messages) == ["system", "user", "assistant", "tool", "tool", "assistant", "tool", "assistant"]
    results = [m for m in agent.messages if m["role"] == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in results] == [
        ("1", f"wrote 1 bytes to {workdir / 'f.txt'}"), ("2", "x"), ("3", "x")]
    assert [e[0] for e in events.log] == ["text", "tool_call", "tool_result", "tool_call", "tool_result",
                                          "tool_call", "tool_result", "text"]
    assert events.log[1] == ("tool_call", "write", {"path": "f.txt", "content": "x"})
    # each request carries everything up to it
    assert [len(r["messages"]) for r in fake_llm.requests] == [2, 5, 7]
    assert saved(agent) == agent.messages[1:]


def test_tool_error_goes_to_model(agent: Agent, fake_llm: FakeLLM) -> None:
    """A failing tool returns Error text to the model instead of raising."""
    fake_llm.replies += [assistant("", [call("1", "nope")]), assistant("ok")]
    agent.run("go", Recorder())
    assert agent.messages[3] == {"role": "tool", "tool_call_id": "1", "content": "Error: 'nope'"}


def test_usage_kept_when_reply_has_none(agent: Agent, fake_llm: FakeLLM) -> None:
    """usage stays from the last request that reported it."""
    fake_llm.replies += [assistant("a", usage={"prompt_tokens": 1}), assistant("b")]
    agent.run("1", Recorder())
    agent.run("2", Recorder())
    assert agent.usage == {"prompt_tokens": 1}
    assert all("usage" not in m for m in agent.messages)


def test_api_error_saves_user_message(agent: Agent, fake_llm: FakeLLM) -> None:
    """An api error propagates, but the user message is already saved and the turn recorded."""
    fake_llm.replies.append(llm.ApiError("boom"))
    with pytest.raises(llm.ApiError):
        agent.run("hi", Recorder())
    assert saved(agent) == [{"role": "user", "content": "hi"}]
    assert len(agent.turns) == 1


def test_stop_during_stream_keeps_text_only(agent: Agent, fake_llm: FakeLLM) -> None:
    """Stopped mid-answer: partial tool calls are dropped, text is kept, nothing runs."""
    def reply(stop: Event) -> dict:
        stop.set()
        return assistant("partial", [call("1", "bash", command="touch x")])

    fake_llm.replies.append(reply)
    events = Recorder()
    agent.run("hi", events)
    assert agent.messages[2:] == [{"role": "assistant", "content": "partial"}]
    assert all(e[0] != "tool_call" for e in events.log)
    assert saved(agent)[-1] == {"role": "assistant", "content": "partial"}
    assert len(fake_llm.requests) == 1


def test_stop_without_text_adds_nothing(agent: Agent, fake_llm: FakeLLM) -> None:
    """Stopped before any text: only the user message remains."""
    def reply(stop: Event) -> dict:
        stop.set()
        return assistant("")

    fake_llm.replies.append(reply)
    agent.run("hi", Recorder())
    assert roles(agent.messages) == ["system", "user"]


def test_stop_during_tools_answers_every_call(agent: Agent, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calls after a stop get "interrupted by user" so the history stays valid."""
    def stopping(cwd, stop, **args) -> str:
        stop.set()
        return "stopped here"

    monkeypatch.setitem(tools.TOOLS, "stopper", (stopping, {}))
    fake_llm.replies.append(assistant("", [call("1", "stopper"), call("2", "bash", command="touch x"), call("3", "read", path="y")]))
    agent.run("hi", Recorder())
    results = [(m["tool_call_id"], m["content"]) for m in agent.messages if m["role"] == "tool"]
    assert results == [("1", "stopped here"), ("2", "interrupted by user"), ("3", "interrupted by user")]
    assert len(fake_llm.requests) == 1
    assert saved(agent) == agent.messages[1:]


def test_stop_flag_cleared_on_next_run(agent: Agent, fake_llm: FakeLLM) -> None:
    """A stop from a previous answer doesn't cancel the next one."""
    agent.stop()
    assert agent.stopped.is_set()
    agent.run("hi", Recorder())
    assert agent.messages[-1]["content"] == "ok"


def test_empty_conversation_leaves_no_file(agent: Agent) -> None:
    """Nothing is written until there is a message."""
    assert not agent.session.exists()
    assert not agent.sessions.exists()


def test_session_appends_across_turns(agent: Agent, fake_llm: FakeLLM) -> None:
    """Later turns append to the same file."""
    agent.run("1", Recorder())
    agent.run("2", Recorder())
    assert [m["content"] for m in saved(agent)] == ["1", "ok", "2", "ok"]
    assert agent.saved == len(agent.messages)


def test_session_name_is_start_time(agent: Agent) -> None:
    """Session files are named by start time and sort chronologically."""
    import re
    assert re.fullmatch(r"\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d\.jsonl", agent.session.name)


def test_reset(agent: Agent, fake_llm: FakeLLM, workdir: Path) -> None:
    """reset starts over with a rebuilt system prompt and a new session."""
    agent.run("1", Recorder())
    agent.usage = {"x": 1}
    (workdir / "AGENTS.md").write_text("new rules")
    agent.reset()
    assert roles(agent.messages) == ["system"]
    assert "new rules" in agent.messages[0]["content"]
    assert agent.usage is None and agent.turns == [] and agent.saved == 1


def test_resume(agent: Agent, fake_llm: FakeLLM, config: Config, workdir: Path) -> None:
    """A resumed session continues in the same file with a fresh system prompt."""
    fake_llm.replies += [assistant("", [call("1", "bash", command="echo hi")]), assistant("first")]
    agent.run("1", Recorder())
    path = agent.session
    other = Agent(config, workdir)
    other.resume(path)
    assert other.messages[1:] == agent.messages[1:]
    assert other.messages[0]["role"] == "system"
    assert other.session == path and other.saved == len(other.messages)
    other.run("2", Recorder())
    assert [m.get("content") for m in saved(other)][-2:] == ["2", "ok"]
    assert len(saved(other)) == len(other.messages) - 1
    # the model saw the old conversation
    assert fake_llm.requests[-1]["messages"][1]["content"] == "1"


def test_undo_redo_messages(agent: Agent, fake_llm: FakeLLM) -> None:
    """undo drops the last turn and returns its message; redo brings it back; both rewrite the file."""
    agent.run("one", Recorder())
    agent.run("two", Recorder())
    assert agent.undo() == "two"
    assert [m["content"] for m in agent.messages[1:]] == ["one", "ok"]
    assert [m["content"] for m in saved(agent)] == ["one", "ok"]
    assert agent.undo() == "one"
    assert roles(agent.messages) == ["system"]
    assert not agent.session.exists()
    assert agent.undo() is None
    assert agent.redo() and agent.redo()
    assert [m["content"] for m in saved(agent)] == ["one", "ok", "two", "ok"]
    assert not agent.redo()


def test_new_message_clears_redo(agent: Agent, fake_llm: FakeLLM) -> None:
    """Sending after undo forgets what was undone."""
    agent.run("one", Recorder())
    agent.undo()
    agent.run("other", Recorder())
    assert not agent.redo()


def test_undo_reverts_files_in_git_repo(config: Config, git_workdir: Path, fake_llm: FakeLLM) -> None:
    """In a git repo undo reverts the turn's file changes and redo reapplies them."""
    (git_workdir / "a.txt").write_text("original")
    agent = Agent(config, git_workdir)
    fake_llm.replies += [
        assistant("", [call("1", "edit", path="a.txt", oldText="original", newText="changed"),
                       call("2", "write", path="new.txt", content="n"),
                       call("3", "bash", command="rm -f doomed")]),
        assistant("done"),
    ]
    (git_workdir / "doomed").write_text("d")
    agent.run("change", Recorder())
    assert (git_workdir / "a.txt").read_text() == "changed"
    assert agent.turns[0][1] and agent.turns[0][2] and agent.turns[0][1] != agent.turns[0][2]

    agent.undo()
    assert (git_workdir / "a.txt").read_text() == "original"
    assert not (git_workdir / "new.txt").exists()
    assert (git_workdir / "doomed").read_text() == "d"

    agent.redo()
    assert (git_workdir / "a.txt").read_text() == "changed"
    assert (git_workdir / "new.txt").read_text() == "n"
    assert not (git_workdir / "doomed").exists()


def test_undo_outside_git_keeps_files(agent: Agent, fake_llm: FakeLLM, workdir: Path) -> None:
    """Outside git, undo only drops messages."""
    fake_llm.replies += [assistant("", [call("1", "write", path="f", content="x")]), assistant("ok")]
    agent.run("w", Recorder())
    assert agent.turns == [(1, None, None)]
    assert agent.undo() == "w"
    assert (workdir / "f").read_text() == "x"
