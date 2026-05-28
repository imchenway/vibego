# TASK_20260528_001 Telegram计划确认三选项一致性修复

## 1. 背景与现象

- 用户反馈：终端里 Codex 原生 `Implement this plan?` 有三项选择，但 Telegram 里只看到两项，导致通过 Telegram 操作时无法选择 `Yes, clear context and implement`。
- 附件截图证据：终端三项分别为：
  1. `Yes, implement this plan`：继续当前线程进入实现；
  2. `Yes, clear context and implement`：fresh thread，清空上下文后携带计划实现；
  3. `No, stay in Plan mode`：继续计划模式。
- 代码现状证据：`bot.py`（锚点：`_build_plan_confirm_keyboard`、`_maybe_send_plan_confirm_prompt`）原先只构造 Yes/No 两个按钮；`bot.py`（锚点：`on_plan_confirm_callback`）原先只识别 `PLAN_CONFIRM_ACTION_YES` 和 `PLAN_CONFIRM_ACTION_NO`。

## 2. 根因

1. Telegram PlanConfirm 是 vibego 自建桥接 UI，不是 Codex 原生 UI 自动映射。
2. 原实现只保存 `session_key` 和并行上下文，没有保存模型刚输出的计划正文，因此无法安全启动 fresh context 后再把计划带入新线程。
3. 如果 fresh context 缺少计划正文却仍派发 `Implement the plan.`，新线程没有上文，会变成高风险误执行。因此必须 fail-closed。

## 3. 已确认方案

用户通过 Telegram 按钮选择 `完整三项一致 (Recommended)`，本次按完整一致方案一次性落地：

- Codex 模型下 Telegram PlanConfirm 展示三项：
  - `✅ Yes, implement this plan`
  - `🧹 Yes, clear context and implement`
  - `📝 No, stay in Plan mode`
- `Yes, implement this plan` 保持原行为：向当前会话发送 `Implement the plan.`，并继续使用退出 Plan UI 的按键序列。
- `Yes, clear context and implement` 新增行为：
  - 使用上一轮 `<proposed_plan>` 输出正文构造 fresh context prompt；
  - 主会话：调用 `scripts/start_tmux_codex.sh --kill`，不设置 `MODEL_RESUME_SESSION_ID`，启动全新 Codex 会话；
  - 并行会话：重启对应并行 tmux/pointer，不回落主会话；
  - fresh 派发强制 `allow_session_discovery_fallback=False`，继续依赖 worker marker 同源校验；
  - 缺计划正文、非 Codex 模型、并行任务失效、重启失败均 fail-closed。
- 非 Codex 模型暂保持原两项，避免伪造未证实的 fresh context 能力。

## 4. 受影响目录与文件

| 范围 | 文件 | 影响说明 | 证据锚点 |
|---|---|---|---|
| Worker 运行逻辑 | `bot.py` | PlanConfirm 会话保存计划正文；新增 fresh action；三选项 keyboard；fresh 主/并行重启与派发 | `PlanConfirmSession`、`PLAN_CONFIRM_ACTION_FRESH`、`_build_plan_confirm_keyboard`、`_build_plan_fresh_context_prompt`、`_restart_main_tmux_fresh_session`、`_restart_parallel_tmux_fresh_session`、`on_plan_confirm_callback` |
| 测试资产 | `tests/test_plan_confirm_bridge.py` | 覆盖计划正文传递、三选项展示、主会话 fresh、缺正文 fail-closed、并行 fresh | `test_deliver_pending_messages_triggers_plan_confirm`、`test_maybe_send_plan_confirm_prompt_uses_codex_three_option_keyboard`、`test_plan_confirm_fresh_context_restarts_main_tmux_and_dispatches_plan`、`test_plan_confirm_fresh_context_missing_plan_fails_closed`、`test_parallel_plan_confirm_fresh_context_restarts_bound_parallel_tmux` |
| 任务文档 | `docs/TASK_20260528_001_Telegram计划确认三选项一致性修复.md` | 记录设计、契约、测试矩阵和回滚 | 本文 |
| 协作证据 | `AGENTS.md` | 增补 Telegram PlanConfirm 三选项约束 | `Telegram PlanConfirm 三选项约束` |

## 5. 契约变更

### 5.1 Telegram 用户交互契约

- Codex 模型收到 `<proposed_plan>...</proposed_plan>` 最终回复后，Telegram 侧必须展示三项选择，选项含义与终端一致。
- `Yes, clear context and implement` 必须是新的 Codex 线程，不得继续复用旧 JSONL 会话。
- 缺少计划正文时必须提示失败并保留原确认会话，禁止盲派。

### 5.2 会话绑定与安全契约

- fresh 派发必须设置 `allow_session_discovery_fallback=False`，让 `_dispatch_prompt_to_model` 使用 worker marker 校验新 JSONL，避免串到 Codex App 或旧线程。
- 并行 PlanConfirm 必须沿用 `ParallelDispatchContext` 路由，fresh 重启只作用于对应并行 tmux，不允许回落主会话。

### 5.3 数据库/API/配置契约

- 数据库：不涉及 SQLite 表结构、索引、迁移。
- HTTP API：本仓库无 REST Controller 证据，本次不涉及。
- 配置：不新增环境变量；复用既有 `MODEL_WORKDIR`、`CODEX_SESSION_FILE_PATH`、`TMUX_SESSION`、并行 pointer/ready 文件机制。
- 依赖/构建/CI：不新增依赖，不改构建链，不改 CI。

## 6. 实施顺序

1. 基线验证：先运行 PlanConfirm/Plan UI 相关聚焦测试，确认现有行为绿灯。
2. TDD 红灯：新增三选项、计划正文传递、fresh 主/并行、缺正文 fail-closed 测试，并确认失败。
3. 最小实现：只修改 `bot.py` 中 PlanConfirm 与 fresh session 相关逻辑。
4. 回归验证：执行聚焦测试和 `vibego_cli doctor`。
5. 文档/证据：更新本文与 `AGENTS.md`。

## 7. 测试矩阵

| 用例 | 覆盖点 | 结果 |
|---|---|---|
| `test_deliver_pending_messages_triggers_plan_confirm` | `<proposed_plan>` 触发确认时把计划正文传给 PlanConfirm | 通过 |
| `test_maybe_send_plan_confirm_prompt_uses_codex_three_option_keyboard` | Codex Telegram keyboard 三项一致，session 保存 `plan_text` | 通过 |
| `test_plan_confirm_yes_dispatches_implement_prompt` | 原 Yes 行为兼容，不增加 fresh 参数 | 通过 |
| `test_plan_confirm_fresh_context_restarts_main_tmux_and_dispatches_plan` | 主会话 fresh 重启、清旧绑定、携带计划正文、禁用 fallback | 通过 |
| `test_plan_confirm_fresh_context_missing_plan_fails_closed` | 缺计划正文 fail-closed，不重启不派发 | 通过 |
| `test_parallel_plan_confirm_fresh_context_restarts_bound_parallel_tmux` | 并行 fresh 只重启对应并行 tmux，携带 dispatch context | 通过 |
| `test_parallel_plan_confirm_yes_dispatches_bound_parallel_context` | 原并行 Yes 行为兼容 | 通过 |
| `test_parallel_plan_confirm_yes_fails_closed_when_context_stale` | 并行 stale fail-closed 兼容 | 通过 |
| `tests/test_task_description.py -k dispatch_prompt_force_exit_plan_ui` | 原 Plan UI 退出按键链路兼容 | 通过 |

## 8. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| 主会话 fresh 重启失败 | 用户无法进入 fresh context | fail-closed，Telegram 展示失败原因，不删除确认会话 |
| 并行任务已关闭或被删除 | 无法定位并行 tmux/pointer | fail-closed，不回落主会话 |
| 新线程未生成 worker marker 会话 | 可能串会话 | `allow_session_discovery_fallback=False` 强制拒绝无 marker 会话 |
| 非 Codex 模型能力不一致 | UI 误导用户 | 非 Codex 保持两项，不展示 fresh |

回滚方式：还原 `bot.py` 中 PlanConfirm fresh action、计划正文保存和 fresh 重启 helper；还原 `tests/test_plan_confirm_bridge.py` 新增用例与 `AGENTS.md` 新证据行。本次无数据库迁移，无需数据回滚。

## 9. 当前验证记录

- `python3.11 -m pytest -q tests/test_plan_confirm_bridge.py -k 'three_option or fresh_context or deliver_pending_messages_triggers_plan_confirm'`：先红后绿，最终 5 passed。
- `python3.11 -m pytest -q tests/test_plan_confirm_bridge.py`：16 passed。
- `python3.11 -m pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py -k 'plan_confirm or dispatch_prompt_force_exit_plan_ui or extract_terminal_collaboration_mode'`：27 passed, 181 deselected。

> 后续最终验证命令会在任务收尾处继续补充。
- `python3.11 -m pytest -q tests/test_plan_confirm_bridge.py tests/test_request_user_input_flow.py tests/test_task_description.py -k 'plan_confirm or request_input or dispatch_prompt_force_exit_plan_ui or extract_terminal_collaboration_mode'`：50 passed, 183 deselected。
- `python3.11 -m vibego_cli doctor`：通过，`python_ok=true`，关键运行配置存在。

## 10. 完成状态 Checklist

- [x] Telegram Codex PlanConfirm 三选项展示一致。
- [x] PlanConfirm session 保存计划正文，fresh context 不丢需求上下文。
- [x] 主会话 fresh context 重启新 Codex 线程，不 resume 旧会话。
- [x] 并行会话 fresh context 只重启对应并行 tmux，不回落主会话。
- [x] 缺计划正文/并行上下文失效/非 Codex fresh 能力未证实时 fail-closed。
- [x] 原 Yes/No、并行 Yes、旧按钮兼容测试保持通过。
- [x] 未新增依赖、未改数据库、未改构建链/CI。
