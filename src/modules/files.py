"""@file mentions: listing files for the input's menu, and attaching mentioned files to a message."""

import json
import os
import re
import subprocess
from pathlib import Path

MENU_ROWS = 10
SKIP_DIRS = {"node_modules", "__pycache__", "venv"}
ATTACHED = "\n\n<attached-files>\n"
FILE = re.compile(r'<file path="([^"]+)">\n(.*?)\n</file>', re.S)
MENTION = re.compile(r"(?:^|\s)@(\S+)")


def list_files(cwd: Path) -> list[str]:
    """Files under cwd: git's list when it's a repo (respects .gitignore), else a walk skipping hidden and vendor dirs."""
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=cwd,
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        pass
    files: list[str] = []
    for root, dirs, names in os.walk(cwd):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        files += [os.path.relpath(os.path.join(root, n), cwd) for n in names]
        if len(files) > 5000:
            break
    return files


def match(files: list[str], query: str) -> list[str]:
    """Paths containing query, file names starting with it first, then shorter paths."""
    q = query.lower()
    hits = [f for f in files if q in f.lower()]
    return sorted(hits, key=lambda f: (not os.path.basename(f).lower().startswith(q), len(f)))[:MENU_ROWS]


def message(agent, line: str) -> dict:
    """The user message for line, with every @path that is a file attached and the typed line kept as "_display"."""
    parts = []
    for name in dict.fromkeys(MENTION.findall(line)):
        if (agent.cwd / name).is_file():
            # read like the model would, so the read tool's limits and plugins' tool hooks apply
            text = agent.call_tool("read", json.dumps({"path": name}))
            if not text.startswith("Error:"):
                parts.append(f'<file path="{name}">\n{text}\n</file>')
    if not parts:
        return {"role": "user", "content": line}
    return {"role": "user", "content": line + ATTACHED + "\n".join(parts) + "\n</attached-files>", "_display": line}


def attached(content: str) -> list[tuple[str, str]]:
    """The (path, contents) attached to a message."""
    return FILE.findall(content.partition(ATTACHED)[2])
