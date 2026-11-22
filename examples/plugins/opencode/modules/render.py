"""Pure rendering: logo, tool blocks, diffs, todos, the permission prompt, menus, sidebar and spinner, as rich renderables.

c is colors(app): the theme's colors, so everything follows /themes."""

import difflib
import json
from pathlib import Path

from rich.console import Group
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.color import Color

# "py agent" in block letters, the first word muted like opencode's logo
LOGO = (["█▀▀█ █  █", "█▀▀▀ ▀▄▄█", "▀    ▄▄▄▀"],
        ["█▀▀█ █▀▀▀ █▀▀▀ █▀▀▄ ▀█▀", "█▀▀█ █ ▀█ █▀▀  █  █  █ ", "▀  ▀ ▀▀▀▀ ▀▀▀▀ ▀  ▀  ▀ "])
# lines of tool output shown with details on
SHORT = 10
# theme colors given to primary agents in order
AGENT_COLORS = ["secondary", "accent", "success", "warning", "primary", "error"]


def colors(app) -> dict[str, str]:
    """The theme's colors plus muted text and diff backgrounds."""
    v = app.get_css_variables()
    bg = Color.parse(v["background"])
    return {**v, "muted": Color.parse(v["foreground"]).blend(bg, 0.45).hex,
            "faint": Color.parse(v["foreground"]).blend(bg, 0.7).hex,
            "added": Color.parse(v["success"]).blend(bg, 0.8).hex,
            "removed": Color.parse(v["error"]).blend(bg, 0.8).hex}


def logo(c: dict) -> Text:
    rows = [Text.assemble((left, c["muted"]), "  ", (right, f"bold {c['foreground']}")) for left, right in zip(*LOGO)]
    return Text("\n").join(rows)


def parse_args(arguments: str) -> dict:
    """Tool call arguments as a dict, empty when they aren't a json object."""
    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def rel(cwd: Path, path: str) -> str:
    """A path relative to cwd when inside it."""
    p = (cwd / Path(path or ".").expanduser()).resolve()
    try:
        return str(p.relative_to(cwd)) or "."
    except ValueError:
        return str(p)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _count(name: str, output: str | None) -> int:
    """Matches a glob or grep found."""
    if not output or output == "No files found" or output.startswith("Error:"):
        return 0
    if name == "grep":
        return int(output.split()[1])
    # the truncation note comes after a blank line
    return len(output.split("\n\n")[0].splitlines())


def title(name: str, args: dict, output: str | None, cwd: Path) -> tuple[str, str]:
    """The icon and one line opencode shows for a tool call."""
    path = rel(cwd, str(args.get("path", ".")))
    where = f" in {path}" if args.get("path") else ""
    count = _count(name, output)
    if name == "read":
        extra = ", ".join(f"{k}={args[k]}" for k in ("offset", "limit") if k in args)
        return "→", f"Read {path}" + (f" [{extra}]" if extra else "")
    if name == "write":
        return "#", f"Wrote {path}" if output else f"Write {path}"
    if name == "edit":
        return "←", f"Edit {path}"
    if name == "bash":
        return "$", str(args.get("command", ""))
    if name == "glob":
        return "✱", f'Glob "{args.get("pattern", "")}"{where}' + (f" ({plural(count, 'match')})" if output else "")
    if name == "grep":
        return "✱", f'Grep "{args.get("pattern", "")}"{where}' + (f" ({plural(count, 'match')})" if output else "")
    if name == "list":
        return "→", f"List {path}"
    if name == "webfetch":
        return "%", f"WebFetch {args.get('url', '')}"
    if name in ("todowrite", "todoread"):
        return "#", "Todos"
    if name == "task":
        return "◉", f"{str(args.get('subagent_type') or 'general').capitalize()} Task — {args.get('description', '')}"
    return "⚙", f"{name} {', '.join(f'{k}={v}' for k, v in args.items())}"[:120]


def todos(items: list, c: dict) -> Text:
    """[✓] done, [•] in progress, [ ] pending, one per line."""
    out = Text()
    for todo in items:
        if not isinstance(todo, dict):
            continue
        status = todo.get("status")
        mark, style = {"completed": ("[✓]", f"strike {c['muted']}"), "cancelled": ("[✗]", f"strike {c['faint']}"),
                       "in_progress": ("[•]", c["warning"])}.get(status, ("[ ]", c["foreground"]))
        out.append(("\n" if out else "") + f"{mark} {todo.get('content', '')}", style)
    return out


def edit_start(path: Path, new_text: str) -> int | None:
    """Line where an edit's new text starts in the file, None if it can't be found any more."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    at = text.find(new_text) if new_text else -1
    return text.count("\n", 0, at) + 1 if at >= 0 else None


def diff(args: dict, start: int | None, c: dict) -> Text:
    """Unified diff of an edit with old and new line numbers, added and removed lines on a colored background."""
    old, new = str(args.get("oldText", "")).splitlines(), str(args.get("newText", "")).splitlines()
    first = start or 1
    width = max((len(line) for line in old + new), default=0) + 2
    out = Text()
    for n, group in enumerate(difflib.SequenceMatcher(None, old, new).get_grouped_opcodes(2)):
        if n:
            out.append(" " * 11 + "⋮\n", c["faint"])
        for tag, i1, i2, j1, j2 in group:
            rows = []
            if tag == "equal":
                rows = [(first + i, first + j, " ", new[j], "") for i, j in zip(range(i1, i2), range(j1, j2))]
            else:
                rows += [(first + i, None, "-", old[i], c["removed"]) for i in range(i1, i2)]
                rows += [(None, first + j, "+", new[j], c["added"]) for j in range(j1, j2)]
            for a, b, sign, line, bg in rows:
                numbers = f"{a or '':>4} {b or '':>4} " if start else ""
                style = f"on {bg}" if bg else c["muted"]
                out.append(numbers, c["faint"]).append(f"{sign} {line}".ljust(width), style).append("\n")
    out.right_crop(1)
    return out


def tool(name: str, args: dict, output: str | None, details: bool, cwd: Path, c: dict,
         start: int | None = None, steps: list | None = None):
    """A whole tool block: icon and title, then the output when details are on."""
    icon, line = title(name, args, output, cwd)
    failed = output is not None and output.startswith("Error:")
    running = output is None
    head = Text.assemble((icon + " ", c["muted"] if running else c["error"] if failed else c["primary"]),
                         (line, c["muted"] if running else c["foreground"]))
    if name == "bash" and args.get("description"):
        head = Text.assemble((f"# {args['description']}\n", c["muted"]), head)
    parts = [head]
    if name == "todowrite":
        parts.append(todos(args.get("todos") or [], c))
    if name == "task":
        for step_name, step_args in (steps or [])[-6:]:
            parts.append(Text.assemble(("└ ", c["faint"]), (" ".join(title(step_name, step_args, "", cwd)), c["muted"])))
        if output is None:
            parts.append(Text(f"{plural(len(steps or []), 'tool call')} so far", c["faint"]))
    if failed:
        parts.append(Text(output if details else output.splitlines()[0], c["error"]))
    elif output is not None and details:
        parts += _details(name, args, output, cwd, c, start)
    return Group(*parts)


def _lines(text: str, c: dict) -> Text:
    rows = text.splitlines() or ["(no output)"]
    if len(rows) > SHORT:
        rows = rows[:SHORT] + [f"… {len(rows) - SHORT} more lines"]
    return Text("\n".join(rows), c["muted"])


def _details(name: str, args: dict, output: str, cwd: Path, c: dict, start: int | None) -> list:
    if name == "edit":
        return [diff(args, start, c)]
    if name == "write":
        content = str(args.get("content", ""))
        rows = content.splitlines()
        code = "\n".join(rows[:SHORT])
        lexer = Syntax.guess_lexer(str(args.get("path", "")), code)
        syntax = Syntax(code, lexer, theme="ansi_dark", line_numbers=True, background_color="default", word_wrap=True)
        return [syntax, *([Text(f"… {len(rows) - SHORT} more lines", c["faint"])] if len(rows) > SHORT else [])]
    if name in ("bash", "webfetch", "list"):
        return [_lines(output, c)]
    if name == "task":
        return [Text(output.splitlines()[0][:200] if output.strip() else "", c["muted"])]
    return []


def picker(question: str, options: list[str], selected: int, body, c: dict) -> Text:
    """opencode's permission prompt: what it's about, then the options side by side."""
    out = Text.assemble(("△ ", c["warning"]), (question, f"bold {c['foreground']}"))
    if body is not None:
        out.append("\n").append_text(body if isinstance(body, Text) else Text(str(body)))
    out.append("\n\n")
    for i, option in enumerate(options):
        style = f"bold {c['background']} on {c['warning']}" if i == selected else f"{c['foreground']} on {c['panel']}"
        out.append(f" {option} ", style).append("  ")
    keys = "  ⇆ select · enter confirm · a allow always · esc reject" if len(options) == 3 else "  ⇆ select · enter confirm · esc cancel"
    return out.append(keys, c["muted"])


def menu(items: list[tuple[str, str]], selected: int, c: dict) -> Text:
    """Completion menu over the input: name and description per row."""
    width = max((len(name) for name, _ in items), default=0) + 2
    out = Text()
    for i, (name, description) in enumerate(items):
        on = f" on {c['panel']}" if i == selected else ""
        out.append(("\n" if i else "") + f" {name:<{width}}", (c["primary"] if i == selected else c["foreground"]) + on)
        out.append(f"{description} ", c["muted"] + on)
    return out


def hints(rows: list[tuple[str, str, str]], c: dict) -> Table:
    """Command, what it does and its key, for the home screen."""
    grid = Table.grid(padding=(0, 2))
    for name, text, key in rows:
        grid.add_row(Text(name, c["foreground"]), Text(text, c["muted"]), Text(key, c["faint"]))
    return grid


def spinner(frame: int, color: str, c: dict) -> Text:
    """A block sweeping back and forth with a fading trail."""
    cells, width = 8, 8
    pos = frame % (2 * cells - 2)
    head = pos if pos < cells else 2 * cells - 2 - pos
    step = 1 if pos < cells else -1
    trail = {head: color, head - step: Color.parse(color).blend(Color.parse(c["background"]), 0.4).hex,
             head - 2 * step: Color.parse(color).blend(Color.parse(c["background"]), 0.7).hex}
    return Text.assemble(*(("■", trail[i]) if i in trail else ("⬝", c["faint"]) for i in range(width)))


def sidebar(title: str, tokens: int, limit: int | None, todo: list, changes: list, c: dict) -> Group:
    """Session title, context used, the todo list and modified files."""
    parts = [Text(title, f"bold {c['foreground']}"), Text()]
    context = Text.assemble(("Context\n", f"bold {c['foreground']}"), (f"{tokens:,} tokens", c["muted"]))
    if limit:
        context.append(f"\n{tokens / limit:.0%} used", c["muted"])
    parts += [context, Text()]
    if todo:
        parts += [Text("Todo", f"bold {c['foreground']}"), todos(todo, c), Text()]
    if changes:
        files = Text("Modified Files", f"bold {c['foreground']}")
        for path, added, removed in changes:
            files.append(f"\n{path}  ", c["muted"]).append(f"+{added}", c["success"]).append(f" -{removed}", c["error"])
        parts.append(files)
    return Group(*parts)
