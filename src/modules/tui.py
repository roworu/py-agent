"""Full-screen chat UI: message log, input with history, status line."""

import asyncio
import json
import re
import time
from pathlib import Path

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Markdown, Static, TextArea
from textual.widgets.markdown import MarkdownStream

from . import commands, files
from .agent import Agent
from .llm import ApiError

HISTORY_LINES = 1000
# files in the @ menu are listed again after this many seconds
LIST_TTL = 10


def _shown_args(arguments: str) -> str:
    try:
        return "\n".join(f"{k}={v!r}" for k, v in json.loads(arguments or "{}").items())
    except (ValueError, AttributeError):
        return arguments


def _history_entry(line: str) -> str:
    try:
        entry = json.loads(line)
    except ValueError:
        return line
    return entry if isinstance(entry, str) else line


def _preview(text: str, lines: int = 4) -> str:
    rows = text.splitlines()
    more = f"\n... {len(rows) - lines} more lines" if len(rows) > lines else ""
    return "\n".join(rows[:lines]) + more


class PromptInput(TextArea):
    """Multi-line input like claude code: enter sends, shift+enter, ctrl+j or \\ then enter add a line; up/down walk history from the first/last line.

    Typing @ opens a menu of files under the input: up/down select, tab or enter insert, esc closes."""

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, history: Path) -> None:
        super().__init__(placeholder="message, /help, esc stops", highlight_cursor_line=False)
        self.history_file = history
        lines = history.read_text().splitlines() if history.is_file() else []
        # stored as json so multi-line entries fit on one line; plain lines are from older versions
        self.entries: list[str] = [_history_entry(line) for line in lines[-HISTORY_LINES:]]
        self.index = len(self.entries)
        # (name, description) rows of the menu under the input, and the selected one
        self.items: list[tuple[str, str]] = []
        self.selected = 0
        # files for the @ menu, and when they were listed
        self.files: list[str] = []
        self.listed = 0.0

    def remember(self, text: str) -> None:
        """Add an entry to the history and its file, skipping repeats."""
        if not self.entries or self.entries[-1] != text:
            self.entries.append(text)
            with self.history_file.open("a") as f:
                f.write(json.dumps(text) + "\n")
        self.index = len(self.entries)

    def update_suggestion(self) -> None:
        # ghost text completing a slash command, accepted with right
        match = next((n for n in commands.NAMES if n.startswith(self.text) and n != self.text), "")
        self.suggestion = match[len(self.text):] if self.text.startswith("/") else ""

    def on_text_area_changed(self) -> None:
        self.refresh_menu()

    def on_text_area_selection_changed(self) -> None:
        self.refresh_menu()

    def mention(self) -> str | None:
        """The @word being typed at the cursor, without the @."""
        row, col = self.cursor_location
        found = re.search(r"(?:^|\s)@(\S*)$", self.document.get_line(row)[:col])
        return found.group(1) if found else None

    def menu_items(self) -> list[tuple[str, str]]:
        """Rows for the menu: files matching the @word at the cursor; subclasses can add their own."""
        if (query := self.mention()) is None:
            return []
        if time.monotonic() - self.listed > LIST_TTL:
            self.files, self.listed = files.list_files(self.app.agent.cwd), time.monotonic()
        return [(f, "") for f in files.match(self.files, query)]

    def refresh_menu(self) -> None:
        self.items = self.menu_items()[:files.MENU_ROWS]
        self.selected = min(self.selected, max(len(self.items) - 1, 0))
        self.draw_menu()

    def draw_menu(self) -> None:
        """Show the menu rows, the selected one highlighted, or hide the menu when empty."""
        rows = Text("\n").join(Text.assemble((name, "reverse" if i == self.selected else ""), ("  " + text, "dim"))
                               for i, (name, text) in enumerate(self.items))
        for menu in self.app.query("#menu"):
            menu.display = bool(self.items)
            menu.update(rows)

    def accept(self, submit: bool) -> None:
        """Insert the selected row: replace the @word with @path; submit is True when picked with enter."""
        name = self.items[self.selected][0]
        self.items = []
        row, col = self.cursor_location
        self.replace(f"@{name} ", (row, col - len(self.mention()) - 1), (row, col))
        self.draw_menu()

    async def _on_key(self, event: events.Key) -> None:
        if self.items and event.key in ("tab", "enter", "escape"):
            event.stop()
            event.prevent_default()
            if event.key == "escape":
                self.items = []
                self.draw_menu()
            else:
                self.accept(submit=event.key == "enter")
        elif event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            row, col = self.cursor_location
            # backslash then enter works in terminals that send shift+enter as plain enter
            if col and self.document.get_line(row)[col - 1] == "\\":
                self.replace("\n", (row, col - 1), (row, col))
            else:
                self.post_message(self.Submitted(self.text))
        else:
            await super()._on_key(event)

    def _show(self, index: int) -> None:
        self.index = index
        self.text = self.entries[index] if index < len(self.entries) else ""
        self.move_cursor(self.document.end)

    def action_cursor_up(self, select: bool = False) -> None:
        if self.items and not select:
            self.selected = (self.selected - 1) % len(self.items)
            self.draw_menu()
        elif self.cursor_location[0] == 0 and not select and self.index > 0:
            self._show(self.index - 1)
        else:
            super().action_cursor_up(select)

    def action_cursor_down(self, select: bool = False) -> None:
        if self.items and not select:
            self.selected = (self.selected + 1) % len(self.items)
            self.draw_menu()
        elif self.cursor_location[0] == self.document.line_count - 1 and not select and self.index < len(self.entries):
            self._show(self.index + 1)
        else:
            super().action_cursor_down(select)


class Picker(Static, can_focus=True):
    """A question with options, picked with up/down and enter or a number; esc gives None."""

    BINDINGS = [("up", "move(-1)"), ("down", "move(1)"), ("enter", "pick"), ("escape", "cancel"),
                *((str(n), f"pick({n - 1})") for n in range(1, 10))]

    def __init__(self, question: str, options: list[str], body, future: asyncio.Future) -> None:
        super().__init__()
        self.question, self.options, self.body, self.future = question, options, body, future
        self.selected = 0
        self.draw()

    def draw(self) -> None:
        """Show the question, body and options with the selected one marked."""
        rows = Text("\n").join(Text(f"{'❯' if i == self.selected else ' '} {i + 1}. {option}",
                                     "bold" if i == self.selected else "") for i, option in enumerate(self.options))
        self.update(Group(Text(self.question, "bold"), *([self.body] if self.body else []), rows))

    def resolve(self, choice: int | None) -> None:
        if not self.future.done():
            self.future.set_result(choice)

    def action_move(self, step: int) -> None:
        self.selected = (self.selected + step) % len(self.options)
        self.draw()

    def action_pick(self, choice: int | None = None) -> None:
        if choice is None or choice < len(self.options):
            self.resolve(self.selected if choice is None else choice)

    def action_cancel(self) -> None:
        self.resolve(None)


class Chat(App):
    """Chat with the agent; it answers in a worker thread."""

    CSS = """
    #log { height: 1fr; padding: 0 1; }
    #log > * { margin-bottom: 1; }
    .user { color: $accent; text-style: bold; }
    .reasoning { color: $text-muted; text-style: italic; }
    .info { color: $text-muted; }
    .error { color: $error; }
    PromptInput { height: auto; max-height: 10; }
    #menu { height: auto; padding: 0 1; display: none; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [("escape", "stop", "stop answer")]
    # input and picker widget classes, so plugins can swap in subclasses
    Input = PromptInput
    Picker = Picker

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self.agent = agent
        self.busy = False
        # messages sent while busy, run in order once the answer ends
        self.queue: list[str] = []
        # markdown block being streamed into, and its css classes ("" text, "reasoning")
        self.stream: MarkdownStream | None = None
        self.stream_classes = ""
        # tool call id -> its block and panel, updated in place with the result
        self.tool_blocks: dict[str, tuple[Static, Panel]] = {}
        # the open question, if any
        self.picker: Picker | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="log")
        yield self.Input(self.agent.config.history)
        yield Static(id="menu")
        yield Static(id="status")

    async def on_mount(self) -> None:
        self.query_one("#log").anchor()
        self.query_one(PromptInput).focus()
        self.update_status()
        # a resumed session (py-agent -c) is shown right away
        await self.replay()

    def on_unmount(self) -> None:
        # let a running answer end so the worker thread doesn't hold up exit
        self.agent.stop()
        if self.picker:
            self.picker.resolve(None)

    def update_status(self) -> None:
        """Show model, thinking level and whether an answer is running."""
        busy = " · working, esc to stop" if self.busy else ""
        queued = f" · {len(self.queue)} queued" if self.queue else ""
        self.query_one("#status", Static).update(f"{self.agent.model.name} · thinking {self.agent.thinking}{busy}{queued}")

    async def add(self, widget: Widget) -> Widget:
        """Append a block to the log."""
        await self.query_one("#log").mount(widget)
        return widget

    async def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        line = event.value.strip()
        input = self.query_one(PromptInput)
        input.clear()
        if not line:
            return
        input.remember(line)
        if self.busy:
            self.queue.append(line)
            self.update_status()
        else:
            await self.send(line)

    async def send(self, line: str) -> None:
        """Run a slash command or start answering a message."""
        if line.startswith("/"):
            reply, action = commands.run(self.agent, line)
            if action == "quit":
                self.exit()
                return
            if action == "send":
                # a command that writes a prompt for the model, like /init; the command is shown, also on replay
                await self.start({"role": "user", "content": reply, "_display": line})
                return
            if action == "compact":
                await self.add(Static(Text("compacting, esc to stop"), classes="info"))
                await self.set_busy(True)
                self.answer(reply, compact=True)
                return
            if action in ("clear", "replay", "undo"):
                await self.query_one("#log").remove_children()
            if action in ("replay", "undo"):
                await self.replay()
            if action == "undo":
                # like opencode, the undone message goes back into the input to edit and resend
                self.query_one(PromptInput).text = reply
                reply = "undone"
            await self.add(Static(Text(reply), classes="info"))
            self.update_status()
        else:
            await self.start(files.message(self.agent, line))

    async def start(self, message: dict) -> None:
        """Show a user message and start answering it."""
        await self.show_user(message)
        await self.set_busy(True)
        self.answer(message)

    async def show_user(self, message: dict) -> None:
        """Show a user message in the log, when sent and on replay; "_display" replaces the content."""
        await self.add(Static(Text(f"> {message.get('_display', message['content'])}"), classes="user"))

    async def show_answer(self, message: dict) -> None:
        """Show a saved assistant message in the log on replay; live answers stream through write."""
        await self.add(Markdown(message["content"]))

    async def ask(self, question: str, options: list[str], body=None) -> int | None:
        """Show a question above the input and wait for the picked option's index; None on esc."""
        future = asyncio.get_running_loop().create_future()
        await self.end_stream()
        self.picker = self.Picker(question, options, body, future)
        await self.mount(self.picker, before=self.query_one(PromptInput))
        self.picker.focus()
        try:
            return await future
        finally:
            await self.picker.remove()
            self.picker = None
            # the app may be closing, then the input is gone
            for input in self.query(PromptInput):
                input.focus()

    async def send_queued(self) -> None:
        """Send queued messages until one starts an answer."""
        while self.queue and not self.busy:
            await self.send(self.queue.pop(0))
            self.update_status()

    async def replay(self) -> None:
        """Show the agent's conversation in the log, after a resume or undo."""
        results = {m["tool_call_id"]: m["content"] for m in self.agent.messages if m["role"] == "tool"}
        for m in self.agent.messages[1:]:
            if m["role"] == "user":
                await self.show_user(m)
            elif m["role"] == "assistant":
                if m.get("content"):
                    await self.show_answer(m)
                for call in m.get("tool_calls", []):
                    await self.show_tool_call(call["id"], call["function"]["name"], call["function"]["arguments"])
                    self.show_tool_result(call["id"], results.get(call["id"], ""))

    def action_stop(self) -> None:
        self.agent.stop()
        # like claude code, esc hands queued messages back to the input instead of sending them
        if self.queue:
            input = self.query_one(PromptInput)
            input.text = "\n".join([*self.queue, input.text]).strip()
            self.queue.clear()
            self.update_status()

    @work(thread=True)
    def answer(self, message: str | dict, compact: bool = False) -> None:
        """Run the agent off the event loop, forwarding its events to the ui; with compact, message is the /compact text."""
        try:
            if compact:
                self.call_from_thread(self.compacted, self.agent.compact(message))
            elif self.agent.run(message, ThreadEvents(self)) == "stopped":
                self.call_from_thread(self.show_stopped)
        except ApiError as e:
            self.call_from_thread(self.show_error, str(e))
        finally:
            self.call_from_thread(self.set_busy, False)
            self.call_from_thread(self.send_queued)

    async def compacted(self, done: bool) -> None:
        """Show the summary that replaced the conversation."""
        if done:
            await self.query_one("#log").remove_children()
            await self.replay()
        await self.add(Static(Text("compacted" if done else "not compacted"), classes="info"))

    async def set_busy(self, busy: bool) -> None:
        await self.end_stream()
        self.busy = busy
        self.update_status()

    async def show_error(self, message: str) -> None:
        await self.end_stream()
        await self.add(Static(Text(f"error: {message}"), classes="error"))

    async def show_retry(self, delay: float, error: str) -> None:
        """Show that a failed request is retried."""
        await self.end_stream()
        await self.add(Static(Text(f"retrying in {delay:g}s: {error}"), classes="info"))

    async def show_auto_compacted(self) -> None:
        """Show that the full context was compacted during an answer; the log above stays as it was."""
        await self.end_stream()
        await self.add(Static(Text("context nearly full, compacted the conversation"), classes="info"))

    async def show_stopped(self) -> None:
        """Show that the answer was interrupted with esc."""
        await self.end_stream()
        await self.add(Static(Text("interrupted"), classes="info"))

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

    async def show_tool_call(self, id: str, name: str, arguments: str) -> None:
        await self.end_stream()
        body = Text(_shown_args(arguments), no_wrap=True, overflow="ellipsis")
        panel = Panel(body, title=f"→ {name}", title_align="left")
        self.tool_blocks[id] = (await self.add(Static(panel)), panel)

    def show_tool_result(self, id: str, result: str) -> None:
        block, panel = self.tool_blocks.pop(id)
        panel.renderable.append("\n" + _preview(result), style="dim")
        block.update(panel)


class ThreadEvents:
    """Agent events from the worker thread, run on the ui thread."""

    def __init__(self, app: Chat) -> None:
        self.app = app

    def text(self, text: str) -> None:
        self.app.call_from_thread(self.app.write, text)

    def reasoning(self, text: str) -> None:
        self.app.call_from_thread(self.app.write, text, "reasoning")

    def tool_call(self, id: str, name: str, arguments: str) -> None:
        self.app.call_from_thread(self.app.show_tool_call, id, name, arguments)

    def tool_result(self, id: str, result: str) -> None:
        self.app.call_from_thread(self.app.show_tool_result, id, result)

    def retry(self, delay: float, error: str) -> None:
        self.app.call_from_thread(self.app.show_retry, delay, error)

    def compacted(self) -> None:
        self.app.call_from_thread(self.app.show_auto_compacted)

    def ask(self, question: str, options: list[str], body=None) -> int | None:
        # waits in the worker thread for the user's pick in the ui
        return self.app.call_from_thread(self.app.ask, question, options, body)

