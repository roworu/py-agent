"""Full-screen chat UI: message log, input with history, status line."""

import json
from pathlib import Path

from rich.panel import Panel
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.suggester import SuggestFromList
from textual.widget import Widget
from textual.widgets import Input, Markdown, Static
from textual.widgets.markdown import MarkdownStream

from . import commands
from .agent import Agent
from .llm import ApiError

HISTORY_LINES = 1000


def _shown_args(arguments: str) -> str:
    try:
        return "\n".join(f"{k}={v!r}" for k, v in json.loads(arguments or "{}").items())
    except (ValueError, AttributeError):
        return arguments


def _preview(text: str, lines: int = 4) -> str:
    rows = text.splitlines()
    more = f"\n... {len(rows) - lines} more lines" if len(rows) > lines else ""
    return "\n".join(rows[:lines]) + more


class PromptInput(Input):
    """Single-line input with up/down history kept in a file."""

    BINDINGS = [("up", "older"), ("down", "newer")]

    def __init__(self, history: Path) -> None:
        super().__init__(placeholder="message, /help, esc stops", suggester=SuggestFromList(commands.NAMES))
        self.history = history
        lines = history.read_text().splitlines() if history.is_file() else []
        self.entries: list[str] = lines[-HISTORY_LINES:]
        self.index = len(self.entries)

    def remember(self, line: str) -> None:
        """Add a line to the history and its file, skipping repeats."""
        if not self.entries or self.entries[-1] != line:
            self.entries.append(line)
            with self.history.open("a") as f:
                f.write(line + "\n")
        self.index = len(self.entries)

    def _show(self, index: int) -> None:
        self.index = index
        self.value = self.entries[index] if index < len(self.entries) else ""
        self.cursor_position = len(self.value)

    def action_older(self) -> None:
        if self.index > 0:
            self._show(self.index - 1)

    def action_newer(self) -> None:
        if self.index < len(self.entries):
            self._show(self.index + 1)


class Chat(App):
    """Chat with the agent; it answers in a worker thread."""

    CSS = """
    #log { height: 1fr; padding: 0 1; }
    #log > * { margin-bottom: 1; }
    .user { color: $accent; text-style: bold; }
    .reasoning { color: $text-muted; text-style: italic; }
    .info { color: $text-muted; }
    .error { color: $error; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [("escape", "stop", "stop answer")]

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self.agent = agent
        self.busy = False
        # markdown block being streamed into, and its css classes ("" text, "reasoning")
        self.stream: MarkdownStream | None = None
        self.stream_classes = ""
        # last tool panel, updated in place with the result
        self.tool_block: Static | None = None
        self.tool_panel: Panel | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="log")
        yield PromptInput(self.agent.config.history)
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#log").anchor()
        self.query_one(PromptInput).focus()
        self.update_status()

    def on_unmount(self) -> None:
        # let a running answer end so the worker thread doesn't hold up exit
        self.agent.stop()

    def update_status(self) -> None:
        """Show model, thinking level and whether an answer is running."""
        busy = " · working, esc to stop" if self.busy else ""
        self.query_one("#status", Static).update(f"{self.agent.model.name} · thinking {self.agent.thinking}{busy}")

    async def add(self, widget: Widget) -> Widget:
        """Append a block to the log."""
        await self.query_one("#log").mount(widget)
        return widget

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.clear()
        if not line:
            return
        event.input.remember(line)
        if self.busy:
            self.notify("still answering: esc to stop, ctrl+q to quit")
        elif line.startswith("/"):
            reply, action = commands.run(self.agent, line)
            if action == "quit":
                self.exit()
                return
            if action == "clear":
                await self.query_one("#log").remove_children()
            await self.add(Static(Text(reply), classes="info"))
            self.update_status()
        else:
            await self.add(Static(Text(f"> {line}"), classes="user"))
            self.answer(line)

    def action_stop(self) -> None:
        self.agent.stop()

    @work(thread=True)
    def answer(self, line: str) -> None:
        """Run the agent off the event loop, forwarding its events to the ui."""
        self.call_from_thread(self.set_busy, True)
        try:
            self.agent.run(line, ThreadEvents(self))
        except ApiError as e:
            self.call_from_thread(self.show_error, str(e))
        finally:
            self.call_from_thread(self.set_busy, False)

    async def set_busy(self, busy: bool) -> None:
        await self.end_stream()
        self.busy = busy
        self.update_status()

    async def show_error(self, message: str) -> None:
        await self.end_stream()
        await self.add(Static(Text(f"error: {message}"), classes="error"))

    async def end_stream(self) -> None:
        """Finish the markdown block being streamed into."""
        if self.stream:
            await self.stream.stop()
            self.stream = None

    async def write(self, text: str, classes: str = "") -> None:
        """Stream text into the current markdown block, starting a new one when the kind changes."""
        if not self.stream or self.stream_classes != classes:
            await self.end_stream()
            self.stream = Markdown.get_stream(await self.add(Markdown(classes=classes)))
            self.stream_classes = classes
        await self.stream.write(text)

    async def show_tool_call(self, name: str, arguments: str) -> None:
        await self.end_stream()
        body = Text(_shown_args(arguments), no_wrap=True, overflow="ellipsis")
        self.tool_panel = Panel(body, title=f"→ {name}", title_align="left")
        self.tool_block = await self.add(Static(self.tool_panel))

    def show_tool_result(self, result: str) -> None:
        self.tool_panel.renderable.append("\n" + _preview(result), style="dim")
        self.tool_block.update(self.tool_panel)


class ThreadEvents:
    """Agent events from the worker thread, run on the ui thread."""

    def __init__(self, app: Chat) -> None:
        self.app = app

    def text(self, text: str) -> None:
        self.app.call_from_thread(self.app.write, text)

    def reasoning(self, text: str) -> None:
        self.app.call_from_thread(self.app.write, text, "reasoning")

    def tool_call(self, name: str, arguments: str) -> None:
        self.app.call_from_thread(self.app.show_tool_call, name, arguments)

    def tool_result(self, result: str) -> None:
        self.app.call_from_thread(self.app.show_tool_result, result)
