"""Built-in tools: read, write, edit, bash."""

import json
import os
import signal
import subprocess
from pathlib import Path
from threading import Event

MAX_LINES = 2000


def _path(cwd: Path, path: str) -> Path:
    return (cwd / Path(path).expanduser()).resolve()


def read(cwd: Path, stop: Event, path: str, offset: int = 1, limit: int = MAX_LINES) -> str:
    """Read a text file with line offset and limit."""
    p = _path(cwd, path)
    lines = p.read_text(errors="replace").splitlines()
    start = max(offset, 1) - 1
    if lines and start >= len(lines):
        raise ValueError(f"offset {offset} is beyond end of file ({len(lines)} lines)")
    end = start + min(limit, MAX_LINES)
    text = "\n".join(lines[start:end])
    if end < len(lines):
        text += f"\n\n[showing lines {start + 1}-{end} of {len(lines)}, use offset={end + 1} to continue]"
    return text


def write(cwd: Path, stop: Event, path: str, content: str) -> str:
    """Create or overwrite a file, creating parent dirs."""
    p = _path(cwd, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content.encode())} bytes to {p}"


def edit(cwd: Path, stop: Event, path: str, oldText: str, newText: str) -> str:
    """Replace one exact occurrence of text."""
    p = _path(cwd, path)
    text = p.read_text()
    if text.count(oldText) != 1:
        raise ValueError(f"oldText must match exactly once in {p}")
    p.write_text(text.replace(oldText, newText, 1))
    return f"edited {p}"


def bash(cwd: Path, stop: Event, command: str) -> str:
    """Run bash in cwd and return the last MAX_LINES of output; killed when stop is set."""
    proc = subprocess.Popen(
        args=["bash", "-c", command],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    while True:
        try:
            out, _ = proc.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            if stop.is_set():
                os.killpg(proc.pid, signal.SIGKILL)
                # keep what it printed before the kill
                out, _ = proc.communicate()
                break

    lines: list[str] = out.decode(errors="replace").splitlines()
    text: str = "\n".join(lines[-MAX_LINES:])
    if len(lines) > MAX_LINES:
        text = f"[output truncated, showing last {MAX_LINES} of {len(lines)} lines]\n{text}"
    if proc.returncode:
        text += f"\n\n[exit code {proc.returncode}]"
    return text or "(no output)"


def _spec(function, description: str, props: dict, required: list[str]) -> tuple:
    return function, {
        "type": "function",
        "function": {
            "name": function.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


_STR, _INT = {"type": "string"}, {"type": "integer"}
_PATH = {"type": "string", "description": "Path relative to cwd or absolute"}

TOOLS = {
    function.__name__: (function, spec)
    for function, spec in [
        _spec(
            function=read,
            description=f"Read a text file, limited to {MAX_LINES} lines. Use offset and limit for large files.",
            props={
                "path": _PATH,
                "offset": {**_INT, "description": "1-based starting line"},
                "limit": {**_INT, "description": "Maximum lines to read"},
            },
            required=["path"],
        ),
        _spec(
            function=write,
            description="Create or overwrite a file, creating parent directories.",
            props={"path": _PATH, "content": _STR},
            required=["path", "content"],
        ),
        _spec(
            function=edit,
            description="Replace exact text that must occur once.",
            props={"path": _PATH, "oldText": _STR, "newText": _STR},
            required=["path", "oldText", "newText"],
        ),
        _spec(
            function=bash,
            description=f"Run bash in cwd and return the last {MAX_LINES} lines of output.",
            props={"command": _STR},
            required=["command"],
        ),
    ]
}
SPECS = [spec for _, spec in TOOLS.values()]


def run(name: str, arguments: str, cwd: Path, stop: Event) -> str:
    """Execute a tool call and return errors as text."""
    try:
        return TOOLS[name][0](cwd, stop, **json.loads(arguments or "{}"))
    except (KeyError, OSError, TypeError, ValueError) as e:
        return f"Error: {e}"
