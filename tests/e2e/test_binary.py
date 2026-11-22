"""End to end: the built binary in a real terminal, talking to a local fake OpenAI-compatible server."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from fakes import FakeServer, assistant, call


ROOT = Path(__file__).resolve().parents[2]


def session_messages(config_dir: Path, workdir: Path) -> list[dict]:
    """Messages of the newest session saved for workdir, [] if none."""
    folder = config_dir / "sessions" / str(workdir).replace(os.sep, "-")
    files = sorted(folder.glob("*.jsonl"))
    return [json.loads(line) for line in files[-1].read_text().splitlines()] if files else []


def run(binary: Path, workdir: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    """Run the binary without a terminal."""
    return subprocess.run([str(binary), *args], cwd=workdir, env=env, capture_output=True, text=True, timeout=60)


def test_help(binary: Path, workdir: Path, env: dict) -> None:
    """--help prints usage and exits cleanly."""
    out = run(binary, workdir, env, "--help")
    assert out.returncode == 0
    assert "usage: py-agent" in out.stdout and "--continue" in out.stdout


def test_bad_config_exits_with_message(binary: Path, workdir: Path, env: dict, config_dir: Path) -> None:
    """Broken settings stop the binary before the ui with a readable message."""
    config_dir.mkdir()
    (config_dir / "settings.json").write_text('{"providers": {"p": {"models": []}}}')
    out = run(binary, workdir, env)
    assert out.returncode == 1
    assert "providers.p.baseUrl is missing" in out.stderr


def test_first_run_creates_config(tui, config_dir: Path) -> None:
    """With no config the binary writes defaults (with the build's version) and starts on the default model."""
    t = tui(ready="llama.cpp/qwen3.6-35b · thinking off")
    settings = json.loads((config_dir / "settings.json").read_text())
    version = next(line for line in (ROOT / "pyproject.toml").read_text().splitlines() if line.startswith("version"))
    assert f'"{settings["version"]}"' in version
    assert json.loads((config_dir / "auth.json").read_text()) == {"llama.cpp": "123"}
    assert t.quit() == 0


def test_tool_loop(tui, settings, server: FakeServer, workdir: Path, config_dir: Path) -> None:
    """A real turn: the model writes a file, runs bash on it, reads it, then answers; all saved."""
    (workdir / "AGENTS.md").write_text("project rule: e2e-marker")
    server.replies += [
        assistant("on it", [call("c1", "write", path="out/hello.txt", content="hello e2e\n"),
                            call("c2", "bash", command="wc -l out/hello.txt > count.txt; echo ran-bash")]),
        assistant("", [call("c3", "read", path="count.txt"), call("c4", "edit", path="out/hello.txt", oldText="e2e", newText="world")]),
        assistant("all-done-here", usage={"prompt_tokens": 5, "completion_tokens": 1}),
    ]
    t = tui()
    t.say("make files")
    t.wait(lambda: len(session_messages(config_dir, workdir)) == 8, what="8 saved messages")
    t.wait_text("all-done-here")

    assert (workdir / "out" / "hello.txt").read_text() == "hello world\n"
    assert (workdir / "count.txt").read_text().startswith("1 ")
    assert len(server.requests) == 3
    first = server.requests[0]
    assert first["model"] == "m" and first["stream"] is True
    assert server.headers[0]["Authorization"] == "Bearer sk-test"
    system = first["messages"][0]["content"]
    # prompts are bundled with --add-data
    assert "py-agent harness" in system and "- bash:" in system and "{tools}" not in system
    assert "project rule: e2e-marker" in system and str(workdir) in system
    assert [s["function"]["name"] for s in first["tools"]] == ["read", "write", "edit", "bash"]
    results = {m["tool_call_id"]: m["content"] for m in server.requests[1]["messages"] if m["role"] == "tool"}
    assert results["c1"].startswith("wrote 10 bytes") and results["c2"] == "ran-bash"
    results = {m["tool_call_id"]: m["content"] for m in server.requests[2]["messages"] if m["role"] == "tool"}
    assert results["c3"].startswith("1 ") and results["c4"].startswith("edited")

    saved = session_messages(config_dir, workdir)
    assert [m["role"] for m in saved] == ["user", "assistant", "tool", "tool", "assistant", "tool", "tool", "assistant"]
    assert saved[0] == {"role": "user", "content": "make files"} and saved[-1]["content"] == "all-done-here"
    assert all("usage" not in m for m in saved)
    assert (config_dir / "history").read_text() == '"make files"\n'
    assert "→ write" in t.text and "→ bash" in t.text
    assert t.quit() == 0


def test_model_and_thinking_commands(tui, settings, server: FakeServer) -> None:
    """/model and /think change what is sent to the server."""
    settings([{"id": "m"}, {"id": "r", "reasoning": True, "thinkingLevelMap": {"high": "very-high"}}])
    t = tui()
    t.say("/model")
    t.wait_text("* p/m")
    t.say("/model r")
    t.wait_text("model: p/r · thinking off")
    t.say("/think high")
    t.wait_text("p/r · thinking high")
    t.say("hello")
    t.wait(lambda: server.requests, what="a request")
    assert server.requests[0]["model"] == "r"
    assert server.requests[0]["reasoning_effort"] == "very-high"
    assert t.quit() == 0


def test_continue_resumes(tui, settings, server: FakeServer, workdir: Path, config_dir: Path) -> None:
    """-c continues the last session: it is redrawn and sent with the next message."""
    server.replies += [assistant("first-answer"), assistant("second-answer")]
    t = tui()
    t.say("first-question")
    t.wait(lambda: len(session_messages(config_dir, workdir)) == 2, what="first turn saved")
    assert t.quit() == 0

    t = tui("-c")
    t.wait_text("first-answer")
    t.say("second-question")
    t.wait(lambda: len(session_messages(config_dir, workdir)) == 4, what="second turn saved")
    sent = [m["content"] for m in server.requests[1]["messages"][1:]]
    assert sent == ["first-question", "first-answer", "second-question"]
    assert len(list((config_dir / "sessions").rglob("*.jsonl"))) == 1
    assert t.quit() == 0


def test_resume_command(tui, settings, server: FakeServer, workdir: Path, config_dir: Path) -> None:
    """/resume lists earlier sessions and /resume 1 loads one."""
    t = tui()
    t.say("older-session")
    t.wait(lambda: session_messages(config_dir, workdir), what="session saved")
    t.say("/clear")
    t.wait_text("new conversation")
    t.say("/resume")
    t.wait_text("'older-session'")
    t.say("/resume 1")
    t.wait_text("resumed ")
    assert t.quit() == 0


def test_undo_reverts_files(tui, settings, server: FakeServer, workdir: Path, config_dir: Path) -> None:
    """In a git repo /undo reverts the turn's files and /redo brings them back."""
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    (workdir / "keep.txt").write_text("original")
    server.replies += [
        assistant("", [call("1", "write", path="new.txt", content="n"),
                       call("2", "edit", path="keep.txt", oldText="original", newText="changed")]),
        assistant("changed-it"),
    ]
    t = tui()
    t.say("change things")
    t.wait_text("changed-it")
    assert (workdir / "new.txt").exists() and (workdir / "keep.txt").read_text() == "changed"
    t.say("/undo")
    t.wait_text("undone")
    t.wait(lambda: not (workdir / "new.txt").exists(), what="new.txt removed")
    assert (workdir / "keep.txt").read_text() == "original"
    assert session_messages(config_dir, workdir) == []
    # the undone message is back in the input; clear it before the next command
    t.press("end")
    t.type("\x7f" * 20)
    t.say("/redo")
    t.wait_text("redone")
    t.wait(lambda: (workdir / "new.txt").exists(), what="new.txt restored")
    assert (workdir / "keep.txt").read_text() == "changed"
    # the user's own repo has no commits or staged files from snapshots
    assert subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=workdir, capture_output=True, text=True).stdout == ""
    assert t.quit() == 0


def test_esc_kills_running_bash(tui, settings, server: FakeServer, workdir: Path, config_dir: Path) -> None:
    """esc kills a running bash command quickly and the session stays resumable."""
    server.replies.append(assistant("", [call("1", "bash", command="sleep 30; touch late.txt"),
                                         call("2", "bash", command="touch never.txt")]))
    t = tui()
    t.say("run slow")
    t.wait_text("→ bash")
    time.sleep(0.5)
    start = time.monotonic()
    t.press("esc")
    t.wait(lambda: len(session_messages(config_dir, workdir)) == 4, timeout=10, what="interrupted turn saved")
    assert time.monotonic() - start < 10
    saved = session_messages(config_dir, workdir)
    assert [m["role"] for m in saved] == ["user", "assistant", "tool", "tool"]
    assert saved[3]["content"] == "interrupted by user"
    assert len(server.requests) == 1
    assert not (workdir / "never.txt").exists()
    time.sleep(0.5)
    assert not (workdir / "late.txt").exists()
    assert t.quit() == 0


def test_api_error_shown(tui, settings, server: FakeServer) -> None:
    """A rejected request is shown as an error and the app keeps working."""
    server.replies += [(400, '{"error": "model-not-found"}'), assistant("recovered")]
    t = tui()
    t.say("one")
    t.wait_text("model-not-found")
    t.say("two")
    t.wait_text("recovered")
    assert t.quit() == 0


def test_unreachable_server(tui, config_dir: Path) -> None:
    """A dead endpoint is reported, not a crash."""
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(json.dumps(
        {"providers": {"p": {"baseUrl": "http://127.0.0.1:9/v1", "models": [{"id": "m"}]}}}))
    t = tui()
    t.say("hello")
    t.wait_text("error:")
    assert t.proc.poll() is None
    assert t.quit() == 0


def test_plugin_in_binary(tui, settings, server: FakeServer, workdir: Path, config_dir: Path) -> None:
    """Plugins load in the frozen binary: they can import modules, add tools and commands."""
    folder = config_dir / "plugins" / "e2e"
    folder.mkdir(parents=True)
    (folder / "helper.py").write_text("REPLY = 'pong-from-plugin'\n")
    (folder / "main.py").write_text(
        "from modules import tools\n"
        "from .helper import REPLY\n"
        "def stamp(cwd, stop, file: str) -> str:\n"
        "    (cwd / file).write_text('stamped')\n"
        "    return 'stamped ' + file\n"
        "def setup(api):\n"
        "    assert api.tools is tools\n"
        "    api.add_tool(stamp, 'Stamp a file.', {'file': {'type': 'string'}}, ['file'])\n"
        "    api.add_command('/ping', lambda agent, arg: (REPLY, None))\n")
    server.replies += [assistant("", [call("1", "stamp", file="s.txt")]), assistant("ok")]
    t = tui()
    t.say("/ping")
    t.wait_text("pong-from-plugin")
    t.say("stamp it")
    t.wait(lambda: len(server.requests) == 2, what="second request")
    assert (workdir / "s.txt").read_text() == "stamped"
    assert "stamp" in [s["function"]["name"] for s in server.requests[0]["tools"]]
    assert "- stamp: Stamp a file." in server.requests[0]["messages"][0]["content"]
    assert t.quit() == 0


@pytest.mark.parametrize("name", ["bash_guard", "cost", "stats", "context", "theme", "done_notify", "git_context"])
def test_example_plugins_start(tui, settings, server: FakeServer, config_dir: Path, name: str) -> None:
    """Each bundled example plugin loads in the binary and the app answers."""
    shutil.copytree(ROOT / "examples" / "plugins" / name, config_dir / "plugins" / name)
    server.replies.append(assistant("plugin-answer", usage={"prompt_tokens": 1, "completion_tokens": 1}))
    t = tui()
    t.say("hi")
    t.wait_text("plugin-answer")
    assert t.quit() == 0


def test_command_replies(tui, settings, server: FakeServer) -> None:
    """Commands that only reply answer without a request, and /exit quits cleanly."""
    t = tui()
    for line, reply in [("/help", "esc stops the current answer"), ("/nope", "/think [level]"),
                        ("/model nope", "unknown model nope"), ("/think bogus", "available: off"),
                        ("/undo", "nothing to undo"), ("/redo", "nothing to redo"),
                        ("/resume", "no sessions in this folder")]:
        t.say(line)
        t.wait_text(reply)
    assert server.requests == []
    t.say("/exit")
    assert t.proc.wait(10) == 0


def test_multiline_input_and_history(tui, settings, server: FakeServer, config_dir: Path) -> None:
    """Backslash-enter and ctrl+j add lines; the message is kept in history and up recalls it after a restart."""
    t = tui()
    t.type("line one\\")
    t.press("enter")
    t.type("line two")
    t.press("ctrl+j")
    t.say("line three")
    t.wait(lambda: server.requests, what="a request")
    assert server.requests[0]["messages"][-1]["content"] == "line one\nline two\nline three"
    assert (config_dir / "history").read_text() == '"line one\\nline two\\nline three"\n'
    assert t.quit() == 0

    t = tui()
    t.press("up")
    t.press("enter")
    t.wait(lambda: len(server.requests) == 2, what="second request")
    assert server.requests[1]["messages"][-1]["content"] == "line one\nline two\nline three"
    assert t.quit() == 0


def test_queue_while_busy(tui, settings, server: FakeServer) -> None:
    """A message sent during an answer is queued and sent once the answer ends."""
    server.replies += [assistant("", [call("1", "bash", command="sleep 3")]), assistant("first-done"),
                       assistant("queued-done")]
    t = tui()
    t.say("slow")
    t.wait_text("→ bash")
    t.say("queued-msg")
    t.wait_text("1 queued")
    t.wait_text("queued-done")
    assert len(server.requests) == 3
    assert "queued-msg" not in json.dumps(server.requests[1]["messages"])
    assert server.requests[2]["messages"][-1]["content"] == "queued-msg"
    assert t.quit() == 0


def test_esc_returns_queue_to_input(tui, settings, server: FakeServer, workdir: Path, config_dir: Path) -> None:
    """esc hands queued messages back to the input instead of sending them."""
    server.replies += [assistant("", [call("1", "bash", command="sleep 30")]), assistant("later-done")]
    t = tui()
    t.say("slow")
    t.wait_text("→ bash")
    t.say("later-msg")
    t.wait_text("1 queued")
    t.press("esc")
    t.wait(lambda: len(session_messages(config_dir, workdir)) == 3, timeout=10, what="interrupted turn saved")
    time.sleep(1)
    assert len(server.requests) == 1
    t.press("enter")
    t.wait_text("later-done")
    assert server.requests[1]["messages"][-1]["content"] == "later-msg"
    assert t.quit() == 0


def test_settings_defaults(tui, settings, server: FakeServer, config_dir: Path) -> None:
    """defaultModel, defaultThinkingLevel and overrideSystemPrompt are honored by the binary."""
    (config_dir / "prompt.md").write_text("custom-prompt-marker")
    settings([{"id": "m"}, {"id": "r", "reasoning": True, "thinkingLevelMap": {"high": "very-high"}}],
             defaultModel="p/r", defaultThinkingLevel="high", overrideSystemPrompt="prompt.md")
    t = tui(ready="p/r · thinking high")
    t.say("hello")
    t.wait(lambda: server.requests, what="a request")
    first = server.requests[0]
    assert first["model"] == "r" and first["reasoning_effort"] == "very-high"
    system = first["messages"][0]["content"]
    assert system.startswith("custom-prompt-marker") and "py-agent harness" not in system
    assert t.quit() == 0


def test_reasoning_and_tool_error(tui, settings, server: FakeServer) -> None:
    """Reasoning is shown and echoed back; a failing tool shows its error and the loop goes on."""
    server.replies += [assistant("", [call("1", "read", path="missing.txt")], reasoning="deep-thought"),
                       assistant("final-words")]
    t = tui()
    t.say("think")
    t.wait_text("final-words")
    assert "deep-thought" in t.text and "→ read" in t.text and "Error:" in t.text
    sent = server.requests[1]["messages"]
    assert sent[2]["reasoning_content"] == "deep-thought"
    assert sent[3]["role"] == "tool" and sent[3]["content"].startswith("Error:")
    assert t.quit() == 0
