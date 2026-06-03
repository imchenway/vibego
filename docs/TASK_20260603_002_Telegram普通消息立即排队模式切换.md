# TASK_20260603_002 Telegram 普通消息立即/排队模式切换（DEVELOP）

## 1. 需求与结论

用户确认：底部同一排新增发送消息模式按钮，但只支持两种普通消息发送方式：`立即` / `排队`，不做 `引导`。

结论：Worker 底部常驻键盘从一行两列扩展为一行三列：

1. `📟 命令管理`
2. `🧭 PLAN: ...`（Copilot 为 `🧭 MODE: ...`）
3. `✉️ 立即` / `✉️ 排队`

第三个按钮只影响普通 Telegram 直聊入口；任务推送、批量推送仍沿用其已有的发送方式选择流程。

## 2. 现状证据

| 事实 | 证据 |
| --- | --- |
| Worker 底部键盘由 `_build_worker_main_keyboard` 生成 | `bot.py`（锚点：`_build_worker_main_keyboard`） |
| 普通 Telegram 直聊统一进入 `_handle_prompt_dispatch`，再调用 `_dispatch_prompt_to_model` | `bot.py`（锚点：`_handle_prompt_dispatch`、`_dispatch_prompt_to_model`） |
| 底层已经支持 `immediate` / `queued` 两种发送方式 | `bot.py`（锚点：`PUSH_SEND_MODE_IMMEDIATE`、`PUSH_SEND_MODE_QUEUED`、`_normalize_push_send_mode`、`tmux_send_line`、`tmux_queue_line`） |
| 仅 Codex/Copilot 支持排队发送 | `bot.py`（锚点：`_supports_queued_send_mode`） |
| Codex CLI 没有独立“引导发送”模式，本轮只做立即/排队 | 本轮调研结论；本任务实现不新增 guided 状态。 |

## 3. 方案对比

### 方案 A：同排第三按钮切换普通直聊发送方式（本次采用）

- 做法：在底部一行增加 `✉️ 立即/排队`，点击后在 worker 运行期持久化普通直聊发送方式。
- 优点：符合“同一排加一个按钮”；无需新增命令菜单层级；用户发下一条消息前能直接看到当前方式。
- 缺点：Telegram 小屏幕上三列会比两列更拥挤，但按钮文案短，可接受。

### 方案 B：使用 `/send_mode` 命令切换

- 优点：底部不拥挤。
- 缺点：不符合“同一排加一个按钮”，切换路径更长。

### 方案 C：加入立即/排队/引导三态

- 优点：和最初三模式描述一致。
- 缺点：Codex CLI 只确认支持立即与排队，没有可稳定映射的独立引导发送动作；做三态会制造假状态。本次已被用户否决。

## 4. 受影响目录与边界

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `bot.py` | 是 | 新增普通直聊发送方式状态、底部按钮、按钮 handler，并在 `_handle_prompt_dispatch` 透传 `send_mode`。 |
| `tests/test_chat_menu_buttons.py` | 是 | 覆盖一行三列、按钮文案、切换到排队、非支持模型拒绝排队。 |
| `tests/test_task_description.py` | 是 | 覆盖普通文本直聊读取 worker 发送方式并透传 `queued`。 |
| `docs/` | 是 | 新增本任务文档，记录契约、测试矩阵、风险与回滚。 |
| `AGENTS.md` | 是 | 更新 Worker 主菜单按钮约束事实，避免后续按旧两列契约实现。 |
| 数据库 | 否 | 发送方式是运行期 UI 状态，写入状态文件；不新增 SQLite 表/字段。 |
| Telegram Bot API 契约 | 否 | 只改变 reply keyboard 内容，不新增外部 API。 |
| 任务推送/批量推送 | 否 | 已有任务推送链路仍用原本显式发送方式选择。 |

## 5. 契约变更

1. Worker 底部常驻键盘必须是一行三列：`命令管理`、`PLAN/MODE`、`发送方式`；其中 Codex/非 Copilot 的可见文案使用 `PLAN`，不再显示 `PLAN MODE`。
2. 发送方式按钮文案：
   - `✉️ 立即`：普通直聊使用 `send_mode=immediate`。
   - `✉️ 排队`：普通直聊使用 `send_mode=queued`。
3. 点击 `✉️ 立即`：
   - Codex/Copilot：切换为排队发送，并刷新按钮为 `✉️ 排队`。
   - 非 Codex/Copilot：提示“不支持排队发送”，保持 `✉️ 立即`。
4. 点击 `✉️ 排队`：切回立即发送。
5. 状态持久化到运行期状态文件；状态文件异常时回退立即发送，不阻断消息投递。
6. 普通直聊聚合、图文聚合最终仍通过 `_handle_prompt_dispatch`，因此统一读取当前发送方式。

## 6. 测试矩阵

| 用例 | 覆盖点 | 状态 |
| --- | --- | --- |
| `test_worker_keyboard_structure` | 底部键盘一行三列 | 通过 |
| `test_worker_keyboard_button_text` | 第三列默认 `✉️ 立即` | 通过 |
| `test_worker_main_keyboard_probes_visible_plan_mode_button` | PLAN 状态刷新与三列顺序，且按钮不再显示 `MODE` | 通过 |
| `test_worker_direct_send_mode_button_toggles_to_queued` | Codex 下立即切排队并刷新按钮 | 通过 |
| `test_worker_direct_send_mode_button_rejects_queued_when_model_unsupported` | Gemini 等不支持模型拒绝排队 | 通过 |
| `test_on_text_direct_prompt_uses_worker_queued_send_mode` | 普通直聊透传 `send_mode=queued` | 通过 |
| `tests/test_chat_menu_buttons.py` | 主菜单按钮回归集合 | 通过 |
| `tests/test_task_description.py` | 普通消息与任务描述相关回归集合 | 通过 |

## 7. 实施顺序

1. 基线验证：先跑受影响筛选测试，确认旧契约测试通过。
2. 红灯测试：先把底部键盘和普通直聊发送方式测试改为新契约，确认失败来自未实现功能。
3. 最小实现：
   - 新增发送方式状态读取/持久化 helper。
   - `_build_worker_main_keyboard` 增加第三按钮。
   - 新增 `on_worker_direct_send_mode_button`。
   - `_handle_prompt_dispatch` 透传当前 `send_mode`。
4. 聚焦验证：跑受影响 pytest 集合。
5. 文档与证据：更新 `docs/` 与 `AGENTS.md`。

## 8. 风险与回滚

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| Telegram 小屏三列挤压 | 移动端按钮略窄 | 文案压缩为 `✉️ 立即/排队`。 |
| 非支持模型误切 queued | Gemini/ClaudeCode 可能无法排队发送 | `_supports_queued_send_mode` fail-closed，非支持模型保持立即。 |
| 状态文件损坏 | 按钮状态读取失败 | 解析失败回退立即发送并记录 warning。 |
| 普通直聊 queued 导致用户以为任务推送也切换 | 认知混淆 | 契约明确：只影响普通 Telegram 直聊，任务推送仍按原流程。 |

回滚方式：还原 `bot.py` 中发送方式 helper、第三按钮、按钮 handler 与 `_handle_prompt_dispatch` 的 `send_mode` 透传；还原对应测试断言；删除运行期 `*_direct_send_mode.json` 状态文件即可，无数据库回滚。

## 9. 验证记录

- Baseline：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py -k 'worker_keyboard or worker_main_keyboard or plan_mode_button or copilot_mode_button or direct_prompt'` -> `11 passed, 244 deselected`。
- Red：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py -k 'worker_keyboard or worker_main_keyboard or direct_send_mode or direct_prompt'` -> `6 failed, 5 passed, 247 deselected`，失败来自键盘仍两列、handler 缺失、普通直聊未透传 queued。
- Green：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py -k 'worker_keyboard or worker_main_keyboard or direct_send_mode or direct_prompt'` -> `11 passed, 247 deselected`。
- 聚焦扩展：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py -k 'worker_keyboard or worker_main_keyboard or plan_mode_button or copilot_mode_button or direct_send_mode or direct_prompt or handle_prompt_dispatch'` -> `16 passed, 242 deselected`。
- `python3.11 -m pytest -q tests/test_chat_menu_buttons.py` -> `53 passed`。
- `python3.11 -m pytest -q tests/test_task_description.py` -> `205 passed`。
- 聚焦三文件双轮第 1 次：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py tests/test_tmux_send_line.py` -> `274 passed`。
- 聚焦三文件双轮第 2 次：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py tests/test_tmux_send_line.py` -> `274 passed`。
- 语法检查：`python3.11 -m py_compile bot.py` -> 通过。
- 运行诊断：`python3.11 -m vibego_cli doctor` -> 通过，`python_ok=true`，依赖缺失列表为空。
- 全量 pytest：`python3.11 -m pytest -q` -> `956 passed, 3 failed, 6 warnings`。失败项为 `tests/test_agents_template_migration.py` 中 AGENTS 模板/强制规约文案既有不一致：`test_enforced_notice_points_to_agents_md`、`test_enforced_notice_adds_user_requirement_header_before_prompt`、`test_agents_template_requires_comet_for_complex_workflows`；与本次底部键盘/普通直聊发送方式改动无直接交集。

## 10. 2026-06-03 PLAN 按钮文案收敛

用户追加确认：`PLAN MODE` 的 `MODE` 文案去掉。

本次收敛：

1. Codex/非 Copilot 的底部模式按钮从 `🧭 PLAN MODE: ON/OFF/?` 改为 `🧭 PLAN: ON/OFF/?`。
2. 切换后的 Telegram 回执从 `当前 PLAN MODE：...` 改为 `当前 PLAN：...`。
3. Copilot 的 `🧭 MODE: ...` 不变，因为 Copilot 不是 PLAN 二态按钮，而是 `INTERACTIVE/PLAN/AUTOPILOT` 三态模式按钮。
4. handler 临时兼容旧按钮文本 `🧭 PLAN MODE:`，避免重启前 Telegram 客户端保留旧键盘时点击无响应；新渲染键盘只展示 `🧭 PLAN:`。

### 10.1 验证记录

- Red：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py -k 'worker_keyboard or worker_main_keyboard or plan_mode_button or direct_send_mode'` -> `6 failed, 5 passed, 42 deselected`，失败点均为旧文案 `PLAN MODE` 仍存在。
- Green：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py -k 'worker_keyboard or worker_main_keyboard or plan_mode_button or direct_send_mode'` -> `11 passed, 42 deselected`。
- 聚焦完整按钮测试：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py` -> `53 passed`。
- 聚焦三文件回归：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py tests/test_tmux_send_line.py` -> `274 passed`。
- 语法检查：`python3.11 -m py_compile bot.py` -> 通过。
- 运行诊断：`python3.11 -m vibego_cli doctor` -> 通过，`python_ok=true`，依赖缺失列表为空。
- 全量 pytest：`python3.11 -m pytest -q` -> `956 passed, 3 failed, 6 warnings`。失败项仍为既有 `tests/test_agents_template_migration.py` 中 AGENTS 模板/强制规约文案不一致：`test_enforced_notice_points_to_agents_md`、`test_enforced_notice_adds_user_requirement_header_before_prompt`、`test_agents_template_requires_comet_for_complex_workflows`；与本次 PLAN 按钮文案收敛无直接交集。


## 11. 2026-06-03 Telegram 回复时刷新 PLAN 状态

用户追加确认：每次向 Telegram 回复消息时，都更新底部键盘里 PLAN 的 `ON/OFF/?` 值。

本次收敛：

1. 所有“恢复/展示 Worker 主键盘”的回复默认调用 `_build_worker_main_keyboard()`，重新探测当前终端 PLAN 状态。
2. 不再在普通回复场景使用 `refresh_plan_mode_state=False` 读取旧缓存，避免用户看到过期的 `🧭 PLAN: ON/OFF/?`。
3. 仅保留 PLAN/Copilot MODE 切换流程中的显式状态复用：切换流程已经拿到 `after_state` 或错误态 `unknown`，此时不重复探测，直接用已知状态渲染。
4. InlineKeyboard 场景不强行替换为 Worker 主键盘，避免破坏任务列表、Goal 面板、命令详情等交互按钮；这些交互结束并恢复主键盘时会刷新 PLAN 状态。

### 11.1 验证记录

- Red：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py -k 'goal_not_supported_reply_refreshes_plan_state or worker_goal_button_fails_closed'` -> `1 failed, 1 passed, 52 deselected`，失败点为 `_answer_goal_not_supported` 使用旧缓存且未探测 PLAN 状态。
- Green：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py -k 'goal_not_supported_reply_refreshes_plan_state or worker_goal_button_fails_closed'` -> `2 passed, 52 deselected`。
- 聚焦完整按钮测试：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py` -> `54 passed`。
- 聚焦三文件回归：`python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py tests/test_tmux_send_line.py` -> `275 passed`。
- 语法检查：`python3.11 -m py_compile bot.py` -> 通过。
- 运行诊断：`python3.11 -m vibego_cli doctor` -> 通过，`python_ok=true`，依赖缺失列表为空。
- 全量 pytest：`python3.11 -m pytest -q` -> `957 passed, 3 failed, 6 warnings`。失败项仍为既有 `tests/test_agents_template_migration.py` 中 AGENTS 模板/强制规约文案不一致：`test_enforced_notice_points_to_agents_md`、`test_enforced_notice_adds_user_requirement_header_before_prompt`、`test_agents_template_requires_comet_for_complex_workflows`；与本次主键盘回复刷新 PLAN 状态无直接交集。

## 12. 2026-06-03 后续变更提示：普通直聊默认排队并移除发送方式按钮

后续 `TASK_20260603_005_普通消息默认排队并移除发送方式按钮.md` 已按用户确认覆盖本文件的第三列按钮历史契约：

1. Worker 底部主键盘从一行三列回到一行两列：`📟 命令管理` + `🧭 PLAN: ...` / `🧭 MODE: ...`。
2. `✉️ 立即/排队` 不再展示；旧 Telegram 客户端残留按钮只提示按钮已移除并刷新主键盘。
3. 普通直聊发送方式不再读写 `*_direct_send_mode.json`，Codex/Copilot 默认 `queued`，不支持 queued 的模型自动回退 `immediate`。
4. 本文件中任务推送“立即发送/排队发送”的显式选择仍有效；后续实现和排障以 `TASK_20260603_005` 与 `AGENTS.md` 最新 Facts Table 为准。
