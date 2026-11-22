"""tools: read, write, edit, bash, specs and run's error handling."""

import json
import threading
import time
from pathlib import Path
from threading import Event

import pytest

from modules import tools


def run(name: str, cwd: Path, **args) -> str:
    """Call a tool the way the agent does, with json arguments."""
    return tools.run(name, json.dumps(args), cwd, Event())


# read

def test_read_whole_file(workdir: Path) -> None:
    """Small files come back as is, without a trailing newline."""
    (workdir / "f.txt").write_text("a\nb\n")
    assert run("read", workdir, path="f.txt") == "a\nb"


def test_read_offset_limit(workdir: Path) -> None:
    """offset is 1-based; a cut-off read says how to continue."""
    (workdir / "f.txt").write_text("\n".join(str(i) for i in range(1, 11)))
    assert run("read", workdir, path="f.txt", offset=3, limit=2) == \
        "3\n4\n\n[showing lines 3-4 of 10, use offset=5 to continue]"
    assert run("read", workdir, path="f.txt", offset=9) == "9\n10"


def test_read_offset_zero_is_start(workdir: Path) -> None:
    """offset below 1 reads from the first line."""
    (workdir / "f.txt").write_text("a\nb")
    assert run("read", workdir, path="f.txt", offset=0) == "a\nb"


def test_read_offset_past_end(workdir: Path) -> None:
    """Reading past the end is an error naming the length."""
    (workdir / "f.txt").write_text("a\nb")
    assert run("read", workdir, path="f.txt", offset=5) == "Error: offset 5 is beyond end of file (2 lines)"


def test_read_empty_file(workdir: Path) -> None:
    """An empty file reads as empty at any offset."""
    (workdir / "f.txt").write_text("")
    assert run("read", workdir, path="f.txt", offset=5) == ""


def test_read_line_limit(workdir: Path) -> None:
    """At most MAX_LINES lines per read, even if limit asks for more."""
    (workdir / "f.txt").write_text("x\n" * (tools.MAX_LINES + 5))
    out = run("read", workdir, path="f.txt", limit=10**6)
    assert out.count("x") == tools.MAX_LINES
    assert out.endswith(f"[showing lines 1-{tools.MAX_LINES} of {tools.MAX_LINES + 5}, use offset={tools.MAX_LINES + 1} to continue]")


def test_read_char_limit(workdir: Path) -> None:
    """A huge single line is cut to MAX_CHARS."""
    (workdir / "f.txt").write_text("y" * (tools.MAX_CHARS + 100))
    out = run("read", workdir, path="f.txt")
    assert out == "y" * tools.MAX_CHARS + f"\n\n[output truncated to first {tools.MAX_CHARS} chars]"


def test_read_binary_does_not_crash(workdir: Path) -> None:
    """Invalid utf-8 is replaced, not raised."""
    (workdir / "f.bin").write_bytes(b"ok\xff\xfe")
    assert run("read", workdir, path="f.bin").startswith("ok")


def test_read_absolute_and_home_paths(workdir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absolute paths and ~ work as well as relative ones."""
    (tmp_path / "abs.txt").write_text("abs")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run("read", workdir, path=str(tmp_path / "abs.txt")) == "abs"
    assert run("read", workdir, path="~/abs.txt") == "abs"
    assert run("read", workdir, path="../abs.txt") == "abs"


def test_read_missing_file(workdir: Path) -> None:
    """A missing file is an error message."""
    assert run("read", workdir, path="nope").startswith("Error: [Errno 2]")


def test_read_directory(workdir: Path) -> None:
    """Reading a folder is an error message."""
    assert run("read", workdir, path=".").startswith("Error:")


# write

def test_write_creates_parents(workdir: Path) -> None:
    """write makes missing folders and reports the byte count."""
    out = run("write", workdir, path="a/b/c.txt", content="héllo")
    assert (workdir / "a/b/c.txt").read_text() == "héllo"
    assert out == f"wrote 6 bytes to {workdir / 'a/b/c.txt'}"


def test_write_overwrites(workdir: Path) -> None:
    """An existing file is replaced."""
    (workdir / "f").write_text("old")
    run("write", workdir, path="f", content="new")
    assert (workdir / "f").read_text() == "new"


def test_write_into_file_path_errors(workdir: Path) -> None:
    """Writing below a file is an error message."""
    (workdir / "f").write_text("")
    assert run("write", workdir, path="f/x", content="").startswith("Error:")


# edit

def test_edit_replaces_once(workdir: Path) -> None:
    """A unique match is replaced."""
    (workdir / "f").write_text("one two three")
    assert run("edit", workdir, path="f", oldText="two", newText="2") == f"edited {workdir / 'f'}"
    assert (workdir / "f").read_text() == "one 2 three"


@pytest.mark.parametrize("text", ["a a", "b"])
def test_edit_needs_exactly_one_match(workdir: Path, text: str) -> None:
    """Zero or several matches are refused and the file is left alone."""
    (workdir / "f").write_text(text)
    assert run("edit", workdir, path="f", oldText="a", newText="x") == f"Error: oldText must match exactly once in {workdir / 'f'}"
    assert (workdir / "f").read_text() == text


def test_edit_crlf(workdir: Path) -> None:
    """lf oldText matches a crlf file and crlf is kept."""
    (workdir / "f").write_bytes(b"a\r\nb\r\nc\r\n")
    run("edit", workdir, path="f", oldText="a\nb", newText="x\ny")
    assert (workdir / "f").read_bytes() == b"x\r\ny\r\nc\r\n"


def test_edit_keeps_lf(workdir: Path) -> None:
    """lf files are not turned into crlf."""
    (workdir / "f").write_bytes(b"a\nb\n")
    run("edit", workdir, path="f", oldText="a", newText="x\nz")
    assert (workdir / "f").read_bytes() == b"x\nz\nb\n"


def test_edit_missing_file(workdir: Path) -> None:
    """Editing a missing file is an error message."""
    assert run("edit", workdir, path="nope", oldText="a", newText="b").startswith("Error:")


# bash

def test_bash_output_and_cwd(workdir: Path) -> None:
    """Runs in cwd and returns stdout and stderr together."""
    assert run("bash", workdir, command="pwd; echo err >&2") == f"{workdir}\nerr"


def test_bash_exit_code(workdir: Path) -> None:
    """A failing command reports its exit code."""
    assert run("bash", workdir, command="echo x; exit 3") == "x\n\n[exit code 3]"


def test_bash_no_output(workdir: Path) -> None:
    """Silence is reported explicitly."""
    assert run("bash", workdir, command="true") == "(no output)"


def test_bash_no_stdin(workdir: Path) -> None:
    """Commands reading stdin get eof instead of hanging."""
    assert run("bash", workdir, command="cat; echo done", timeout=5) == "done"


def test_bash_timeout_keeps_output(workdir: Path) -> None:
    """The process is killed after timeout and earlier output is kept."""
    start = time.monotonic()
    out = run("bash", workdir, command="echo hi; sleep 10", timeout=1)
    assert time.monotonic() - start < 5
    assert out.startswith("hi") and "[killed after 1s timeout]" in out


def test_bash_timeout_kills_children(workdir: Path) -> None:
    """The whole process group dies, so background children don't write later."""
    run("bash", workdir, command="(sleep 1.5; touch late) & sleep 10", timeout=1)
    time.sleep(1)
    assert not (workdir / "late").exists()


def test_bash_stop_event(workdir: Path) -> None:
    """Setting stop kills the command within a poll."""
    stop = Event()
    threading.Timer(0.3, stop.set).start()
    start = time.monotonic()
    out = tools.bash(workdir, stop, "echo started; sleep 10")
    assert time.monotonic() - start < 3
    assert out.startswith("started") and "timeout" not in out


def test_bash_line_limit(workdir: Path) -> None:
    """Only the last MAX_LINES lines are returned."""
    out = run("bash", workdir, command=f"seq {tools.MAX_LINES + 10}")
    lines = out.splitlines()
    assert lines[0] == f"[output truncated, showing last {tools.MAX_LINES} of {tools.MAX_LINES + 10} lines]"
    assert lines[1] == "11" and lines[-1] == str(tools.MAX_LINES + 10)


def test_bash_char_limit(workdir: Path) -> None:
    """Long output is cut to the last MAX_CHARS characters."""
    out = run("bash", workdir, command=f"head -c {tools.MAX_CHARS * 2} /dev/zero | tr '\\0' z")
    assert out.startswith(f"[output truncated to last {tools.MAX_CHARS} chars]\n")
    assert out.endswith("z" * tools.MAX_CHARS)


# specs and run

def test_specs_match_tools() -> None:
    """Each built-in tool has a valid function spec loaded from prompts/tools."""
    assert list(tools.TOOLS) == ["read", "write", "edit", "bash"]
    assert tools.SPECS == [spec for _, spec in tools.TOOLS.values()]
    for name, (function, spec) in tools.TOOLS.items():
        fn = spec["function"]
        assert spec["type"] == "function" and fn["name"] == name == function.__name__
        params = fn["parameters"]
        assert params["type"] == "object"
        assert set(params["required"]) <= set(params["properties"])
        assert "{" not in fn["description"], "placeholders must be filled in"


def test_spec_descriptions_mention_limits() -> None:
    """{MAX_LINES} in descriptions is filled with the real value."""
    assert str(tools.MAX_LINES) in tools.TOOLS["read"][1]["function"]["description"]


def test_required_args_match_signatures() -> None:
    """Spec properties are exactly the tool's keyword arguments."""
    import inspect
    for function, spec in tools.TOOLS.values():
        args = list(inspect.signature(function).parameters)[2:]
        assert set(spec["function"]["parameters"]["properties"]) == set(args)


@pytest.mark.parametrize("name, arguments, expected", [
    ("nope", "{}", "Error: 'nope'"),
    ("read", "{bad", "Error: Expecting property name"),
    ("read", "{}", "Error: read() missing 1 required"),
    ("read", '{"path": "f", "extra": 1}', "Error: read() got an unexpected keyword"),
])
def test_run_returns_errors_as_text(workdir: Path, name: str, arguments: str, expected: str) -> None:
    """Unknown tools, bad json and bad arguments come back as Error text."""
    assert tools.run(name, arguments, workdir, Event()).startswith(expected)


def test_run_empty_arguments(workdir: Path) -> None:
    """An empty argument string means no arguments."""
    assert tools.run("bash", "", workdir, Event()).startswith("Error: bash() missing")
