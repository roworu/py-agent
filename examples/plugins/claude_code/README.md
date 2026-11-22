# claude_code

Makes py-agent look and behave like Claude Code. It's a showcase of what plugins can change: UI, input, keys, tools, commands, prompt and the agent, all without touching py-agent itself.

Install: copy this folder into `<config dir>/plugins/`.

## What you get

**Look**
- Welcome box with a mascot, the model, cwd, tips and recent sessions; it starts at the top and the input follows the log until it fills the screen.
- `⏺` before answers; tool calls as `Bash(ls)`, `Read(path)`, `Update(path)` with `⎿` results, the dot dim while running and red on errors.
- Edit diffs with line numbers in red and green; writes show their first lines.
- A spinner ("✻ Pondering… (12s · ↓ 300 tokens · esc to interrupt)") that names the todo in progress.
- The todo list under the spinner while the model works; the spinner hides while a question is open.
- Status line: the mode on the left, model and context left on the right.
- Reasoning collapsed to `✻ Thinking…`.
- `ctrl+o` toggles transcript mode: reasoning and full tool output everywhere.

**Approvals and modes**
- A picker before `write`, `edit` and `bash`: `↑`/`↓` and `enter`, or a number; `esc` says no and stops the turn.
- "Don't ask again" for a command saves its prefix (`git status`, `npm test`) for this folder in `<config dir>/claude_code/allowed.json`. Commands with `;`, `&`, `|`, `$`, backticks, redirects or brackets never match a saved prefix.
- Modes, cycled with `shift+tab` and shown in the status line:
  - normal: asks before edits and commands
  - accept edits: asks before commands only
  - plan: edits are refused and only read-only commands run (`ls`, `cat`, `grep`, `rg`, `find`, `git status/log/diff/show`, ..., piped together if needed), without asking; the model researches and presents a plan with `exit_plan_mode`, and approving it switches to accept edits or normal
- Read before edit: `write` and `edit` refuse a file the model hasn't read in this conversation, or that changed since it did.

**Input**
- A `>` prompt in a rounded box; `?` on an empty input lists the shortcuts.
- Typing `/` opens a command menu with descriptions, added to py-agent's `@` file menu. `↑`/`↓` select, `tab` completes, `enter` runs the command or inserts the file, `esc` closes.
- `@path` attachments (built into py-agent) are listed under the message, and the file counts as read.
- `! cmd` runs a command without the model and adds it and its output to the conversation.
- `# note` appends `- note` to `CLAUDE.md` in the current folder and applies it right away.
- `esc` twice clears the input; on an empty input it opens rewind: pick an earlier message to go back to, its later messages and file changes are undone, and the message goes back into the input.

**Commands**
- `/init` asks the model to write a `CLAUDE.md`.
- `/context` shows a grid of how full the context window is.
- Custom commands: every `.md` in `~/.claude/commands/` and `./.claude/commands/` becomes `/<file name>`. `$ARGUMENTS` is replaced with what follows the command; optional frontmatter sets `description` and `argument-hint`. Project commands win over user ones; built-in commands can't be replaced.

**Context**
- After an answer, `/compact` runs by itself once the last request used 80% of the context window (the model's `contextWindow` in `settings.json`); the status line shows how much is left.

A few lines are also added to the system prompt (be concise, prefer editing files, no unasked comments).

## Settings

Constants at the top of the files:

- `modules/context.py`: `DEFAULT_CONTEXT` (for models without `contextWindow`), `AUTO_COMPACT`.
- `main.py`: `PROMPT`, the extra system prompt text.
- `modules/render.py`: `ACCENT` color, `MASCOT`, `SHORT` (result lines shown before `ctrl+o`).
- `modules/slash.py`: `INIT`, the `/init` prompt.

## Files

`main.py` is the plugin: `setup(api)` installs the pieces from `modules/` and builds the `Chat` subclass, with the CSS and key bindings.

| `modules/` | what it does |
|---|---|
| `render.py` | pure functions turning tool calls, results, diffs, todos, pickers and menus into rich renderables; the welcome box. |
| `look.py` | `LookMixin`: spinner, todo list, status line, `⏺` answers, collapsed thinking, tool blocks, transcript mode. |
| `picker.py` | draws py-agent's `Picker` (used by `ask`) like Claude Code. |
| `approval.py` | an `Agent` subclass whose `before_tool` asks before `write`/`edit`/`bash` with `agent.ask`; saved command prefixes. |
| `modes.py` | the mode (kept on the agent), `shift+tab`, the plan mode note and the `exit_plan_mode` tool. |
| `guard.py` | read before edit, with `before_tool`/`after_tool`; forgets what was read on `/clear` and `/compact`. |
| `todo.py` | the `todo_write` tool. |
| `completion.py` | the input subclass adding commands to py-agent's `@` menu, drawn like Claude Code. |
| `shortcuts.py` | `!`, `#`, double `esc` and rewind; showing bash-mode messages and attachments. |
| `context.py` | auto-compact, the "context left" number and `/context`. |
| `slash.py` | `/init` and custom commands. |

Agent features are `Agent` subclasses stacked in `setup`; each UI feature group is a mixin listed before `api.Chat`, so its methods call `super()` to reach the next one. Textual only merges `CSS` and `BINDINGS` from widget classes, so those stay in `main.py`.

## How the tricky parts work

- **Asking from the worker thread**: tool hooks run in the agent's worker thread and call `agent.ask`, which waits for `Chat.ask` on the UI loop. Rewind is started from a key, so it runs as a worker instead: awaiting inside a key handler would block the UI.
- **Hook order**: guard's `Agent` subclass is installed after approval's, so its `before_tool` runs first and an edit of an unread file is refused before anyone is asked.
- **Plan mode note**: `request_messages` adds the note to a copy of the last user message, so the saved session stays clean.
- **Bash mode** is saved right away with `agent.append`, as a user message (the command) and an assistant message (the output), so turns keep alternating for chat templates that require it. Both carry `_kind: "bash"` so replay recognizes them; compact summaries are recognized by `_kind: "compact"`, and attachments by their `_display`.
- **Edit diffs** come from the call's `oldText`/`newText`, and line numbers from finding `newText` in the file, so they also show after `/resume` (without numbers if the text is gone).
- **Slash commands that write a prompt** (`/init`, custom ones) return the `"send"` action; the log shows the typed command instead of the prompt.

## Limits

- Without a UI to ask (e.g. tests that only build the agent), gated tool calls and plans are declined.
- Plan mode's read-only check is a list of programs plus a few banned flags (`find -exec`, `git diff --output`, ...), not a sandbox. It errs on refusing: chaining, `$`, redirects and quoted `|` (`grep 'a|b'`) are refused too.
- The spinner's token count is an estimate (~4 characters per token), as is the split in `/context`, which is scaled to the server's real count once there is one.
