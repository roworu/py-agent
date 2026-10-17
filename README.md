# py-agent

minimal coding agent inspired by [pi](https://github.com/earendil-works/pi). 

Commands: `/model`, `/think`, `/clear`, `/quit`. `esc` stops the current answer.

## config

config taken from default os config dir, or could be overrided by `$PY_AGENT_DIR` env.

`~/.config/py-agent/` (or `$XDG_CONFIG_HOME/py-agent/`).

config files:
- `settings.json`: providers, models and other settings. Created on first run; see [docs/config.md](docs/config.md).

- `auth.json`: `{ "<your_provider_from_settings>": "<key>" }`. Created on first run.

- `AGENTS.md`: appended to the system prompt. If `overrideSystemPrompt==True` - it will completely override system prompt.
Be careful with it, as you have to mention tools in order for agent to work at all.

- `history`: input history.

## tools

Built-in tools, always enabled: `read`, `bash`, `edit`, `write`.

