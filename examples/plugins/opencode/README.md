# opencode

Makes py-agent look and behave like opencode. Like `claude_code`, it's a showcase of what plugins can change: UI, input, keys, tools, commands, prompt and the agent, all through the plugin api. Needs plugin api 2.

Install: copy this folder into `<config dir>/plugins/`. Don't enable it together with `claude_code`; both replace the same UI.

## What you get

**Look**
- Home screen: a block-letter logo, the prompt box and a few commands, centered. The first message switches to the session view.
- Session view: a header with the session title and tokens used; on terminals 120+ columns wide a sidebar instead, with title, context used, the todo list and modified files (`+added -removed`, from the undo snapshots). `ctrl+x b` toggles it.
- User messages in a box with a bar in the agent's color and attached files as chips; answers as markdown; reasoning as `Thinking: …` behind a bar.
- Tool calls like opencode: `→ Read path`, `← Edit path` with a numbered diff, `# Wrote path` with highlighted code, `$ command` under its description with the output, `✱ Glob "*.py" (3 matches)`, `✱ Grep "x" (5 matches)`, `→ List`, `% WebFetch url`, `# Todos` with `[✓]`/`[•]`/`[ ]`, and `◉ General Task — …` listing the subagent's own tool calls as it works.
- After each answer: `▣ Build · model · 4.2s`, plus `· interrupted` when stopped.
- The prompt box shows the agent, model, provider and thinking level; under it a sweeping spinner and `esc interrupt` while working.
- Footer: folder and git branch; after `ctrl+x` the leader keys.
- A `py-agent` theme in opencode's colors, plus all of textual's themes.

**Agents**
- `build` (all tools) and `plan` (no `write`/`edit`, commands ask first, a reminder to only plan); `tab` / `shift+tab` cycle them, `/agents` lists them.
- `general`, a subagent the model starts with the `task` tool. It gets a fresh conversation and every tool except `task` and the todo tools, runs with the same model, and returns its final answer. Typing `@general` in a message asks the model to hand the work to it.
- Custom agents: `.md` files in `~/.config/opencode/agent/` or `./.opencode/agent/` (or `agents/`), the file name being the agent's name. Frontmatter: `description`, `mode` (`primary`, `subagent` or `all`), `model` (`provider/id` or id), `tools` (`name: false` turns one off), `permission`; the body is added to the system prompt. Agents can also be set in settings, see below.

**Tools**
- `glob`, `grep` and `list`, respecting `.gitignore`, newest files first, at most 100 results.
- `webfetch`: a url as markdown (rough), text or html, at most 5 MB.
- `todowrite` / `todoread`: the session's todo list, shown in the log and the sidebar.
- `task`: the subagent above.
- `bash` takes an optional `description`, shown above the command.

**Permissions**
- Like opencode, everything is allowed by default. Set `"edit"`, `"bash"` or `"webfetch"` to `"ask"` or `"deny"`; `"bash"` can also map command patterns (`"git *"`) to an action, the last match winning. A pattern never allows a command with `;`, `&`, `|`, `$`, backticks, redirects or brackets; it asks instead.
- The prompt: `enter` allows once, `a` allows always (for this session: all edits, or the command's program and subcommand like `git status`), `esc` rejects and stops the answer.
- Read before edit: `write` and `edit` refuse an existing file the model hasn't read in this session, or that changed since.

**Keys**
- `ctrl+x` then: `n` new session, `l` sessions, `m` models, `a` agents, `t` themes, `u` undo, `r` redo, `c` compact, `e` editor, `x` export, `y` copy last answer, `d` details, `i` init, `h` help, `b` sidebar, `q` quit.
- `ctrl+p` command list, `ctrl+t` cycle thinking level, `tab`/`shift+tab` agents, `pageup`/`pagedown` scroll, `esc` twice interrupts, `ctrl+c` clears the input or quits, `ctrl+d` on an empty input quits.
- `!command` runs it without the model (the box shows `Shell`); the command and its output join the conversation.
- `/` opens a command menu and `@` a file and subagent menu, both above the prompt.

**Commands**
- `/new`, `/sessions` (titles, grouped by day, resumable), `/models` (recent first, remembered across runs), `/agents`, `/themes` (previewed while moving through the list, remembered), `/help` (all commands with their keys), `/editor` (`$VISUAL`/`$EDITOR`), `/export` (markdown into `<config dir>/opencode/exports/`, opened in the editor if set), `/copy`, `/details` (tool output on or off), `/thinking` (reasoning on or off), `/init` (writes `AGENTS.md`), `/summarize` (same as `/compact`). py-agent's own commands still work.
- Custom commands: `.md` files in `~/.config/opencode/command/` or `./.opencode/command/` (or `commands/`) become `/<file name>`. `$ARGUMENTS` is what follows the command, `$1`, `$2`... its words, ``!`cmd` `` the command's output, `@file` attaches a file. Frontmatter: `description`, `agent`, `model` (both stay picked afterwards).

**Session titles**: after the first answer the model names the session, in one extra request. Titles show in the header, sidebar and `/sessions`.

A global `AGENTS.md` in the config dir or `~/.config/opencode/` is added to every project's system prompt, and a few lines on using the tools.

## Settings

In `settings.json`, all optional:

```json
"plugins": {
  "opencode": {
    "permission": {"edit": "ask", "bash": {"*": "ask", "git status*": "allow"}, "webfetch": "allow"},
    "agent": {
      "review": {"description": "Reviews code", "mode": "primary", "prompt": "Only review, don't edit.",
                 "tools": {"write": false, "edit": false}}
    }
  }
}
```

The theme, recent models, tool details and thinking toggles and session titles are kept in `<config dir>/opencode/state.json`.

## Files

`main.py` is the plugin: `setup(api)` installs the pieces from `modules/` and builds the `Chat` subclass, with the CSS and key bindings.

| `modules/` | what it does |
|---|---|
| `agents.py` | agent specs (built-in, `.md` files, settings), frontmatter, permissions, and the `Agent` subclass: per-agent tools, prompt and plan reminder, read before edit, the permission prompt. |
| `tools.py` | `glob`, `grep`, `list`, `webfetch`, `todowrite`, `todoread`, `task` and the described `bash`. |
| `slash.py` | the commands and custom commands. |
| `ui.py` | `OpenCodeMixin`: layout, home screen, header, sidebar, footer, messages, tool blocks, shell mode, leader keys, dialogs, editor, export, titles. |
| `prompt.py` | the input (command, file and agent menu, `tab`, leader keys) and the permission picker. |
| `dialog.py` | the searchable list dialog. |
| `render.py` | pure functions turning tool calls, diffs, todos, the picker, menus, sidebar and spinner into rich renderables. |
| `state.py` | `state.json`. |

## What the plugin api gained for this

- `Agent.request_tools()`: the tools offered per request, so agents can turn tools off.
- `api.snapshot` and `snapshot.changes(...)`: per-file line counts between two undo snapshots, for "Modified Files".
- A command replying `("", None)` shows nothing, so `/models` and friends can open a dialog instead.
- `@file` mentions in a `"send"` command's prompt are attached, for custom commands.

## Limits

- No sharing (`/share`), LSP diagnostics, MCP servers or cost tracking; those need opencode's server or its integrations.
- One provider format: whatever py-agent supports (OpenAI-compatible), not opencode's provider list or login.
- `grep` is a Python regex over the files, not ripgrep; `webfetch`'s markdown is a rough conversion.
- Subagent sessions are saved in `<config dir>/opencode/tasks/` and can't be opened from `/sessions`.
- ``!`cmd` `` in custom commands runs before the message is sent, on the UI thread, so a slow command freezes the UI until it ends.
- Without a UI to ask (`-p`), tool calls set to `ask` are rejected.
