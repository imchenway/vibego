# TASK_20260603_001 底部按钮改为命令管理与 PLAN MODE（DEVELOP）

## 1. 需求与目标

用户要求：Worker 底部常驻两个按钮改为：

1. 左侧：`📟 命令管理`
2. 右侧：`🧭 PLAN MODE: ...`

目标是把高频入口从“任务列表 + 命令管理”调整为“命令管理 + 终端协作模式切换”，减少用户进入 `/plan_mode` 命令菜单的额外步骤。

## 2. 当前实现证据

| 事实 | 证据 |
|---|---|
| Worker 底部键盘由 `_build_worker_main_keyboard` 生成 | `bot.py`（锚点：`_build_worker_main_keyboard`） |
| 原底部按钮为 `WORKER_MENU_BUTTON_TEXT` 与 `WORKER_COMMANDS_BUTTON_TEXT` | `bot.py`（锚点：`WORKER_MENU_BUTTON_TEXT`、`WORKER_COMMANDS_BUTTON_TEXT`） |
| PLAN/MODE 切换 handler 已存在，匹配 `🧭 PLAN MODE:` / `🧭 MODE:` | `bot.py`（锚点：`on_worker_plan_mode_button`、`_handle_worker_plan_mode_toggle_request`） |
| 命令管理按钮 handler 已存在 | `bot.py`（锚点：`on_commands_button`） |
| 相关测试集中在 `tests/test_chat_menu_buttons.py` | `tests/test_chat_menu_buttons.py`（锚点：`test_worker_keyboard_button_text`、`test_worker_main_keyboard_*`） |

## 3. 方案选择

### 方案 A：只改底部按钮顺序与右侧 PLAN MODE（本次采用）

- 底部键盘固定一行两列：左 `命令管理`，右 `PLAN MODE/MODE`。
- 任务列表入口不删除，只是不再占用底部键盘；仍可通过命令菜单和既有文本命令进入。
- 复用现有 PLAN/MODE 切换 handler，不新增 callback 或数据库字段。

优点：最小改动、风险低、符合用户明确要求。  
缺点：渲染键盘时需要读取终端模式状态；但已有 `refresh_plan_mode_state=False` 缓存参数可控制频繁探测。

### 方案 B：右侧只显示固定 `PLAN MODE`，不展示 ON/OFF

优点：不探测 tmux，渲染更轻。  
缺点：用户无法从底部按钮直接知道当前状态，弱于既有状态展示能力。

### 方案 C：保留任务列表，新增第三个 PLAN MODE

优点：任务列表入口不变。  
缺点：不符合“底部两个按钮”的明确要求，也会让底部键盘重新变拥挤。

## 4. 受影响目录与边界

| 范围 | 是否影响 | 说明 |
|---|---:|---|
| `bot.py` | 是 | 调整 `_build_worker_main_keyboard`，新增右侧按钮文案解析 helper。 |
| `tests/test_chat_menu_buttons.py` | 是 | 更新底部键盘结构、顺序、状态缓存与切换后刷新断言。 |
| `AGENTS.md` | 是 | 更新 Worker 主菜单按钮收敛事实。 |
| SQLite/DB | 否 | 不涉及任务、命令、项目数据结构。 |
| CLI/脚本/CI | 否 | 不新增依赖，不改构建链与启动脚本。 |
| Telegram 命令菜单 | 否 | `/commands`、`/plan_mode`、`/session_live`、`/goal` 保持原入口。 |

## 5. 契约变更

1. Worker 底部常驻键盘必须只有一行两列。
2. 左侧按钮必须是 `WORKER_COMMANDS_BUTTON_TEXT`（`📟 命令管理`）。
3. Codex/非 Copilot 模型右侧按钮必须是 `WORKER_PLAN_MODE_BUTTON_TEXT_ON/OFF/UNKNOWN` 之一。
4. Copilot 模型右侧按钮保持 Copilot 三态 `WORKER_COPILOT_MODE_BUTTON_TEXT_*`，沿用同一个切换 handler。
5. `refresh_plan_mode_state=False` 时不主动探测 tmux，优先使用缓存状态；无缓存时展示 unknown。
6. 显式传入 `plan_mode_state` / `copilot_mode_state` 时写入缓存，保证切换后刷新出的底部键盘与刚探测到的状态一致。

## 6. TDD 实施记录

### 6.1 Baseline

```bash
python3.11 -m pytest -q tests/test_chat_menu_buttons.py -k 'worker_keyboard or worker_main_keyboard or plan_mode_button'
# 9 passed, 42 deselected
```

### 6.2 红灯

先更新测试期望后执行：

```bash
python3.11 -m pytest -q tests/test_chat_menu_buttons.py -k 'worker_keyboard or worker_main_keyboard or copilot_mode_button'
# 5 failed, 3 passed, 43 deselected
```

红灯说明：旧实现仍返回 `['📋 任务列表', '📟 命令管理']`，且不会展示 PLAN/MODE 按钮。

### 6.3 绿色实现

| 文件 | 修改点 |
|---|---|
| `bot.py` | 新增 `_resolve_worker_plan_mode_button_text`，负责解析 Codex/非 Copilot 的右侧 PLAN MODE 文案。 |
| `bot.py` | 新增 `_resolve_worker_copilot_mode_button_text`，负责解析 Copilot 的右侧 MODE 文案。 |
| `bot.py` | `_build_worker_main_keyboard` 改为 `[命令管理, PLAN MODE/MODE]`。 |
| `tests/test_chat_menu_buttons.py` | 更新底部按钮顺序、可见状态、缓存、显式状态与切换后刷新断言。 |

## 7. 测试矩阵

| 用例 | 覆盖点 | 当前结果 |
|---|---|---|
| `test_worker_keyboard_structure` | 底部键盘一行两列 | 通过 |
| `test_worker_keyboard_button_text` | 左命令管理、右 PLAN MODE，任务列表不在底部 | 通过 |
| `test_worker_main_keyboard_probes_visible_plan_mode_button` | PLAN MODE 可见时刷新状态 | 通过 |
| `test_worker_main_keyboard_uses_cached_plan_mode_when_refresh_disabled` | refresh=False 使用缓存，不探测 tmux | 通过 |
| `test_worker_main_keyboard_uses_explicit_plan_mode_state` | 显式状态渲染并写入缓存 | 通过 |
| `test_worker_main_keyboard_uses_copilot_mode_button` | Copilot 模型展示 MODE 三态 | 通过 |
| `test_worker_plan_mode_button_toggles_and_refreshes_keyboard` | 切换后底部键盘展示最新 PLAN MODE 状态 | 通过 |
| `test_worker_copilot_mode_button_toggles_and_refreshes_keyboard` | Copilot 切换后展示最新 MODE 状态 | 通过 |

## 8. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| 任务列表不再在底部 | 用户需通过命令菜单或原文本入口进入任务列表 | 只移出底部，不删除 handler 与命令入口。 |
| 渲染键盘时探测 tmux | 某些响应可能略慢 | 支持 `refresh_plan_mode_state=False` 使用缓存；异常探测返回 unknown。 |
| Copilot 文案不是 PLAN MODE | Copilot 模型实际是三态 MODE | 沿用既有 `MODE:` 文案，避免错误表达。 |

回滚方式：还原 `bot.py::_build_worker_main_keyboard` 为 `[任务列表, 命令管理]`，删除新增 helper 与测试断言更新；无数据库回滚。

## 9. 当前状态

- [x] 完成 TDD 红灯验证。
- [x] 完成最小实现。
- [x] 已更新 AGENTS 证据。
- [ ] 待最终聚焦验证与 `vibego_cli doctor` 结果补充。

---

## 10. 最终验证记录

### 10.1 聚焦测试

```bash
python3.11 -m pytest -q tests/test_chat_menu_buttons.py
# 51 passed

python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_parallel_session_routing.py tests/test_request_user_input_flow.py tests/test_task_description.py -k 'worker_keyboard or worker_main_keyboard or plan_mode_button or copilot_mode_button or restores_main_keyboard or custom_text_auto_submits or custom_text_submit_failure or push_model_success or push_model_test_push'
# 17 passed, 275 deselected
```

### 10.2 基础诊断与语法校验

```bash
python3.11 -m vibego_cli doctor
# python_ok=true, dependencies=[]

python3.11 -m py_compile bot.py
# exit code 0
```

### 10.3 全量 pytest

```bash
python3.11 -m pytest -q
# 3 failed, 953 passed, 6 warnings
```

全量失败项仍是既有 AGENTS 模板迁移测试，和本次底部键盘契约无直接交集：

- `tests/test_agents_template_migration.py::test_enforced_notice_points_to_agents_md`
- `tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt`
- `tests/test_agents_template_migration.py::test_agents_template_requires_comet_for_complex_workflows`

本次已将因底部键盘契约变更导致的旧断言同步完成；全量中不再出现 `WORKER_MENU_BUTTON_TEXT` 底部键盘相关失败。

## 11. 完成状态 Checklist

- [x] 底部常驻键盘左侧改为 `📟 命令管理`。
- [x] Codex/非 Copilot 右侧展示 `🧭 PLAN MODE: ON/OFF/?`。
- [x] Copilot 右侧保留 `🧭 MODE: INTERACTIVE/PLAN/AUTOPILOT/?`。
- [x] 任务列表不再占用底部键盘。
- [x] PLAN/MODE 按钮点击后复用既有切换链路并刷新底部状态。
- [x] 主菜单恢复场景测试已同步新底部键盘契约。
- [x] 未改数据库、未新增依赖、未改构建链/CI。
- [ ] 全量 pytest 未全绿：仅剩既有 AGENTS 模板迁移测试失败，已记录。
