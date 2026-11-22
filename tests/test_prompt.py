"""prompt: built-in or override prompt, context files, date and cwd."""

from datetime import date
from pathlib import Path

from modules import prompt, tools


def test_default_lists_tools(workdir: Path) -> None:
    """The built-in prompt lists every tool with the first line of its description."""
    text = prompt.build(workdir)
    assert "{tools}" not in text
    for spec in tools.SPECS:
        fn = spec["function"]
        assert f"- {fn['name']}: {fn['description'].splitlines()[0]}" in text


def test_ends_with_date_and_cwd(workdir: Path) -> None:
    """Runtime info closes the prompt."""
    assert prompt.build(workdir).endswith(f"Current date: {date.today()}\nCurrent working directory: {workdir}")


def test_context_files_in_order(workdir: Path) -> None:
    """AGENTS.md, CLAUDE.md and GEMINI.md are appended in that order with their path."""
    for name in reversed(prompt.CONTEXT_FILES):
        (workdir / name).write_text(f"rules from {name}")
    text = prompt.build(workdir)
    positions = [text.index(f"Additional context: {workdir / n}\n\nrules from {n}") for n in prompt.CONTEXT_FILES]
    assert positions == sorted(positions)
    assert positions[-1] < text.index("Current date:")


def test_context_folder_is_ignored(workdir: Path) -> None:
    """A folder named like a context file is skipped."""
    (workdir / "AGENTS.md").mkdir()
    assert "Additional context" not in prompt.build(workdir)


def test_override_replaces_builtin(workdir: Path, tmp_path: Path) -> None:
    """An override file replaces the built-in prompt but context and runtime info stay."""
    (tmp_path / "mine.md").write_text("be terse")
    (workdir / "AGENTS.md").write_text("ctx")
    text = prompt.build(workdir, tmp_path / "mine.md")
    assert text.startswith("be terse\n\n")
    assert "Available tools" not in text
    assert "ctx" in text and str(workdir) in text


def test_added_tool_is_listed(workdir: Path) -> None:
    """Tools added to SPECS show up in the prompt."""
    tools.SPECS.append({"function": {"name": "zap", "description": "Zap things.\nmore"}})
    assert "- zap: Zap things." in prompt.build(workdir)
