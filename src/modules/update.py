"""Self update: compare with the latest github release and replace the running binary with it."""

import json
import os
import platform
import sys
import time
import urllib.request
from pathlib import Path

from .config import VERSION, Config

REPO = "roworu/py-agent"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD = f"https://github.com/{REPO}/releases/latest/download/"


def asset() -> str:
    """Release asset name for this os and cpu, like py-agent-linux-x86_64 or py-agent-macos-arm64."""
    system = {"darwin": "macos"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return f"py-agent-{system}-{arch}"


def _parse(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(n) for n in version.removeprefix("v").split("."))
    except ValueError:
        return None


def is_binary() -> bool:
    """Whether this is a release build, the only kind that updates itself; running from source has no version."""
    return bool(getattr(sys, "frozen", False)) and _parse(VERSION) is not None


def newer(config: Config) -> str | None:
    """The latest release's version if it's newer than this one; None when not, not due yet, or github can't be reached."""
    # the file's mtime is when github was last asked
    stamp = config.dir / "update-check"
    due = not stamp.exists() or time.time() - stamp.stat().st_mtime >= config.update_check_days * 86400
    if not is_binary() or config.update_check_days <= 0 or not due:
        return None
    try:
        with urllib.request.urlopen(LATEST, timeout=5) as resp:
            latest = json.load(resp)["tag_name"].removeprefix("v")
    except (OSError, ValueError, KeyError):
        return None
    # only after a successful check, so being offline retries on the next start
    stamp.touch()
    return latest if (_parse(latest) or ()) > _parse(VERSION) else None


def install() -> str:
    """Download the latest release over the running binary; returns what happened, errors as "Error: ..."."""
    if not is_binary():
        return "Error: not a release build, update with git instead"
    path = Path(sys.executable)
    tmp = path.with_name(f".{path.name}.new")
    try:
        with urllib.request.urlopen(DOWNLOAD + asset(), timeout=60) as resp, tmp.open("wb") as f:
            while chunk := resp.read(1 << 16):
                f.write(chunk)
        tmp.chmod(0o755)
        # the running process keeps the old file open, so replacing it is safe
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        return f"Error: {e}"
    return "updated, restart py-agent to use the new version"
