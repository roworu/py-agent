"""Undo support: snapshots of the cwd in a separate git dir, so the user's own repo is never touched."""

import subprocess
from pathlib import Path


def _git(git_dir: Path, cwd: Path, *args: str) -> str:
    cmd = ["git", "--literal-pathspecs", "--git-dir", str(git_dir), "--work-tree", str(cwd), *args]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout


def take(git_dir: Path, cwd: Path) -> str | None:
    """Record the cwd's files and return the tree hash; None outside a git repo, like opencode."""
    try:
        # scanning a whole home folder would be far too slow, so only git projects get snapshots
        if subprocess.run(["git", "rev-parse"], cwd=cwd, capture_output=True).returncode:
            return None
        if not git_dir.exists():
            git_dir.parent.mkdir(parents=True, exist_ok=True)
            _git(git_dir, cwd, "init", "-q")
        _git(git_dir, cwd, "add", "-A", ".")
        return _git(git_dir, cwd, "write-tree").strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def restore(git_dir: Path, cwd: Path, current: str, target: str) -> None:
    """Make the files that differ between two snapshots look as they do in target."""
    def changed(filter: str) -> list[str]:
        out = _git(git_dir, cwd, "diff", "--name-only", "--no-renames", "-z", f"--diff-filter={filter}", current, target)
        return [p for p in out.split("\0") if p]

    for path in changed("D"):
        (cwd / path).unlink(missing_ok=True)
    if paths := changed("d"):
        _git(git_dir, cwd, "checkout", target, "--", *paths)


def changes(git_dir: Path, cwd: Path, a: str, b: str) -> list[tuple[str, int, int]]:
    """(path, added lines, removed lines) per file that differs between two snapshots; binary files count 0."""
    out = _git(git_dir, cwd, "diff", "--numstat", "--no-renames", "-z", a, b)
    rows = []
    for row in filter(None, out.split("\0")):
        added, removed, path = row.split("\t", 2)
        rows.append((path, int(added) if added.isdigit() else 0, int(removed) if removed.isdigit() else 0))
    return rows
