"""Pure rendering: tool blocks, edit diffs, todos, pickers and the welcome box, as rich renderables."""

import difflib
import json
from pathlib import Path

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ACCENT = "#d77757"
TITLES = {"read": "Read", "write": "Write", "edit": "Update", "bash": "Bash",
          "todo_write": "Update Todos", "exit_plan_mode": "Plan"}
MASCOT = Text("  ▄▄▄▄▄\n █ ● ● █~\n  ▀█▀█▀", ACCENT)
TIPS = "/ for commands · @ for files · ! for bash mode · # to memorize · esc twice to rewind · ctrl+o transcript"
# lines shown under a tool call until ctrl+o
SHORT = 3


def parse_args(arguments: str) -> dict:
    """Tool call arguments as a dict, empty when they aren't a json object."""
    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def plural(n: int, word: str) -> str:
    """1 line, 2 lines."""
    return f"{n} {word}{'' if n == 1 else 's'}"


def call_line(name: str, args: dict) -> str:
    """Bash(ls -la), Read(src/main.py), ... like claude code shows tool calls."""
    title = TITLES.get(name, name.capitalize())
    if name in ("todo_write", "exit_plan_mode"):
        return title
    main = str(args.get("command") or args.get("path") or next(iter(args.values()), ""))
    return f"{title}({main.splitlines()[0] if main else ''})"


def todos(items: list, indent: str = "     ") -> Text:
    """☒ done, ☐ in progress (bold), ☐ pending; one per line, each starting with a newline."""
    out = Text()
    for todo in items:
        if not isinstance(todo, dict):
            continue
        done, current = todo.get("status") == "completed", todo.get("status") == "in_progress"
        style = "strike dim" if done else "bold" if current else ""
        out.append("\n" + indent).append(f"{'☒' if done else '☐'} {todo.get('content', '')}", style)
    return out


def edit_start(path: Path, new_text: str) -> int | None:
    """Line where an edit's new text starts in the file, None if it can't be found any more."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    at = text.find(new_text) if new_text else -1
    return text.count("\n", 0, at) + 1 if at >= 0 else None


def _diff(args: dict, start: int | None, full: bool) -> Text:
    old, new = str(args.get("oldText", "")).splitlines(), str(args.get("newText", "")).splitlines()
    rows, added, removed = [], 0, 0
    for group in difflib.SequenceMatcher(None, old, new).get_grouped_opcodes(1):
        if rows:
            rows.append((None, "…", "dim"))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows += [(j, " " + new[j], "dim") for j in range(j1, j2)]
                continue
            rows += [(i, "-" + old[i], "on #4a2b2b") for i in range(i1, i2)]
            rows += [(j, "+" + new[j], "on #2b4a2b") for j in range(j1, j2)]
            removed, added = removed + i2 - i1, added + j2 - j1
    out = Text(f"\n  ⎿  Updated {args.get('path')} with {plural(added, 'addition')} and {plural(removed, 'removal')}")
    shown = rows if full else rows[:12]
    for i, line, style in shown:
        # old and new text start on the same line, so both sides number from start
        number = f"{start + i:>4} " if start is not None and i is not None else "     " if start is not None else ""
        out.append("\n     ").append(number, "dim").append(line, style)
    if len(rows) > len(shown):
        out.append(f"\n     … +{len(rows) - len(shown)} lines (ctrl+o to expand)", "dim")
    return out


def _lines(rows: list[str], full: bool) -> Text:
    if not full and len(rows) > SHORT + 1:
        rows = rows[:SHORT] + [f"… +{len(rows) - SHORT} lines (ctrl+o to expand)"]
    return Text("\n  ⎿  " + "\n     ".join(rows or ["(no output)"]), "dim")


def result(name: str, args: dict, result: str, full: bool = False, start: int | None = None) -> Text:
    """The ⎿ lines under a tool call: a diff for edits, a summary or the first lines, everything when full."""
    if result.startswith("Error:"):
        return Text("\n  ⎿  " + (result if full else result.splitlines()[0]), "red")
    if name == "edit":
        return _diff(args, start, full)
    rows = result.splitlines()
    if name == "read":
        return _lines(rows, True) if full else Text(f"\n  ⎿  Read {plural(len(rows), 'line')}", "dim")
    if name == "write":
        content = str(args.get("content", "")).splitlines()
        return _lines([f"Wrote {plural(len(content), 'line')} to {args.get('path')}", *content], full)
    if name in ("todo_write", "exit_plan_mode"):
        return Text()
    return _lines(rows, full)


def tool(name: str, args: dict, output: str | None, full: bool, start: int | None = None):
    """A whole tool block: ⏺ call line, then todos, the plan or the result."""
    head = Text.assemble(("⏺ ", "green"), (call_line(name, args), "bold"))
    if name == "todo_write":
        head.append_text(todos(args.get("todos") or []))
    if output is not None:
        head.append_text(result(name, args, output, full, start))
    if name == "exit_plan_mode":
        plan = Panel(Markdown(str(args.get("plan", ""))), border_style=ACCENT, title="plan", title_align="left")
        return Group(head, plan)
    return head


def picker(question: str, options: list[str], selected: int, body=None) -> Panel:
    """A question with numbered options, ❯ on the selected one."""
    lines = Text(question, "bold")
    lines.append("\n")
    for i, option in enumerate(options):
        mark = "❯" if i == selected else " "
        lines.append(f"\n{mark} {i + 1}. {option}", ACCENT if i == selected else "")
    hint = Text("\n↑/↓ to select · enter to confirm · esc to cancel", "dim")
    return Panel(Group(*([body] if body else []), lines, hint), border_style=ACCENT)


def menu(items: list[tuple[str, str]], selected: int) -> Text:
    """Completion menu under the input: name and description per row."""
    width = max((len(name) for name, _ in items), default=0) + 2
    out = Text()
    for i, (name, description) in enumerate(items):
        style = ACCENT if i == selected else "dim"
        out.append(("\n" if i else "") + f"{name:<{width}}", style).append(description, style if i == selected else "dim")
    return out


def welcome(cwd: Path) -> Panel:
    """Mascot, greeting, cwd and tips in a rounded box."""
    grid = Table.grid(padding=(0, 2))
    grid.add_row(MASCOT, Text.assemble(("✻ ", ACCENT), ("Welcome to py-agent!", "bold"),
                                       f"\n\n/help for help · shift+tab cycles modes\ncwd: {cwd}"))
    return Panel(Group(grid, Text(), Text(TIPS, "dim")), border_style=ACCENT, expand=False)
