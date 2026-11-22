"""Behavior: refuse risky bash commands before they run."""

import re

BLOCKED: list[str] = [r"\bsudo\b", r"\brm\s+-\w*r\w*\s+(/|~)(\s|$)", r"\bgit\s+push\b.*(--force|\s-f\b)"]


def setup(api) -> None:
    """Swap in an Agent subclass that checks bash commands before they run."""

    class GuardedAgent(api.Agent):
        def before_tool(self, name: str, args: dict) -> str | None:
            command = str(args.get("command", ""))
            if name == "bash" and any(re.search(p, command) for p in BLOCKED):
                return f"Error: blocked by bash_guard plugin, ask the user to run it: {command}"
            return super().before_tool(name, args)

    api.Agent = GuardedAgent
