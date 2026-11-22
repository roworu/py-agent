# Config

Files live in `$PY_AGENT_DIR`, else `$XDG_CONFIG_HOME/py-agent/`, else `~/.config/py-agent/`. Missing `settings.json` and `auth.json` are created on first run with the defaults below. A broken file stops py-agent with a message naming the bad key.

## settings.json

Default:

```json
{
  "version": "0.0.1",
  "defaultThinkingLevel": "off",
  "providers": {
    "llama.cpp": {
      "baseUrl": "http://localhost:8080/v1",
      "models": [
        { "id": "qwen3.6-35b" },
        {
          "id": "qwen3.8-27b",
          "reasoning": true,
          "thinkingLevelMap": { "minimal": null, "xhigh": null, "max": null }
        }
      ]
    }
  }
}
```

- `version`: py-agent version that created the file.
- `defaultModel` (optional): `provider/id` or a bare id. Default: the first model.
- `defaultThinkingLevel`: `off` `minimal` `low` `medium` `high` `xhigh` `max`.
- `updateCheckDays` (optional): days between checks for a newer release at startup, `0` never checks. Default: `7`. The last check time is kept in `update-check`.
- `overrideSystemPrompt` (optional): file that replaces the built-in prompt, relative to the config dir. It must list the tools, or the model won't know about them.
- `providers`: name → provider. The name is also the key in `auth.json`.
  - `baseUrl`: OpenAI-compatible endpoint; `/chat/completions` is appended.
  - `models`: list of models, in the order `/model` shows them.
    - `id`: model id sent to the endpoint.
    - `reasoning` (optional): `true` enables thinking levels other than `off`.
    - `thinkingLevelMap` (optional): `reasoning_effort` sent per level. Levels map to themselves by default; `null` disables one.
    - `contextWindow` (optional): tokens the model accepts. The conversation is compacted before a request once the last one filled 80% of it; plugins can also show it.
- `plugins` (optional): plugin folder name → its settings, read with `api.settings(name)`. `"enabled": false` skips the plugin.

## auth.json

Default:

```json
{ "llama.cpp": "123" }
```

One key per provider, sent as `Authorization: Bearer <key>`. Created with `0600` permissions.

## history

Input history, one JSON string per line so multi-line entries fit. Up/down in the input walks it.

## sessions/

Saved conversations, one folder per working folder. See [usage.md](usage.md#sessions).

## snapshots/

Separate git repos holding file snapshots for `/undo`, one per working folder. Safe to delete.

## plugins/

Every folder in `plugins/` with a `main.py` is loaded at startup, in name order. See [plugins.md](plugins.md).
