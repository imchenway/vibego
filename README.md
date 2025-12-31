# vibego - 通过 telegram 随时随地的进行 vibe coding

**通过 Telegram 随时随地驱动你的终端 AI CLI（支持 Codex / ClaudeCode）**

For the english version, see [README-en](README-en.md).

## 功能介绍

1. 通过 Telegram 随时随地驱动你的终端 AI CLI;
2. 通过 telegram 做到简单的任务管理与缺陷报告，可在 Telegram 中直接记录并追踪；
3. 通过 telegram 随时在 Codex / ClaudeCode 终端 CLI 间一键切换；
4. 通过 Telegram Bot API 的 HTTPS 请求通道传输指令到 CLI，链路全程由 TLS 加密保护。
5. 运行期日志和状态文件统一写入本机 ~/.config/vibego/，敏感数据不出终端；

## 环境依赖

**终端环境安装且登录了 codex/claudeCode**

```bash
brew install python@3.11 tmux
brew install pipx
python3 -m venv ~/.config/vibego/runtime/venv
source ~/.config/vibego/runtime/venv/bin/activate
```

- 最低需要 Python 3.9，推荐 3.11+；根据 [datetime 官方文档](https://docs.python.org/3/library/datetime.html#datetime.UTC)，`datetime.UTC` 仅在 3.11+ 中提供，本仓库已通过 `timezone.utc` 兜底兼容更早版本。
- `scripts/run_bot.sh` 会在启动时自动优先选择可用的 `python3.11`（可通过 `VIBEGO_PYTHON_BIN` 覆盖），并在找不到 3.11 时回退到系统 `python3` 但确保版本 ≥3.9。

## 快速开始

### 创建并获取 telegram bot token

建议通过 PyPI 安装的 `vibego` 命令完成初始化与启动，示例：

- 首次创建 Token 可参考 Telegram 官方 BotFather 指南（<https://core.telegram.org/bots#botfather>）：
    1) 在 Telegram 客户端搜索 `@BotFather` 并开始对话；
    2) 发送 `/start`，然后依次发送 `/newbot`，根据提示输入机器人名称与用户名；
    3) BotFather 将返回以 `123456789:ABC...` 形式的 HTTP API Token，请妥善保存；
    4) 若需重新获取或重置 Token，可在同一对话中发送 `/token`，选择目标机器人后领取新令牌。

### 安装 & 启动 vibego

执行该步骤之前，确保您的终端已经安装并登录了 codex / claudeCode / gemini（按需），且已经准备好了 telegram bot token。

- `demo` 启动脚本会在运行前自动把仓库根目录的 [AGENTS.md](AGENTS.md) 写入 `$HOME/.codex/AGENTS.md` / `$HOME/.claude/CLAUDE.md` 的 `<!-- vibego-agents:start -->...<!-- vibego-agents:end -->` 区块；若文件原本不存在会直接创建，存在则保留你已有内容并备份为 `.vibego.bak`，方便进一步自定义

```bash
pipx install vibego  # 或者 pip install --user vibego
vibego init          # 初始化配置目录并写入 Master Bot Token
vibego start         # 启动 master 服务
```

然后在 telegram 创建的 bot中点击`/statr`，enjoy it！

## 目录结构

- `bot.py`：aiogram 3 worker，支持多模型会话解析（Codex / ClaudeCode / Gemini）。
- `scripts/run_bot.sh`：一键启动脚本（自动建 venv、启动 tmux + 模型 CLI + bot）。
- `scripts/stop_bot.sh`：终止当前项目 worker（tmux + bot 进程）。
- `scripts/start_tmux_codex.sh`：底层 tmux/CLI 启动器，被 `run_bot.sh` 调用，默认以 `tmux -u` 强制启用 UTF-8。
- `scripts/models/`：模型配置模块（`common.sh`/`codex.sh`/`claudecode.sh`/`gemini.sh`）。
- `logs/<model>/<project>/`：运行日志（`run_bot.log`、`model.log`、`bot.pid`、`current_session.txt`），默认位于
  `~/.config/vibego/logs/`。
    - `model.log` 由 `scripts/log_writer.py` 控制，单文件上限 20MB，仅保留最近 24 小时的归档（可通过 `MODEL_LOG_MAX_BYTES`、
      `MODEL_LOG_RETENTION_SECONDS` 覆盖）。
- `.env.example`：环境配置模板（复制为 `.env` 后按需修改）。

## Spec-Driven Development（speckit）工作流（实验）

vibego 仓库内包含 `.specify/` 脚本与模板，可用于按 Spec-Driven Development 的节奏产出可审阅的规格/计划/任务，
再进入实现阶段；用于降低“纯 vibe coding”带来的不确定性。

参考入口（本仓库样例，绝对路径可复核）：
- 评估结论：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/assessment-report.md`
- 快速复现与演示：`/Users/david/hypha/tools/vibego/specs/001-speckit-feasibility/quickstart.md`

上游参考（官方）：
- Spec Kit：https://github.com/github/spec-kit
- SDD 流程说明：https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md

安全边界提醒（必读）：
- 不要在任何文档/日志/报错中粘贴真实 token、chat_id 或用户标识；示例请使用占位符。
- 运行期日志/状态文件必须写入 `~/.config/vibego/`（或由 `VIBEGO_CONFIG_DIR`/`MASTER_CONFIG_ROOT` 覆盖），不要落入仓库。

## 日志 & 目录

```
~/.config/vibego/logs/
  └─ codex/
      └─ mall-backend/
           ├─ run_bot.log     # run_bot.sh 输出
           ├─ model.log       # tmux pipe-pane 捕获的模型 CLI 输出
           ├─ bot.pid         # 当前 bot 进程 PID（stop_bot.sh 使用）
           └─ current_session.txt  # 最近一次 JSONL 会话指针
```

> 从 2025 年起，所有日志、数据库、状态文件默认写入 `~/.config/vibego/`；`scripts/migrate_runtime.sh`
> 可将旧版本在仓库内生成的运行期文件一次性迁移到该目录。

## 模型切换

- 支持参数：`codex`、`claudecode`、`gemini`。
- 切换流程：`stop_bot.sh --model <旧>` → `run_bot.sh --model <新>`。
- 每个模型在 `scripts/models/<model>.sh` 中维护独立配置，互不依赖；公共逻辑位于 `scripts/models/common.sh`。
- `ACTIVE_MODEL` 会在 `/start` 回复及日志中显示，并写入环境变量供 `bot.py` 使用。

### Codex

| 变量                   | 说明                                            |
|----------------------|-----------------------------------------------|
| `CODEX_WORKDIR`      | Codex CLI 工作目录（默认 `.env` 中自定义或 fallback ROOT） |
| `CODEX_CMD`          | 启动命令，默认 `codex --dangerously-bypass-...`      |
| `CODEX_SESSION_ROOT` | JSONL 根目录（默认 `~/.codex/sessions`）             |
| `CODEX_SESSION_GLOB` | JSONL 文件匹配（默认 `rollout-*.jsonl`）              |

### ClaudeCode

| 变量                    | 说明                                    |
|-----------------------|---------------------------------------|
| `CLAUDE_WORKDIR`      | 工程目录（默认与 Codex 相同）                    |
| `CLAUDE_CMD`          | CLI 启动命令，示例 `claude --project <path>` |
| `CLAUDE_PROJECT_ROOT` | JSONL 根目录（默认 `~/.claude/projects`）    |
| `CLAUDE_SESSION_GLOB` | JSONL 文件匹配（默认 `*.jsonl`）              |
| `CLAUDE_PROJECT_KEY`  | 可选：显式指定 `~/.claude/projects/<key>` 路径 |

### Gemini

Gemini 基于官方 `gemini-cli`（Homebrew 包名 `gemini-cli`，命令为 `gemini`）。

默认会话落盘路径（可复核）：

```
~/.gemini/tmp/<sha256(工作目录绝对路径字符串)>/chats/session-*.json
```

| 变量                 | 说明 |
|--------------------|------|
| `GEMINI_WORKDIR`   | 工程目录（默认与 `MODEL_WORKDIR` 相同） |
| `GEMINI_CMD`       | CLI 启动命令，默认 `gemini --approval-mode yolo --sandbox`（高风险，需自行评估） |
| `GEMINI_SESSION_ROOT` | 会话根目录，默认 `~/.gemini/tmp` |
| `GEMINI_SESSION_GLOB` | 会话文件匹配，默认 `session-*.json` |

启动时会自动把仓库根目录的 `AGENTS.md` 同步到 `~/.gemini/GEMINI.md` 的 `<!-- vibego-agents:start -->...<!-- vibego-agents:end -->` 区块，
用于让 Gemini CLI 自动继承 vibego 的强制规约。

## aiogram Worker 行为

- `/start`：返回 `chat_id`、`MODE`、`ACTIVE_MODEL`；日志打印 `chat_id` 与 `user_id`。
- 文本消息：
    1. 依据 `ACTIVE_MODEL` 解析会话文件：Codex/ClaudeCode 为 JSONL，Gemini 为 `session-*.json`；
       默认读取 `current_session.txt` 中记录的会话路径，必要时搜索 `MODEL_SESSION_ROOT` 以回填。
    2. 将 prompt 注入 tmux（发送 `Esc` 清空模式、`C-j` 换行、`Enter` 提交）。
    3. 首次读取 `SESSION_OFFSETS` 初始化偏移；随后通过 `_deliver_pending_messages()` 补发当前尾部内容并持续轮询 JSONL。
    4. watcher 阶段提示 `ACTIVE_MODEL` 正在处理中，完成后自动推送结果（保留 Markdown）。
- MODE=A 下仍支持 `AGENT_CMD` 直接执行 CLI。

## 新增脚本

- `run_bot.sh`
    - `--model <name>`：codex / claudecode / gemini。
    - `--project <slug>`：日志/会话目录名称；未提供时依据工作目录推导。
    - `--foreground`：前台运行（默认后台 + `nohup`）。
    - `--no-stop`：启动前跳过 stop（默认先执行 `stop_bot.sh` 保证幂等）。
- `stop_bot.sh`
    - 幂等终止：`tmux kill-session`、关闭 `bot.pid` 指向的进程、移除缓存。
    - 示例：`./scripts/stop_bot.sh --model codex --project mall-backend`。

## 配置要点

### `.env`（Master 全局配置）

- 文件位置：`~/.config/vibego/.env`（可通过环境变量 `VIBEGO_CONFIG_DIR` 自定义）。
- `MASTER_BOT_TOKEN`：master bot 的 Token，由 `vibego init` 引导输入，启动时必须存在。
- `MASTER_CHAT_ID` / `MASTER_USER_ID`：首次在 Telegram 与 master 交互时自动写入，表示已授权的管理员账号。
- `MASTER_WHITELIST`：逗号分隔的 chat_id 列表，留空表示不限制；若与自动写入冲突以最新值为准。
- 其他可选变量（代理、日志级别、默认模型等）可按需新增，未设置时脚本使用默认值。

- 项目配置持久化在 `~/.config/vibego/config/master.db`（SQLite），对应的 JSON 镜像为
  `~/.config/vibego/config/projects.json`。如需离线编辑，可参考仓库内的 `config/projects.json.example` 模板。
- Master「⚙️ 项目管理」可新增/编辑/删除项目；仍可离线编辑 JSON，启动时会自动导入并同步至数据库。
- 必填字段：`bot_name`、`bot_token`、`project_slug`、`default_model`。
- 可选字段：`workdir`（项目路径）、`allowed_chat_id`（用于预设授权）。留空时，worker 首次收到消息会自动记录 chat_id 并写回
  `~/.config/vibego/state/master_state.json`。
- 其他自定义字段暂不读取。

### wx-dev-preview 端口配置

- `wx-dev-preview` 会调用微信开发者工具 CLI 的 `--port`，该端口为 IDE HTTP 服务端口；若端口未配置则命令会直接报错。
- 配置文件：`~/.config/vibego/config/wx_devtools_ports.json`（若设置了 `VIBEGO_CONFIG_DIR`/`MASTER_CONFIG_ROOT`，则路径随之变化）
- 配置模板：`config/wx_devtools_ports.json.example`
- 端口获取：微信开发者工具 → 设置 → 安全设置 → 服务端口（官方文档：https://developers.weixin.qq.com/miniprogram/dev/devtools/cli.html）

### 自动授权 & 状态

- worker 启动时若 `allowed_chat_id` 为空，首次合法消息会写入 `state/state.json` 并立即生效。
- master 重启：先调用 `stop_bot.sh` 清理，再依据 state 还原正在运行的项目。

## 后续规划

- Master bot 将统一轮询多个项目 bot，并调用 run/stop 脚本管理
  worker；当前版本先提供 worker 端结构与日志规范。
- Gemini 已接入；后续可按需补充更细粒度的工具调用回推与会话管理能力。

## 注意

- `~/.config/vibego/.env` 内包含敏感 Token 与管理员信息，请勿提交至版本库。
- 若需减少日志体积，可按需清理 `logs/<model>/<project>/` 或调整脚本输出阈值。
- 如果以源码仓库方式运行过旧版本，请执行 `./scripts/migrate_runtime.sh` 并确认仓库中仅保留 `.example`
  模板文件，避免误将数据库或日志提交至 Git。
- Master 会缓存版本检测结果，每个版本只提醒一次；如需立即重试可执行 `/projects` 或重启 master。

## Master 控制

- 管理员 Bot 使用 `MASTER_BOT_TOKEN` 启动（运行 `python master.py`）。
- 项目列表由 Master 仓库（`~/.config/vibego/config/master.db`）维护，可通过项目管理按钮或
  `~/.config/vibego/config/projects.json` 同步文件更新。示例字段：
    - `bot_name`：对应 Telegram 机器人的用户名（可带或不带 `@`，展示与交互时自动加 `@`）
    - `bot_token`：对应 worker 的 Telegram Token
    - `default_model`：默认模型（codex / claudecode / gemini）
    - `project_slug`：日志/目录名称
    - `workdir`：项目工作目录（可选）
    - `allowed_chat_id`：项目 worker 的授权 chat（用于 run_bot 时注入环境）
- 状态持久化：`~/.config/vibego/state/master_state.json` 自动记录各项目当前模型与运行状态，master 重启时会先 `stop_bot.sh`
  清理现场，再根据状态恢复。

### 管理命令

| 命令                          | 说明                                                            |
|-----------------------------|---------------------------------------------------------------|
| `/projects`                 | 列出所有项目当前状态与模型                                                 |
| `/run <project> [model]`    | 启动指定项目 worker，模型可选（默认使用当前/配置值）                                |
| `/stop <project>`           | 停止项目 worker                                                   |
| `/switch <project> <model>` | 停止后以新模型重新启动                                                   |
| `/start`                    | 显示帮助与项目数量                                                     |
| `/upgrade`                  | 执行 `pipx upgrade vibego && vibego stop && vibego start` 完成自升级 |

- `<project>` 参数可填写 `project_slug` 或对应 `@bot_name`，命令回复将自动展示可点击的 `@` 链接。

> master 仅与管理员 bot 交互；项目 bot 仍由 worker（run_bot.sh 启动的 `bot.py`）负责处理业务消息。
