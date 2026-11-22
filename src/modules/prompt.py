"""System prompt: built-in or override file, project context files, then date and cwd."""

from datetime import date
from pathlib import Path

from . import tools

CONTEXT_FILES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
SYSTEM = Path(__file__).parent / "prompts" / "system.md"
COMPACT = Path(__file__).parent / "prompts" / "compact.md"


def _default() -> str:
    names = "\n".join(f"- {t['function']['name']}: {t['function']['description'].splitlines()[0]}" for t in tools.SPECS)
    return SYSTEM.read_text().format(tools=names)


def build(cwd: Path, override: Path | None = None) -> str:
    """Build the system prompt with project instructions and runtime info."""
    parts: list[str] = [override.read_text(errors="replace") if override else _default()]
    for name in CONTEXT_FILES:
        path: Path = cwd / name
        if path.is_file():
            parts.append(f"Additional context: {path}\n\n{path.read_text(errors='replace')}")
    parts.append(f"Current date: {date.today()}\nCurrent working directory: {cwd}")
    return "\n\n".join(parts)

