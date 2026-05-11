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

## 14. 2026-05-10 缺陷修复：`/goal` 回传串到 Codex App 会话

### 14.1 现象 -> 影响 -> 根因 -> 修法 -> 验证

- 现象：用户在 Telegram 使用 goal 模式后，tmux 中对应 Codex CLI 会话并未作为回传来源，Telegram 收到了 Codex App 侧另一个对话消息。
- 影响：Telegram 侧“看似已收到模型响应”，但响应并不属于刚才被注入 `/goal` 的 active thread，容易误导用户继续在错误上下文中操作。
- 根因：`_dispatch_goal_command(...)` 复用通用 `_dispatch_prompt_to_model(...)`，而通用链路在 pointer 缺失/未及时写入时允许 `_fallback_locate_latest_session(...)` 扫描全局 Codex sessions。普通 prompt 这样兜底可降低“未检测到会话日志”的误报；但 `/goal` 是 Codex active thread 级能力，输入 tmux 与监听 JSONL 必须同源，否则会串到 Codex App 或其它 Codex CLI 会话。
- 修法：新增 `allow_session_discovery_fallback` 开关，默认保持普通 prompt 兼容；`/goal` 专用分发显式传 `False`，禁止 pointer 缺失时扫描全局 latest rollout，仅允许使用 `CHAT_SESSION_MAP`、当前 worker pointer 或并行 `dispatch_context.pointer_file`。
- 验证：新增红灯测试 `test_goal_dispatch_does_not_fallback_to_global_latest_session`，先复现“fallback 到 Codex App rollout 后 ok=True”的错误，再实现后转绿。

### 14.2 受影响目录与边界

| 类型 | 路径 | 影响 |
| --- | --- | --- |
| 实现 | `bot.py` | `_dispatch_prompt_to_model(...)` 增加会话发现兜底开关；`_await_session_path(...)` 在开关关闭时不扫描全局 session root；`_dispatch_goal_command(...)` 禁止 `/goal` 使用全局 latest fallback。 |
| 测试 | `tests/test_task_description.py` | 新增 `/goal` 主会话缺失 pointer 时不得绑定 Codex App 最新 session 的回归测试。 |
| 文档 | `docs/TASK_20260510_001_codex_goal模式支持方案.md` | 记录本次缺陷、根因、契约变化、测试矩阵、风险与回滚。 |
| 证据 | `AGENTS.md` | 补充 `/goal` 回传同源约束的仓库事实证据。 |
| 脚本 | `scripts/models/codex.sh`、`scripts/start_tmux_codex.sh` | 本次不改；上一阶段已完成 `features.goals=true` 启动兜底。 |
| 数据库 | `tasks/` SQLite 表结构 | 本次不改；仍不落库保存 goal 状态，Codex active thread 是唯一事实源。 |
| 前端/UI | 无 | 本仓库本次只涉及 Telegram bot 后端逻辑与测试；无浏览器/H5/管理后台页面。 |

### 14.3 契约变更

- 内部函数新增可选参数：
  - `_dispatch_prompt_to_model(..., allow_session_discovery_fallback: bool = True)`
  - `_await_session_path(..., allow_session_discovery_fallback: bool = True)`
- 对外 Telegram 契约不变：
  - `/goal`、`/goal <objective>`、`/goal pause/resume/clear` 命令格式不变。
  - GOAL 面板按钮文案不变。
- 行为口径变更：
  - 普通 prompt：仍可沿用既有全局 session discovery fallback。
  - `/goal`：pointer 缺失时宁可提示“未检测到会话日志”，也不绑定全局最新 Codex rollout。

### 14.4 TDD 记录

1. Baseline：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session' tests/test_session_binding.py tests/test_codex_jsonl_phase.py tests/test_chat_menu_buttons.py tests/test_start_tmux_model_cmd.py
```

结果：`15 passed, 249 deselected`。

2. 红灯：

```bash
python3.11 -m pytest -q tests/test_task_description.py::test_goal_dispatch_does_not_fallback_to_global_latest_session
```

结果：失败，`ok` 实际为 `True`；日志显示命中 `strict fallback locate latest session ... codex-app-rollout.jsonl`。

3. 绿灯：

```bash
python3.11 -m pytest -q tests/test_task_description.py::test_goal_dispatch_does_not_fallback_to_global_latest_session
```

结果：`1 passed`。

4. 聚焦回归第 1 轮：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session' tests/test_session_binding.py tests/test_codex_jsonl_phase.py tests/test_chat_menu_buttons.py tests/test_start_tmux_model_cmd.py
```

结果：`16 passed, 249 deselected`。

5. 聚焦回归第 2 轮与语法检查：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session' tests/test_session_binding.py tests/test_codex_jsonl_phase.py tests/test_chat_menu_buttons.py tests/test_start_tmux_model_cmd.py
python3.11 -m py_compile bot.py
```

结果：第二轮 `16 passed, 249 deselected`；`py_compile` 通过。

6. 运行期诊断与依赖脚本：

```bash
python3.11 -m vibego_cli doctor
bash scripts/test_deps_check.sh
```

结果：`doctor` 显示 `python_ok=true` 且关键依赖缺失列表为空；依赖检查脚本通过。

7. 注释收口后的 final sanity：

```bash
python3.11 -m py_compile bot.py
python3.11 -m pytest -q tests/test_task_description.py::test_goal_dispatch_does_not_fallback_to_global_latest_session
```

结果：`py_compile` 通过；单测 `1 passed`。

### 14.5 测试矩阵

| 场景 | 预期 | 覆盖 |
| --- | --- | --- |
| `/goal` 主会话 pointer 为空且全局存在 Codex App 最新 session | 不调用 `_fallback_locate_latest_session`，不绑定 App session，返回失败提示 | `test_goal_dispatch_does_not_fallback_to_global_latest_session` |
| `/goal` 处于并行回复上下文 | 使用 `dispatch_context.tmux_session` 与 `dispatch_context.pointer_file` | `test_goal_command_respects_parallel_reply_target` |
| `/goal` 有参/无参 | 原样发送 `/goal ...` 或 `/goal`，不追加强制规约前缀 | `test_goal_command_dispatches_objective_to_codex`、`test_goal_command_without_args_queries_current_goal` |
| 非 Codex 模型 | fail-closed，不调用 tmux 分发 | `test_goal_command_fails_closed_for_non_codex` |
| goal 内部工具/开发者消息 | 不刷到 Telegram，仅 final answer 投递 | `tests/test_codex_jsonl_phase.py` 相关用例 |
| 普通并行首次派发 | 仍禁止并行 fresh session 缺失时 fallback 到旧 session | `test_dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session` |

### 14.6 风险与回滚

- 风险 1：如果用户的 worker pointer 长期未写入，`/goal` 会比普通 prompt 更容易提示失败；这是故意 fail-closed，用于避免串会话。
- 风险 2：普通 prompt 仍保留 fallback，因此若未来发现普通链路也会串 Codex App，需要单独扩大任务范围，不混入本次 `/goal` 修复。
- 回滚方式：
  1. 回滚 `bot.py` 中 `allow_session_discovery_fallback` 参数与 `/goal` 调用处的 `False`。
  2. 回滚 `tests/test_task_description.py::test_goal_dispatch_does_not_fallback_to_global_latest_session`。
  3. 回滚本节文档与 `AGENTS.md` 对应证据行。

## 15. 2026-05-11 PLAN：`/goal` 回传仍未正确进入 Telegram 的二次缺陷分析

### 15.1 本轮任务口径

用户反馈：`goal 模式下，消息还是没有正确响应发到 telegram`。

本轮处于 PLAN 阶段：只读取证、分析根因、制定 TDD 修复计划；不修改源码实现。由于问题延续自本任务第 14 节“`/goal` 回传串到 Codex App 会话”，本轮继续续写当前主任务文档，而不是新建独立任务。

### 15.2 规约与证据读取

- 已读取 `$HOME/.config/vibego/AGENTS.md`：要求默认 PLAN、vibe -> design -> develop、Bug 必须按“现象 -> 影响 -> 根因 -> 修法 -> 验证”收敛，develop 必须 TDD。
- 已读取仓库根目录 `AGENTS.md`：Strict Evidence Mode，仓库事实必须给出 `文件路径 + 锚点`；写入范围允许 `docs/` 任务文档、受影响实现与测试；`/goal` 现有事实强调 tmux 输入会话与 JSONL 回传会话必须同源。
- 本轮扫描受影响范围：仅发现 `./AGENTS.md`；未发现更近目录的 `AGENTS.md`、`AGENTS.evidence.json`、`PROJECT-STYLE.md`、`CODE-GUIDELINES.md`、`DESIGN.md`。
- 已优先读取当前 `/docs` 下最新且最完整的 goal 主任务文档：`docs/TASK_20260510_001_codex_goal模式支持方案.md`。

### 15.3 当前现象取证

#### 现象 A：`/goal` 相关定向测试仍通过，但线上仍复现

本轮执行现有 goal/会话相关聚焦回归：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session' tests/test_session_binding.py tests/test_codex_jsonl_phase.py tests/test_chat_menu_buttons.py tests/test_start_tmux_model_cmd.py
```

结果：`16 passed, 249 deselected`。

结论：现有测试只覆盖了“bot 内部不调用全局 fallback”的路径，没有覆盖 `session_binder.py` 把 pointer 写错到 Codex App / 当前 Codex 会话的场景。

#### 现象 B：运行日志显示 `/goal` 后仍绑定到了非 worker tmux 对应的 Codex session

本机运行日志出现：

- `vibegobot` 在 `2026-05-11 08:22:03` 绑定 fresh session：`/Users/david/.codex/sessions/2026/05/11/rollout-2026-05-11T08-21-02-019e1468-a3c0-7f72-890b-18c0197b706c.jsonl`。
- 同一 session 文件经只读结构统计，事件类型包含当前 Codex 对话的 `exec_command`、`update_plan`、`message user/assistant commentary` 等，而不是 Telegram worker tmux 中 `/goal` 应产生的独立 active thread final answer。
- 另一个历史 worker `hyphagiraffebot` 在 `2026-05-11 08:21:26` 曾因短时间大量发送 `模型输出` 触发 Telegram Flood control：`Retry in 257 seconds`，说明错误会话/错误事件被当作模型输出连续刷 Telegram，造成“响应不正确”与“发送失败”双重问题。

> 注意：本节只记录运行结构与路径，不沉淀用户正文内容。

### 15.4 当前代码链路证据

1. `/goal` 命令已通过 `bot.py::_dispatch_goal_command(...)` 调用 `_dispatch_prompt_to_model(...)`，且显式传入 `allow_session_discovery_fallback=False`。
   - 证据：`bot.py`（锚点：`_dispatch_goal_command`、`allow_session_discovery_fallback=False`）。
2. `_dispatch_prompt_to_model(...)` 在 `needs_session_wait` 时调用 `_await_session_path(...)`，会信任 pointer 文件返回的 session 路径。
   - 证据：`bot.py`（锚点：`needs_session_wait`、`_await_session_path(...)`、`_update_pointer(pointer_path, session_path)`）。
3. `_await_session_path(...)` 在 `allow_session_discovery_fallback=False` 时只禁止 bot 进程自己扫描全局 latest，但仍会接受 pointer 文件已写入的路径。
   - 证据：`bot.py`（锚点：`if not allow_session_discovery_fallback: return None` 位于 pointer 二次读取之后）。
4. `scripts/start_tmux_codex.sh` 每次启动都会清空 pointer 并启动 `scripts/session_binder.py`，binder 搜索根默认包含 `$HOME/.codex/sessions`。
   - 证据：`scripts/start_tmux_codex.sh`（锚点：`: > "$SESSION_POINTER_FILE"`、`BINDER_CMD+=(--session-root "$MODEL_SESSION_ROOT")`、`MODEL_SESSION_ROOT="${CODEX_SESSION_ROOT:-$HOME/.codex/sessions}"`）。
5. `scripts/session_binder.py::_select_latest_session(...)` 仅按 `mtime + cwd + boot_ts` 选择最新 rollout；只要 Codex App 当前会话 cwd 与项目相同且 mtime 新于 boot_ts，就可能被选中并写入 pointer。
   - 证据：`scripts/session_binder.py`（锚点：`_select_latest_session`、`if cwd != target_cwd: continue`、`if mtime > latest_mtime`、`_write_text(pointer, str(candidate))`）。
6. Codex `/goal` 内部会产生 `developer`、`get_goal/update_goal`、`commentary/final_answer` 等多类事件；当前 `_extract_codex_payload(...)` 会过滤 developer 与非 final phase，但若绑定错 session，错误 session 的 final_answer 仍会被正常投递。
   - 证据：`bot.py`（锚点：`role and role != "assistant"`、`CODEX_MESSAGE_PHASE_FINAL_ANSWER`、`function_call`）。

### 15.5 根因分析

#### 根因候选 1（高置信）：session_binder 污染 pointer，`allow_session_discovery_fallback=False` 没有覆盖 pointer 已错写场景

第 14 节修复的是 bot 进程内部的 fallback 扫描；但真实运行链路中还有一个独立后台进程 `session_binder.py` 会扫描 `$HOME/.codex/sessions` 并写入 pointer。只要当前 Codex App / Codex 本轮开发会话与 worker 的 `MODEL_WORKDIR` 相同，并且 mtime 更新晚于 worker 启动时间，binder 就会把它当成“最新同 cwd 会话”。随后 `/goal` 分发虽然禁止 fallback，但仍信任 pointer，最终 Telegram watcher 监听错误 JSONL。

这是目前最高置信根因，因为它同时解释：

- 为什么“禁用 fallback”后仍会串到 Codex App：错不在 `_fallback_locate_latest_session`，而在 pointer 上游已被 binder 写错。
- 为什么日志里 `vibegobot` 绑定到当前 Codex session：当前 Codex session cwd 与仓库 cwd 一致，满足 binder 的 cwd 过滤。
- 为什么 Telegram 响应“不正确”而不是“完全没响应”：错误 session 的 assistant final_answer 被当作 worker 输出发送。

#### 根因候选 2（中置信）：新会话首次绑定时 offset 初始化过晚，可能跳过 `/goal` 已生成的早期输出

`_dispatch_prompt_to_model(...)` 发送 tmux 命令后才在 `session_key not in SESSION_OFFSETS` 时调用 `_initial_session_offset(session_path)` 初始化 offset。如果 pointer 绑定较慢，而 Codex `/goal` 很快写入了“查看/设置目标”的响应，则初始化 offset 可能落在响应之后，仅靠 `SESSION_INITIAL_BACKTRACK_BYTES=16384` 回看尾部，仍可能漏掉较早输出。

此候选可解释“没有回传”，但不能解释“绑定到 Codex App 当前 session”的日志，因此优先级低于候选 1。develop 阶段应把它作为同源修复后的补充测试覆盖，避免后续出现“绑定对了但漏首条响应”。

#### 根因候选 3（中低置信）：goal 长任务输出事件形态导致 Telegram Flood control 或刷屏

运行日志存在大量“准备发送模型输出/模型输出发送成功”后触发 Flood control 的记录。可能原因是绑定错 session 后，历史/其它会话里的大量 final_answer 被连续发送；也可能是 goal 持续运行时产生多个 final_answer。由于当前结构统计显示错误 session 大量事件并非 worker `/goal` 同源输出，该问题更可能是根因 1 的副作用。develop 阶段可增加“同一轮 goal 中间 commentary/event_msg 不发送、final_answer 去重/节流”的保护测试，但不应把 Telegram 发送节流作为根本修法替代会话同源修复。

### 15.6 推荐修复方案

#### 方案 A：只在 `_dispatch_goal_command` 里继续加强 bot fallback 禁用

- 做法：继续给 `_await_session_path` 增加参数、减少扫描。
- 优点：改动小。
- 缺点：无法阻止 `session_binder.py` 先把 pointer 写错；已被本轮日志证伪。
- 结论：不推荐。

#### 方案 B：限制 session_binder 搜索根，不再让 worker binder 扫描全局 `$HOME/.codex/sessions`

- 做法：为 worker tmux session 使用专属会话根或专属 marker；若 Codex CLI 无法配置会话输出目录，则至少让 binder 不直接把全局同 cwd 最新会话作为可信来源。
- 优点：从源头降低 Codex App 与 worker tmux 串会话概率。
- 缺点：需要确认 Codex CLI 是否可配置 session root；若不可配置，单纯移除全局 root 会导致 binder 找不到真实 tmux session。
- 结论：方向正确，但需要结合方案 C 的 tmux 归属校验，否则风险较高。

#### 方案 C（推荐 🌟）：为 pointer 绑定增加“worker tmux 同源校验”，并让 `/goal` 只接受同源 pointer

核心原则：`/goal` 的 source of truth 是 Codex active thread，因此必须满足“Telegram 输入进入哪个 tmux session，就只能监听该 tmux session 启动/恢复出来的 session 文件”。

建议实现：

1. `scripts/start_tmux_codex.sh` 启动/复用 worker tmux 时生成本轮 `VIBEGO_SESSION_BIND_TOKEN`，写入 worker 环境或命令上下文。
2. `scripts/session_binder.py` 不再仅用 `cwd + mtime` 判断候选；新增严格模式：
   - 优先绑定 `active_session_id.txt` 对应的 Codex 原生 session id；
   - 或从 tmux pane 当前命令/环境/启动时间窗口校验候选；
   - 对 `/goal` 必须要求 strict source：候选文件首行 `session_meta.payload.cwd` 匹配、session id 匹配 worker active id、mtime 不早于本轮 tmux 命令发送时间。
3. `bot.py::_dispatch_prompt_to_model(...)` 对 `/goal` 这类 active-thread 命令记录 `dispatch_started_at` 或 `pre_dispatch_offset`：
   - 如果已有 pointer 指向 session，发送前先保存该 session 的当前 offset，后续从该 offset 读；
   - 如果 pointer 等待后才出现，offset 初始化不能用“文件尾部”，应从“命令发送前可证明的 offset”或至少从 session 创建后的第一条 post-dispatch 事件开始。
4. 对 `session_binder.py` 增加单测，复现“同 cwd 的 Codex App 会话 mtime 更新晚于 boot_ts，但不是 worker session”时不得写入 pointer。
5. 对 `bot.py` 增加单测，复现 pointer 被写到非 worker session 时 `/goal` fail-closed，而不是发送错误 session 的 final_answer。

优点：直接打掉当前根因；不靠 Telegram 发送层兜底；符合第 14 节同源约束。缺点：需要补充 session 绑定契约，改动涉及脚本 + bot + 测试，需严格 TDD。

#### 方案 D：Telegram 发送层限流/合并，避免 Flood control

- 做法：对模型输出做节流、合并或队列。
- 优点：能降低 Flood control。
- 缺点：不能解决 `/goal` 监听错 session；只会把错误内容更慢地发出去。
- 结论：只可作为后续增强，不作为本次主修法。

### 15.7 受影响范围

| 类型 | 路径 | 影响 |
| --- | --- | --- |
| 实现 | `scripts/session_binder.py` | 增加 worker session 同源校验；避免同 cwd 的 Codex App/其它 Codex 会话污染 pointer。 |
| 实现 | `scripts/start_tmux_codex.sh` | 启动 worker 时生成/传递 session 绑定上下文；必要时输出 active session 约束给 binder。 |
| 实现 | `bot.py` | `/goal` 分发前后记录可验证 offset/dispatch 时间；对 pointer 同源性失败时 fail-closed；避免错 session 仍被 watcher 发送。 |
| 测试 | `tests/test_session_binder.py`（若已有相近文件则复用） | 覆盖 binder 不绑定同 cwd 但非 worker 的最新 Codex App session。 |
| 测试 | `tests/test_task_description.py` | 覆盖 `/goal` pointer 被污染时不发送错误 session 输出；覆盖 post-dispatch offset 不漏首条响应。 |
| 测试 | `tests/test_start_tmux_model_cmd.py` | 覆盖启动脚本向 binder 传递新增同源参数。 |
| 文档 | `docs/TASK_20260510_001_codex_goal模式支持方案.md` | 记录二次根因、契约变化、测试矩阵、风险与回滚。 |
| 数据库 | 无 | 不新增 SQLite 表/字段。 |
| 前端/UI | 无 | 本次不涉及浏览器/H5/管理后台 UI。 |
| 外部契约 | Telegram `/goal` 命令格式不变 | 仅内部绑定同源约束增强。 |

### 15.8 测试矩阵（develop 阶段 TDD）

#### baseline gate

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session' \
  tests/test_session_binding.py \
  tests/test_codex_jsonl_phase.py \
  tests/test_chat_menu_buttons.py \
  tests/test_start_tmux_model_cmd.py
```

本轮 PLAN 已执行，当前结果：`16 passed, 249 deselected`。

#### 红灯测试（先写，预期失败）

| 用例 | 文件 | 失败原因 |
| --- | --- | --- |
| binder 面对同 cwd、mtime 最新但缺少 worker 同源标识的 Codex App session，不应写 pointer | `tests/test_session_binder.py` | 当前 `_select_latest_session` 只按 cwd/mtime/boot_ts，仍会选中 |
| binder 面对 worker session 与 App session 同时存在时，只绑定 worker session | `tests/test_session_binder.py` | 当前无 worker 同源判定 |
| `/goal` 若 pointer 指向非 worker session，应 fail-closed 并提示会话同源校验失败 | `tests/test_task_description.py` | 当前只要 pointer 存在就信任 |
| `/goal` 发送前已有有效 session，offset 应从发送前位置开始，不漏掉快速返回的 goal 响应 | `tests/test_task_description.py` | 当前新绑定时可能用文件尾部初始化 |
| 启动脚本把新增同源参数传给 `session_binder.py` | `tests/test_start_tmux_model_cmd.py` | 当前 BINDER_CMD 未带该参数 |
| goal 错误会话大量 final_answer 不应触发连续 Telegram 发送 | `tests/test_codex_jsonl_phase.py` 或新发送层单测 | 当前错误 session 被视为普通输出 |

#### 绿灯与回归

实现后至少执行两轮：

```bash
python3.11 -m pytest -q \
  tests/test_session_binder.py \
  tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session' \
  tests/test_session_binding.py \
  tests/test_codex_jsonl_phase.py \
  tests/test_chat_menu_buttons.py \
  tests/test_start_tmux_model_cmd.py

python3.11 -m pytest -q \
  tests/test_session_binder.py \
  tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session' \
  tests/test_session_binding.py \
  tests/test_codex_jsonl_phase.py \
  tests/test_chat_menu_buttons.py \
  tests/test_start_tmux_model_cmd.py

python3.11 -m py_compile bot.py scripts/session_binder.py
python3.11 -m vibego_cli doctor
bash scripts/test_deps_check.sh
```

全量 `python3.11 -m pytest -q` 可作为探测执行；若仍失败于已知 `tests/test_parallel_flow.py` send_mode 旧夹具问题，应继续按 fail-closed 记录，不混入本次修复。

### 15.9 契约变更

- Telegram 外部命令不变：`/goal`、`/goal <objective>`、`/goal pause/resume/clear`。
- 内部新增约束：`/goal` 回传 session 必须通过 worker tmux 同源校验；同 cwd 最新 session 不能作为充分条件。
- pointer 文件语义增强：`current_session.txt` 不再只是“同 cwd 最新 Codex session”，而必须是“当前 worker tmux 所属 Codex session”。
- 不新增数据库契约；不新增依赖；不修改用户 `$HOME/.codex/config.toml`。

### 15.10 风险与回滚

#### 风险

1. 若 Codex CLI 目前无法暴露稳定的 worker session id，强同源校验可能导致 `/goal` 更容易 fail-closed；但这比把错误会话发到 Telegram 更安全。
2. 修改 `session_binder.py` 会影响 Codex 主会话首次绑定、`/bind_session` 之后指针恢复、并行/主会话共存场景；必须用现有会话绑定测试兜住。
3. 如果只修 binder，不修 offset，仍可能出现“绑定正确但首条响应被 offset 跳过”的次级问题。

#### 回滚

1. 回滚 `scripts/session_binder.py` 的同源校验参数与选择逻辑。
2. 回滚 `scripts/start_tmux_codex.sh` 中传给 binder 的新增参数。
3. 回滚 `bot.py` 中 `/goal` 同源校验与 offset 保护逻辑。
4. 回滚新增测试与本节文档。

### 15.11 推荐结论

推荐采用 **方案 C：为 pointer 绑定增加 worker tmux 同源校验，并让 `/goal` 只接受同源 pointer**。

在用户确认进入 develop 后，严格按 TDD 执行：先补红灯测试复现 `session_binder` 写错 pointer，再最小修改实现，最后执行受影响集合双轮验证。

## 16. 2026-05-11 DEVELOP：方案 C 同源 marker 修复落地记录

### 16.1 用户决策

用户回复 `1C`，确认采用第 15 节的 **方案 C：为 pointer 绑定增加 worker tmux 同源校验，并让 `/goal` 只接受同源 pointer**。

### 16.2 实现摘要

本次实现不新增第三方依赖、不修改数据库、不修改 Telegram 外部命令契约。

核心改动：

1. `scripts/start_tmux_codex.sh`
   - 每次非 resume 的 Codex worker 启动时生成 `vibego-session-bind-token:<random>`。
   - 将该 marker 写入 worker 私有 `session_binder_token.txt`。
   - 复制原 Codex `model_instructions_file` 到 worker 私有 `codex_model_instructions.md`，并追加 marker 注释，确保 Codex JSONL 首行 `session_meta.payload.base_instructions.text` 可被同源识别。
   - 启动 `session_binder.py` 时传入 `--required-marker`。
   - resume 历史主会话时清空 marker 文件，避免旧线程因缺少本次 marker 被误拒。

2. `scripts/session_binder.py`
   - 新增 `--required-marker` 参数。
   - `_select_latest_session(...)` 在 Codex JSONL 分支中继续保留 `cwd + boot_ts + mtime` 过滤，同时要求候选首行 `base_instructions.text` 包含 worker marker。
   - 同 cwd 但缺少 marker 的 Codex App/其它 Codex 会话不再写入 pointer。

3. `bot.py`
   - 新增 `SESSION_BINDER_TOKEN_FILE` 配置读取。
   - `/goal` 这类 `allow_session_discovery_fallback=False` 的 Codex active-thread 命令会读取 marker 文件。
   - cached session / pointer session 若缺少 marker，会被拒绝并清理本 chat 的会话映射与 offset。
   - `_await_session_path(...)` 新增 `session_validator`，等待 pointer 时不会接受缺少 marker 的会话。

4. `scripts/run_bot.sh`
   - 导出 `SESSION_BINDER_TOKEN_FILE`，让 bot 与 start script 使用同一个 marker 文件路径。

5. `AGENTS.md`
   - 更新 Codex `/goal` 回传约束证据，补充 marker fail-closed 口径。

### 16.3 受影响目录

| 路径 | 影响 | 说明 |
| --- | --- | --- |
| `bot.py` | 行为修复 | `/goal` 严格校验 pointer/cached session 是否属于当前 worker。 |
| `scripts/session_binder.py` | 行为修复 | 绑定 pointer 时拒绝缺少 worker marker 的同 cwd 会话。 |
| `scripts/start_tmux_codex.sh` | 启动契约增强 | 为 Codex worker 注入 marker 并传给 binder。 |
| `scripts/run_bot.sh` | 环境变量增强 | 导出 marker 文件路径。 |
| `tests/test_session_binder_codex.py` | 新增测试 | 覆盖同 cwd Codex App 会话污染 pointer 的红灯场景。 |
| `tests/test_task_description.py` | 回归测试 | 覆盖 `/goal` 拒绝缺少 worker marker 的 pointer。 |
| `tests/test_start_tmux_model_cmd.py` | 回归测试 | 覆盖启动脚本已接线 marker/binder 参数。 |
| `AGENTS.md` | 证据更新 | 更新 `/goal` 同源约束证据。 |
| `docs/TASK_20260510_001_codex_goal模式支持方案.md` | 任务记录 | 记录本次 develop 结果与验证。 |

不受影响边界：

- 数据库：无表结构、字段、索引、迁移变更。
- Telegram 命令：`/goal` 外部命令格式不变。
- Gemini/Copilot：`--required-marker` 仅在启动脚本写入 marker 文件时传递；Gemini/Copilot 现有 session_binder 测试保持通过。
- Codex resume：本次对历史 resume 清空 marker 文件，避免旧线程无 marker 被误判。

### 16.4 契约变更

内部契约新增：

- `SESSION_BINDER_TOKEN_FILE`：worker 私有 marker 文件，默认位于 `current_session.txt` 同目录下的 `session_binder_token.txt`。
- `scripts/session_binder.py --required-marker <marker>`：当 marker 非空时，Codex JSONL 候选必须在首行 `session_meta.payload.base_instructions.text` 中包含该 marker。
- `codex_model_instructions.md`：worker 私有 Codex 指令副本，用于注入 marker；原用户/全局指令文件不被直接改写。

### 16.5 TDD 记录

#### baseline

```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session'
python3.11 -m pytest -q tests/test_gemini_support.py tests/test_copilot_support.py -k session_binder
python3.11 -m pytest -q tests/test_start_tmux_model_cmd.py tests/test_codex_jsonl_phase.py tests/test_chat_menu_buttons.py
```

结果：

- `10 passed, 181 deselected`
- `3 passed, 19 deselected`
- `64 passed`

#### 红灯测试

新增/调整测试后先执行：

```bash
python3.11 -m pytest -q \
  tests/test_session_binder_codex.py \
  tests/test_start_tmux_model_cmd.py::test_start_tmux_script_wires_codex_session_marker_to_binder \
  tests/test_task_description.py::test_goal_dispatch_rejects_pointer_without_worker_marker
```

预期失败并已确认：

- `_select_latest_session()` 不支持 `required_marker`。
- 启动脚本缺少 `SESSION_BINDER_TOKEN_FILE` / `--required-marker`。
- `bot.py` 缺少 `SESSION_BINDER_TOKEN_FILE`。

#### 绿灯与回归

实现后执行：

```bash
python3.11 -m py_compile bot.py scripts/session_binder.py
python3.11 -m pytest -q \
  tests/test_session_binder_codex.py \
  tests/test_start_tmux_model_cmd.py::test_start_tmux_script_wires_codex_session_marker_to_binder \
  tests/test_task_description.py::test_goal_dispatch_rejects_pointer_without_worker_marker
```

结果：`4 passed`。

受影响集合第一轮：

```bash
python3.11 -m pytest -q tests/test_session_binder_codex.py tests/test_gemini_support.py tests/test_copilot_support.py -k 'session_binder or codex_session_binder'
python3.11 -m pytest -q tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session'
python3.11 -m pytest -q tests/test_start_tmux_model_cmd.py tests/test_codex_jsonl_phase.py tests/test_chat_menu_buttons.py
```

结果：

- `5 passed, 19 deselected`
- `11 passed, 181 deselected`
- `65 passed`

受影响集合第二轮与基础门禁：

```bash
python3.11 -m pytest -q tests/test_session_binder_codex.py tests/test_gemini_support.py tests/test_copilot_support.py -k 'session_binder or codex_session_binder'
python3.11 -m pytest -q tests/test_task_description.py -k 'goal or dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session'
python3.11 -m pytest -q tests/test_start_tmux_model_cmd.py tests/test_codex_jsonl_phase.py tests/test_chat_menu_buttons.py
bash -n scripts/start_tmux_codex.sh scripts/run_bot.sh
python3.11 -m vibego_cli doctor
bash scripts/test_deps_check.sh
```

结果：

- `5 passed, 19 deselected`
- `11 passed, 181 deselected`
- `65 passed`
- `bash -n` 通过
- `doctor` 通过，`python_ok=true`
- `scripts/test_deps_check.sh` 通过

全量探测：

```bash
python3.11 -m pytest -q
```

结果：`878 passed, 24 failed, 6 warnings`。

失败集中在 `tests/test_parallel_flow.py`，错误为 `ParallelLaunchSession.__init__()` / `_begin_parallel_launch()` 缺少 `send_mode` 参数。该失败不在本次 `/goal` 同源修复影响面内；本轮按 fail-closed 记录，不混入本次修复扩大范围。

### 16.6 风险与回滚

风险：

1. 首次应用该修复后，必须重启对应 worker/tmux，旧进程不会自动生成 marker。
2. 若 Codex CLI 后续不再把 `model_instructions_file` 内容写入 `session_meta.payload.base_instructions.text`，binder 会 fail-closed，表现为 pointer 不绑定；这是安全失败，不会串到 Codex App 会话。
3. 历史 resume 会话暂不强制 marker；如后续要完全覆盖 resume，需要基于 Codex resume JSONL 结构另做专项设计。

回滚：

1. 回滚 `scripts/start_tmux_codex.sh` 的 marker 生成、指令副本和 `--required-marker` 传参。
2. 回滚 `scripts/session_binder.py` 的 `required_marker` 参数与过滤逻辑。
3. 回滚 `bot.py` 的 marker 校验与 `_await_session_path` validator。
4. 回滚新增测试和 AGENTS/docs 证据更新。
