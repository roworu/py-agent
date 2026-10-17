"""Slash commands: show or change the agent's model, thinking level and conversation."""

from .agent import Agent

NAMES: list[str] = ["/model", "/think", "/clear", "/help", "/quit", "/exit"]
HELP = """commands:
  /model [name]   list models or switch (provider/id or id)
  /think [level]  show or set the thinking level
  /clear          start a new conversation
  /quit, /exit    exit (also ctrl+q)
esc stops the current answer"""


def run(agent: Agent, line: str) -> tuple[str, str | None]:
    """Apply a slash command; return the reply and the ui action ("clear", "quit" or None)."""
    name, _, arg = line.partition(" ")
    arg = arg.strip()
    if name in ("/quit", "/exit"):
        return "", "quit"
    if name == "/clear":
        agent.reset()
        return "new conversation", "clear"
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
    return HELP, None
