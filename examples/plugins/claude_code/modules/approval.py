"""Approval prompts before write, edit and bash, asked through agent.ask; allowed commands are saved per folder."""

import json

from rich.text import Text

GATED = ("write", "edit", "bash")
# a command with any of these could chain or hide another one, so it never matches a saved rule
UNSAFE = set(";&|`$<>()\n")
NO = "No, and tell py-agent what to do differently (esc)"
# the only commands plan mode runs, without asking; anything else is refused there
READ_ONLY = {"ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "tree", "pwd", "file", "stat", "du", "diff", "which"}
READ_ONLY_GIT = {"status", "log", "diff", "show", "blame", "ls-files", "grep", "rev-parse"}
# flags that make one of those write files or run other programs
WRITING_FLAGS = ("-exec", "-execdir", "-ok", "-delete", "-fprint", "-fls", "--output", "--pre")
PLAN_REFUSAL = ("Error: plan mode is on, so only simple read-only commands run (ls, cat, grep, rg, find, "
                "git status/log/diff/show, ...; pipes between them are fine). Present your plan with exit_plan_mode.")


def read_only(command: str) -> bool:
    """Whether every piped part is a known read-only command; anything that could chain, substitute or redirect isn't."""
    if (UNSAFE - {"|"}) & set(command):
        return False
    for part in command.split("|"):
        words = part.split()
        if not words or any(w.startswith(WRITING_FLAGS) for w in words):
            return False
        if not (words[0] in READ_ONLY or words[0] == "git" and len(words) > 1 and words[1] in READ_ONLY_GIT):
            return False
    return True


def rule_for(command: str) -> str | None:
    """The prefix "don't ask again" saves: the program, plus its subcommand if it has one (git status, npm test)."""
    words = command.split()
    if not words or UNSAFE & set(command):
        return None
    return " ".join(words[:2]) if len(words) > 1 and not words[1].startswith("-") else words[0]


def install(api) -> None:
    """Swap in an Agent subclass that asks before gated tools, depending on the mode."""

    class ApprovalAgent(api.Agent):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            # {folder: [command prefixes]}, like claude code's per-project allow list
            self.rules_file = self.config.dir / "claude_code" / "allowed.json"
            rules = json.loads(self.rules_file.read_text()) if self.rules_file.is_file() else {}
            self.rules: list[str] = rules.get(str(self.cwd), [])

        def allowed(self, command: str) -> bool:
            return not UNSAFE & set(command) and any(command == r or command.startswith(r + " ") for r in self.rules)

        def allow(self, rule: str) -> None:
            """Save a command prefix for this folder."""
            self.rules.append(rule)
            rules = json.loads(self.rules_file.read_text()) if self.rules_file.is_file() else {}
            rules[str(self.cwd)] = self.rules
            self.rules_file.parent.mkdir(parents=True, exist_ok=True)
            self.rules_file.write_text(json.dumps(rules, indent=2))

        def before_tool(self, name: str, args: dict) -> str | None:
            if refusal := super().before_tool(name, args):
                return refusal
            return self.permit(name, args) if name in GATED else None

        def permit(self, name: str, args: dict) -> str | None:
            """Ask before a tool runs; None runs it, text goes back to the model instead."""
            command = str(args.get("command", ""))
            if name in ("write", "edit"):
                if self.mode == "plan":
                    return "Error: plan mode is on, so files can't be changed. Present your plan with exit_plan_mode."
                if self.mode == "accept edits":
                    return None
                question = f"Do you want to {'make this edit to' if name == 'edit' else 'write'} {args.get('path')}?"
                options = ["Yes", "Yes, allow all edits during this session (shift+tab)", NO]
                choice = self.ask(question, options)
                if choice == 1:
                    self.mode = "accept edits"
            else:
                if self.mode == "plan":
                    return None if read_only(command) else PLAN_REFUSAL
                if self.allowed(command):
                    return None
                rule = rule_for(command)
                options = ["Yes", *([f"Yes, and don't ask again for `{rule}` commands in this folder"] if rule else []), NO]
                choice = self.ask("Do you want to run this command?", options, Text(command + "\n", "bold"))
                if rule and choice == 1:
                    self.allow(rule)
            # esc, or no ui to ask
            if choice in (None, len(options) - 1):
                self.stop()
                return "Error: the user declined this tool call. Wait for their instructions."
            return None

    api.Agent = ApprovalAgent
