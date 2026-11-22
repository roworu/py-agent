"""snapshot: tree hashes of the cwd in a separate git dir, and restoring changed files."""

import subprocess
from pathlib import Path

from modules import snapshot


def test_none_outside_git(workdir: Path, tmp_path: Path) -> None:
    """No snapshots outside a git repo, and no git dir is created."""
    assert snapshot.take(tmp_path / "snap", workdir) is None
    assert not (tmp_path / "snap").exists()


def test_same_files_same_hash(git_workdir: Path, tmp_path: Path) -> None:
    """Unchanged files give the same tree; a change gives a new one."""
    (git_workdir / "a").write_text("1")
    first = snapshot.take(tmp_path / "snap", git_workdir)
    assert first and first == snapshot.take(tmp_path / "snap", git_workdir)
    (git_workdir / "a").write_text("2")
    assert snapshot.take(tmp_path / "snap", git_workdir) != first


def test_user_repo_untouched(git_workdir: Path, tmp_path: Path) -> None:
    """The user's own index stays empty."""
    (git_workdir / "a").write_text("1")
    snapshot.take(tmp_path / "snap", git_workdir)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=git_workdir, capture_output=True, text=True).stdout
    assert status == "?? a\n"
    assert (tmp_path / "snap" / "HEAD").is_file()


def test_restore_both_ways(git_workdir: Path, tmp_path: Path) -> None:
    """Restore reverts modified, added and deleted files, and back again."""
    snap = tmp_path / "snap"
    (git_workdir / "keep").write_text("k")
    (git_workdir / "mod").write_text("old")
    (git_workdir / "gone").write_text("g")
    before = snapshot.take(snap, git_workdir)
    (git_workdir / "mod").write_text("new")
    (git_workdir / "gone").unlink()
    (git_workdir / "sub").mkdir()
    (git_workdir / "sub" / "added").write_text("a")
    after = snapshot.take(snap, git_workdir)

    snapshot.restore(snap, git_workdir, after, before)
    assert (git_workdir / "mod").read_text() == "old"
    assert (git_workdir / "gone").read_text() == "g"
    assert not (git_workdir / "sub" / "added").exists()
    assert (git_workdir / "keep").read_text() == "k"

    snapshot.restore(snap, git_workdir, before, after)
    assert (git_workdir / "mod").read_text() == "new"
    assert not (git_workdir / "gone").exists()
    assert (git_workdir / "sub" / "added").read_text() == "a"


def test_restore_leaves_unrelated_changes(git_workdir: Path, tmp_path: Path) -> None:
    """Files changed after the snapshot but not in the turn are not touched."""
    snap = tmp_path / "snap"
    (git_workdir / "a").write_text("1")
    before = snapshot.take(snap, git_workdir)
    (git_workdir / "a").write_text("2")
    after = snapshot.take(snap, git_workdir)
    (git_workdir / "b").write_text("mine")
    snapshot.restore(snap, git_workdir, after, before)
    assert (git_workdir / "a").read_text() == "1"
    assert (git_workdir / "b").read_text() == "mine"


def test_gitignored_files_skipped(git_workdir: Path, tmp_path: Path) -> None:
    """Ignored files are not snapshotted, so restore leaves them."""
    snap = tmp_path / "snap"
    (git_workdir / ".gitignore").write_text("*.log\n")
    before = snapshot.take(snap, git_workdir)
    (git_workdir / "x.log").write_text("log")
    after = snapshot.take(snap, git_workdir)
    assert before == after


def test_odd_file_names(git_workdir: Path, tmp_path: Path) -> None:
    """Spaces, globs and leading dashes in names restore fine."""
    snap = tmp_path / "snap"
    names = ["with space", "*star", "-dash", "ünï"]
    for n in names:
        (git_workdir / n).write_text("old")
    before = snapshot.take(snap, git_workdir)
    for n in names:
        (git_workdir / n).write_text("new")
    after = snapshot.take(snap, git_workdir)
    snapshot.restore(snap, git_workdir, after, before)
    assert all((git_workdir / n).read_text() == "old" for n in names)


def test_missing_git_returns_none(git_workdir: Path, tmp_path: Path, monkeypatch) -> None:
    """Without a git binary snapshots are just off."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert snapshot.take(tmp_path / "snap", git_workdir) is None
