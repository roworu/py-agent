"""System prompt: built-in or override file, project context files, then date and cwd."""

from datetime import date
from pathlib import Path

from . import tools

CONTEXT_FILES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")


def _default() -> str:
    names = "\n".join(f"- {t['function']['name']}: {t['function']['description'].splitlines()[0]}" for t in tools.SPECS)
    return \
f"""You are an expert coding assistant operating inside py-agent harness. You help users by reading files, executing commands, editing code, and writing new files.

Available tools:
---
{names}
---
"""


def build(cwd: Path, override: Path | None = None) -> str:
    """Build the system prompt with project instructions and runtime info."""
    parts: list[str] = [override.read_text() if override else _default()]
    for name in CONTEXT_FILES:
        path: Path = cwd / name
        if path.is_file():
            parts.append(f"Additional context: {path}\n\n{path.read_text()}")
    parts.append(f"Current date: {date.today()}\nCurrent working directory: {cwd}")
    return "\n\n".join(parts)
