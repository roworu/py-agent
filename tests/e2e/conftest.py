"""e2e fixtures: the pyinstaller binary (built like CI, or $PY_AGENT_BIN) and a pty driver for its tui."""

import fcntl
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import termios
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KEYS = {"enter": "\r", "esc": "\x1b", "ctrl+q": "\x11", "ctrl+j": "\n", "end": "\x1b[F", "up": "\x1b[A"}
# csi, osc and two-byte escapes, so only the drawn text is left
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-Z\\-_]")


@pytest.fixture(scope="session")
def binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """$PY_AGENT_BIN if set, else build one from a copy of the repo exactly as CI does."""
    if path := os.environ.get("PY_AGENT_BIN"):
        return Path(path).resolve()
    if not importlib.util.find_spec("PyInstaller"):
        pytest.skip("pyinstaller not installed and PY_AGENT_BIN not set")
    build = tmp_path_factory.mktemp("build")
    version = re.search(r'^version = "(.*)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    shutil.copytree(ROOT / "src", build / "src", ignore=shutil.ignore_patterns("__pycache__"))
    cfg = build / "src" / "modules" / "config.py"
    cfg.write_text(re.sub(r"^VERSION = .*$", f'VERSION = "{version}"', cfg.read_text(), flags=re.M))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--onefile", "--name", "py-agent", "--paths", "src",
                    "--add-data", "src/modules/prompts:modules/prompts", "src/main.py"],
                   cwd=build, check=True, capture_output=True)
    return build / "dist" / "py-agent"


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """Environment for the binary: own config dir and home, a real terminal type."""
    home = tmp_path / "home"
    home.mkdir()
    return {"PATH": os.environ["PATH"], "HOME": str(home), "PY_AGENT_DIR": str(tmp_path / "config"),
            "TERM": "xterm-256color", "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1"}


@pytest.fixture
def config_dir(env: dict[str, str]) -> Path:
    """The binary's config dir."""
    return Path(env["PY_AGENT_DIR"])


@pytest.fixture
def settings(config_dir: Path, server) -> Callable[..., None]:
    """Write settings.json pointing at the fake server; extra keys and models can be added."""
    def write(models: list[dict] | None = None, **extra) -> None:
        config_dir.mkdir(parents=True, exist_ok=True)
        provider = {"baseUrl": server.url, "models": models or [{"id": "m"}]}
        (config_dir / "settings.json").write_text(json.dumps({"providers": {"p": provider}, **extra}))
        (config_dir / "auth.json").write_text(json.dumps({"p": "sk-test"}))
    write()
    return write


class Tui:
    """The binary running in a pseudo terminal; output is collected in the background."""

    def __init__(self, binary: Path, cwd: Path, env: dict[str, str], *args: str) -> None:
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        self.proc = subprocess.Popen([str(binary), *args], cwd=cwd, env=env, stdin=slave, stdout=slave,
                                     stderr=slave, start_new_session=True)
        os.close(slave)
        self.fd = master
        self.raw = b""
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.raw += data

    @property
    def text(self) -> str:
        """Everything drawn so far with escape codes removed."""
        return ANSI.sub("", self.raw.decode(errors="replace"))

    def wait(self, check: Callable[[], object], timeout: float = 20, what: str = "") -> None:
        """Poll until check() is truthy, failing with the screen text otherwise."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        if check():
            return
        raise AssertionError(f"timed out waiting for {what or check}; exit={self.proc.poll()}\n{self.text[-3000:]}")

    def wait_text(self, needle: str, timeout: float = 20) -> None:
        """Wait until needle has been drawn."""
        self.wait(lambda: needle in self.text, timeout, repr(needle))

    def type(self, text: str) -> None:
        """Send text as typed keys."""
        os.write(self.fd, text.encode())
        # a burst followed at once by more keys can be read as one chunk and misparsed
        time.sleep(0.2)

    def press(self, key: str) -> None:
        """Send a named key."""
        os.write(self.fd, KEYS[key].encode())
        # a lone esc is told apart from escape sequences by a pause after it
        time.sleep(0.2)

    def say(self, text: str) -> None:
        """Type a line and send it."""
        self.type(text)
        self.press("enter")

    def quit(self) -> int:
        """ctrl+q and wait for a clean exit."""
        self.press("ctrl+q")
        return self.proc.wait(10)

    def close(self) -> None:
        """Kill if still running and release the terminal."""
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        os.close(self.fd)


@pytest.fixture
def tui(binary: Path, workdir: Path, env: dict[str, str]):
    """Start the binary in workdir; call with extra args. Every started process is cleaned up."""
    started: list[Tui] = []

    def start(*args: str, ready: str = "thinking off") -> Tui:
        t = Tui(binary, workdir, env, *args)
        started.append(t)
        t.wait_text(ready, timeout=60)
        # let textual finish mounting before keys arrive
        time.sleep(0.5)
        return t
    yield start
    for t in started:
        t.close()
