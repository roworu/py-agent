"""opencode's slash commands, and custom ones from .opencode/command/*.md.

Commands with a dialog or a toggle call the running Chat (api.chat) and reply nothing."""

import re
import shlex
from pathlib import Path
from threading import Event

from .agents import frontmatter

INIT = ("Please analyze this codebase and create an AGENTS.md file in its root for future coding agents working here. "
        "Include the commands to build, lint and run tests (also a single test), the code style, and the high-level "
        "architecture that takes reading several files to understand. Keep it short, about 20 lines; don't list every "
        "file or add generic advice. If there's already an AGENTS.md, improve it.")
# name -> (method on the Chat, description); they open a dialog or toggle something
UI = {
    "/sessions": ("open_sessions", "list and resume sessions"),
    "/models": ("open_models", "list and switch models"),
    "/agents": ("open_agents", "list and switch agents"),
    "/themes": ("open_themes", "list and switch themes"),
    "/editor": ("open_editor", "write the message in $EDITOR"),
    "/export": ("export", "export the session to markdown"),
    "/copy": ("copy_last", "copy the last answer"),
    "/details": ("toggle_details", "show or hide tool details"),
    "/thinking": ("toggle_thinking", "show or hide thinking"),
    "/help": ("open_commands", "list commands"),
}


def expand(template: str, arg: str, agent, bash) -> str:
    """Fill $ARGUMENTS, $1..$n and !`command` (its output) in a command's prompt."""
    try:
        words = shlex.split(arg)
    except ValueError:
        words = arg.split()
    text = template.replace("$ARGUMENTS", arg)
    text = re.sub(r"\$(\d+)", lambda m: words[int(m[1]) - 1] if 0 < int(m[1]) <= len(words) else "", text)
    return re.sub(r"!`([^`]+)`", lambda m: bash(agent.cwd, Event(), m[1]), text).strip()


def install(api) -> None:
    """Register the commands."""
    for name, (method, description) in UI.items():
        api.add_command(name, lambda agent, arg, method=method: (getattr(api.chat, method)(arg) or "", None), description)
    api.add_command("/new", lambda agent, arg: (agent.reset() or "", "clear"), "start a new session")
    api.add_command("/init", lambda agent, arg: (INIT + (f" {arg}" if arg else ""), "send"), "create or update AGENTS.md")
    api.add_command("/summarize", lambda agent, arg: (arg, "compact"), "summarize the session to free context")
    folders = [root / name for root in (Path.home() / ".config" / "opencode", api.cwd / ".opencode")
               for name in ("command", "commands")]
    for path in (p for folder in folders for p in sorted(folder.glob("*.md"))):
        name = "/" + path.stem
        # built-in commands keep their meaning
        if name in api.commands.NAMES and name not in api.commands.EXTRA:
            continue
        meta, body = frontmatter(path.read_text(errors="replace"))

        def run(agent, arg: str, body=body, meta=meta) -> tuple[str, str]:
            # the command's agent and model stay picked afterwards
            if meta.get("agent"):
                agent.switch(str(meta["agent"]))
            if meta.get("model") and (model := agent.config.find_model(str(meta["model"]))):
                agent.set_model(model)
            return expand(body, arg, agent, api.tools.TOOLS["bash"][0]), "send"

        first = next((line for line in body.splitlines() if line.strip()), "")
        api.add_command(name, run, str(meta.get("description") or first[:60]), "[arguments]")
