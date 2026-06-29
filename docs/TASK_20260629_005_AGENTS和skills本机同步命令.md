# TASK_20260629_005_AGENTS 和 skills 本机同步命令

## 任务背景

- 用户诉求：新增一个可点击命令，直接把最新 `AGENTS.md` 与 Vibego 内置 skills 同步到本机，避免只改提示词/skill 时必须重新发布包并执行 `/upgrade`。
- 当前现状：`scripts/run_bot.sh` 与 `scripts/start_tmux_codex.sh` 启动时会调用 `scripts/models/common.sh::sync_vibego_agents_for_model()`，把包内 `AGENTS-template.md` 与 `vibego_cli/data/skills/*/SKILL.md` 注入到本机模型规约文件。
- 当前限制：如果最新 AGENTS/skills 只存在于本地仓库，而 pipx 已安装包尚未发布升级，现有自动同步仍会使用 pipx 包内旧模板/旧 skill，无法做到“点击即同步最新”。

## 需求目标

1. Master 侧新增可点击入口：推荐 `/agents_sync` slash command + 系统设置按钮“🔄 同步 AGENTS/Skills”。
2. 点击后同步本机以下目标：
   - `~/.codex/AGENTS.md`
   - `~/.claude/CLAUDE.md`
   - `~/.gemini/GEMINI.md`
   - `~/.config/vibego/AGENTS.md`
3. 同步对象包含：
   - `AGENTS-template.md`
   - `vibego_cli/data/skills/*/SKILL.md`
   - `vibego_cli/data/skills/*/agents/*.yaml`（如存在）
4. 不执行 `pipx upgrade`，不重启 master，不启动/停止 worker。
5. 保留现有目标文件中非 vibego managed block 的用户自定义内容。
6. 失败时 fail-closed：任何目标写入失败时明确列出失败目标，不伪装成功。

## 推荐方案

### A. 配置覆盖目录 + Master/CLI 同步命令（推荐）

- 新增 `vibego agents-sync` CLI 子命令，Master `/agents_sync` 和按钮调用同一 Python 服务。
- 源路径优先级：
  1. `VIBEGO_AGENTS_SOURCE_ROOT` / `MASTER_AGENTS_SOURCE_ROOT` 指向的本地仓库；
  2. `~/.config/vibego/agents/source_root.json` 记录的上次成功源；
  3. 当前安装包根目录（兜底）。
- 命令先把最新模板/skills 复制到持久覆盖目录：`~/.config/vibego/agents/current/`。
- 修改 `scripts/run_bot.sh` 与 `scripts/start_tmux_codex.sh`：若覆盖目录存在，优先从该目录同步，避免下一次 worker 启动又用 pipx 旧包覆盖本机 AGENTS。
- 同步完成后立即更新 Codex/Claude/Gemini/统一 AGENTS 目标文件。

优点：真正支持“不发布包也同步最新提示词/skills”；不写 pipx site-packages；后续 worker 启动不回退。  
缺点：需要一次性改 CLI、Master、启动脚本与测试。

### B. 只在 Master 命令中直接改目标 AGENTS 文件

- 点击后从当前安装包或指定源读取模板/skills，直接写四个目标文件。

优点：改动最小。  
缺点：下一次 worker 启动仍可能用 pipx 包内旧模板覆盖，无法稳定满足“不发版也保持最新”。

### C. 点击后从远端 raw URL 拉取最新模板/skills

- Master 命令访问远端仓库 raw 文件并更新本机。

优点：真正“远端最新”。  
缺点：需要网络、认证、私有仓库策略；高风险，不适合作为默认。

## 结论

推荐方案 A。它把“提示词/skill 热更新”变成配置覆盖能力，而不是临时 patch pipx 包或依赖远端网络。

## 开发设计

### 受影响目录/文件

- `vibego_cli/main.py`：新增 `agents-sync` 子命令。
- `vibego_cli/agents_sync.py`：新增同步服务，负责源解析、覆盖目录更新、managed block 渲染和目标文件写入。
- `master.py`：新增 `/agents_sync` 命令、系统设置按钮、后台任务锁、状态消息编辑。
- `scripts/run_bot.sh`：优先使用 `~/.config/vibego/agents/current/AGENTS-template.md` 与覆盖 skills。
- `scripts/start_tmux_codex.sh`：同上，避免恢复/新会话覆盖回旧 skill。
- `tests/test_agents_sync.py`：新增 CLI/service 单元测试。
- `tests/test_master_update_notifications.py` 或 `tests/test_chat_menu_buttons.py`：新增 Master 命令/按钮测试。
- `tests/test_start_tmux_model_cmd.py`：新增覆盖目录优先生效测试。
- `docs/TASK_20260629_005_AGENTS和skills本机同步命令.md/html`：方案与验收记录。

### 契约变更

- 新增 CLI：`vibego agents-sync [--source-root PATH] [--json]`。
- 新增 Master 命令：`/agents_sync`。
- 新增系统设置按钮：`🔄 同步 AGENTS/Skills`。
- 新增持久覆盖目录：`~/.config/vibego/agents/current/`。
- 新增可选源记录：`~/.config/vibego/agents/source_root.json`。

### 关键流程

1. 用户点击 `/agents_sync` 或系统设置按钮。
2. Master 校验管理员权限与任务锁。
3. 同步服务解析源路径。
4. 校验源路径存在 `AGENTS-template.md` 和 `vibego_cli/data/skills`。
5. 复制模板/skills 到 `~/.config/vibego/agents/current/`。
6. 使用同一 managed block 规则更新本机四类目标文件。
7. 返回同步摘要：源路径、目标文件、技能数量、失败列表、是否使用覆盖目录。

### 测试矩阵

| 场景 | 预期 |
| --- | --- |
| 指定本地仓库为 source-root | 复制最新模板/skills 到覆盖目录，并同步四个目标文件 |
| 未指定 source-root | 回退到安装包根目录，仍可同步 |
| 目标文件已有用户内容 | 只替换 `<!-- vibego-agents:start -->...end`，保留其他内容 |
| skill 缺失 | fail-closed，返回清晰错误，不写半成品 |
| Master 非管理员调用 | 返回未授权，不执行同步 |
| 并发点击 | 第二个请求提示已有同步任务 |
| run_bot/start_tmux 启动 | 优先读取覆盖目录，不被 pipx 旧模板覆盖 |

### 风险与回滚

- 风险：source-root 指错会同步错误模板。缓解：同步前校验 `AGENTS-template.md`、skills 目录和可读文件数量；结果消息展示源路径。
- 风险：覆盖目录内容不完整。缓解：先写临时目录，再原子替换 current。
- 风险：用户本地自定义 AGENTS 被覆盖。缓解：只替换 managed block，缺 marker 时先备份 `.vibego.bak`。
- 回滚：删除 `~/.config/vibego/agents/current/` 后启动脚本自动回退到安装包内模板/skills。

## 待决策项

- D1：是否采用方案 A（配置覆盖目录 + Master/CLI 同步命令）作为一次性完整实现？

## 实施记录（2026-06-29）

### 已落地

1. 新增 `vibego_cli/agents_sync.py`：
   - 统一实现 source-root 解析、override 目录发布、managed block 渲染、目标文件更新。
   - source-root 优先级：显式参数 > `VIBEGO_AGENTS_SOURCE_ROOT` / `MASTER_AGENTS_SOURCE_ROOT` > `CONFIG_ROOT/agents/source_root.json` > 当前安装包根目录。
   - override 目录：`CONFIG_ROOT/agents/current/`，包含 `AGENTS-template.md`、`vibego_cli/data/skills/**` 与 `manifest.json`。
   - 目标文件默认：Codex、Claude、Gemini 与 `CONFIG_ROOT/AGENTS.md`，并支持 `CODEX_AGENTS_FILE` / `CLAUDE_AGENTS_FILE` / `GEMINI_AGENTS_FILE` / `VIBEGO_AGENTS_FILE` 重定向。

2. 新增 CLI：`vibego agents-sync [--source-root PATH] [--json]`。
   - 成功输出来源、override、skills 数量与目标状态。
   - 失败返回非 0，不伪装成功。

3. 新增 Master 入口：
   - slash command：`/agents_sync`。
   - 系统设置按钮：`🧩 同步 AGENTS/Skills`。
   - 使用后台任务锁，避免重复点击并发写入。

4. 启动防覆盖：
   - `scripts/run_bot.sh` 与 `scripts/start_tmux_codex.sh` 在启动同步前检查 `CONFIG_ROOT/agents/current/manifest.json`。
   - 若 override 有效，设置 `VIBEGO_BUILTIN_SKILLS_DIR` 并使用 override 内模板/skills。
   - 若 manifest 存在但模板或 skills 损坏，直接退出，避免静默回退到 pipx 旧包。
   - 若 override 不存在，保留原安装包回退行为。

5. 规约同步：
   - `AGENTS.md` 的 CLI 子命令事实已补充 `agents-sync`。

### 验证记录

| 命令 | 结果 | 说明 |
| --- | --- | --- |
| `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_agents_sync.py` | 5 passed | 服务与 CLI 同步行为 |
| `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_master_update_notifications.py::test_cmd_agents_sync_authorized tests/test_chat_menu_buttons.py::test_master_system_settings_menu_includes_agents_sync tests/test_start_tmux_model_cmd.py::test_start_tmux_prefers_agents_override_when_manifest_exists tests/test_start_tmux_model_cmd.py::test_run_bot_prefers_agents_override_when_manifest_exists` | 4 passed | Master 入口与启动脚本红绿闭环 |
| `BOT_TOKEN=123456:ABC MODEL_WORKDIR=/Users/david/hypha/tools/vibego python3.11 -m pytest -q tests/test_agents_sync.py tests/test_master_update_notifications.py tests/test_chat_menu_buttons.py tests/test_start_tmux_model_cmd.py` | 93 passed | 受影响文件级测试集合；`MODEL_WORKDIR` 用于满足 worker 环境自检 |

### 已知边界

- 该命令不会联网拉取远端仓库；“最新”来自显式 `--source-root`、环境变量或上次成功记录的本地源目录。
- 若要让 Telegram 按钮始终使用本地源码目录，应先执行一次：
  `vibego agents-sync --source-root /Users/david/hypha/tools/vibego`，或在 master 环境中配置 `MASTER_AGENTS_SOURCE_ROOT=/Users/david/hypha/tools/vibego`。

## 追加修正（命令管理入口）

### 用户反馈

用户在 Telegram 的“命令管理”页未看到新增按钮。截图显示的是项目命令中心列表，而不是 Master 的“系统设置”页。

### 根因

- 第一版实现新增了 Master slash command `/agents_sync` 与系统设置按钮，但没有把它加入 `command_center.defaults.DEFAULT_GLOBAL_COMMANDS`，因此不会出现在项目命令管理列表中。
- 当前 Telegram 已渲染的旧消息不会自动刷新，需要重新打开命令管理页。

### 修正

- 已新增默认通用命令 `agents-sync`：
  - 标题：`同步 AGENTS/Skills 到本机`
  - 命令：优先使用 `VIBEGO_AGENTS_SOURCE_ROOT` / `MASTER_AGENTS_SOURCE_ROOT`；若当前项目目录本身是 vibego 源码，则使用 `MODEL_WORKDIR`；否则回退 `ROOT_DIR`。
  - 执行时设置 `PYTHONPATH=$SRC`，确保在源码项目内点击时可以直接使用未发布的新实现。
- 已执行 `python3.11 -m vibego_cli commands-seed` 注入本机命令库，当前 `master_commands.db` 中已存在并启用 `agents-sync`。

### 追加验证

| 命令 | 结果 |
| --- | --- |
| `BOT_TOKEN=123456:ABC python3.11 -m pytest -q tests/test_agents_sync.py::test_default_global_commands_include_agents_sync_button` | 1 passed |
| `python3.11 -m vibego_cli commands-seed` | 已注入通用命令：agents-sync |
| `BOT_TOKEN=123456:ABC MODEL_WORKDIR=/Users/david/hypha/tools/vibego python3.11 -m pytest -q tests/test_agents_sync.py tests/test_master_update_notifications.py tests/test_chat_menu_buttons.py tests/test_start_tmux_model_cmd.py` | 94 passed |
