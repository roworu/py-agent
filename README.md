# py-agent

minimal coding agent.

## config

config taken from default os config dir, or could be overrided by `$PY_AGENT_DIR` env.

`~/.config/py-agent/` (or `$XDG_CONFIG_HOME/py-agent/`).

config files:
- `settings.json`: providers, models and other settings. Created on first run; see [docs/config.md](docs/config.md).

- `auth.json`: `{ "<your_provider_from_settings>": "<key>" }`. Created on first run.

- `sessions/`, `snapshots/`: saved conversations and file snapshots for `/undo`.

- `plugins/`: every folder with `main.py` is loaded at startup; see [docs/plugins.md](docs/plugins.md).

## docs

- [usage](docs/usage.md): keys, commands, tools, system prompt.
- [config](docs/config.md): config files and their defaults.
- [plugins](docs/plugins.md): plugin api and examples.
