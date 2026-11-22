"""Slash commands: show or change the agent's model, thinking level and conversation."""

import json
from collections.abc import Callable

from .agent import Agent

# name -> (arguments, description), for /help and plugin command menus; plugins add theirs
DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "/model": ("[name]", "list models or switch (provider/id or id)"),
    "/think": ("[level]", "show or set the thinking level"),
    "/clear": ("", "start a new conversation"),
    "/compact": ("[text]", "summarize the conversation to free context, text guides the summary"),
    "/resume": ("[n]", "list this folder's sessions or continue one"),
    "/undo": ("", "drop the last message and revert its file changes"),
    "/redo": ("", "bring back what /undo dropped"),
    "/help": ("", "list commands"),
    "/quit": ("", "exit (also ctrl+q)"),
    "/exit": ("", "exit"),
}
NAMES: list[str] = list(DESCRIPTIONS)


def _help() -> str:
    lines = [f"  {f'{name} {args}'.strip():<16}{text}" for name, (args, text) in DESCRIPTIONS.items()]
    return "\n".join(["commands:", *lines, "esc stops the current answer"])


HELP = _help()
# plugin commands: name -> handler(agent, arg) returning (reply, ui action)
EXTRA: dict[str, Callable[[Agent, str], tuple[str, str | None]]] = {}


def run(agent: Agent, line: str) -> tuple[str, str | None]:
    """Apply a slash command; return the reply and the ui action ("clear", "replay", "undo", "compact", "send", "quit" or None)."""
    name, _, arg = line.partition(" ")
    arg = arg.strip()
    if name in EXTRA:
        return EXTRA[name](agent, arg)
    if name in ("/quit", "/exit"):
        return "", "quit"
    if name == "/clear":
        agent.reset()
        return "new conversation", "clear"
    if name == "/compact":
        # slow, so the ui runs agent.compact(arg) in its worker
        return arg, "compact"
    if name == "/resume":
        sessions = sorted(agent.sessions.glob("*.jsonl"), reverse=True)[:20]
        if arg.isdigit() and 0 < int(arg) <= len(sessions):
            agent.resume(sessions[int(arg) - 1])
            return f"resumed {sessions[int(arg) - 1].stem}", "replay"
        # the first line is always the opening user message
        firsts = [json.loads(p.open().readline()) for p in sessions]
        lines = [f"{i:>2}. {p.stem}  {m.get('_display', m['content'])[:60]!r}" for i, (p, m) in enumerate(zip(sessions, firsts), 1)]
        return "\n".join(lines) + "\n/resume <n> to continue one" if lines else "no sessions in this folder", None
    if name == "/undo":
        # the ui puts the message back into the input
        text = agent.undo()
        return (text, "undo") if text is not None else ("nothing to undo", None)
    if name == "/redo":
        return ("redone", "replay") if agent.redo() else ("nothing to redo", None)
    if name == "/model" and not arg:
        return "\n".join(f"{'*' if m is agent.model else ' '} {m.name}" for m in agent.config.models), None
    if name == "/model":
        if not (model := agent.config.find_model(arg)):
            return f"unknown model {arg}", None
        agent.set_model(model)
        return f"model: {model.name} · thinking {agent.thinking}", None
    if name == "/think" and agent.model.supports(arg):
        agent.thinking = arg
        return f"thinking: {arg}", None
    if name == "/think":
        return f"thinking: {agent.thinking} (available: {', '.join(['off', *agent.model.efforts])})", None
    return _help(), None

