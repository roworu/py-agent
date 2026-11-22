"""opencode's other tools: glob, grep, list, webfetch, todowrite, todoread and task (a subagent), and a bash
taking a description for the ui."""

import fnmatch
import json
import os
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

LIMIT = 100
# files bigger than this are skipped by grep
MAX_FILE = 1_000_000
MAX_FETCH = 5_000_000
MAX_CHARS = 50_000


def _base(cwd: Path, path: str) -> Path:
    return (cwd / Path(path or ".").expanduser()).resolve()


def _shown(cwd: Path, p: Path) -> str:
    """A path relative to cwd when inside it."""
    try:
        return str(p.relative_to(cwd))
    except ValueError:
        return str(p)


def _matches(rel: str, pattern: str) -> bool:
    """Glob match where a pattern without / matches the file name at any depth and **/ also matches no folder."""
    rel = rel.replace(os.sep, "/")
    if "/" not in pattern:
        return fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pattern)
    return fnmatch.fnmatch(rel, pattern) or pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:])


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


class _Html(HTMLParser):
    """Html to plain text, or rough markdown: headings, links, list items, bold, italic and code."""

    SKIP = {"script", "style", "noscript", "head", "svg", "template"}
    BLOCK = {"p", "div", "section", "article", "br", "tr", "ul", "ol", "table", "header", "footer", "main",
             "nav", "pre", "blockquote", "hr", "form"}
    MARKS = {"strong": "**", "b": "**", "em": "_", "i": "_", "code": "`"}

    def __init__(self, markdown: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.markdown, self.out, self.skip, self.links = markdown, [], 0, []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n\n")
        elif not self.markdown:
            if tag == "li":
                self.out.append("\n")
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "a":
            self.links.append(dict(attrs).get("href"))
            self.out.append("[")
        elif tag in self.MARKS:
            self.out.append(self.MARKS[tag])

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif not self.markdown:
            return
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n")
        elif tag == "a" and self.links:
            href = self.links.pop()
            self.out.append(f"]({href})" if href else "]")
        elif tag in self.MARKS:
            self.out.append(self.MARKS[tag])

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        text = re.sub(r" *\n *", "\n", "".join(self.out))
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_text(html: str, markdown: bool) -> str:
    """Readable text of a page."""
    parser = _Html(markdown)
    parser.feed(html)
    parser.close()
    return parser.text()


def install(api, specs: list) -> None:
    """Register the tools; task runs subagents from specs."""
    files = api.files
    bash = api.tools.TOOLS["bash"]
    run_bash = bash[0]

    def listed(base: Path, stop) -> list[Path]:
        """Files under base: git's list when it's a repo (respects .gitignore), else a walk."""
        if base.is_file():
            return [base]
        found = []
        for rel in files.list_files(base):
            if stop.is_set():
                break
            found.append(base / rel)
        return found

    def glob(cwd: Path, stop, pattern: str, path: str = ".") -> str:
        """Files matching a glob pattern, newest first."""
        base = _base(cwd, path)
        hits = sorted((p for p in listed(base, stop) if _matches(_shown(base, p), pattern)), key=_mtime, reverse=True)
        if not hits:
            return "No files found"
        text = "\n".join(_shown(cwd, p) for p in hits[:LIMIT])
        if len(hits) > LIMIT:
            text += f"\n\n(Results are truncated: showing the newest {LIMIT} of {len(hits)}. Use a more specific path or pattern.)"
        return text

    def grep(cwd: Path, stop, pattern: str, path: str = ".", include: str = "") -> str:
        """Lines matching a regex in files under path, grouped by file, newest files first."""
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: bad regex: {e}"
        base = _base(cwd, path)
        hits: dict[Path, list[tuple[int, str]]] = {}
        for p in listed(base, stop):
            if include and not _matches(_shown(base, p), include):
                continue
            try:
                if p.stat().st_size > MAX_FILE:
                    continue
                text = p.read_text(errors="replace")
            except OSError:
                continue
            # binary files
            if "\0" in text:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.setdefault(p, []).append((n, line.strip()[:200]))
        total = sum(len(rows) for rows in hits.values())
        if not total:
            return "No files found"
        out, shown = [f"Found {total} matches"], 0
        for p in sorted(hits, key=_mtime, reverse=True):
            if shown >= LIMIT:
                break
            rows = hits[p][:LIMIT - shown]
            shown += len(rows)
            out += ["", f"{_shown(cwd, p)}:", *(f"  Line {n}: {line}" for n, line in rows)]
        if shown < total:
            out += ["", f"(Results are truncated: showing {shown} of {total} matches. Use a more specific path or pattern.)"]
        return "\n".join(out)

    def ls(cwd: Path, stop, path: str = ".", ignore: list | None = None) -> str:
        """A tree of the files under path."""
        base = _base(cwd, path)
        if not base.is_dir():
            return f"Error: {path} is not a folder"
        rels = sorted(_shown(base, p).replace(os.sep, "/") for p in listed(base, stop))
        rels = [r for r in rels if not any(_matches(r, g) or fnmatch.fnmatch(r, g.rstrip("/") + "/*") for g in ignore or [])]
        out, opened = [f"{base}/"], set()
        for rel in rels[:LIMIT]:
            parts = rel.split("/")
            for depth in range(len(parts) - 1):
                folder = "/".join(parts[:depth + 1])
                if folder not in opened:
                    opened.add(folder)
                    out.append("  " * (depth + 1) + parts[depth] + "/")
            out.append("  " * len(parts) + parts[-1])
        if len(rels) > LIMIT:
            out.append(f"\n(showing {LIMIT} of {len(rels)} files)")
        return "\n".join(out)

    ls.__name__ = "list"

    def webfetch(cwd: Path, stop, url: str, format: str = "markdown", timeout: int = 30) -> str:
        """Fetch a url as markdown, text or html."""
        if not url.startswith(("http://", "https://")):
            return "Error: the url must start with http:// or https://"
        request = urllib.request.Request(url, headers={"User-Agent": "py-agent", "Accept": "text/html, text/plain, */*"})
        try:
            with urllib.request.urlopen(request, timeout=min(max(timeout, 1), 120)) as response:
                data = response.read(MAX_FETCH + 1)
                kind, charset = response.headers.get_content_type(), response.headers.get_content_charset()
        except (urllib.error.URLError, OSError, ValueError) as e:
            return f"Error: {e}"
        if len(data) > MAX_FETCH:
            return "Error: the response is over 5 MB"
        text = data.decode(charset or "utf-8", errors="replace")
        if "html" in kind and format != "html":
            text = html_text(text, format == "markdown")
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n\n[truncated to the first {MAX_CHARS} chars]"
        return text

    def todowrite(cwd: Path, stop, todos: list) -> str:
        """Replace the session's todo list."""
        api.agent.todos = [t for t in todos if isinstance(t, dict)]
        left = sum(t.get("status") not in ("completed", "cancelled") for t in api.agent.todos)
        return f"{left} todos left\n" + json.dumps(api.agent.todos, indent=2)

    def todoread(cwd: Path, stop) -> str:
        """The session's todo list."""
        return json.dumps(api.agent.todos, indent=2)

    def task(cwd: Path, stop, description: str, prompt: str, subagent_type: str = "general") -> str:
        """Run a subagent on a task and return its final answer."""
        names = [s.name for s in specs if s.subagent]
        if subagent_type not in names:
            return f"Error: unknown subagent {subagent_type}, use one of {', '.join(names)}"
        parent = api.agent
        sub = api.Agent(api.config, api.cwd)
        sub.parent, sub.model, sub.thinking = parent, parent.model, parent.thinking
        sub.switch(subagent_type)
        # esc stops the subagent too, and a rejected permission stops the parent
        sub.stopped = stop
        sub.always = parent.always
        # kept apart, so /sessions only lists the user's own sessions
        sub.session = api.config.dir / "opencode" / "tasks" / sub.session.name
        try:
            sub.run(prompt, SubEvents(parent.events, api))
        except api.llm.ApiError as e:
            return f"Error: {e}"
        answer = next((m["content"] for m in reversed(sub.messages) if m["role"] == "assistant" and m.get("content")), "")
        return answer or "(the subagent gave no answer)"

    def described_bash(cwd: Path, stop, command: str, timeout: int = 120, description: str = "") -> str:
        """Run bash; the description is only for the ui."""
        return run_bash(cwd, stop, command, timeout)

    described_bash.__name__ = "bash"
    spec = bash[1]["function"]
    props = {**spec["parameters"]["properties"], "description": {
        "type": "string", "description": "What the command does in 5-10 words, shown to the user"}}
    api.add_tool(described_bash, spec["description"], props, spec["parameters"]["required"])

    path_prop = {"type": "string", "description": "Folder to search in, default the current one"}
    api.add_tool(glob, f"Find files by glob pattern (e.g. **/*.py, src/**/*.ts), newest first, at most {LIMIT}. "
                 "Respects .gitignore.", {"pattern": {"type": "string"}, "path": path_prop}, ["pattern"])
    api.add_tool(grep, f"Search file contents with a regex; returns matching lines with line numbers, at most {LIMIT}. "
                 "include filters files by glob (e.g. *.py). Respects .gitignore.",
                 {"pattern": {"type": "string"}, "path": path_prop, "include": {"type": "string"}}, ["pattern"])
    api.add_tool(ls, "List the files under a folder as a tree. ignore takes glob patterns to leave out.",
                 {"path": path_prop, "ignore": {"type": "array", "items": {"type": "string"}}}, [])
    api.add_tool(webfetch, "Fetch a url and return its content as markdown (default), text or html.",
                 {"url": {"type": "string"}, "format": {"type": "string", "enum": ["markdown", "text", "html"]},
                  "timeout": {"type": "integer", "description": "seconds, at most 120"}}, ["url"])
    api.add_tool(todowrite, "Keep a todo list for multi-step work, shown to the user. Send the whole list each time; "
                 "keep one item in_progress and mark items completed as soon as they're done.",
                 {"todos": {"type": "array", "items": {"type": "object", "properties": {
                     "id": {"type": "string"}, "content": {"type": "string"},
                     "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                     "priority": {"type": "string", "enum": ["high", "medium", "low"]}},
                     "required": ["content", "status"]}}}, ["todos"])
    api.add_tool(todoread, "Read the current todo list.", {}, [])
    agents = "\n".join(f"- {s.name}: {s.description}" for s in specs if s.subagent)
    api.add_tool(task, "Start a subagent on a self-contained task (searching a codebase, researching a question, a "
                 "multi-step job) and get its final answer back. It doesn't see this conversation, so describe the "
                 f"task fully in prompt. description is 3-5 words for the user. Subagents:\n{agents}",
                 {"description": {"type": "string"}, "prompt": {"type": "string"},
                  "subagent_type": {"type": "string", "enum": [s.name for s in specs if s.subagent]}},
                 ["description", "prompt"])


class SubEvents:
    """A subagent's events: its tool calls go to the running task block, questions to the user, the rest is dropped."""

    def __init__(self, parent, api) -> None:
        self.parent, self.api = parent, api

    def text(self, text: str) -> None: ...
    def reasoning(self, text: str) -> None: ...
    def tool_result(self, id: str, result: str) -> None: ...
    def retry(self, delay: float, error: str) -> None: ...
    def compacted(self) -> None: ...

    def tool_call(self, id: str, name: str, arguments: str) -> None:
        if chat := self.api.chat:
            chat.call_from_thread(chat.task_step, name, arguments)

    def ask(self, question: str, options: list[str], body=None) -> int | None:
        return self.parent.ask(question, options, body) if self.parent else None
