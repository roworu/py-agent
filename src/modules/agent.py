"""Agent loop: send the history, run requested tools, repeat until the model stops calling them."""

import json
import os
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Protocol

from . import llm, prompt, snapshot, tools
from .config import Config, Model

# share of the model's contextWindow at which the conversation is compacted before the next request
AUTO_COMPACT = 0.8


class Events(Protocol):
    """What the agent reports while answering."""

    def text(self, text: str) -> None: ...
    def reasoning(self, text: str) -> None: ...
    def tool_call(self, id: str, name: str, arguments: str) -> None: ...
    def tool_result(self, id: str, result: str) -> None: ...
    def retry(self, delay: float, error: str) -> None: ...
    def ask(self, question: str, options: list[str], body=None) -> int | None: ...
    def compacted(self) -> None: ...


class Agent:
    def __init__(self, config: Config, cwd: Path) -> None:
        self.config, self.cwd = config, cwd
        # one folder per project, named after its path like claude code does
        project = str(cwd).replace(os.sep, "-")
        self.sessions = config.dir / "sessions" / project
        self.snapshots = config.dir / "snapshots" / project
        self.thinking = config.default_thinking
        self.set_model(config.default_model)
        self.stopped = Event()
        # events of the running answer, for ask
        self.events: Events | None = None
        # token usage added up per model since start, across /clear and /compact
        self.total: dict[str, dict[str, int]] = {}
        self.reset()

    def set_model(self, model: Model) -> None:
        """Switch model, falling back to thinking off if it doesn't support the current level."""
        self.model = model
        if not model.supports(self.thinking):
            self.thinking = "off"

    def reset(self) -> None:
        """Start a new conversation with a freshly built system prompt."""
        # rebuilt each time so edited context files are picked up
        self.messages = [{"role": "system", "content": prompt.build(self.cwd, self.config.prompt_file)}]
        # created on the first save, so empty conversations leave no file
        stamp = f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
        self.session = self.sessions / f"{stamp}.jsonl"
        # a session started within the same second (/compact right away) must not append to the last one;
        # "_n" sorts after the plain name, so -c still picks the newest
        n = 1
        while self.session.exists():
            n += 1
            self.session = self.sessions / f"{stamp}_{n}.jsonl"
        self.saved = 1
        # token usage the server reported for the last request
        self.usage: dict | None = None
        # (index of the user message, snapshot before, snapshot after) per turn, and undone (turn, messages)
        self.turns: list[tuple[int, str | None, str | None]] = []
        self.undone: list[tuple[tuple[int, str | None, str | None], list[dict]]] = []

    def resume(self, path: Path) -> None:
        """Continue a saved session, with a freshly built system prompt."""
        self.reset()
        self.messages += [json.loads(line) for line in path.read_text().splitlines()]
        self.session, self.saved = path, len(self.messages)

    def _save(self) -> None:
        """Append messages not yet in the session file; only called when every tool call has its result."""
        if self.saved < len(self.messages):
            self.session.parent.mkdir(parents=True, exist_ok=True)
            with self.session.open("a") as f:
                f.writelines(json.dumps(m) + "\n" for m in self.messages[self.saved:])
            self.saved = len(self.messages)

    def append(self, *messages: dict) -> None:
        """Add messages to the conversation and save them; call between turns."""
        self.messages += messages
        self._save()

    def ask(self, question: str, options: list[str], body=None) -> int | None:
        """Ask the user to pick an option during an answer; None when cancelled or there's no ui."""
        return self.events.ask(question, options, body) if self.events else None

    def before_tool(self, name: str, args: dict) -> str | None:
        """Called before each tool call; returned text refuses it and goes to the model instead."""
        return None

    def after_tool(self, name: str, args: dict, result: str) -> str:
        """Called after each tool call ran; returns the result the model sees."""
        return result

    def call_tool(self, name: str, arguments: str) -> str:
        """Run a tool call through before_tool and after_tool, returning the result the model sees."""
        try:
            args = json.loads(arguments or "{}")
        except ValueError:
            args = {}
        # bad arguments are still passed to tools.run, which reports them to the model
        args = args if isinstance(args, dict) else {}
        result = self.before_tool(name, args) or tools.run(name, arguments, self.cwd, self.stopped)
        return self.after_tool(name, args, result)

    def request_messages(self) -> list[dict]:
        """Messages sent with each request; override to add notes without saving them."""
        return self.messages

    def _rewrite(self) -> None:
        self.session.unlink(missing_ok=True)
        self.saved = 1
        self._save()

    def undo(self) -> str | None:
        """Drop the last turn and revert the files it changed; return its user message, None if nothing to undo."""
        if not self.turns:
            return None
        i, before, after = turn = self.turns.pop()
        self.undone.append((turn, self.messages[i:]))
        del self.messages[i:]
        if before and after:
            snapshot.restore(self.snapshots, self.cwd, after, before)
        self._rewrite()
        message = self.undone[-1][1][0]
        return message.get("_display", message["content"])

    def redo(self) -> bool:
        """Bring back the last undone turn and its file changes."""
        if not self.undone:
            return False
        (i, before, after), messages = self.undone.pop()
        self.turns.append((i, before, after))
        self.messages += messages
        if before and after:
            snapshot.restore(self.snapshots, self.cwd, before, after)
        self._save()
        return True

    def _count(self, usage: dict | None) -> None:
        """Add a request's token usage to the current model's total."""
        if not usage:
            return
        total = self.total.setdefault(self.model.name, {})
        for key, n in usage.items():
            # skips nested details like prompt_tokens_details
            if isinstance(n, int):
                total[key] = total.get(key, 0) + n

    def _near_limit(self) -> bool:
        """Whether the last request plus its answer filled AUTO_COMPACT of the model's context window."""
        usage = self.usage or {}
        used = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        return bool(self.model.context_window) and used >= self.model.context_window * AUTO_COMPACT

    def _compact_before(self, turn: int) -> int | None:
        """Compact the messages before this turn and keep the turn's own; return its new index, None if not compacted."""
        pending = self.messages[turn:]
        del self.messages[turn:]
        done = self.compact()
        new_turn = len(self.messages)
        self.messages += pending
        if done and self.events:
            self.events.compacted()
        return new_turn if done else None

    def compact(self, instructions: str = "") -> bool:
        """Replace the conversation with a summary the model writes, in a new session; False if stopped or nothing to compact."""
        if len(self.messages) == 1:
            return False
        self.stopped.clear()
        request = {"role": "user", "content": prompt.COMPACT.read_text().format(instructions=instructions).strip(),
                   "_kind": "compact"}
        # no tools, so the answer is the summary
        msg = llm.complete(self.model, [*self.messages, request], [], self.thinking,
                           lambda t: None, lambda t: None, self.stopped)
        self._count(msg.pop("usage", None))
        if self.stopped.is_set() or not msg["content"]:
            return False
        self.reset()
        # kept as request and answer, since some chat templates reject two user messages in a row
        self.messages += [request, {"role": "assistant", "content": msg["content"]}]
        self._save()
        return True

    def stop(self) -> None:
        """Make the running answer stop at the next stream event or bash poll."""
        self.stopped.set()

    def run(self, message: str | dict, events: Events) -> str:
        """Answer one user message (text, or a message dict with "_" keys), running tools as the model asks; returns "done" or "stopped"."""
        self.stopped.clear()
        self.undone.clear()
        self.events = events
        turn, before = len(self.messages), snapshot.take(self.snapshots, self.cwd)
        self.messages.append({"role": "user", "content": message} if isinstance(message, str) else message)
        # auto-compact runs at most once per turn, so a turn too big on its own fails instead of looping
        compacted = False
        try:
            while not self.stopped.is_set():
                self._save()
                if not compacted and self._near_limit():
                    compacted = True
                    turn = self._compact_before(turn) or turn
                    continue
                try:
                    msg = llm.complete(self.model, self.request_messages(), tools.SPECS, self.thinking,
                                       events.text, events.reasoning, self.stopped, events.retry)
                except llm.ContextTooLong:
                    if compacted:
                        raise
                    compacted = True
                    if (new_turn := self._compact_before(turn)) is None:
                        raise
                    turn = new_turn
                    continue
                self._count(msg.get("usage"))
                self.usage = msg.pop("usage", None) or self.usage
                if self.stopped.is_set():
                    # tool call arguments may be cut off, keep only the text
                    if msg["content"]:
                        self.messages.append({"role": "assistant", "content": msg["content"]})
                    break
                self.messages.append(msg)
                calls = msg.get("tool_calls", [])
                if not calls:
                    break
                for call in calls:
                    # every tool call needs a result or the next request is rejected
                    result = "interrupted by user"
                    if not self.stopped.is_set():
                        name, arguments = call["function"]["name"], call["function"]["arguments"]
                        events.tool_call(call["id"], name, arguments)
                        result = self.call_tool(name, arguments)
                        events.tool_result(call["id"], result)
                    self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            self._save()
        finally:
            self.events = None
            self.turns.append((turn, before, snapshot.take(self.snapshots, self.cwd)))
        return "stopped" if self.stopped.is_set() else "done"

