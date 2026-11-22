"""tui: the textual app driven headless with textual's pilot, with a scripted model."""

import asyncio
import io
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from rich.console import Console
from textual.widgets import Markdown, Static

from fakes import FakeLLM, assistant, call
from modules import llm, plugins, tui
from modules.agent import Agent
from modules.config import Config
from modules.tui import Chat, PromptInput


EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "plugins"


def plain(renderable) -> str:
    """Render anything rich can print to plain text."""
    console = Console(width=120, file=io.StringIO(), record=True)
    console.print(renderable)
    return console.export_text().rstrip()


def log(app: Chat) -> list[tuple[str, str]]:
    """(kind, text) of every block in the log; kind is the css class, "md" or "tool"."""
    out = []
    for w in app.query_one("#log").children:
        if isinstance(w, Markdown):
            out.append(("reasoning" if w.has_class("reasoning") else "md", w.source.strip()))
        elif isinstance(w, Static):
            kind = next(iter(w.classes), "tool")
            out.append((kind, plain(w.content)))
    return out


def status(app: Chat) -> str:
    """Text of the status line."""
    return str(app.query_one("#status", Static).content)


async def settle(app: Chat, pilot) -> None:
    """Wait for the answer worker and the ui updates it posted."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def say(app: Chat, pilot, text: str) -> None:
    """Type a message and press enter."""
    app.query_one(PromptInput).text = text
    await pilot.press("enter")
    await settle(app, pilot)


def drive(app: Chat, script) -> None:
    """Run app headless and call script(pilot) inside it."""
    async def main() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await script(pilot)
    asyncio.run(main())


@pytest.fixture
def agent(config: Config, workdir: Path, fake_llm: FakeLLM) -> Agent:
    """An agent with a scripted model."""
    return Agent(config, workdir)


@pytest.fixture
def app(agent: Agent) -> Chat:
    """The chat app for that agent."""
    return Chat(agent)


# pure helpers

def test_shown_args() -> None:
    """Tool arguments are shown one per line; bad json is shown raw."""
    assert tui._shown_args('{"path": "x", "n": 1}') == "path='x'\nn=1"
    assert tui._shown_args("") == ""
    assert tui._shown_args("{bad") == "{bad"
    assert tui._shown_args("[1]") == "[1]"


def test_preview() -> None:
    """Long results show the first lines and how many more."""
    assert tui._preview("a\nb") == "a\nb"
    assert tui._preview("\n".join("123456")) == "1\n2\n3\n4\n... 2 more lines"


def test_history_entry() -> None:
    """History lines are json strings; anything else is kept as plain text."""
    assert tui._history_entry('"a\\nb"') == "a\nb"
    assert tui._history_entry("plain") == "plain"
    assert tui._history_entry("123") == "123"


# app

def test_mount_shows_status(app: Chat) -> None:
    """Status line shows model and thinking; input has focus."""
    async def script(pilot) -> None:
        assert status(app) == "p/m · thinking off"
        assert app.focused is app.query_one(PromptInput)
        assert log(app) == []
    drive(app, script)


def test_message_and_answer(app: Chat, fake_llm: FakeLLM, config: Config) -> None:
    """A message is shown, answered as markdown, and remembered in history."""
    fake_llm.replies.append(assistant("**hello** there"))

    async def script(pilot) -> None:
        await say(app, pilot, "hi")
        assert log(app) == [("user", "> hi"), ("md", "**hello** there")]
        assert app.query_one(PromptInput).text == ""
        assert not app.busy and status(app) == "p/m · thinking off"
    drive(app, script)
    assert config.history.read_text() == '"hi"\n'
    assert fake_llm.requests[0]["messages"][-1] == {"role": "user", "content": "hi"}


def test_reasoning_then_text_are_separate_blocks(app: Chat, fake_llm: FakeLLM) -> None:
    """Reasoning streams into its own muted block."""
    fake_llm.replies.append(assistant("answer", reasoning="pondering"))

    async def script(pilot) -> None:
        await say(app, pilot, "q")
        assert log(app)[1:] == [("reasoning", "pondering"), ("md", "answer")]
    drive(app, script)


def test_tool_call_panel(app: Chat, fake_llm: FakeLLM, workdir: Path) -> None:
    """Tool calls get a panel with their arguments and a result preview."""
    fake_llm.replies += [assistant("", [call("1", "bash", command="seq 6")]), assistant("done")]

    async def script(pilot) -> None:
        await say(app, pilot, "count")
        kinds = [k for k, _ in log(app)]
        assert kinds == ["user", "tool", "md"]
        panel = log(app)[1][1]
        assert "→ bash" in panel and "command='seq 6'" in panel
        assert "1" in panel and "... 2 more lines" in panel
    drive(app, script)


def test_empty_message_ignored(app: Chat, fake_llm: FakeLLM) -> None:
    """Blank input sends nothing."""
    async def script(pilot) -> None:
        await say(app, pilot, "   \n ")
        assert log(app) == []
    drive(app, script)
    assert fake_llm.requests == []


def test_api_error_shown(app: Chat, fake_llm: FakeLLM) -> None:
    """Api errors appear in the log and the app stays usable."""
    fake_llm.replies += [llm.ApiError("500 boom"), assistant("fine")]

    async def script(pilot) -> None:
        await say(app, pilot, "a")
        assert log(app)[-1] == ("error", "error: 500 boom")
        assert not app.busy
        await say(app, pilot, "b")
        assert log(app)[-1] == ("md", "fine")
    drive(app, script)


def test_slash_commands(app: Chat) -> None:
    """Commands reply in the log and refresh the status line."""
    async def script(pilot) -> None:
        await say(app, pilot, "/model q/r")
        assert log(app) == [("info", "model: q/r · thinking off")]
        await say(app, pilot, "/think high")
        assert status(app) == "q/r · thinking high"
        await say(app, pilot, "/help")
        assert log(app)[-1][1].startswith("commands:")
    drive(app, script)


def test_clear_empties_log(app: Chat, fake_llm: FakeLLM) -> None:
    """/clear empties the log and the conversation."""
    async def script(pilot) -> None:
        await say(app, pilot, "hi")
        await say(app, pilot, "/clear")
        assert log(app) == [("info", "new conversation")]
        assert len(app.agent.messages) == 1
    drive(app, script)


def test_quit(app: Chat) -> None:
    """/quit exits the app."""
    async def script(pilot) -> None:
        await say(app, pilot, "/quit")
        assert app._exit
    drive(app, script)


def test_undo_puts_message_back(app: Chat, fake_llm: FakeLLM) -> None:
    """/undo redraws the log and puts the undone message into the input; /redo brings it back."""
    fake_llm.replies += [assistant("one"), assistant("two")]

    async def script(pilot) -> None:
        await say(app, pilot, "first")
        await say(app, pilot, "second")
        await say(app, pilot, "/undo")
        assert log(app) == [("user", "> first"), ("md", "one"), ("info", "undone")]
        assert app.query_one(PromptInput).text == "second"
        app.query_one(PromptInput).text = ""
        await say(app, pilot, "/redo")
        assert log(app) == [("user", "> first"), ("md", "one"), ("user", "> second"), ("md", "two"), ("info", "redone")]
    drive(app, script)


def test_replay_resumed_session(agent: Agent, fake_llm: FakeLLM, workdir: Path) -> None:
    """A session resumed before start (py-agent -c) is drawn on mount, tool results included."""
    fake_llm.replies += [assistant("", [call("1", "bash", command="echo from-tool")]), assistant("final")]

    class Silent:
        def __getattr__(self, name):
            return lambda *a: None

    agent.run("old message", Silent())
    resumed = Agent(agent.config, workdir)
    resumed.resume(agent.session)
    app = Chat(resumed)

    async def script(pilot) -> None:
        entries = log(app)
        assert [k for k, _ in entries] == ["user", "tool", "md"]
        assert "from-tool" in entries[1][1] and entries[2][1] == "final"
    drive(app, script)


def test_queue_while_busy_then_sent(app: Chat, fake_llm: FakeLLM) -> None:
    """Messages sent while answering are queued, shown in status, and sent in order after."""
    release = threading.Event()

    def slow(stop: threading.Event) -> dict:
        release.wait(5)
        return assistant("first answer")

    fake_llm.replies += [slow, assistant("second answer")]

    async def script(pilot) -> None:
        app.query_one(PromptInput).text = "one"
        await pilot.press("enter")
        await pilot.pause()
        assert app.busy and "working, esc to stop" in status(app)
        app.query_one(PromptInput).text = "two"
        await pilot.press("enter")
        await pilot.pause()
        assert app.queue == ["two"] and status(app).endswith("· 1 queued")
        release.set()
        await settle(app, pilot)
        await settle(app, pilot)
        assert [t for k, t in log(app)] == ["> one", "first answer", "> two", "second answer"]
        assert app.queue == [] and not app.busy
    drive(app, script)


def test_esc_stops_and_returns_queue(app: Chat, fake_llm: FakeLLM) -> None:
    """esc stops the answer and hands queued messages back to the input."""
    def until_stopped(stop: threading.Event) -> dict:
        stop.wait(5)
        return assistant("")

    fake_llm.replies.append(until_stopped)

    async def script(pilot) -> None:
        app.query_one(PromptInput).text = "long"
        await pilot.press("enter")
        await pilot.pause()
        for text in ("q1", "q2"):
            app.query_one(PromptInput).text = text
            await pilot.press("enter")
        app.query_one(PromptInput).text = "draft"
        await pilot.press("escape")
        await settle(app, pilot)
        assert app.agent.stopped.is_set()
        assert app.query_one(PromptInput).text == "q1\nq2\ndraft"
        assert app.queue == [] and not app.busy
    drive(app, script)
    assert len(fake_llm.requests) == 1


def test_newline_keys(app: Chat, fake_llm: FakeLLM) -> None:
    """ctrl+j and backslash-enter add lines instead of sending."""
    async def script(pilot) -> None:
        box = app.query_one(PromptInput)
        await pilot.press("a", "ctrl+j", "b")
        assert box.text == "a\nb"
        box.text = "c\\"
        box.move_cursor(box.document.end)
        await pilot.press("enter")
        assert box.text == "c\n"
        await pilot.press("d", "enter")
        await settle(app, pilot)
        assert log(app)[0] == ("user", "> c\nd")
    drive(app, script)


def test_history_navigation(agent: Agent, config: Config) -> None:
    """up/down walk history from the first/last line; old plain lines still load."""
    config.dir.mkdir(parents=True, exist_ok=True)
    config.history.write_text('legacy\n"multi\\nline"\n"last"\n')
    app = Chat(agent)

    async def script(pilot) -> None:
        box = app.query_one(PromptInput)
        assert box.entries == ["legacy", "multi\nline", "last"]
        await pilot.press("up")
        assert box.text == "last"
        await pilot.press("up")
        assert box.text == "multi\nline"
        # cursor is on the last line, so up moves within the entry first
        await pilot.press("up")
        assert box.text == "multi\nline"
        await pilot.press("up")
        assert box.text == "legacy"
        await pilot.press("up")
        assert box.text == "legacy"
        await pilot.press("down", "down")
        assert box.text == "last"
        await pilot.press("down")
        assert box.text == ""
    drive(app, script)


def test_history_skips_repeats(app: Chat, fake_llm: FakeLLM, config: Config) -> None:
    """Sending the same line twice stores it once."""
    async def script(pilot) -> None:
        await say(app, pilot, "/help")
        await say(app, pilot, "/help")
        await say(app, pilot, "multi\nline")
    drive(app, script)
    assert config.history.read_text().splitlines() == ['"/help"', '"multi\\nline"']


def test_command_suggestion(app: Chat) -> None:
    """Typing a command prefix suggests the rest; right accepts it."""
    async def script(pilot) -> None:
        box = app.query_one(PromptInput)
        await pilot.press("/", "m", "o")
        assert box.suggestion == "del"
        await pilot.press("right")
        assert box.text == "/model"
        box.text = "hello"
        box.update_suggestion()
        assert box.suggestion == ""
    drive(app, script)


def test_unmount_stops_agent(app: Chat) -> None:
    """Closing the app stops a running answer."""
    async def script(pilot) -> None:
        pass
    drive(app, script)
    assert app.agent.stopped.is_set()


# ui example plugins, loaded through the plugin api

def load_ui_plugin(config: Config, name: str) -> Chat:
    """Load an example plugin and build its Chat."""
    api = plugins.Api(replace(config, plugins=[EXAMPLES / name / "main.py"]), config.dir)
    plugins.load(api)
    return api.Chat(api.Agent(api.config, api.cwd))


def test_context_plugin_status(config: Config, fake_llm: FakeLLM) -> None:
    """context adds the last request's tokens to the status line."""
    fake_llm.replies.append(assistant("x", usage={"prompt_tokens": 1200, "completion_tokens": 34}))
    app = load_ui_plugin(config, "context")

    async def script(pilot) -> None:
        assert status(app) == "p/m · thinking off"
        await say(app, pilot, "hi")
        assert status(app) == "p/m · thinking off · 1,234 tokens"
    drive(app, script)


def test_done_notify_plugin(config: Config, fake_llm: FakeLLM) -> None:
    """done_notify posts a toast when an answer ends."""
    app = load_ui_plugin(config, "done_notify")

    async def script(pilot) -> None:
        await say(app, pilot, "hi")
        assert [n.message for n in app._notifications] == ["answer finished"]
    drive(app, script)


def test_theme_plugin(config: Config) -> None:
    """theme switches to gruvbox on mount."""
    app = load_ui_plugin(config, "theme")

    async def script(pilot) -> None:
        assert app.theme == "gruvbox"
    drive(app, script)
