# Usage

Run `py-agent` in the folder you want to work in. The model works in that folder through tools. `py-agent -c` continues the last session in that folder.

`py-agent -p "prompt"` answers one message without the UI, for scripts and CI: the answer goes to stdout, tool calls and retries to stderr. It's saved as a session like any other, so `-c -p "..."` continues it. Plugins that ask the user (approvals) get no answer, which declines. Errors exit with status 1.

## Keys

- `enter`: send the message. Messages sent while an answer runs are queued and sent in order after it.
- `shift+enter`, `ctrl+j`, or `\` then `enter`: new line. Pasted text keeps its lines.
- `esc`: stop the current answer. Running `bash` commands are killed, queued messages go back into the input.
- `up` / `down`: walk input history, from the first / last line of the input.
- `right`: accept the suggested slash command.
- `@`: opens a menu of files in the folder (`git ls-files` in a repo, else a walk that stops at 5000 files; listed again after 10 seconds). `up`/`down` select, `tab` or `enter` inserts `@path`, `esc` closes it.
- `ctrl+q`: quit.

## Commands

- `/model`: list models, `*` marks the current one.
- `/model <name>`: switch model, by `provider/id` or bare id. Thinking falls back to `off` if the new model doesn't support the current level.
- `/think`: show the thinking level and the ones the model supports.
- `/think <level>`: set the thinking level.
- `/clear`: start a new conversation. The system prompt is rebuilt, so edited context files are picked up.
- `/compact [text]`: replace the conversation with a summary the model writes, to free up context. `text` tells it what to focus on. Continues in a new session file; `esc` stops it and keeps the conversation as it was. Also runs by itself during an answer, once per message: before a request when the last one filled 80% of the model's `contextWindow` (see [config.md](config.md)), and when the server rejects a request as too long, which is then retried.
- `/resume`: list the last 20 sessions in this folder.
- `/resume <n>`: continue session `n` from that list.
- `/undo`: drop the last message and its answer, and revert the files changed meanwhile. The message goes back into the input. Repeat to go further back.
- `/redo`: bring back what `/undo` dropped, until a new message is sent.
- `/update`: download the latest release over the running binary; restart py-agent to use it. Release builds check GitHub at startup once a week (`updateCheckDays` in [config.md](config.md)) and show a short notice when a newer version is out.
- `/help`: list commands.
- `/quit`, `/exit`: quit.

## @file mentions

Every `@path` in a message that names a file attaches its contents, read with the `read` tool, so its limits apply. The log and `/undo` show the message as typed. Works with `-p` too.

## Tools

Always enabled. Paths are relative to the current folder, or absolute.

- `read(path, offset?, limit?)`: read a text file, at most 2000 lines and 50,000 characters at a time.
- `write(path, content)`: create or overwrite a file, creating parent folders.
- `edit(path, oldText, newText)`: replace text that occurs exactly once. Windows line endings are kept.
- `bash(command, timeout?)`: run a bash command in the current folder. Killed after `timeout` seconds (default 120). Returns the last 2000 lines and 50,000 characters of output, plus the exit code if it failed.

## Sessions

Every conversation is saved to `<config dir>/sessions/<folder path>/<start time>.jsonl`, one message per line, as it goes. A resumed session gets a freshly built system prompt.

## Undo

Before and after each message, py-agent snapshots the folder's files into a separate git repo in `<config dir>/snapshots/`; your own repo is never touched. `/undo` restores only the files that changed during the dropped turn, whether by `write`, `edit` or `bash`. As in opencode, this only works when the folder is inside a git repo (files in `.gitignore` are skipped); elsewhere `/undo` drops the messages only.

## System prompt

Built from, in order:

1. The built-in prompt listing the tools ([`src/modules/prompts/system.md`](../src/modules/prompts/system.md), tool descriptions and parameters in [`prompts/tools/`](../src/modules/prompts/tools)), or the `overrideSystemPrompt` file (see [config.md](config.md)).
2. `AGENTS.md`, `CLAUDE.md` and `GEMINI.md` from the current folder, if present.
3. Today's date and the current folder.
