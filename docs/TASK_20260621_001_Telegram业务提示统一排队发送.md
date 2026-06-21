# TASK_20260621_001 Telegram 业务提示统一排队发送（DEVELOP）

## 1. 需求与确认结论

用户反馈：“发出去的消息都应该是排队消息”，现状只把普通直聊收口到默认 `queued`，任务推送、快捷回复、`request_user_input` 回填、PlanConfirm Implement 等业务 prompt 仍有部分路径使用 `immediate`。

本次 Telegram 按钮确认结果：

1. **排队范围**：采用“业务提示全排队”。
   - 普通直聊、任务推送、快捷回复、`request_user_input`、PlanConfirm Implement、任务摘要生成等用户/业务 prompt 统一优先 `queued`。
   - `/plan`、`/goal`、`/compact`、退出菜单、fresh context 原生按键等控制命令保持 immediate 或原生按键语义。
2. **不支持模型**：Gemini / ClaudeCode 等当前不支持 queued 的模型继续回退 `immediate`。

目标不是把所有 tmux 输入都改成 queued，而是按第一性原理区分：

- **业务消息**：用户期望模型在当前工作之后继续处理，避免打断当前执行，因此排队。
- **控制命令**：用户期望立即改变 CLI 状态或驱动原生菜单，因此不排队。

## 2. 当前实现证据

| 事实 | 证据 |
| --- | --- |
| queued 能力由模型类型决定 | `bot.py`（锚点：`_supports_queued_send_mode`） |
| 普通直聊入口在 `_handle_prompt_dispatch` | `bot.py`（锚点：`_handle_prompt_dispatch`） |
| 任务推送最终走 `_push_task_to_model` / `_dispatch_prompt_to_model` | `bot.py`（锚点：`_push_task_to_model`、`_dispatch_prompt_to_model`） |
| `request_user_input` 回填通过 `_submit_request_input_session` 回推 tmux | `bot.py`（锚点：`_submit_request_input_session`） |
| PlanConfirm Implement 通过 `on_plan_confirm_callback` / `on_plan_develop_retry_callback` 投递实现 prompt | `bot.py`（锚点：`on_plan_confirm_callback`、`on_plan_develop_retry_callback`） |
| 快捷回复通过 `on_model_quick_reply_all` / `on_model_quick_reply_partial_supplement` 投递 | `bot.py`（锚点：`on_model_quick_reply_all`、`on_model_quick_reply_partial_supplement`） |
| 旧普通消息默认 queued 只覆盖普通直聊 | `docs/TASK_20260603_005_普通消息默认排队并移除发送方式按钮.md`（锚点：`目标：移除 Worker 底部`、`任务推送、批量推送、PlanConfirm、并行任务等非普通直聊入口保持原显式发送方式选择`） |

## 3. 方案选择

### 方案 A：新增统一业务提示发送方式解析器（本次采用）

新增 `_resolve_business_prompt_send_mode()`：

- 支持 queued 的模型返回 `queued`。
- 不支持 queued 的模型返回 `immediate`。
- 所有 Telegram 业务 prompt 入口调用该函数。
- 控制命令不调用该函数，保持原逻辑。

优点：单点表达“业务提示默认排队”的产品契约，后续新增业务入口更容易复用；兼容不支持模型。  
缺点：需要逐个梳理业务入口，测试矩阵较大。

### 方案 B：在 `_dispatch_prompt_to_model` 内全局默认 queued

优点：改动点最少。  
缺点：会误伤 `/plan`、fresh context、退出菜单、原生按键驱动等控制输入，可能导致 CLI 状态变更延迟或失效，因此不采用。

### 方案 C：保留任务推送发送方式选择，只改部分入口

优点：改动较小。  
缺点：仍会出现“发出去的消息不是排队”的例外，与用户确认的“业务提示全排队”不一致，因此不采用。

## 4. 受影响目录与边界

| 范围 | 是否影响 | 说明 |
| --- | ---: | --- |
| `bot.py` | 是 | 新增业务提示发送方式解析器；任务推送、普通直聊、快捷回复、`request_user_input`、PlanConfirm Implement、任务摘要生成统一使用该解析器。 |
| `tests/test_task_description.py` | 是 | 覆盖任务推送、快捷回复、任务摘要等业务入口的 `send_mode=queued`。 |
| `tests/test_request_user_input_flow.py` | 是 | 覆盖 request_user_input 回填与并行上下文回填均使用 queued。 |
| `tests/test_plan_confirm_bridge.py` | 是 | 覆盖 PlanConfirm Implement 与回调 ack 路径使用 queued。 |
| `tests/test_chat_menu_buttons.py` | 是 | 更新旧发送方式按钮兼容提示文案。 |
| `AGENTS.md` | 是 | 增加 Telegram 业务提示统一排队事实与证据。 |
| `docs/` | 是 | 新增本任务文档，并给历史普通消息默认排队文档追加后续变更提示。 |
| SQLite/DB | 否 | 不新增表字段，不改迁移。 |
| Telegram 命令菜单 | 否 | `/plan`、`/goal`、`/compact` 等控制命令不改变。 |
| 构建/依赖/CI | 否 | 不新增依赖，不改构建链，不改 CI。 |

## 5. 契约变更

1. Telegram 业务 prompt 统一通过 `_resolve_business_prompt_send_mode()` 决策发送方式。
2. Codex/Copilot 等支持 queued 的模型：
   - 普通直聊：`send_mode=queued`。
   - 任务推送：即使旧键盘残留传入 immediate，也按 `queued`。
   - 快捷回复：`send_mode=queued`。
   - `request_user_input` 回填：`send_mode=queued`。
   - PlanConfirm Implement / retry：`send_mode=queued`。
   - 任务摘要生成：`send_mode=queued`。
3. 不支持 queued 的模型继续回退 `immediate`，避免发送不可用队列指令。
4. 控制命令保持原契约：
   - `/plan`、`/goal`、`/compact` 不因本任务改成 queued。
   - fresh context 仍驱动 Codex TUI 原生菜单，不重启 CLI、不排队文本 prompt。
   - 退出 Plan UI 的按键序列仍按原生 immediate/按键语义执行。
5. 旧任务推送“发送方式”键盘只作为兼容入口，文案提示“业务提示默认排队”，点击旧按钮继续执行但不改变真实业务发送方式。

## 6. TDD 实施记录

### 6.1 Baseline（修改前）

```bash
python3.11 -m pytest -q tests/test_task_description.py tests/test_tmux_send_line.py tests/test_plan_confirm_bridge.py tests/test_request_user_input_flow.py
# 268 passed
```

### 6.2 RED（测试先行）

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_push_model_success \
  tests/test_task_description.py::test_push_model_skip_keeps_selected_push_mode \
  tests/test_task_description.py::test_push_model_test_push \
  tests/test_task_description.py::test_push_model_choice_for_copilot_dispatches_queued_without_send_mode_prompt \
  tests/test_task_description.py::test_task_summary_command_triggers_request \
  tests/test_task_description.py::test_model_quick_reply_all_dispatches_as_queued_business_prompt \
  tests/test_task_description.py::test_model_quick_reply_partial_dispatches_as_queued_business_prompt \
  tests/test_request_user_input_flow.py::test_request_input_submit_dispatches_structured_payload \
  tests/test_request_user_input_flow.py::test_request_input_submit_dispatches_parallel_context \
  tests/test_plan_confirm_bridge.py::test_plan_confirm_yes_dispatches_implement_prompt \
  tests/test_plan_confirm_bridge.py::test_plan_confirm_yes_acknowledges_callback_before_dispatch
# 11 failed
```

红灯原因符合预期：旧实现中任务推送、快捷回复、`request_user_input`、PlanConfirm Implement 等路径仍使用 immediate 或未显式传入业务发送方式。

### 6.3 GREEN（最小实现后）

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_push_model_success \
  tests/test_task_description.py::test_push_model_skip_keeps_selected_push_mode \
  tests/test_task_description.py::test_push_model_test_push \
  tests/test_task_description.py::test_push_model_choice_for_copilot_dispatches_queued_without_send_mode_prompt \
  tests/test_task_description.py::test_task_summary_command_triggers_request \
  tests/test_task_description.py::test_model_quick_reply_all_dispatches_as_queued_business_prompt \
  tests/test_task_description.py::test_model_quick_reply_partial_dispatches_as_queued_business_prompt \
  tests/test_request_user_input_flow.py::test_request_input_submit_dispatches_structured_payload \
  tests/test_request_user_input_flow.py::test_request_input_submit_dispatches_parallel_context \
  tests/test_plan_confirm_bridge.py::test_plan_confirm_yes_dispatches_implement_prompt \
  tests/test_plan_confirm_bridge.py::test_plan_confirm_yes_acknowledges_callback_before_dispatch
# 11 passed, 2 warnings
```

## 7. 测试矩阵

| 用例 | 预期 | 覆盖测试 |
| --- | --- | --- |
| 普通直聊 | Codex/Copilot 默认 queued；不支持模型回退 immediate | `test_on_text_direct_prompt_uses_default_queued_send_mode`、`test_on_text_direct_prompt_falls_back_to_immediate_when_queue_unsupported` |
| 任务推送补充说明 | 跳过旧发送方式选择，直接 queued | `test_push_model_success` |
| 任务推送无补充 | 点击跳过后直接 queued | `test_push_model_skip_keeps_selected_push_mode` |
| 任务推送测试按钮 | 测试任务推送也 queued | `test_push_model_test_push` |
| Copilot 任务推送 | 不再弹发送方式选择，直接 queued | `test_push_model_choice_for_copilot_dispatches_queued_without_send_mode_prompt` |
| 快捷回复全部/部分 | 两类快捷回复均 queued | `test_model_quick_reply_all_dispatches_as_queued_business_prompt`、`test_model_quick_reply_partial_dispatches_as_queued_business_prompt` |
| request_user_input 回填 | 普通与并行上下文回填均 queued，question_context 不变 | `test_request_input_submit_dispatches_structured_payload`、`test_request_input_submit_dispatches_parallel_context` |
| PlanConfirm Implement | 当前线程实现 prompt queued；回调先 ack 不变 | `test_plan_confirm_yes_dispatches_implement_prompt`、`test_plan_confirm_yes_acknowledges_callback_before_dispatch` |
| 任务摘要生成 | 摘要生成 prompt queued | `test_task_summary_command_triggers_request` |
| 旧按钮兼容 | 文案明确业务提示默认排队，控制命令保持 immediate | `test_worker_direct_send_mode_button_removed_refreshes_keyboard` |

## 8. 风险与回滚

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 业务入口遗漏 | 仍可能出现 immediate 例外 | 本次覆盖任务推送、快捷回复、request_user_input、PlanConfirm、任务摘要；后续新增业务入口必须复用 `_resolve_business_prompt_send_mode()`。 |
| 控制命令被误排队 | CLI 状态切换可能延迟或错位 | 不在 `_dispatch_prompt_to_model` 全局改默认值，只在业务入口显式传 `send_mode`。 |
| 不支持 queued 模型 | queued 指令不可用 | `_resolve_business_prompt_send_mode()` 先判断 `_supports_queued_send_mode()`，不支持则回退 immediate。 |
| 旧任务推送键盘认知冲突 | 用户可能以为还能选择 immediate | 文案改为“发送方式已收口”，点击旧按钮仅兼容继续，不改变真实发送方式。 |

回滚方式：恢复各业务入口原 `send_mode` 或旧任务推送发送方式选择流程，移除 `_resolve_business_prompt_send_mode()` 的调用，并回滚本任务新增/调整测试；无数据库回滚。

## 9. 最终验证记录

> 本节记录本任务实施过程已执行命令；最终交付前仍以最新终端输出为准。

### 9.1 业务排队聚焦用例

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_push_model_success \
  tests/test_task_description.py::test_push_model_skip_keeps_selected_push_mode \
  tests/test_task_description.py::test_push_model_test_push \
  tests/test_task_description.py::test_push_model_choice_for_copilot_dispatches_queued_without_send_mode_prompt \
  tests/test_task_description.py::test_task_summary_command_triggers_request \
  tests/test_task_description.py::test_model_quick_reply_all_dispatches_as_queued_business_prompt \
  tests/test_task_description.py::test_model_quick_reply_partial_dispatches_as_queued_business_prompt \
  tests/test_request_user_input_flow.py::test_request_input_submit_dispatches_structured_payload \
  tests/test_request_user_input_flow.py::test_request_input_submit_dispatches_parallel_context \
  tests/test_plan_confirm_bridge.py::test_plan_confirm_yes_dispatches_implement_prompt \
  tests/test_plan_confirm_bridge.py::test_plan_confirm_yes_acknowledges_callback_before_dispatch
# 11 passed, 2 warnings
```

### 9.2 受影响主测试集

```bash
python3.11 -m pytest -q tests/test_task_description.py tests/test_request_user_input_flow.py tests/test_plan_confirm_bridge.py tests/test_tmux_send_line.py
# 270 passed
```

### 9.3 相邻入口测试集

```bash
python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_task_batch_push.py tests/test_parallel_session_routing.py
# 79 passed
```

### 9.4 最终受影响合并测试集

```bash
python3.11 -m pytest -q tests/test_task_description.py tests/test_request_user_input_flow.py tests/test_plan_confirm_bridge.py tests/test_tmux_send_line.py tests/test_chat_menu_buttons.py tests/test_task_batch_push.py tests/test_parallel_session_routing.py
# 349 passed
```

### 9.5 语法检查与运行诊断

```bash
python3.11 -m py_compile bot.py
# exit 0

python3.11 -m vibego_cli doctor
# exit 0，python_ok=true，dependencies=[]
```

### 9.6 全量 pytest

```bash
python3.11 -m pytest -q
# 3 failed, 981 passed, 6 warnings
```

失败项均为既有 AGENTS 模板迁移测试，与本次业务提示统一排队改动隔离：

- `tests/test_agents_template_migration.py::test_enforced_notice_points_to_agents_md`
- `tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt`
- `tests/test_agents_template_migration.py::test_agents_template_requires_comet_for_complex_workflows`

## 10. 完成状态 Checklist

- [x] 普通直聊保持默认 queued / unsupported immediate fallback。
- [x] 任务推送不再真实选择 immediate，统一业务发送方式。
- [x] 快捷回复全部/部分均 queued。
- [x] `request_user_input` 回填 queued，且不改变原 answers/schema/question_context 契约。
- [x] PlanConfirm Implement / retry queued，callback 先 ack 契约不变。
- [x] 任务摘要生成 queued。
- [x] 控制命令与 fresh/退出菜单不改为 queued。
- [x] 已更新 `AGENTS.md` 事实表。
