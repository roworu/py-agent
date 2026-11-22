"""Built-in tools: read, write, edit, bash."""

import contextlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from threading import Event

MAX_LINES = 2000
MAX_CHARS = 50_000
DESCRIPTIONS = Path(__file__).parent / "prompts" / "tools"


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
    # a single minified line can still blow up the context
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n\n[output truncated to first {MAX_CHARS} chars]"
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
    # newline="" keeps crlf files crlf instead of rewriting every line
    with open(p, newline="") as f:
        text = f.read()
    if "\r\n" in text:
        oldText, newText = oldText.replace("\n", "\r\n"), newText.replace("\n", "\r\n")
    if text.count(oldText) != 1:
        raise ValueError(f"oldText must match exactly once in {p}")
    with open(p, "w", newline="") as f:
        f.write(text.replace(oldText, newText, 1))
    return f"edited {p}"


def bash(cwd: Path, stop: Event, command: str, timeout: int = 120) -> str:
    """Run bash in cwd and return the last MAX_LINES of output; killed when stop is set or after timeout seconds."""
    deadline = time.monotonic() + timeout
    timed_out = False
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
            timed_out = time.monotonic() > deadline
            if stop.is_set() or timed_out:
                # the group may have exited since the last poll
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                # keep what it printed before the kill
                out, _ = proc.communicate()
                break

    lines: list[str] = out.decode(errors="replace").splitlines()
    text: str = "\n".join(lines[-MAX_LINES:])
    if len(lines) > MAX_LINES:
        text = f"[output truncated, showing last {MAX_LINES} of {len(lines)} lines]\n{text}"
    if len(text) > MAX_CHARS:
        text = f"[output truncated to last {MAX_CHARS} chars]\n{text[-MAX_CHARS:]}"
    if proc.returncode:
        text += f"\n\n[exit code {proc.returncode}]"
    if timed_out:
        text += f"\n\n[killed after {timeout}s timeout]"
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


def _load(function) -> tuple:
    """Spec from prompts/tools/<name>.json; {MAX_LINES} and {MAX_CHARS} in the description are filled in."""
    data = json.loads((DESCRIPTIONS / f"{function.__name__}.json").read_text())
    description = data["description"].format(MAX_LINES=MAX_LINES, MAX_CHARS=MAX_CHARS)
    return _spec(function, description, data["properties"], data["required"])


TOOLS = {function.__name__: _load(function) for function in (read, write, edit, bash)}
SPECS = [spec for _, spec in TOOLS.values()]


def run(name: str, arguments: str, cwd: Path, stop: Event) -> str:
    """Execute a tool call and return errors as text."""
    try:
        return TOOLS[name][0](cwd, stop, **json.loads(arguments or "{}"))
    except (KeyError, OSError, TypeError, ValueError) as e:
        return f"Error: {e}"

