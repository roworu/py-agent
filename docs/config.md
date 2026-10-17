# Config

Files live in `$PY_AGENT_DIR`, else `~/.config/py-agent/`. Missing files are created on first run.

## settings.json

```json
{
  "version": "0.0.1",
  "defaultModel": "ds/deepseek-flash",
  "defaultThinkingLevel": "off",
  "overrideSystemPrompt": "SYSTEM.md",
  "providers": {
    "ds": {
      "baseUrl": "https://api.deepseek.com/",
      "models": [
        {
          "id": "deepseek-flash",
          "reasoning": true,
          "thinkingLevelMap": { "minimal": null, "medium": null }
        }
      ]
    }
  }
}
```

- `version`: py-agent version that created the file.
- `defaultModel`: `provider/id` or a bare id. Default: the first model.
- `defaultThinkingLevel`: `off` `minimal` `low` `medium` `high` `xhigh` `max`.
- `overrideSystemPrompt`: file that replaces the built-in prompt, relative to the config dir.
- `thinkingLevelMap`: `reasoning_effort` sent per level. Levels map to themselves by default; `null` disables one.

## auth.json

```json
{ "ds": "sk-..." }
```

One key per provider, sent as `Authorization: Bearer <key>`.

## AGENTS.md

Appended to the system prompt, from the config dir and from `/` down to the cwd.
