# Plugins

A plugin is a folder in `<config dir>/plugins/` with a `main.py`. Every such folder is loaded at startup, in name order, before the agent and UI are created. `main.py` defines `setup(api)`:

```python
def setup(api):
    api.add_command("/hi", lambda agent, arg: (f"hi {arg}", None))
```

The folder is imported as a package, so `main.py` can import its own files with `from .helper import x` or `from . import helper`. A plugin that fails to load stops py-agent with a traceback. Plugins run with full access to your machine; only install ones you trust.

`"plugins": {"<folder name>": {"enabled": false}}` in `settings.json` turns a plugin off without deleting it. Other keys there are the plugin's own settings, read with `api.settings(name)`.

`api.VERSION` is the plugin api version, currently `2`. A plugin that needs a newer one sets `API_VERSION = n` in its `main.py`, and py-agent refuses to start with a message saying so.

Ready-made examples are in [`examples/plugins/`](../examples/plugins): copy a folder into `plugins/` to use it.

## api

`api` is a `modules.plugins.Api`.

| attribute | what it is |
|---|---|
| `api.VERSION` | plugin api version, `2`. |
| `api.config` | the loaded `Config` (models, default model and thinking level, config dir). Replace it with `dataclasses.replace(...)` to change settings. A model's `context_window` is its `contextWindow` from `settings.json`, or `None`. |
| `api.cwd` | folder py-agent runs in. |
| `api.llm` | streaming client module; `complete(...)` sends one request and returns the assistant message, with the server's token counts in `usage` when sent. |
| `api.prompt` | system prompt module; `build(cwd, override)` returns the prompt. |
| `api.files` | `@file` module; `message(agent, line)` builds the user message with mentioned files attached, `attached(content)` returns the attached `(path, contents)`, `list_files(cwd)` feeds the `@` menu. |
| `api.snapshot` | undo snapshots module; `changes(agent.snapshots, cwd, a, b)` lists `(path, added, removed)` lines per file changed between two snapshots, e.g. the `before`/`after` hashes in `agent.turns`. |
| `api.tools` | tools module; `TOOLS` maps name → `(function, spec)`, `SPECS` is what the model sees. |
| `api.commands` | slash commands module; `run(agent, line)` handles a line starting with `/`. |
| `api.Agent` | agent class, created after all plugins load. Replace with a subclass to change the loop. |
| `api.Chat` | textual app class, created after all plugins load. Replace with a subclass to change the UI. |
| `api.agent` | the running `Agent`; `None` during `setup`. See [the agent](#the-agent). |
| `api.chat` | the running `Chat`; `None` during `setup`. |

### api.settings(name)

The plugin's section of `"plugins"` in `settings.json`, `{}` if there is none.

### api.add_tool(function, description, props, required)

Add a tool the model can call. `function(cwd, stop, **args)` returns text; `stop` is a `threading.Event` set when the user presses esc. Return errors as `"Error: ..."` text rather than raising. `props` is a JSON schema `properties` object, `required` lists required argument names. The tool is listed in the built-in system prompt.

```python
def ls(cwd, stop, path: str = ".") -> str:
    return "\n".join(sorted(p.name for p in (cwd / path).iterdir()))

def setup(api):
    api.add_tool(ls, "List a folder.", {"path": {"type": "string"}}, [])
```

### api.add_command(name, handler, description="", arguments="")

Add a slash command, or replace a built-in one with the same name. `handler(agent, arg)` returns `(reply, action)`, where `action` is `"clear"` (empty the log), `"replay"` (redraw the log from `agent.messages`), `"undo"` (redraw and put `reply` into the input), `"compact"` (run `agent.compact(reply)` in the background and redraw), `"send"` (send `reply` to the model as a message, for commands that write a prompt), `"update"` (install the latest release in the background), `"quit"` or `None`. `@file` mentions in a `"send"` reply are attached like in a typed message. An empty reply with `None` shows nothing, for commands that open their own UI through `api.chat`. The name is added to input autocomplete, and `description` and `arguments` (e.g. `"[name]"`) to `/help` and `api.commands.DESCRIPTIONS`, which holds the built-in ones too.

```python
def setup(api):
    api.add_command("/review", lambda agent, arg: (f"Review the uncommitted changes. {arg}", "send"),
                    "ask the model to review the diff", "[focus]")
```

## The agent

`api.agent` is the running `Agent`:

- `agent.messages`: the conversation, starting with the system prompt. Keys starting with `_` are saved in the session but never sent to the model: `_display` is shown (and put back by `/undo`) instead of the content, `_kind` tells what made the message (`"compact"` for the `/compact` request). Plugins can add their own.
- `agent.run(message, events)`: answers a user message, given as text or as a message dict with `_` keys, and returns `"done"` or `"stopped"` (esc).
- `agent.call_tool(name, arguments)`: runs a tool call (arguments as json text) through `before_tool` and `after_tool`, as the loop does.
- `agent.append(*messages)`: adds messages and saves them right away; call it between turns.
- `agent.ask(question, options, body=None)`: during an answer, asks the user to pick an option and returns its index, or `None` on esc or without a UI.
- `agent.usage`: the server's token usage for the last request (`prompt_tokens`, `completion_tokens`, ...), `None` until one arrives.
- `agent.total`: token usage added up per model (`provider/id` → counts like `usage`) since py-agent started, across `/clear` and `/compact`, compact requests included.
- `agent.compact(instructions="")`: replaces the conversation with a summary, returning `False` if stopped or empty; it blocks while the model answers, so call it between turns, outside the ui thread. `run` also calls it by itself near the model's `contextWindow` (`modules.agent.AUTO_COMPACT`, 0.8) or when the server rejects a request as too long.

Replace `api.Agent` with a subclass to hook into the loop. Several plugins stack when each subclasses the current `api.Agent` and calls `super()`:

- `before_tool(name, args) -> str | None`: before each tool call; returned text refuses the call and goes to the model as its result.
- `after_tool(name, args, result) -> str`: after each tool call; returns the result the model sees.
- `request_messages() -> list[dict]`: the messages sent with each request, `agent.messages` by default; return a changed copy to add notes that aren't saved.
- `request_tools() -> list[dict]`: the tool specs sent with each request, `api.tools.SPECS` by default; return fewer to turn tools off, e.g. per mode.

```python
def setup(api):
    class Careful(api.Agent):
        def before_tool(self, name, args):
            if name == "bash" and self.ask(f"Run {args.get('command')}?", ["Yes", "No"]) != 0:
                return "Error: the user declined."
            return super().before_tool(name, args)
    api.Agent = Careful
```

`examples/plugins/bash_guard` refuses risky commands this way.

## Changing behavior

Wrap or replace module functions. The agent looks them up on each use, so the change applies everywhere:

```python
def setup(api):
    build = api.prompt.build
    api.prompt.build = lambda cwd, override=None: build(cwd, override) + "\n\nAnswer briefly."
```

Token usage is not shown by default. `examples/plugins/context` adds it to the status line from `agent.usage`; `examples/plugins/cost` prices `agent.total` in a `/cost` command, with prices from `api.settings("cost")`.

`examples/plugins/claude_code` puts all of this together to look and behave like Claude Code: approval prompts and modes, plan mode, a `/` and `@` completion menu, rewind, `/init`, custom commands, read-before-edit and more. Its `main.py` installs the pieces from its `modules/` folder; see its [README](../examples/plugins/claude_code/README.md).

`examples/plugins/opencode` does the same for opencode: a home screen, build and plan agents on `tab`, subagents through a `task` tool, `glob`/`grep`/`list`/`webfetch`/todo tools, permissions, `ctrl+x` leader keys, searchable dialogs for models, sessions, themes and commands, a sidebar and session titles; see its [README](../examples/plugins/opencode/README.md).

## Changing the UI

Subclass the current `api.Chat` and assign it back, so several plugins stack:

```python
def setup(api):
    class MyChat(api.Chat):
        CSS = api.Chat.CSS + "#status { background: $panel; }"
    api.Chat = MyChat
```

Textual runs `on_mount` and other `on_*` handlers of every class in the chain, so don't call `super()` in them. Plain methods need `super()` as usual, or replace them entirely:

- `update_status()`, `set_busy(busy)`, `write(text, classes)` (streamed answer or reasoning)
- `start(message)`: shows a user message dict and starts answering it
- `show_user(message)`, `show_answer(message)`: a user or saved assistant message dict, so `_display` and `_kind` can be read; `show_answer` is only used on replay
- `show_tool_call(id, name, arguments)`, `show_tool_result(id, result)`: paired by the tool call id
- `show_retry(delay, error)`: a failed request (429 or 5xx) is retried after `delay` seconds
- `show_auto_compacted()`: the conversation was compacted during an answer; the log isn't redrawn
- `show_stopped()`: the answer was interrupted with esc
- `ask(question, options, body=None)`: shows a `Picker` above the input and returns the picked index, `None` on esc; `agent.ask` calls it from the worker thread

The input's menu under the box (`#menu`) lists files for `@`. A `Chat.Input` subclass can override `menu_items()` to return other `(name, description)` rows, `draw_menu()` to change their look and `accept(submit)` to change what picking one does; `examples/plugins/claude_code` adds slash commands this way.

To change the input box or the picker, subclass `api.Chat.Input` or `api.Chat.Picker` and set it as `Input` or `Picker` on your `Chat` subclass. Don't wait for anything (a future, `ask`) inside a key handler or action, since that blocks the UI; start it with `self.run_worker(coroutine)` instead.
