"""Input shortcuts: `! cmd` bash mode, `# note` memory, @file attachments, esc twice to clear the input or rewind."""

import time

from rich.text import Text
from textual import work
from textual.widgets import Static

from . import render


class ShortcutsMixin:
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.last_esc = 0.0

    async def send(self, line: str) -> None:
        if line.startswith("!"):
            command = line[1:].strip()
            message = {"role": "user", "content": f"<bash-input>{command}</bash-input>", "_kind": "bash", "_display": command}
            await self.show_user(message)
            await self.set_busy(True)
            self.run_bash(message)
        elif line.startswith("#") and "\n" not in line:
            self.memorize(line[1:].strip())
        else:
            await super().send(line)

    @work(thread=True)
    def run_bash(self, message: dict) -> None:
        """Run a `! cmd` line without the model, then add it and its output to the conversation."""
        self.agent.stopped.clear()
        try:
            out = self.api.tools.bash(self.agent.cwd, self.agent.stopped, message["_display"])
            # command and output as a user/assistant pair, so turns keep alternating for strict chat templates
            answer = {"role": "assistant", "content": f"<bash-stdout>{out}</bash-stdout>", "_kind": "bash"}
            self.agent.append(message, answer)
            self.call_from_thread(self.show_answer, answer)
        finally:
            self.call_from_thread(self.set_busy, False)
            self.call_from_thread(self.send_queued)

    def memorize(self, note: str) -> None:
        """Add a line to the project's CLAUDE.md and rebuild the system prompt so it applies now."""
        path = self.agent.cwd / "CLAUDE.md"
        text = path.read_text(errors="replace") if path.is_file() else ""
        path.write_text(text + ("\n" if text and not text.endswith("\n") else "") + f"- {note}\n")
        self.agent.messages[0]["content"] = self.api.prompt.build(self.agent.cwd, self.agent.config.prompt_file)
        self.notify(f"saved to {path}")

    async def show_user(self, message: dict) -> None:
        if message.get("_kind") == "bash":
            await self.add(Static(Text(f"! {message['_display']}"), classes="user bash"))
            return
        await super().show_user(message)
        for path, contents in self.api.files.attached(message["content"]):
            await self.add(Static(Text(f"  ⎿  Read {path} ({render.plural(len(contents.splitlines()), 'line')})", "dim")))

    async def show_answer(self, message: dict) -> None:
        if message.get("_kind") == "bash":
            out = message["content"].removeprefix("<bash-stdout>").removesuffix("</bash-stdout>")
            await self.add(Static(render.result("bash", {}, out)[1:]))
        else:
            await super().show_answer(message)

    def action_stop(self) -> None:
        if self.busy:
            super().action_stop()
            return
        # idle: esc twice clears the input, or opens rewind when it's empty
        input, now = self.query_one(self.Input), time.monotonic()
        double, self.last_esc = now - self.last_esc < 1, now
        if double:
            self.last_esc = 0.0
            if input.text:
                input.clear()
            else:
                # a worker, since waiting for the picker inside a key handler would block the ui
                self.run_worker(self.rewind())
        elif input.text:
            self.notify("esc again to clear", timeout=1)
        elif self.agent.turns:
            self.notify("esc again to rewind", timeout=1)

    async def rewind(self) -> None:
        """Pick an earlier message to go back to; later messages and their file changes are undone."""
        turns = self.agent.turns[-9:][::-1]
        if not turns:
            return
        messages = [self.agent.messages[i] for i, _, _ in turns]
        options = [m.get("_display", m["content"]).splitlines()[0][:70] for m in messages]
        choice = await self.ask("Rewind to before which message? Files are restored too.", options)
        if choice is None:
            return
        for _ in range(choice + 1):
            text = self.agent.undo()
        await self.query_one("#log").remove_children()
        await self.replay()
        self.query_one(self.Input).text = text
