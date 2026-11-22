"""The opencode look and keys: home screen with the logo, header, sidebar, messages, tool blocks, the prompt box with
the agent and model, footer, ctrl+x leader keys, dialogs, shell mode, session titles, editor and export."""

import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Event

from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import SuspendNotSupported
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Static

from . import render, state
from .dialog import SelectDialog
from .slash import UI

THEME = Theme(name="py-agent", primary="#fab283", secondary="#5c9cf5", accent="#9d7cd8", success="#7fd88f",
              warning="#f5a742", error="#e06c75", foreground="#eeeeee", background="#0a0a0a", surface="#141414",
              panel="#1e1e1e", dark=True)
# key after ctrl+x -> command
LEADER = {"n": "/new", "l": "/sessions", "m": "/models", "a": "/agents", "t": "/themes", "u": "/undo", "r": "/redo",
          "c": "/compact", "e": "/editor", "x": "/export", "y": "/copy", "d": "/details", "i": "/init",
          "h": "/help", "q": "/exit", "b": "sidebar"}
KEYS = {command: f"ctrl+x {key}" for key, command in LEADER.items()} | {"/help": "ctrl+p", "/agents": "tab"}
LEADER_TIMEOUT = 2.0
HOME = [("/new", "new session"), ("/sessions", "list sessions"), ("/models", "switch model"),
        ("/agents", "switch agent"), ("/help", "all commands")]
TITLE_PROMPT = ("Write a title of at most 50 characters for a conversation that starts with the message below. "
                "Reply with the title only, no quotes or punctuation at the end.\n\n")
# commands that only open a dialog or toggle something, so they also run while the model works
QUIET = {*UI, "sidebar"}


class OpenCodeMixin:
    # set by main.py
    api = None
    specs: list = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.state = state.load(self.agent.config)
        self.details: bool = self.state.get("details", True)
        self.thinking_shown: bool = self.state.get("thinking", True)
        # None shows the sidebar on wide terminals
        self.sidebar_shown: bool | None = None
        self.leader_at, self.last_esc, self.frame = 0.0, 0.0, 0
        # start, agent and interruption of the running answer, for the line under it
        self.turn_started, self.turn_agent, self.interrupted = 0.0, "build", False
        self.branch = ""
        self.changes: list[tuple[str, int, int]] = []
        # tool call id -> its block; the running task block; a shell command's block
        self.tool_calls: dict[str, Static] = {}
        self.running_task: Static | None = None
        self.shell_block: Static | None = None
        self._colors, self._colors_theme = {}, None
        # widget id -> key of what it shows, so the timer only redraws what changed
        self.drawn: dict[str, object] = {}
        # set on exit, so a title request still waiting doesn't hold it up
        self.closing = Event()

    def on_unmount(self) -> None:
        self.closing.set()

    def compose(self):
        yield Static(id="header")
        yield Static(id="sidebar")
        yield from super().compose()

    async def on_mount(self) -> None:
        self.register_theme(THEME)
        theme = self.state.get("theme", THEME.name)
        self.theme = theme if theme in self.available_themes else THEME.name
        if (name := self.state.get("model")) and (model := self.agent.config.find_model(name)):
            self.agent.set_model(model)
        input = self.query_one(self.Input)
        input.placeholder = 'Ask anything... "Fix a TODO in the codebase"'
        await self.mount(Static(id="logo"), before=self.query_one("#log"))
        await self.mount(Static(id="meta"), after=input)
        await self.mount(Static(id="hints"), after=self.query_one("#meta"))
        await self.mount(Static(id="tips"), after=self.query_one("#hints"))
        # completions open above the prompt
        self.screen.move_child(self.query_one("#menu"), before=input)
        self.theme_changed_signal.subscribe(self, lambda theme: self.redraw())
        self.refresh_git()
        self.redraw()
        self.set_interval(0.08, self.tick)

    @property
    def c(self) -> dict[str, str]:
        """The theme's colors, worked out again after /themes."""
        if self._colors_theme != self.theme:
            self._colors, self._colors_theme = render.colors(self), self.theme
        return self._colors

    @property
    def primaries(self) -> list[str]:
        return [s.name for s in self.specs if s.primary]

    @property
    def subagents(self) -> list[str]:
        return [s.name for s in self.specs if s.subagent]

    def agent_color(self, name: str | None = None) -> str:
        names = self.primaries
        name = name or self.agent.agent_name
        i = names.index(name) if name in names else len(names)
        return self.c[render.AGENT_COLORS[i % len(render.AGENT_COLORS)]]

    def put(self, selector: str, key, make) -> None:
        """Update a widget with make() when key changed since the last time."""
        if self.drawn.get(selector) != key:
            self.drawn[selector] = key
            self.query_one(selector, Static).update(make())

    def redraw(self) -> None:
        """Draw everything again, after a theme change or a toggle."""
        self.drawn.clear()
        self.query_one("#logo", Static).update(render.logo(self.c))
        tips = Table.grid(padding=(0, 2))
        for name, text in HOME:
            tips.add_row(Text(name, self.c["foreground"]), Text(text, self.c["muted"]), Text(KEYS.get(name, ""), self.c["faint"]))
        self.query_one("#tips", Static).update(tips)
        for block in self.query(".tool"):
            self.draw_tool(block)
        self.tick()

    # what changes while running

    def tick(self) -> None:
        """Layout classes, prompt box, hints, header, sidebar and footer."""
        self.frame += 1
        screen = self.screen
        if isinstance(screen, ModalScreen):
            return
        home = len(self.agent.messages) == 1 and not self.busy
        wide = self.size.width >= 120
        screen.set_class(home, "home")
        screen.set_class(not home and (wide if self.sidebar_shown is None else self.sidebar_shown), "with-sidebar")
        screen.set_class(not self.thinking_shown, "hide-thinking")
        self.draw_prompt()
        self.draw_header()
        self.update_status()

    def tokens(self) -> int:
        usage = self.agent.usage or {}
        return usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)

    def session_title(self) -> str:
        """The session's generated title, else its first message."""
        if title := self.state.get("titles", {}).get(self.agent.session.stem):
            return title
        first = next((m for m in self.agent.messages[1:] if m["role"] == "user"), None)
        return first.get("_display", first["content"]).splitlines()[0][:60] if first else "New session"

    def draw_prompt(self) -> None:
        input, c = self.query_one(self.Input), self.c
        color = self.agent_color()
        shell = input.text.startswith("!")
        label = "Shell" if shell else self.agent.agent_name.capitalize()
        model, thinking = self.agent.model, self.agent.thinking

        def meta() -> Text:
            line = Text.assemble((label, f"bold {color}"), "  ", (model.id, c["foreground"]), " ", (model.provider, c["muted"]))
            if thinking != "off":
                line.append(f" · {thinking}", c["warning"])
            return line

        if self.drawn.get("color") != color:
            self.drawn["color"] = color
            for widget in (input, self.query_one("#meta")):
                widget.styles.border_left = ("thick", color)
        self.put("#meta", (label, model.name, thinking, color), meta)
        again = time.monotonic() - self.last_esc < 1.5
        busy = self.busy and not self.picker

        def hints() -> Table:
            grid = Table.grid(expand=True)
            grid.add_column()
            grid.add_column(justify="right")
            left = Text()
            if busy:
                left = render.spinner(self.frame, color, c)
                left.append("  esc ", c["foreground"]).append("again to interrupt" if again else "interrupt", c["muted"])
            right = Text.assemble(("tab ", c["foreground"]), ("switch agent  ", c["muted"]),
                                  ("ctrl+p ", c["foreground"]), ("commands", c["muted"]))
            grid.add_row(left, right)
            return grid

        self.put("#hints", (busy, self.frame if busy else 0, again, color, self.size.width), hints)

    def draw_header(self) -> None:
        if self.screen.has_class("home"):
            return
        tokens, limit = self.tokens(), self.agent.model.context_window
        title, c = self.session_title(), self.c

        def header() -> Table:
            grid = Table.grid(expand=True)
            grid.add_column()
            grid.add_column(justify="right")
            right = f"{tokens:,}" + (f"  {tokens / limit:.0%}" if limit else "")
            grid.add_row(Text.assemble(("# ", c["muted"]), (title, f"bold {c['foreground']}")), Text(right, c["muted"]))
            return grid

        self.put("#header", (title, tokens, self.size.width, self.theme), header)
        if self.screen.has_class("with-sidebar"):
            todos = getattr(self.agent, "todos", [])
            self.put("#sidebar", (title, tokens, json.dumps(todos), tuple(self.changes), self.theme),
                     lambda: render.sidebar(title, tokens, limit, todos, self.changes, c))

    def update_status(self) -> None:
        """Folder and git branch on the left; the leader keys or queued messages on the right."""
        c = self.c
        left = Text(str(self.agent.cwd).replace(str(Path.home()), "~", 1), c["muted"])
        if self.branch:
            left.append(f":{self.branch}", c["muted"])
        right = Text()
        if self.leader_pending():
            right.append("ctrl+x ", f"bold {c['primary']}")
            right.append("n new · l sessions · m models · u undo · r redo · c compact · b sidebar · h help", c["muted"])
        elif self.queue:
            right.append(f"{len(self.queue)} queued", c["warning"])

        def status() -> Table:
            grid = Table.grid(expand=True)
            grid.add_column()
            grid.add_column(justify="right")
            grid.add_row(left, right)
            return grid

        self.put("#status", (str(left), str(right), self.size.width, self.theme), status)

    def refresh_git(self) -> None:
        try:
            out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.agent.cwd,
                                 capture_output=True, text=True, timeout=2)
            self.branch = out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            self.branch = ""

    def refresh_changes(self) -> None:
        """Files changed since the first turn of this session, for the sidebar."""
        turns = [t for t in self.agent.turns if t[1] and t[2]]
        try:
            self.changes = self.api.snapshot.changes(self.agent.snapshots, self.agent.cwd, turns[0][1], turns[-1][2]) if turns else []
        except (OSError, subprocess.CalledProcessError):
            self.changes = []

    # messages

    async def show_user(self, message: dict) -> None:
        kind, c = message.get("_kind"), self.c
        if kind == "compact":
            await self.add(Static(Text.assemble(("▣ ", c["primary"]), ("Compaction · summary below", c["muted"])), classes="footer"))
            return
        if kind == "shell":
            block = self.shell_block = await self.add(Static(classes="tool"))
            block.call = {"name": "bash", "args": {"command": message["_display"][1:].strip()}, "output": None,
                          "start": None, "steps": []}
            self.draw_tool(block)
            return
        text = Text(message.get("_display", message["content"]))
        for i, (path, contents) in enumerate(self.api.files.attached(message["content"])):
            text.append("\n\n" if i == 0 else " ").append(f" {path} ", f"{c['background']} on {c['secondary']}")
        block = await self.add(Static(text, classes="user"))
        block.styles.border_left = ("thick", self.agent_color())

    async def show_answer(self, message: dict) -> None:
        if message.get("_kind") == "shell" and self.shell_block:
            self.shell_block.call["output"] = message["content"].removeprefix("Output:\n")
            self.draw_tool(self.shell_block)
            self.shell_block = None
            return
        await super().show_answer(message)

    async def write(self, text: str, classes: str = "") -> None:
        # a new reasoning block starts with a label, like opencode's "Thinking:"
        if classes == "reasoning" and (not self.stream or self.stream_classes != "reasoning"):
            await super().write("_Thinking:_ ", classes)
        await super().write(text, classes)

    async def start(self, message: dict) -> None:
        shown = message.get("_display", message["content"])
        # @general in a message hands it to that subagent
        if names := [n for n in self.subagents if re.search(rf"(?:^|\s)@{re.escape(n)}\b", shown)]:
            note = f"\n\nUse the task tool with subagent_type {names[0]} for this."
            message = {**message, "content": message["content"] + note, "_display": shown}
        self.turn_started, self.turn_agent, self.interrupted = time.monotonic(), self.agent.agent_name, False
        await super().start(message)

    async def show_stopped(self) -> None:
        await self.end_stream()
        self.interrupted = True

    async def set_busy(self, busy: bool) -> None:
        was = self.busy
        await super().set_busy(busy)
        if was and not busy and self.turn_started:
            seconds, self.turn_started = time.monotonic() - self.turn_started, 0.0
            c = self.c
            line = Text.assemble(("▣ ", self.agent_color(self.turn_agent)),
                                 (f"{self.turn_agent.capitalize()} · {self.agent.model.id} · {seconds:.1f}s", c["muted"]))
            if self.interrupted:
                line.append(" · interrupted", c["error"])
            await self.add(Static(line, classes="footer"))
            self.refresh_changes()
            self.refresh_git()
            self.make_title()
        self.tick()

    # tools

    async def show_tool_call(self, id: str, name: str, arguments: str) -> None:
        await self.end_stream()
        block = self.tool_calls[id] = await self.add(Static(classes="tool"))
        block.call = {"name": name, "args": render.parse_args(arguments), "output": None, "start": None, "steps": []}
        if name == "task":
            self.running_task = block
        self.draw_tool(block)

    def show_tool_result(self, id: str, result: str) -> None:
        if not (block := self.tool_calls.pop(id, None)):
            return
        call = block.call
        call["output"] = result
        if call["name"] == "edit" and not result.startswith("Error:"):
            path = (self.agent.cwd / Path(str(call["args"].get("path", ""))).expanduser()).resolve()
            call["start"] = render.edit_start(path, str(call["args"].get("newText", "")))
        if block is self.running_task:
            self.running_task = None
        self.draw_tool(block)

    def task_step(self, name: str, arguments: str) -> None:
        """A tool call of the running subagent, listed under its task."""
        if block := self.running_task:
            block.call["steps"].append((name, render.parse_args(arguments)))
            self.draw_tool(block)

    def draw_tool(self, block: Static) -> None:
        call = block.call
        block.update(render.tool(call["name"], call["args"], call["output"], self.details, self.agent.cwd, self.c,
                                 call["start"], call["steps"]))

    # shell mode

    async def send(self, line: str) -> None:
        if line.startswith("!") and line[1:].strip():
            message = {"role": "user", "content": f"I ran this shell command:\n$ {line[1:].strip()}", "_kind": "shell",
                       "_display": line}
            await self.show_user(message)
            await self.set_busy(True)
            self.run_shell(message)
        else:
            await super().send(line)

    @work(thread=True)
    def run_shell(self, message: dict) -> None:
        """Run a `!command` without the model; it and its output join the conversation."""
        self.agent.stopped.clear()
        try:
            out = self.api.tools.TOOLS["bash"][0](self.agent.cwd, self.agent.stopped, message["_display"][1:].strip())
            # a user/assistant pair, so turns keep alternating for strict chat templates
            answer = {"role": "assistant", "content": f"Output:\n{out}", "_kind": "shell"}
            self.agent.append(message, answer)
            self.call_from_thread(self.show_answer, answer)
        finally:
            self.call_from_thread(self.set_busy, False)
            self.call_from_thread(self.send_queued)

    # keys

    def leader_pending(self) -> bool:
        return time.monotonic() - self.leader_at < LEADER_TIMEOUT

    def action_leader(self) -> None:
        self.leader_at = time.monotonic()
        self.update_status()

    async def run_leader(self, key: str) -> None:
        """The key after ctrl+x."""
        self.leader_at = 0.0
        await self.run_command(LEADER.get(key, ""))

    async def run_command(self, command: str) -> None:
        if command == "sidebar":
            self.sidebar_shown = not self.screen.has_class("with-sidebar")
        elif command and self.busy and command not in QUIET:
            self.notify("wait for the answer to finish, or esc twice to interrupt", timeout=2)
        elif command:
            await self.send(command)
        self.tick()

    def cycle_agent(self, step: int) -> None:
        names = self.primaries
        i = names.index(self.agent.agent_name) if self.agent.agent_name in names else -1
        self.agent.switch(names[(i + step) % len(names)])
        self.tick()

    def action_cycle_thinking(self) -> None:
        levels = ["off", *self.agent.model.efforts]
        self.agent.thinking = levels[(levels.index(self.agent.thinking) + 1) % len(levels)] if self.agent.thinking in levels else "off"
        self.tick()

    def action_palette(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            self.open_commands()

    def action_clear_or_quit(self) -> None:
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss(None)
        elif (input := self.query_one(self.Input)).text:
            input.clear()
        else:
            self.exit()

    def action_scroll_log(self, pages: int) -> None:
        log = self.query_one("#log")
        log.scroll_page_down() if pages > 0 else log.scroll_page_up()

    def action_stop(self) -> None:
        if not self.busy:
            if (input := self.query_one(self.Input)).text.startswith("!"):
                input.clear()
            return
        # like opencode, esc twice interrupts
        now = time.monotonic()
        if now - self.last_esc < 1.5:
            self.last_esc = 0.0
            super().action_stop()
        else:
            self.last_esc = now
        self.tick()

    # commands with a dialog or a toggle; each takes the command's argument

    def open_commands(self, arg: str = "") -> None:
        commands = self.api.commands
        rows = [(None, name, f"{name:<14}{commands.DESCRIPTIONS.get(name, ('', ''))[1]}", KEYS.get(name, ""))
                for name in commands.NAMES]
        self.push_screen(SelectDialog("Commands", rows), self.run_command)

    def open_sessions(self, arg: str = "") -> None:
        titles, rows = self.state.get("titles", {}), []
        for path in sorted(self.agent.sessions.glob("*.jsonl"), reverse=True)[:100]:
            with path.open() as f:
                first = json.loads(f.readline() or "{}")
            label = titles.get(path.stem) or str(first.get("_display", first.get("content", ""))).splitlines()[0][:60]
            rows.append((path.stem[:10], str(path), label, path.stem[11:16].replace("-", ":")))
        if not rows:
            self.notify("no sessions in this folder yet")
            return
        self.push_screen(SelectDialog("Sessions", rows, str(self.agent.session)), self.resume_session)

    async def resume_session(self, value: str | None) -> None:
        if not value or self.busy:
            return
        self.agent.resume(Path(value))
        await self.query_one("#log").remove_children()
        await self.replay()
        self.refresh_changes()
        self.tick()

    def open_models(self, arg: str = "") -> None:
        config = self.agent.config
        recent = [n for n in self.state.get("recent", []) if config.find_model(n)]
        rows = [("Recent", "recent:" + n, n.split("/", 1)[1], n.split("/", 1)[0]) for n in recent]
        rows += [(m.provider, m.name, m.id, "") for m in config.models]
        self.push_screen(SelectDialog("Select model", rows, self.agent.model.name), self.pick_model)

    def pick_model(self, value: str | None) -> None:
        if not value or not (model := self.agent.config.find_model(value.removeprefix("recent:"))):
            return
        self.agent.set_model(model)
        self.state["recent"] = [model.name, *(n for n in self.state.get("recent", []) if n != model.name)][:5]
        self.state["model"] = model.name
        state.save(self.agent.config, self.state)
        self.tick()

    def open_agents(self, arg: str = "") -> None:
        rows = [(None, s.name, s.name, s.description) for s in self.specs if s.primary]
        self.push_screen(SelectDialog("Select agent", rows, self.agent.agent_name), self.pick_agent)

    def pick_agent(self, value: str | None) -> None:
        if value:
            self.agent.switch(value)
            self.tick()

    def open_themes(self, arg: str = "") -> None:
        before = self.theme
        rows = [(None, name, name, "") for name in sorted(self.available_themes)]

        def done(value: str | None) -> None:
            self.theme = value or before
            if value:
                self.state["theme"] = value
                state.save(self.agent.config, self.state)

        # themes are previewed while moving through the list, like opencode
        self.push_screen(SelectDialog("Themes", rows, before, lambda name: setattr(self, "theme", name)), done)

    def edit_file(self, path: Path) -> bool:
        """Open a file in $VISUAL or $EDITOR with the ui suspended; False when there's no editor."""
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if not editor:
            return False
        try:
            with self.suspend():
                subprocess.run([*shlex.split(editor), str(path)])
        except (OSError, SuspendNotSupported) as e:
            self.notify(f"can't open the editor: {e}", severity="error")
            return False
        return True

    def open_editor(self, arg: str = "") -> None:
        input = self.query_one(self.Input)
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(input.text)
        path = Path(f.name)
        try:
            if not self.edit_file(path):
                self.notify("set $EDITOR to write messages in your editor", severity="warning")
                return
            input.text = path.read_text(encoding="utf-8").rstrip()
            input.move_cursor(input.document.end)
        finally:
            path.unlink(missing_ok=True)

    def export(self, arg: str = "") -> None:
        """Write the session as markdown to <config dir>/opencode/exports/ and open it in the editor."""
        lines = [f"# {self.session_title()}", ""]
        for m in self.agent.messages[1:]:
            if m["role"] == "user":
                lines += ["## User", "", str(m.get("_display", m["content"])), ""]
            elif m["role"] == "assistant":
                lines += ["## Assistant", "", m.get("content") or "", ""]
                for call in m.get("tool_calls", []):
                    lines += [f"**{call['function']['name']}**", "", "```json", call["function"]["arguments"], "```", ""]
            elif m["role"] == "tool":
                lines += ["```", m["content"][:2000], "```", ""]
        path = self.agent.config.dir / "opencode" / "exports" / f"{self.agent.session.stem}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        if not self.edit_file(path):
            self.notify(f"exported to {path}")

    def copy_last(self, arg: str = "") -> None:
        answer = next((m["content"] for m in reversed(self.agent.messages) if m["role"] == "assistant" and m.get("content")), "")
        if answer:
            self.copy_to_clipboard(answer)
            self.notify("copied the last answer", timeout=1)

    def toggle_details(self, arg: str = "") -> None:
        self.details = self.state["details"] = not self.details
        state.save(self.agent.config, self.state)
        self.redraw()

    def toggle_thinking(self, arg: str = "") -> None:
        self.thinking_shown = self.state["thinking"] = not self.thinking_shown
        state.save(self.agent.config, self.state)
        self.tick()

    # session titles

    def make_title(self) -> None:
        """Have the model name a new session once its first answer is in."""
        stem = self.agent.session.stem
        if stem in self.state.get("titles", {}) or not self.agent.session.exists():
            return
        first = next((m for m in self.agent.messages[1:] if m["role"] == "user" and not m.get("_kind")), None)
        if first:
            self.generate_title(stem, str(first.get("_display", first["content"])))

    @work(thread=True, group="title")
    def generate_title(self, stem: str, text: str) -> None:
        try:
            msg = self.api.llm.complete(self.agent.model, [{"role": "user", "content": TITLE_PROMPT + text[:2000]}], [],
                                        "off", lambda t: None, lambda t: None, self.closing)
        except self.api.llm.ApiError:
            return
        # some models answer with their thinking inline
        lines = msg["content"].rsplit("</think>", 1)[-1].strip().strip("\"'").splitlines()
        if lines and lines[0].strip() and not self.closing.is_set():
            self.call_from_thread(self.save_title, stem, lines[0].strip()[:60])

    def save_title(self, stem: str, title: str) -> None:
        self.state.setdefault("titles", {})[stem] = title
        state.save(self.agent.config, self.state)
