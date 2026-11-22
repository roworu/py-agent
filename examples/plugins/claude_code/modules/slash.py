"""More slash commands: /init, and custom ones from .claude/commands/*.md like claude code."""

from pathlib import Path

INIT = ("Please analyze this codebase and create a CLAUDE.md file in its root for future coding agents working here. "
        "Include the commands to build, lint and run tests (also a single test), and the high-level architecture "
        "that takes reading several files to understand. Keep it short; don't list every file or add generic advice. "
        "If there's already a CLAUDE.md, suggest improvements to it.")


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split "---\\nkey: value\\n---\\nbody" into its keys and the body."""
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return {}, text
    head, body = text[4:].split("\n---\n", 1)
    meta = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in meta.items()}, body


def install(api) -> None:
    """Register /init and every .md in ~/.claude/commands and ./.claude/commands; project ones win."""
    api.add_command("/init", lambda agent, arg: (INIT, "send"), "write a CLAUDE.md for this project")
    for folder in (Path.home() / ".claude" / "commands", api.cwd / ".claude" / "commands"):
        for path in sorted(folder.glob("*.md")):
            name = "/" + path.stem
            # built-in commands keep their meaning
            if name in api.commands.NAMES and name not in api.commands.EXTRA:
                continue
            meta, body = _frontmatter(path.read_text(errors="replace"))
            first = next((line for line in body.splitlines() if line.strip()), "")
            description = meta.get("description") or first[:60]
            # $ARGUMENTS is replaced with what follows the command, as in claude code
            api.add_command(name, lambda agent, arg, body=body: (body.replace("$ARGUMENTS", arg).strip(), "send"),
                            description, meta.get("argument-hint", ""))
