"""Agent loop: send the history, run requested tools, repeat until the model stops calling them."""

from pathlib import Path
from threading import Event
from typing import Protocol

from . import llm, prompt, tools
from .config import Config, Model


class Events(Protocol):
    """What the agent reports while answering."""

    def text(self, text: str) -> None: ...
    def reasoning(self, text: str) -> None: ...
    def tool_call(self, name: str, arguments: str) -> None: ...
    def tool_result(self, result: str) -> None: ...


class Agent:
    def __init__(self, config: Config, cwd: Path) -> None:
        self.config, self.cwd = config, cwd
        self.thinking = config.default_thinking
        self.set_model(config.default_model)
        self.stopped = Event()
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

    def stop(self) -> None:
        """Make the running answer stop at the next stream event or bash poll."""
        self.stopped.set()

    def run(self, message: str, events: Events) -> None:
        """Answer one user message, running tools as the model asks."""
        self.stopped.clear()
        self.messages.append({"role": "user", "content": message})
        while not self.stopped.is_set():
            msg = llm.complete(self.model, self.messages, tools.SPECS, self.thinking,
                               events.text, events.reasoning, self.stopped)
            if self.stopped.is_set():
                # tool call arguments may be cut off, keep only the text
                if msg["content"]:
                    self.messages.append({"role": "assistant", "content": msg["content"]})
                return
            self.messages.append(msg)
            calls = msg.get("tool_calls", [])
            if not calls:
                return
            for call in calls:
                # every tool call needs a result or the next request is rejected
                result = "interrupted by user"
                if not self.stopped.is_set():
                    name, arguments = call["function"]["name"], call["function"]["arguments"]
                    events.tool_call(name, arguments)
                    result = tools.run(name, arguments, self.cwd, self.stopped)
                    events.tool_result(result)
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
