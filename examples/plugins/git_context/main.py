"""Behavior: add `git status` of the cwd to the system prompt."""

import subprocess
from pathlib import Path


def setup(api) -> None:
    """Wrap prompt.build to append git status."""
    build = api.prompt.build

    def build_with_git(cwd: Path, override: Path | None = None) -> str:
        text = build(cwd, override)
        try:
            out = subprocess.run(["git", "status", "--short", "--branch"], cwd=cwd,
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return text
        return f"{text}\n\nGit status:\n{out}" if out else text

    api.prompt.build = build_with_git
