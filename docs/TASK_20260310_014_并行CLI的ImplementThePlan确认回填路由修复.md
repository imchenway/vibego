# TASK_20260310_014 并行 CLI 的 Implement the plan 确认回填路由修复

## 背景
- 现象：并行 CLI 发到 Telegram 的 `Implement the plan?`，点击 `Yes` 后被错误发到原生 CLI。
- 目标：`Yes` 必须继续发回对应并行 CLI；并行上下文失效时 fail-closed。

## 证据
- `bot.py`：`PlanConfirmSession` 仅保存 `token/chat_id/session_key/user_id/created_at`，缺少并行上下文。
- `bot.py`：`on_plan_confirm_callback` 的 `Yes` 分支调用 `_dispatch_prompt_to_model(...)` 时未透传 `dispatch_context`。
- `tests/test_plan_confirm_bridge.py`：新增并行 `Yes` 回填与 stale fail-closed 回归测试。

## 方案
1. 扩展 `PlanConfirmSession`，保存 `parallel_task_id` 与 `parallel_dispatch_context`。
2. 在 `_maybe_send_plan_confirm_prompt(...)` 创建确认会话时，按 `session_key` 回溯并行上下文并写入会话。
3. 在 `on_plan_confirm_callback(...)` 的 `Yes` 分支中优先使用并行 `dispatch_context`。
4. 若这是并行确认但上下文已失效，则直接提示用户回到最新并行消息重试，不允许回落到原生 CLI。

## 验证
- Baseline：`/opt/homebrew/bin/python3.11 -m pytest -q tests/test_plan_confirm_bridge.py`
- 红灯：新增并行回填测试后首轮失败（并行上下文未透传、stale 未 fail-closed）。
- 绿灯：修复后同一测试文件通过，并进行双轮一致性验证。
