# TASK_20260510_001 Codex `/goal` 模式支持方案（PLAN）

## 1. 任务口径

用户问题：如何支持 Codex 的 `/goal` 模式。

本阶段是 PLAN，不修改源码；目标是基于当前仓库和 Codex 官方口径，回答“应该怎么支持、改哪些地方、如何验证”，并给出可执行的 DEVELOP 方案。

## 2. 规约与证据读取

- 已读取 `$HOME/.config/vibego/AGENTS.md`：要求默认 PLAN 模式、vibe -> design -> develop、TDD 门禁、禁止用运行产物/临时修改交付、最终回复收尾字段。
- 已读取当前仓库 `AGENTS.md`：Strict Evidence Mode、事实必须有 `文件路径 + 锚点`、写入范围仅限受影响实现/测试/docs/必要证据更新、Python + pytest、运行期目录与主/worker 架构。
- 受影响范围内未发现更近的 `AGENTS.md`、`AGENTS.evidence.json`、`PROJECT-STYLE.md`、`CODE-GUIDELINES.md`、`DESIGN.md`。
  - 证据：`find . -name AGENTS.md -o -name AGENTS.evidence.json -o -name PROJECT-STYLE.md -o -name CODE-GUIDELINES.md -o -name DESIGN.md` 仅返回 `./AGENTS.md`。

## 3. 外部官方口径（Codex `/goal`）

### 3.1 官方能力

OpenAI Codex CLI 官方文档说明：

- `/goal` 是实验性功能，只有启用 `features.goals` 后可用。
- 可通过 `/experimental` 开启，或在 `config.toml` 的 `[features]` 下配置 `goals = true`。
- `/goal <objective>` 设置目标。
- `/goal` 查看当前目标。
- `/goal pause`、`/goal resume`、`/goal clear` 分别暂停、恢复、清除目标。
- Codex 会把 goal 绑定在当前 active thread 上继续工作。

证据：OpenAI 官方 Codex CLI 文档 `https://developers.openai.com/codex/cli/slash-commands#set-an-experimental-goal-with-goal`（锚点：`Set an experimental goal with /goal`）。

### 3.2 本机 Codex 能力探测

- 本机 `codex --version` 输出 `codex-cli 0.130.0`，`codex --help` 支持 `--enable <FEATURE>` 与 `-c features.<name>=true` 这类配置覆盖。
- 本机 `codex features list` 中 `goals` 为 `experimental true`。
- 本机 `$HOME/.codex/config.toml` 已存在：
  - `[features]`
  - `goals = true`

结论：当前开发机已经具备 `/goal` 功能，但 vibego 不能假设所有用户机器都已手动开启，因此产品级支持应在 Codex 启动命令中显式兜底启用，或至少提供可配置开关与医生诊断提示。

## 4. 当前仓库现状

### 4.1 Vibego 是 Python + Telegram + tmux + Codex CLI 桥接项目

- 包与 CLI：`pyproject.toml`（锚点：`[project]`, `[project.scripts]`）。
- worker 主体：`bot.py` 负责 Telegram 消息处理、tmux 注入、会话 JSONL 监听。
- Codex 启动：`scripts/models/codex.sh` 和 `scripts/start_tmux_codex.sh` 拼接 Codex 命令。

### 4.2 现有 Codex 启动命令没有显式启用 goal feature

- `scripts/models/codex.sh` 当前默认：`codex --dangerously-bypass-approvals-and-sandbox -c trusted_workspace=true`，再追加 `model_instructions_file` 和 `project_doc_max_bytes`。
  - 证据：`scripts/models/codex.sh:4-15`。
- `scripts/start_tmux_codex.sh` 对 Codex 同样追加 `model_instructions_file` 和 `project_doc_max_bytes`。
  - 证据：`scripts/start_tmux_codex.sh:19-24`。
- README Codex 环境变量只列出 `CODEX_WORKDIR`、`CODEX_CMD`、`CODEX_SESSION_ROOT`、`CODEX_SESSION_GLOB`，未说明 goal feature。
  - 证据：`README.md:109-116`。

### 4.3 现有 Telegram 文本链路会丢弃未知 slash 命令，导致 `/goal` 无法透传

- 最终通用文本 handler 在处理完任务快捷命令、自定义命令后，若 `prompt.startswith("/")` 会直接 `return`。
  - 证据：`bot.py:23497-23571`。
- 目前只有已注册的 bot 命令会被对应 handler 处理，比如 `/bind_session`。
  - 证据：`bot.py:15661-15671`。
- 任务快捷命令 `/TASK_XXXX` 走专门正则 handler。
  - 证据：`bot.py:19746-19758`。

结论：用户在 Telegram 发送 `/goal ...`，现在不会进入 `_dispatch_prompt_to_model(...)`，也不会进入 Codex CLI，因此“本机 Codex 已支持”并不等于“vibego 已支持”。

### 4.4 底层分发链路本身可以安全发送 slash prompt

- `_dispatch_prompt_to_model(...)` 是统一推送入口。
  - 证据：`bot.py:2707-2720`。
- `_prepend_enforced_agents_notice(...)` 会跳过以 `/` 开头的 prompt，避免破坏 slash 命令语义。
  - 证据：`bot.py:2529-2585`；测试证据：`tests/test_task_description.py:3654-3716`。
- `_should_send_plan_switch_command(...)` 对以 `/` 开头的 prompt 不再预注入 `/plan`。
  - 证据：`bot.py:2626-2639`。

结论：核心分发层已具备“发送内部 slash 命令不加规约前缀、不自动切 Plan”的基础能力；主要缺口在“入口路由 + feature 启用 + 用户交互 + 状态反馈”。

### 4.5 现有 PLAN MODE 按钮不能直接复用为 GOAL 状态按钮

- PLAN MODE 状态依赖 tmux 输出尾部的 `Plan mode`/`Default mode` 状态行解析。
  - 证据：`bot.py:4809-4831`。
- 主键盘当前为两行四按钮：任务列表、命令管理、会话实况、PLAN MODE。
  - 证据：`bot.py:4834-4905`；测试证据：`tests/test_chat_menu_buttons.py:35-53`。
- `/goal` 官方是 thread-level durable objective，不是终端底部 mode 状态；目前没有稳定的 tmux status-line 可解析证据。

结论：不应把 GOAL 做成类似 PLAN MODE 的强状态开关；更适合做“命令 + goal 管理入口 + Codex 最终回传”的轻量控制面。

### 4.6 Codex goal JSONL 事件形态观察

从本机历史 Codex session 中可见：

- goal 运行时会写入 `response_item`，`payload.type=message` 且 `role=developer`，内容为 “Continue working toward the active thread goal...” 等目标上下文。
- goal 过程中可能出现 `function_call`：`get_goal`、`update_goal`。
- `update_goal` 完成后，最终 `assistant` 输出通常会在 final answer 中包含 `Goal 已完成；耗时：...`。

当前 `bot.py::_extract_codex_payload(...)` 对 `response_item.message/assistant_message` 仅投递最终文本，已支持最终答案；但没有把 `get_goal/update_goal` 工具调用本身单独转成 Telegram 消息。
- 证据：`bot.py:15164-15260`。

结论：第一版不需要单独解析 goal tool call；否则容易把内部工具过程刷屏。可以保留最终答案投递，并在后续若用户需要“goal 进度条”时再做解析扩展。

## 5. 需求拆解（WHAT / WHY）

### 5.1 用户/场景与业务目标

- 用户：通过 Telegram 远程操作本机 Codex CLI 的开发者。
- 场景：一个任务不是单轮 prompt 能完成，需要 Codex 跨多 turn 持续推进，用户希望在 Telegram 中设置、查看、暂停、恢复、清除目标。
- 业务目标：让用户不打开本机终端，也能使用 Codex `/goal` 管理长任务。
- 成功标准：Telegram 中发送或点击 goal 入口后，真实进入当前 Codex thread 的 `/goal` 语义，而不是只在 vibego 内部保存一个假状态。

### 5.2 核心流程

1. 启动 Codex worker 时确保 `features.goals=true` 生效。
2. 用户发送 `/goal <objective>` 或点击 `🎯 GOAL` 后输入目标。
3. vibego 仅在 Codex 模型下允许 goal；非 Codex 直接 fail-closed。
4. vibego 将 `/goal ...` 原样发入当前主 tmux 或指定并行 tmux。
5. 用户可发送：
   - `/goal` 查看当前目标。
   - `/goal pause` 暂停。
   - `/goal resume` 恢复。
   - `/goal clear` 清除。
6. 模型最终输出仍通过现有 JSONL watcher 回传 Telegram。

### 5.3 权限与角色

- 沿用当前 worker chat 的权限模型；不新增用户角色。
- 不新增 DB 表，不改变 task 权限。
- 仅当前 chat 对应的 Codex 主会话/并行会话接收 goal 命令。

### 5.4 边界与异常

| 场景 | 预期 |
| --- | --- |
| 当前模型不是 Codex | 回复“暂仅支持 Codex `/goal`”，不发送到 tmux |
| Codex 未启动 / tmux 不存在 | 复用 `_dispatch_prompt_to_model` 的 tmux 错误提示 |
| `/goal` 空参数 | 发送 `/goal` 到 Codex，用于查看当前 goal |
| `/goal pause/resume/clear` | 原样发送给 Codex |
| `/goal` + 其他目标文本 | 原样发送 `/goal <objective>` |
| 目标过长 | 使用现有 Telegram 文本/附件策略；slash 命令本身不加规约前缀 |
| 并行会话回复模式 | 若处于 `/TASK_xxx` 并行回复上下文，则发送到对应并行 tmux；否则主会话 |
| features.goals 未启用 | 启动命令默认 `-c features.goals=true`；若用户通过 env 关闭，则由 Codex 返回错误，vibego 给出明确提示 |
| Codex 版本太旧 | doctor 或启动日志提示升级；不在 bot 中模拟 goal |

## 6. 方案对比

### 方案 A：仅文档化，要求用户手动配置并在终端用 `/goal`

- 做法：README 写“在 `$HOME/.codex/config.toml` 加 `goals = true`，终端里使用 `/goal`”。
- 优点：零代码风险。
- 缺点：Telegram 侧仍不能发送 `/goal`，与 vibego 的远程控制目标冲突；用户仍要打开终端。
- 结论：不推荐。

### 方案 B：最小代码支持 `/goal` Telegram 命令

- 做法：新增 `@router.message(Command("goal"))`，Codex-only，参数原样拼成 `/goal ...` 发送 `_dispatch_prompt_to_model(...)`；启动命令可选启用 `features.goals=true`。
- 优点：改动最小，复用现有 tmux/JSONL/watcher；不会污染主键盘。
- 缺点：用户需要记忆 `/goal pause/resume/clear`；没有可发现按钮。
- 适用：先快速打通核心能力。

### 方案 C：推荐方案：命令 + 轻量 GOAL 管理入口 + 启动兜底

- 做法：
  1. 启动 Codex 默认追加 `-c features.goals=true`，提供 `CODEX_GOALS_ENABLED=0` 可关闭。
  2. 新增 `/goal` Telegram 命令 handler。
  3. 主键盘新增 `🎯 GOAL` 入口，点击后展示 inline 管理面板：查看、设置、暂停、恢复、清除。
  4. “设置目标”进入 FSM，用户输入目标后发送 `/goal <objective>`。
  5. `/help`、`/tasks`、README 增加说明。
- 优点：真实支持 Codex goal，同时有可发现入口；不需要解析不稳定的终端状态；不新增 DB/依赖。
- 缺点：主键盘会从 2 行变成 3 行或某一行 3 按钮，需要更新现有键盘测试；`/goal` 仍为 Codex 实验功能。
- 结论：高置信推荐 🌟。

### 方案 D：在 vibego 自建 goal 状态表并驱动 Codex

- 做法：SQLite 存 goal objective/status，并定时向 Codex 续推。
- 优点：vibego 可完全掌控状态展示。
- 缺点：与 Codex thread-level goal 容易双源冲突，产生“vibego 显示 active，但 Codex thread 无 goal”的假状态；需要 DB 迁移和一致性处理。
- 结论：不推荐。第一性原理上，goal 的 source of truth 应是 Codex active thread。

## 7. 推荐开发设计

### 7.1 受影响目录/文件

| 类型 | 文件 | 影响 |
| --- | --- | --- |
| 实现 | `bot.py` | 新增 `/goal` 命令、GOAL 管理按钮、目标输入 FSM、Codex-only fail-closed、帮助文案 |
| 实现 | `tasks/fsm.py` | 新增 `GoalStates.waiting_objective`（或同等 FSM state） |
| 脚本 | `scripts/models/codex.sh` | Codex 命令追加 `-c features.goals=true`，支持 env 关闭 |
| 脚本 | `scripts/start_tmux_codex.sh` | 直接启动脚本同样追加 `-c features.goals=true`，保持 run_bot 与底层脚本一致 |
| 文档 | `README.md` | Codex 变量新增 `CODEX_GOALS_ENABLED` 与 `/goal` 用法 |
| 测试 | `tests/test_task_description.py` | 覆盖 `/goal` Telegram 命令不被通用 slash 丢弃、原样发入模型、不加规约前缀 |
| 测试 | `tests/test_chat_menu_buttons.py` | 覆盖主键盘新增 `🎯 GOAL` 与按钮 handler |
| 测试 | `tests/test_start_tmux_model_cmd.py` | 覆盖 Codex dry-run 默认包含 `features.goals=true`，关闭 env 后不追加；非 Codex 不追加 |
| 测试 | `tests/test_codex_jsonl_phase.py` 或 `tests/test_plan_progress.py` | 可选：证明 goal developer message/tool call 不会刷屏，final answer 仍可投递 |
| DB | 无 | 不新增表/字段/索引 |
| CI/构建 | 无 | 不新增依赖、不改构建工具 |

### 7.2 契约变更

#### Telegram 命令

- 新增 `/goal [objective|pause|resume|clear]`。
- 行为：
  - `/goal`：查看当前 Codex goal。
  - `/goal <objective>`：设置目标。
  - `/goal pause`：暂停。
  - `/goal resume`：恢复。
  - `/goal clear`：清除。

#### Telegram 按钮

- 新增主键盘按钮：`🎯 GOAL`。
- 点击后发送 inline 面板：
  - `👁 查看当前目标` -> 发送 `/goal`
  - `➕ 设置目标` -> 进入目标输入 FSM
  - `⏸ 暂停` -> 发送 `/goal pause`
  - `▶️ 恢复` -> 发送 `/goal resume`
  - `🧹 清除` -> 二次确认后发送 `/goal clear`

#### 环境变量

- 新增：`CODEX_GOALS_ENABLED`
  - 默认：`1`（推荐，因为本功能就是支持 `/goal`）
  - `0/false/no/off`：不主动追加 `-c features.goals=true`
- 不修改用户全局 `$HOME/.codex/config.toml`，避免越权改个人 Codex 配置。

### 7.3 模块伪代码

```python
GOAL_COMMAND = "/goal"
WORKER_GOAL_BUTTON_TEXT = "🎯 GOAL"

class GoalStates(StatesGroup):
    waiting_objective = State()


def _is_goal_command_text(text: str) -> bool:
    return text.strip().split(maxsplit=1)[0] == "/goal"


def _build_goal_slash_command(args: str) -> str:
    args = args.strip()
    return "/goal" if not args else f"/goal {args}"


async def _dispatch_goal_command(message, args, dispatch_context=None):
    if not _is_codex_model():
        await message.answer("/goal 暂仅支持 Codex。", reply_markup=_build_worker_main_keyboard())
        return False
    command = _build_goal_slash_command(args)
    return await _dispatch_prompt_to_model(
        message.chat.id,
        command,
        reply_to=message,
        ack_immediately=True,
        intended_mode=None,
        dispatch_context=dispatch_context,
    )


@router.message(Command("goal"))
async def on_goal_command(message, state):
    await state.clear()
    await _dispatch_goal_command(message, _extract_command_args(message.text or ""))
```

脚本伪代码：

```bash
codex_goals_enabled="${CODEX_GOALS_ENABLED:-1}"
if [[ "$MODEL_KEY" == "codex" && ! "$codex_goals_enabled" =~ ^(0|false|no|off)$ ]]; then
  MODEL_CMD="${MODEL_CMD} -c features.goals=true"
fi
```

## 8. 测试矩阵（TDD）

### 8.1 baseline gate

DEVELOP 前先跑：

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py \
  tests/test_chat_menu_buttons.py \
  tests/test_start_tmux_model_cmd.py \
  tests/test_codex_jsonl_phase.py
```

### 8.2 红灯测试（先写，预期失败）

| 用例 | 文件 | 初始失败原因 |
| --- | --- | --- |
| Telegram `/goal Finish tests` 会发送 `/goal Finish tests` 到 `_dispatch_prompt_to_model` | `tests/test_task_description.py` | 当前无 `/goal` handler，通用文本会丢弃 slash |
| Telegram `/goal` 无参数会发送 `/goal` | `tests/test_task_description.py` | 同上 |
| 非 Codex 模型发送 `/goal` fail-closed，不调用 dispatch | `tests/test_task_description.py` | 当前无 handler |
| `/goal` slash prompt 不注入 `ENFORCED_AGENTS_NOTICE` | `tests/test_task_description.py` | 可复用已有 slash 测试，新增 `/goal` 口径 |
| 主键盘包含 `🎯 GOAL` | `tests/test_chat_menu_buttons.py` | 当前仅两行四按钮 |
| 点击 `🎯 GOAL` 展示 inline 管理面板 | `tests/test_chat_menu_buttons.py` | 当前无 handler |
| 点击“设置目标”进入 FSM，输入目标后发送 `/goal <objective>` | `tests/test_chat_menu_buttons.py` 或新测试文件 | 当前无状态 |
| 点击查看/暂停/恢复/清除分别发送 `/goal`、`/goal pause`、`/goal resume`、`/goal clear` | `tests/test_chat_menu_buttons.py` | 当前无回调 |
| Codex dry-run 默认追加 `-c features.goals=true` | `tests/test_start_tmux_model_cmd.py` | 当前脚本未追加 |
| `CODEX_GOALS_ENABLED=0` 时不追加 goal flag | `tests/test_start_tmux_model_cmd.py` | 当前无开关 |
| 非 Codex dry-run 不追加 goal flag | `tests/test_start_tmux_model_cmd.py` | 防回归 |
| goal developer message/get_goal/update_goal tool call 不刷 Telegram，仅 final answer 投递 | `tests/test_codex_jsonl_phase.py` | 当前可能已符合，作为回归保护 |

### 8.3 绿灯与回归

实现后至少执行两轮一致性：

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py \
  tests/test_chat_menu_buttons.py \
  tests/test_start_tmux_model_cmd.py \
  tests/test_codex_jsonl_phase.py

python3.11 -m pytest -q \
  tests/test_task_description.py \
  tests/test_chat_menu_buttons.py \
  tests/test_start_tmux_model_cmd.py \
  tests/test_codex_jsonl_phase.py

python3.11 -m vibego_cli doctor
```

若实现改动触及 JSONL watcher 或启动脚本公共逻辑，建议追加：

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_plan_progress.py tests/test_session_binding.py
```

## 9. 非功能设计

### 9.1 性能

- `/goal` 只是一次 tmux 输入，性能与普通 prompt 一致。
- 不新增数据库查询，不影响 QPS/TPS。

### 9.2 可观测

- 日志新增 goal dispatch 结构化字段：`goal_action=set/view/pause/resume/clear`、`chat`、`model`、`tmux_session`。
- 不记录完整 objective 到日志，避免泄露长需求正文；可记录长度和 hash。

### 9.3 安全

- 仅允许 `/goal` allowlist，不打开“任意未知 slash 透传”，避免 Telegram bot 命令空间被误用。
- 不修改 `$HOME/.codex/config.toml`；只通过启动命令 `-c features.goals=true` 临时启用。
- `/goal clear` 需二次确认，避免误清。

### 9.4 一致性

- Goal 状态以 Codex active thread 为唯一 source of truth；vibego 不落库模拟。
- Resume 历史 session 后，`/goal` 操作自然作用于恢复后的 active thread。

## 10. 风险与回滚

### 风险

1. `/goal` 是实验功能，Codex 未来可能改命令或 JSONL 事件形态。
2. 主键盘新增按钮会影响现有键盘布局测试和用户肌肉记忆。
3. `features.goals=true` 虽是启动命令覆盖，不改全局 config，但仍启用了实验能力；需提供 env 关闭。
4. 如果用户在并行会话中设置 goal，goal 绑定到并行 Codex thread，不会自动同步到主会话；需要 UI 文案说明。

### 回滚

- 回滚 `bot.py` 的 `/goal` handler、按钮、FSM、回调。
- 回滚 `tasks/fsm.py` 的 GoalStates。
- 回滚 `scripts/models/codex.sh` 和 `scripts/start_tmux_codex.sh` 的 goal feature flag 拼接。
- 回滚 README 与测试。
- 不涉及 DB schema，无迁移回滚。

## 11. 验收标准（AC）

1. Codex worker 启动命令默认包含 `-c features.goals=true`；设置 `CODEX_GOALS_ENABLED=0` 后不包含。
2. Telegram 发送 `/goal Finish migration and keep tests green` 后，tmux 收到完全相同的 slash command，不追加 `ENFORCED_AGENTS_NOTICE`。
3. Telegram 发送 `/goal` 后，tmux 收到 `/goal`。
4. Telegram 发送 `/goal pause/resume/clear` 后，tmux 收到对应命令。
5. 非 Codex 模型下 `/goal` 返回明确不可用提示，不调用 tmux。
6. 主键盘可发现 `🎯 GOAL`，点击后可查看、设置、暂停、恢复、清除。
7. `🎯 GOAL -> 设置目标 -> 输入目标` 后，发送 `/goal <objective>`，并恢复主菜单。
8. `🎯 GOAL -> 清除` 有二次确认。
9. Codex goal 运行过程中的 `get_goal/update_goal` 工具调用不会被当作普通消息刷屏；最终答案仍能回传。
10. 受影响测试双轮通过，`python3.11 -m vibego_cli doctor` 通过。

## 12. 推荐结论

推荐选择 **方案 C：命令 + 轻量 GOAL 管理入口 + 启动兜底**。

理由：

- 符合 Codex 官方 `/goal` 的真实 source of truth：目标绑定在 Codex active thread，而不是 vibego 自建假状态。
- 最小化基础设施改动：不新增 DB、不新增依赖、不改 CI。
- 解决当前真实阻断：Telegram 未知 slash 命令会被丢弃。
- 提供可发现入口，降低用户记忆 `/goal pause/resume/clear` 的成本。

---

## 13. DEVELOP 实施记录（2026-05-10）

### 13.1 进入实现前规约复核

- 已重新读取 `$HOME/.config/vibego/AGENTS.md` 与仓库根目录 `AGENTS.md`。
- 受影响范围再次扫描：仅发现 `./AGENTS.md`；未发现更近目录的 `AGENTS.md`、`AGENTS.evidence.json`、`PROJECT-STYLE.md`、`CODE-GUIDELINES.md`、`DESIGN.md`。
- 执行路径按已确认的推荐方案 C：命令 + 轻量 GOAL 管理入口 + 启动兜底。

### 13.2 baseline gate

执行命令：

```bash
python3.11 -m pytest -q tests/test_task_description.py tests/test_chat_menu_buttons.py tests/test_start_tmux_model_cmd.py tests/test_codex_jsonl_phase.py
```

baseline 结果：`240 passed / 1 failed`，失败项为 `tests/test_task_description.py::test_task_list_outputs_detail_buttons`。

失败现象：任务列表 inline keyboard 中“每页条数”行位于任务详情按钮之前，导致该测试把分页行误纳入“状态筛选按钮行”统计；该问题不是 `/goal` 新需求引入，但会阻断本次受影响集合的门禁。

处置：保持按钮文案不变，仅调整任务列表按钮顺序为“状态筛选 -> 任务详情 -> 每页条数 -> 翻页/操作”，让状态筛选区边界清晰，避免削弱既有测试资产。

### 13.3 TDD 红灯测试

先新增/调整以下测试，再运行聚焦红灯集合：

```bash
python3.11 -m pytest -q \
  tests/test_start_tmux_model_cmd.py::test_start_tmux_dry_run_keeps_codex_config_flags \
  tests/test_start_tmux_model_cmd.py::test_start_tmux_dry_run_can_disable_codex_goal_flag \
  tests/test_chat_menu_buttons.py::test_worker_keyboard_structure \
  tests/test_chat_menu_buttons.py::test_worker_keyboard_button_text \
  tests/test_chat_menu_buttons.py::test_worker_goal_button_opens_goal_panel_for_codex \
  tests/test_chat_menu_buttons.py::test_worker_goal_button_fails_closed_for_non_codex \
  tests/test_task_description.py::test_goal_command_dispatches_objective_to_codex \
  tests/test_task_description.py::test_goal_command_without_args_queries_current_goal \
  tests/test_task_description.py::test_goal_command_fails_closed_for_non_codex \
  tests/test_task_description.py::test_goal_set_callback_enters_objective_state \
  tests/test_task_description.py::test_goal_objective_input_dispatches_goal_and_restores_menu \
  tests/test_task_description.py::test_goal_clear_callback_requires_confirmation \
  tests/test_codex_jsonl_phase.py::test_extract_codex_goal_tool_call_ignored \
  tests/test_codex_jsonl_phase.py::test_extract_codex_goal_developer_message_ignored
```

红灯结果：`12 failed / 3 passed`。

主要失败原因：Codex 启动命令未追加 `features.goals=true`、主键盘无 `🎯 GOAL`、无 `/goal` handler、无 GOAL FSM/callback 常量、developer role 的 goal 内部上下文会被当作普通 message 投递。

### 13.4 实现内容

| 文件 | 修改内容 |
| --- | --- |
| `tasks/fsm.py` | 新增 `GoalStates.waiting_objective`，用于 GOAL 面板的目标输入态。 |
| `bot.py` | 新增 `/goal` Bot 命令；新增 `🎯 GOAL` 主键盘入口；新增 GOAL inline 面板、设置目标 FSM、查看/暂停/恢复/清除/二次确认回调；非 Codex fail-closed；目标命令原样透传 `_dispatch_prompt_to_model(...)`；过滤 Codex goal 的 developer 内部上下文消息；调整任务列表按钮顺序修复 baseline gate。 |
| `scripts/models/codex.sh` | Codex 模型配置默认追加 `-c features.goals=true`；支持 `CODEX_GOALS_ENABLED=0/false/no/off` 关闭；避免重复追加已有 `features.goals`。 |
| `scripts/start_tmux_codex.sh` | 直接启动脚本保持同样的 goal feature flag 兜底与关闭开关。 |
| `README.md` | 增补 `CODEX_GOALS_ENABLED` 与 Telegram `/goal [objective|pause|resume|clear]` 用法说明。 |
| `tests/test_start_tmux_model_cmd.py` | 覆盖 Codex dry-run 默认 goal flag、关闭开关、非 Codex 不追加。 |
| `tests/test_chat_menu_buttons.py` | 覆盖主键盘新增 `🎯 GOAL` 与 GOAL 面板/非 Codex fail-closed。 |
| `tests/test_task_description.py` | 覆盖 `/goal` 有参/无参透传、非 Codex fail-closed、设置目标 FSM、目标输入后恢复主菜单、清除二次确认。 |
| `tests/test_codex_jsonl_phase.py` | 覆盖 `get_goal/update_goal` 内部工具调用不刷屏、developer role 目标上下文不投递。 |

### 13.5 验证记录

聚焦红灯转绿：

```bash
python3.11 -m pytest -q <13.3 中的 15 个聚焦用例>
```

结果：`15 passed`（初次实现）；补充并行回复模式覆盖后，GOAL 聚焦用例 `7 passed`。

受影响集合双轮验证：

```bash
python3.11 -m pytest -q tests/test_task_description.py tests/test_chat_menu_buttons.py tests/test_start_tmux_model_cmd.py tests/test_codex_jsonl_phase.py
```

- 第 1 轮：`254 passed in 15.07s`
- 第 2 轮：`254 passed in 15.07s`

关联回归验证：

```bash
python3.11 -m pytest -q tests/test_task_batch_status.py
```

结果：`9 passed`。

脚本/语法/依赖验证：

```bash
python3.11 -m py_compile bot.py tasks/fsm.py
python3.11 -m vibego_cli doctor
bash scripts/test_deps_check.sh
```

结果：均通过；`doctor` 输出 `python_ok=true` 且关键依赖缺失列表为空。

Codex 模型脚本手工 dry-run：

```bash
bash -lc 'set -euo pipefail; ROOT_DIR="$PWD"; unset MODEL_CMD CODEX_CMD CODEX_GOALS_ENABLED; source scripts/models/common.sh; source scripts/models/codex.sh; model_configure; printf "%s\n" "$MODEL_CMD"'
bash -lc 'set -euo pipefail; ROOT_DIR="$PWD"; unset MODEL_CMD CODEX_CMD; export CODEX_GOALS_ENABLED=0; source scripts/models/common.sh; source scripts/models/codex.sh; model_configure; printf "%s\n" "$MODEL_CMD"'
```

结果：默认命令包含 `-c features.goals=true`；关闭开关后不包含该 flag。

全量 pytest 探测：

```bash
python3.11 -m pytest -q
```

结果：`873 passed / 24 failed`。失败全部集中在 `tests/test_parallel_flow.py`，原因是该测试文件仍按旧构造方式实例化 `ParallelLaunchSession` 或调用 `_begin_parallel_launch(...)`，缺少当前代码已要求的 `send_mode` 参数。该问题在本次 `/goal` 受影响范围之外，未在本任务中扩展修复，需单独建任务处理。

### 13.6 验收 Checklist

| AC | 状态 | 证据 |
| --- | --- | --- |
| Codex 启动默认包含 `-c features.goals=true` | ✅ | `tests/test_start_tmux_model_cmd.py::test_start_tmux_dry_run_keeps_codex_config_flags`；手工 model_configure dry-run |
| `CODEX_GOALS_ENABLED=0` 后不追加 goal flag | ✅ | `tests/test_start_tmux_model_cmd.py::test_start_tmux_dry_run_can_disable_codex_goal_flag`；手工 disabled dry-run |
| `/goal <objective>` 原样透传 | ✅ | `tests/test_task_description.py::test_goal_command_dispatches_objective_to_codex` |
| 并行回复模式下 `/goal` 投递到对应并行 tmux | ✅ | `tests/test_task_description.py::test_goal_command_respects_parallel_reply_target` |
| `/goal` 无参数查询当前目标 | ✅ | `tests/test_task_description.py::test_goal_command_without_args_queries_current_goal` |
| 非 Codex fail-closed | ✅ | `tests/test_task_description.py::test_goal_command_fails_closed_for_non_codex`、`tests/test_chat_menu_buttons.py::test_worker_goal_button_fails_closed_for_non_codex` |
| 主键盘可发现 `🎯 GOAL` | ✅ | `tests/test_chat_menu_buttons.py::test_worker_keyboard_button_text` |
| 点击 GOAL 展示管理面板 | ✅ | `tests/test_chat_menu_buttons.py::test_worker_goal_button_opens_goal_panel_for_codex` |
| 设置目标进入 FSM 并恢复主菜单 | ✅ | `tests/test_task_description.py::test_goal_set_callback_enters_objective_state`、`test_goal_objective_input_dispatches_goal_and_restores_menu` |
| 清除目标二次确认 | ✅ | `tests/test_task_description.py::test_goal_clear_callback_requires_confirmation` |
| goal 内部 tool/developer 消息不刷屏 | ✅ | `tests/test_codex_jsonl_phase.py::test_extract_codex_goal_tool_call_ignored`、`test_extract_codex_goal_developer_message_ignored` |

### 13.7 风险与后续

1. `/goal` 仍是 Codex 实验功能；本实现通过启动参数兜底启用，并提供 `CODEX_GOALS_ENABLED=0` 快速关闭。
2. vibego 不落库保存 goal 状态，避免和 Codex active thread 形成双源状态；因此 GOAL 面板“查看当前目标”仍是向 Codex 发送 `/goal`，等待 Codex 返回。
3. 全量 pytest 的 `tests/test_parallel_flow.py` 存在既有 send_mode 参数适配问题，应单独处理，避免把并行会话重构混入本次 `/goal` 需求。
