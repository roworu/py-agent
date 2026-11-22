"""Agents like opencode's: build and plan (primary, cycled with tab) and general (a subagent for the task tool),
plus custom ones from markdown files or settings. Each agent has its own prompt, model, tools and permissions;
the Agent subclass here applies them, asks for permission and refuses edits of files the model hasn't read."""

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from rich.text import Text

EDITS = ("write", "edit")
# a command with any of these could chain or hide another one, so "allow always" only remembers it exactly
UNSAFE = set(";&|`$<>()\n")
PLAN_NOTE = ("<system-reminder>Plan mode is active. Don't change any files and don't run commands that change "
             "anything: research with the read-only tools, then present a concise plan and wait. The user switches "
             "to the build agent (tab) when they want it carried out.</system-reminder>")
REJECTED = "Error: the user rejected permission to use this tool. Stop and wait for their instructions."


@dataclass
class Spec:
    name: str
    description: str = ""
    # "primary" (cycled with tab), "subagent" (for the task tool) or "all"
    mode: str = "all"
    # added to the system prompt
    prompt: str = ""
    # "provider/id" or id, None keeps the current model
    model: str | None = None
    # tool name -> False turns it off
    tools: dict = field(default_factory=dict)
    # "edit", "bash", "webfetch" -> "allow" | "ask" | "deny"; bash may map command patterns to those
    permission: dict = field(default_factory=dict)

    @property
    def primary(self) -> bool:
        return self.mode in ("primary", "all")

    @property
    def subagent(self) -> bool:
        return self.mode in ("subagent", "all")


BUILT_IN = [
    Spec("build", "The default agent: every tool, for getting work done.", "primary"),
    Spec("plan", "Plans without changing anything: no edits, and commands ask first.", "primary",
         tools={"write": False, "edit": False}, permission={"edit": "deny", "bash": "ask"}),
    Spec("general", "General-purpose agent for researching complex questions and running multi-step tasks.",
         "subagent", tools={"todowrite": False, "todoread": False, "task": False}),
]


def frontmatter(text: str) -> tuple[dict, str]:
    """Split "---\\nkey: value\\n---\\nbody" into its keys and the body; indented lines under an empty key nest one level."""
    if not text.startswith("---\n") or "\n---" not in text[3:]:
        return {}, text
    head, _, body = text[4:].partition("\n---")
    meta, parent = {}, None
    for line in head.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        value = {"true": True, "false": False}.get(value, value.strip("'\""))
        if line[0] in " \t" and parent is not None:
            meta[parent][key.strip("'\"")] = value
        elif value == "":
            parent, meta[key] = key, {}
        else:
            parent, meta[key] = None, value
    return meta, body.partition("\n")[2].strip()


def load(cwd: Path, settings: dict) -> list[Spec]:
    """Built-in agents, then .md files in ~/.config/opencode/agent(s)/ and ./.opencode/agent(s)/, then settings' "agent";
    a later one with the same name replaces an earlier one."""
    specs = {s.name: s for s in BUILT_IN}
    folders = [root / name for root in (Path.home() / ".config" / "opencode", cwd / ".opencode") for name in ("agent", "agents")]
    for path in (p for folder in folders for p in sorted(folder.glob("*.md"))):
        meta, body = frontmatter(path.read_text(errors="replace"))
        specs[path.stem] = _spec(path.stem, {**meta, "prompt": body})
    for name, value in (settings.get("agent") or {}).items():
        if isinstance(value, dict):
            specs[name] = _spec(name, {**vars(specs[name]), **value} if name in specs else value)
    return list(specs.values())


def _spec(name: str, meta: dict) -> Spec:
    dicts = {k: meta[k] if isinstance(meta.get(k), dict) else {} for k in ("tools", "permission")}
    return Spec(name, str(meta.get("description", "")), str(meta.get("mode", "all")), str(meta.get("prompt", "")),
                meta.get("model") or None, **dicts)


def decide(permission: dict, name: str, args: dict) -> str:
    """"allow", "ask" or "deny" for a tool call; bash patterns match the command, the last match wins."""
    rule = permission.get("edit" if name in EDITS else name, "allow")
    if not isinstance(rule, dict):
        return str(rule)
    command, action = str(args.get("command", "")), str(rule.get("*", "allow"))
    for pattern, value in rule.items():
        if pattern != "*" and fnmatch.fnmatchcase(command, pattern):
            # a pattern like "git *" must not allow "git status; rm -rf ~"
            action = "ask" if value == "allow" and UNSAFE & set(command) else str(value)
    return action


def always_key(name: str, args: dict) -> tuple:
    """What "allow always" remembers: all edits, a command's program and subcommand (git status), or the tool."""
    if name in EDITS:
        return ("edit",)
    if name != "bash":
        return (name,)
    command = str(args.get("command", ""))
    words = command.split()
    if not words or UNSAFE & set(command):
        return ("bash", command)
    return ("bash", " ".join(words[:2]) if len(words) > 1 and not words[1].startswith("-") else words[0])


def _covered(key: tuple, name: str, args: dict) -> bool:
    if key[0] != "bash" or name != "bash":
        return key == always_key(name, args)
    command = str(args.get("command", ""))
    return command == key[1] or (not UNSAFE & set(command) and command.startswith(key[1] + " "))


def install(api) -> list[Spec]:
    """Swap in the Agent subclass; returns the agents."""
    settings = api.settings("opencode")
    specs = load(api.cwd, settings)
    by_name = {s.name: s for s in specs}

    class OpenCodeAgent(api.Agent):
        def __init__(self, *args, **kwargs) -> None:
            self.agent_name = "build"
            # set for subagents run by the task tool
            self.parent = None
            super().__init__(*args, **kwargs)

        def reset(self) -> None:
            super().reset()
            # resolved path -> mtime when the model last read it; the todo list; "allow always" answers
            self.seen: dict[Path, int] = {}
            self.todos: list = []
            self.always: set[tuple] = set()

        @property
        def spec(self) -> Spec:
            return by_name.get(self.agent_name, BUILT_IN[0])

        def switch(self, name: str) -> None:
            """Use another agent from the next request on, with its model if it sets one."""
            self.agent_name = name
            if self.spec.model and (model := self.config.find_model(self.spec.model)):
                self.set_model(model)

        def request_tools(self) -> list[dict]:
            off = {name for name, on in self.spec.tools.items() if on is False}
            if self.parent:
                # subagents don't start more subagents
                off.add("task")
            return [s for s in super().request_tools() if s["function"]["name"] not in off]

        def request_messages(self) -> list[dict]:
            messages = super().request_messages()
            if self.spec.prompt:
                messages = [{**messages[0], "content": messages[0]["content"] + "\n\n" + self.spec.prompt}, *messages[1:]]
            if self.agent_name == "plan":
                # added to a copy of the last user message, so the saved session stays clean
                last = max(i for i, m in enumerate(messages) if m["role"] == "user")
                note = {**messages[last], "content": messages[last]["content"] + "\n\n" + PLAN_NOTE}
                messages = [*messages[:last], note, *messages[last + 1:]]
            return messages

        def before_tool(self, name: str, args: dict) -> str | None:
            if name in EDITS:
                path = str(args.get("path", ""))
                p = (self.cwd / Path(path).expanduser()).resolve()
                # like opencode, an existing file must be read first and not have changed since
                if p.is_file() and p not in self.seen:
                    return f"Error: you must read {path} before changing it"
                if p.is_file() and p.stat().st_mtime_ns != self.seen[p]:
                    return f"Error: {path} changed since you last read it, read it again"
            return super().before_tool(name, args) or self.permit(name, args)

        def after_tool(self, name: str, args: dict, result: str) -> str:
            p = (self.cwd / Path(str(args.get("path", ""))).expanduser()).resolve()
            if name in ("read", *EDITS) and not result.startswith("Error:") and p.is_file():
                self.seen[p] = p.stat().st_mtime_ns
            return super().after_tool(name, args, result)

        def permit(self, name: str, args: dict) -> str | None:
            """Apply the permission settings and the agent's own; None runs the tool, text goes to the model instead."""
            action = decide({**(settings.get("permission") or {}), **self.spec.permission}, name, args)
            if action == "allow" or any(_covered(key, name, args) for key in self.always):
                return None
            if action == "deny":
                return f"Error: the {self.agent_name} agent isn't allowed to use {name}"
            choice = self.ask("Permission required", ["Allow once", "Allow always", "Reject"], describe(name, args))
            if choice == 1:
                self.always.add(always_key(name, args))
            if choice in (None, 2):
                self.stop()
                return REJECTED
            return None

    api.Agent = OpenCodeAgent
    return specs


def describe(name: str, args: dict) -> Text:
    """The call a permission prompt is about."""
    if name == "bash":
        return Text(f"$ {args.get('command', '')}")
    if name in EDITS:
        return Text(f"{'Edit' if name == 'edit' else 'Write'} {args.get('path', '')}")
    if name == "webfetch":
        return Text(f"Fetch {args.get('url', '')}")
    return Text(f"{name} {args}")
