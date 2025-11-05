# vibego - vibe coding via Telegram anytime, anywhere

**Drive your terminal AI CLI via Telegram anytime and anywhere (supports Codex / ClaudeCode)**

For the Simplified Chinese version, see [中文文档](README-zh.md).

## Features

1. Control your terminal AI CLI through Telegram whenever you need it.
2. Capture lightweight task management and bug reports directly inside Telegram.
3. Switch between Codex / ClaudeCode terminal CLIs in one tap from Telegram.
4. Send commands over the HTTPS channel provided by the Telegram Bot API, protected by end‑to‑end TLS.
5. Keep runtime logs and state files under `~/.config/vibego/` so sensitive data never leaves the machine.

## Environment Requirements

**The terminal environment has installed and login in codex / claudeCode**
```bash
brew install python@3.11 tmux
python3 -m venv ~/.config/vibego/runtime/venv
source ~/.config/vibego/runtime/venv/bin/activate
```

## Quick Start

### Create and retrieve a Telegram bot token

Use the official Telegram BotFather guide (<https://core.telegram.org/bots#botfather>):

1. Search for `@BotFather` in the Telegram client and start a chat.
2. Send `/start`, then `/newbot`, and follow the prompts for bot name and username.
3. BotFather returns an HTTP API token resembling `123456789:ABC...`; store it safely.
4. To regenerate or reset the token, send `/token` in the same chat and pick the bot.

### Install and start vibego

Before continuing, make sure Codex / ClaudeCode CLIs are installed and logged in, and that you have a Telegram bot token
ready.

- Consider merging the contents of [AGENTS-en.md](AGENTS-en.md)￼ into your `$HOME/.codex/AGENTS.md` or
  `$HOME/.claude/CLAUDE.md`.

```bash
pipx install vibego  # or pip install --user vibego
vibego init          # initialise the config directory and persist the Master Bot Token
vibego start         # start the master service
```

Then click on `/start` in the bot created by Telegram ,Enjoy it!

## Directory Layout

- `bot.py`: aiogram 3 worker that supports multiple model sessions (Codex / ClaudeCode / reserved Gemini).
- `scripts/run_bot.sh`: one-click bootstrap (builds venv, starts tmux + model CLI + bot).
- `scripts/stop_bot.sh`: terminates the worker for a project (tmux session + bot process).
- `scripts/start_tmux_codex.sh`: low-level tmux/CLI launcher invoked by `run_bot.sh`, forces UTF‑8 via `tmux -u`.
- `scripts/models/`: model configuration modules (`common.sh` / `codex.sh` / `claudecode.sh` / `gemini.sh`).
- `logs/<model>/<project>/`: runtime logs (`run_bot.log`, `model.log`, `bot.pid`, `current_session.txt`), defaulted to
  `~/.config/vibego/logs/`.
    - `model.log` is rotated by `scripts/log_writer.py`, with 20 MB cap per file and 24-hour retention (override via
      `MODEL_LOG_MAX_BYTES`, `MODEL_LOG_RETENTION_SECONDS`).
- `.env.example`: configuration template to copy to `.env` and adjust.

## Logs & Directories

```
~/.config/vibego/logs/
  └─ codex/
      └─ mall-backend/
           ├─ run_bot.log     # output from run_bot.sh
           ├─ model.log       # model CLI output captured through tmux pipe-pane
           ├─ bot.pid         # current bot process PID (used by stop_bot.sh)
           └─ current_session.txt  # pointer to the latest JSONL session
```

> Starting in 2025, all logs, databases, and state files default to `~/.config/vibego/`. Use
`./scripts/migrate_runtime.sh` to migrate legacy files created inside the repository back into the runtime directory.

## Model Switching

- Supported model parameters: `codex`, `claudecode`, `gemini` (placeholder).
- Switch flow: `stop_bot.sh --model <old>` → `run_bot.sh --model <new>`.
- Each model keeps an isolated configuration in `scripts/models/<model>.sh`; shared logic lives in
  `scripts/models/common.sh`.
- `ACTIVE_MODEL` is echoed in `/start` replies and logs, and exported to the environment for `bot.py`.

### Codex

| Variable             | Description                                                                      |
|----------------------|----------------------------------------------------------------------------------|
| `CODEX_WORKDIR`      | Codex CLI working directory (defaults to the value in `.env` or repository root) |
| `CODEX_CMD`          | Launch command, default `codex --dangerously-bypass-...`                         |
| `CODEX_SESSION_ROOT` | JSONL root directory (default `~/.codex/sessions`)                               |
| `CODEX_SESSION_GLOB` | JSONL file pattern (default `rollout-*.jsonl`)                                   |

### ClaudeCode

| Variable              | Description                                                  |
|-----------------------|--------------------------------------------------------------|
| `CLAUDE_WORKDIR`      | Project directory (defaults to the same value used by Codex) |
| `CLAUDE_CMD`          | CLI launch command, for example `claude --project <path>`    |
| `CLAUDE_PROJECT_ROOT` | JSONL root directory (default `~/.claude/projects`)          |
| `CLAUDE_SESSION_GLOB` | JSONL file pattern (default `*.jsonl`)                       |
| `CLAUDE_PROJECT_KEY`  | Optional: explicitly set `~/.claude/projects/<key>`          |

### Gemini (placeholder)

- `scripts/models/gemini.sh` currently contains a placeholder command to be expanded once the official CLI is available.

## aiogram Worker Behaviour

- `/start`: returns `chat_id`, `MODE`, and `ACTIVE_MODEL`; logs record `chat_id` and `user_id`.
- Text messages:
    1. Pick the `SessionAdapter` based on `ACTIVE_MODEL`, read `current_session.txt`, and search `MODEL_SESSION_ROOT` if
       necessary.
    2. Inject the prompt into tmux (send `Esc` to clear modes, `Ctrl+J` for newline, `Enter` to submit).
    3. Initialise offsets from `SESSION_OFFSETS`; `_deliver_pending_messages()` streams tail updates from the JSONL log.
    4. During the watcher phase, the bot informs the user the `ACTIVE_MODEL` is processing and pushes the result once
       ready (Markdown preserved).
- MODE = A still honours `AGENT_CMD` for direct CLI execution.

## New Scripts

- `run_bot.sh`
    - `--model <name>`: codex / claudecode / gemini.
    - `--project <slug>`: directory name for logs/sessions; defaults to a slug derived from the working directory.
    - `--foreground`: keep the process in the foreground (default: background via `nohup`).
    - `--no-stop`: skip the pre-launch stop step (defaults to invoking `stop_bot.sh` for idempotency).
- `stop_bot.sh`
    - Idempotent stop: issues `tmux kill-session`, terminates the process from `bot.pid`, and clears cached files.
    - Example: `./scripts/stop_bot.sh --model codex --project mall-backend`.

## Configuration Highlights

### `.env` (master global configuration)

- Location: `~/.config/vibego/.env` (override with `VIBEGO_CONFIG_DIR`).
- `MASTER_BOT_TOKEN`: token for the master bot; collected by `vibego init` and required for startup.
- `MASTER_CHAT_ID` / `MASTER_USER_ID`: captured automatically the first time you interact with the master in Telegram to
  mark authorised admins.
- `MASTER_WHITELIST`: comma-separated list of chat IDs. Leave empty to allow any chat; latest value always wins if auto
  updates occur.
- You can add optional variables for proxies, log level, default model, etc. Scripts fall back to sensible defaults if
  unspecified.

- Project definitions persist in `~/.config/vibego/config/master.db` (SQLite) with a JSON mirror at
  `~/.config/vibego/config/projects.json`. For offline edits, use the `config/projects.json.example` template in the
  repository.
- The master “⚙️ Project Management” menu can create/edit/delete projects; offline JSON edits are imported at startup
  and synced to the database.
- Required fields: `bot_name`, `bot_token`, `project_slug`, `default_model`.
- Optional fields: `workdir` (project path), `allowed_chat_id` (pre-authorised chat). Leave blank to let the worker
  capture the first valid chat and persist it to `~/.config/vibego/state/master_state.json`.
- Other custom fields are currently ignored.

### Automatic Authorisation & State

- If `allowed_chat_id` is empty when the worker starts, the first authorised message writes to `state/state.json` and is
  applied immediately.
- Master restarts: call `stop_bot.sh` first, then restore running projects from the saved state.

## Roadmap

- Master bot will poll all project bots and invoke run/stop scripts to orchestrate workers; current version ships the
  worker layout and logging standard first.
- Gemini CLI support will be added once an official integration path is available.

## Notes

- `~/.config/vibego/.env` stores sensitive tokens and admin metadata—never commit it.
- To trim log size, clean up `logs/<model>/<project>/` as needed or adjust script thresholds.
- If you previously ran an old source checkout, run `./scripts/migrate_runtime.sh` and ensure only `.example` templates
  remain in the repository to avoid committing databases or logs.
- The master caches version checks and reminds you once per release; rerun `/projects` or restart the master to force a
  new check.

## Master Control

- Launch the admin bot with `MASTER_BOT_TOKEN` (running `python master.py`).
- Master stores the project list in `~/.config/vibego/config/master.db`; use the project management menu or edit
  `~/.config/vibego/config/projects.json` directly. Key fields:
    - `bot_name`: Telegram bot username (with or without `@`; CLI adds `@` when displaying).
    - `bot_token`: Telegram token for the worker.
    - `default_model`: default model (codex / claudecode / gemini).
    - `project_slug`: directory/log slug.
    - `workdir`: project working directory (optional).
    - `allowed_chat_id`: authorised chat injected into the runtime environment.
- State snapshot: `~/.config/vibego/state/master_state.json` records the active model and running status for each
  project. On restart the master calls `stop_bot.sh`, then restores workers according to the state file.

### Management Commands

| Command                     | Description                                                                 |
|-----------------------------|-----------------------------------------------------------------------------|
| `/projects`                 | List all projects with their current status and model.                      |
| `/run <project> [model]`    | Start a project worker; optional `model` overrides the default/current one. |
| `/stop <project>`           | Stop the project worker.                                                    |
| `/switch <project> <model>` | Stop the worker and relaunch it with a new model.                           |
| `/start`                    | Show help and the total number of projects.                                 |
| `/upgrade`                  | Run `pipx upgrade vibego && vibego stop && vibego start` to self-upgrade.   |

- `<project>` accepts either the `project_slug` or `@bot_name`. Responses automatically render clickable `@` links.

> The master only interacts with the admin bot. Project bots continue to handle business traffic through the worker (
`bot.py`) launched by `run_bot.sh`.
