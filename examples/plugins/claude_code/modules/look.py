"""Look: welcome box, ⏺ answers, tool blocks, transcript mode (ctrl+o), spinner, todo list and status line."""

import random
import time
from pathlib import Path

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Markdown, Static

from . import render
from .modes import MODE_LINES

GLYPHS = "·✢✳✶✻✽✻✶✳✢"
VERBS = ["Pondering", "Thinking", "Noodling", "Percolating", "Brewing", "Musing", "Cogitating", "Simmering"]


class LookMixin:
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.started, self.verb, self.streamed = 0.0, VERBS[0], 0
        # transcript mode, everything in full
        self.expanded = False
        # tool call id -> its block
        self.tool_calls: dict[str, Static] = {}
        # reasoning block being streamed into, and the model's latest todo list
        self.thought: Static | None = None
        self.todos: list = []

    async def on_mount(self) -> None:
        input = self.query_one(self.Input)
        await self.mount(Static(id="spinner"), Static(id="todos"), before=input)
        self.query_one("#todos").display = False
        input.placeholder = 'Try "explain this codebase"'
        self.set_interval(0.1, self.tick)
        if len(self.agent.messages) == 1:
            await self.add(Static(render.welcome(self.agent.cwd)))

    # spinner, todos and status

    def tick(self) -> None:
        """Animate the spinner line while busy; it names the todo in progress when there is one."""
        spinner = self.query_one("#spinner", Static)
        # answers to approvals and plans change the mode from the worker thread
        self.update_status()
        if not self.busy:
            spinner.update("")
            return
        glyph = GLYPHS[int(time.monotonic() * 8) % len(GLYPHS)]
        current = next((t.get("content") for t in self.todos if isinstance(t, dict) and t.get("status") == "in_progress"), None)
        # ~4 chars per token, real counts only arrive with the last chunk
        n = self.streamed // 4
        tokens = f" · ↓ {n / 1000:.1f}k tokens" if n >= 1000 else f" · ↓ {n} tokens" if n else ""
        spinner.update(f"{glyph} {current or self.verb}… ({time.monotonic() - self.started:.0f}s{tokens} · esc to interrupt)")

    def draw_todos(self) -> None:
        """The todo list under the spinner while working and something is left."""
        left = any(isinstance(t, dict) and t.get("status") != "completed" for t in self.todos)
        block = self.query_one("#todos", Static)
        block.display = self.busy and left
        block.update(render.todos(self.todos, "  ")[1:] if block.display else "")

    def update_status(self) -> None:
        queued = f" · {len(self.queue)} queued" if self.queue else ""
        self.query_one("#status", Static).update(f"{MODE_LINES[self.agent.mode]} · {self.agent.model.name}{queued}"
                                                 f" · context left until auto-compact: {self.context_left():.0%}")

    async def set_busy(self, busy: bool) -> None:
        if busy and not self.busy:
            self.started, self.verb, self.streamed = time.monotonic(), random.choice(VERBS), 0
        await super().set_busy(busy)
        self.draw_todos()

    async def show_stopped(self) -> None:
        await self.end_stream()
        await self.add(Static("  ⎿  Interrupted · What should py-agent do instead?", classes="interrupted"))

    # log

    async def add(self, widget):
        # answers get a ⏺ in front, the rest is shown as is
        if isinstance(widget, Markdown) and not widget.classes:
            await super().add(Horizontal(Static("⏺", classes="dot"), widget, classes="answer"))
            return widget
        return await super().add(widget)

    async def show_user(self, message: dict) -> None:
        if message.get("_kind") == "compact":
            await self.add(Static(Text.assemble(("✻ ", render.ACCENT), "Conversation compacted · summary below")))
        else:
            await super().show_user(message)

    async def write(self, text: str, classes: str = "") -> None:
        self.streamed += len(text)
        if classes != "reasoning":
            self.thought = None
            await super().write(text, classes)
            return
        # reasoning collapses into one line, ctrl+o shows it
        if not self.thought:
            await self.end_stream()
            self.thought = await self.add(Static(classes="thought"))
            self.thought.reasoning = ""
        self.thought.reasoning += text
        self.draw_thought(self.thought)

    def draw_thought(self, block: Static) -> None:
        text = f"✻ Thinking…\n{block.reasoning.strip()}" if self.expanded else "✻ Thinking… (ctrl+o to expand)"
        block.update(Text(text))

    def action_transcript(self) -> None:
        """ctrl+o: show or hide reasoning and full tool output everywhere."""
        self.expanded = not self.expanded
        for block in self.query(".thought"):
            self.draw_thought(block)
        for block in self.query(".tool"):
            self.draw_tool(block)

    # tools

    async def show_tool_call(self, id: str, name: str, arguments: str) -> None:
        await self.end_stream()
        self.thought = None
        block = self.tool_calls[id] = await self.add(Static(classes="tool"))
        block.call = {"name": name, "args": render.parse_args(arguments), "output": None, "start": None}
        if name == "todo_write":
            self.todos = block.call["args"].get("todos") or []
            self.draw_todos()
        self.draw_tool(block)

    def show_tool_result(self, id: str, result: str) -> None:
        block = self.tool_calls.pop(id)
        call = block.call
        call["output"] = result
        if call["name"] == "edit" and not result.startswith("Error:"):
            path = (self.agent.cwd / Path(str(call["args"].get("path", ""))).expanduser()).resolve()
            call["start"] = render.edit_start(path, str(call["args"].get("newText", "")))
        self.draw_tool(block)

    def draw_tool(self, block: Static) -> None:
        call = block.call
        block.update(render.tool(call["name"], call["args"], call["output"], self.expanded, call["start"]))
