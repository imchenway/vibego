# /TASK_0093 当前 CLI 处理的按钮

## 1. 背景

用户希望把任务详情里“推送到模型”的处理方式进一步细化：

1. `当前 CLI 处理` 改为 `现有 CLI 会话处理`
2. 点击后展示当前项目下**现存可用会话**，由用户选择要推送到哪个会话
3. 如果当前只有一个主会话，则不再多一步选择，直接默认推送到主会话

## 2. 关键证据

- 现有处理方式按钮与提示文案：`bot.py`（锚点：`PUSH_TARGET_CURRENT`, `_build_push_dispatch_target_prompt`, `_build_push_dispatch_target_keyboard`）
- 现有“当前 CLI 处理”直接继续后续推送链路：`bot.py`（锚点：`on_task_push_model_dispatch_target`）
- 当前项目会话列表能力已存在：`bot.py`（锚点：`SessionLiveEntry`, `_list_project_live_sessions`, `_resolve_session_live_entry`）
- 活动并行会话查询按当前项目过滤：`parallel_runtime.py`（锚点：`ParallelSessionStore.list_sessions`）
- 现有任务推送 FSM：`tasks/fsm.py`（锚点：`TaskPushStates`）

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- Worker 推送交互：`bot.py`
- FSM 状态：`tasks/fsm.py`
- 测试：
  - `tests/test_parallel_flow.py`
  - `tests/test_task_description.py`
  - `tests/test_chat_menu_buttons.py`
  - `tests/test_parallel_runtime.py`

### 3.2 计划修改的具体单元

1. `tasks/fsm.py`
   - `TaskPushStates.waiting_existing_session`（新增）

2. `bot.py`
   - `PUSH_TARGET_CURRENT`
   - `_build_push_dispatch_target_prompt`
   - `_build_push_existing_session_prompt`（新增）
   - `_build_push_existing_session_markup`（新增）
   - `_build_push_existing_session_view`（新增）
   - `_show_push_existing_session_view`（新增）
   - `_resolve_selected_existing_dispatch_context`（新增）
   - `_continue_push_after_existing_session_selected`（新增）
   - `on_task_push_model_dispatch_target`
   - `on_push_existing_session_message`（新增）
   - `on_push_existing_session_refresh_callback`（新增）
   - `on_push_existing_session_cancel_callback`（新增）
   - `on_push_existing_session_main_callback`（新增）
   - `on_push_existing_session_parallel_callback`（新增）
   - `on_task_push_model_skip`
   - `on_task_push_model_supplement`

### 3.3 直连依赖测试

- `tests/test_parallel_flow.py`
  - 处理方式入口
  - 多会话时进入现有会话选择页
- `tests/test_task_description.py`
  - 主会话默认直推
  - 选择并行会话后继续推送
  - `done` 状态 `/compact`
  - 补充描述 / 附件 / 跳过补充等回归
- `tests/test_chat_menu_buttons.py`
  - 会话实况入口仍保持“会话列表优先”
- `tests/test_parallel_runtime.py`
  - 当前项目会话过滤仍保持稳定

### 3.4 测试范围升级判断

- 结论：**有限升级**
- 原因：
  - 修改了任务推送公共交互链路与 FSM 状态
  - 但未修改 DB schema、构建链、外部 API

## 4. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_parallel_flow.py -k 'push_model_starts_with_dispatch_target_choice' tests/test_task_description.py -k 'push_model_success or push_model_test_push or push_model_done_push or push_model_skip_keeps_selected_push_mode'
```

结果：

```text
5 passed, 193 deselected in 0.15s
```

## 5. TDD 红灯

先补测试：

1. 多会话时选择 `现有 CLI 会话处理` 应进入会话选择页
2. 选择现有并行会话后，后续推送应命中所选并行上下文
3. 原有“主会话直推”相关测试补充固定会话列表桩，避免误依赖真实环境

首次执行：

```bash
python3.11 -m pytest -q tests/test_parallel_flow.py -k 'existing_cli_target_with_multiple_sessions_opens_session_picker or push_model_starts_with_dispatch_target_choice' tests/test_task_description.py -k 'push_model_success or push_model_test_push or push_model_done_push or push_model_selected_existing_parallel_session_routes_to_parallel_context'
```

结果：

```text
1 failed, 4 passed
```

失败原因符合预期：

- `TaskPushStates` 尚无 `waiting_existing_session`
- 缺少现有会话选择页与对应回调处理

## 6. 最小实现

### 6.1 交互文案

- `当前 CLI 处理` 改为 `现有 CLI 会话处理`
- 处理方式提示同步改文案

### 6.2 现有会话选择

- 新增 `waiting_existing_session`
- 当用户选择 `现有 CLI 会话处理` 时：
  - 若当前项目只有主会话：直接继续原流程
  - 若存在主会话 + 活动并行会话：展示 inline 会话选择页

### 6.3 推送路由

- 主会话：继续走 native 路由
- 并行会话：将所选会话恢复为 `ParallelDispatchContext`，继续发到该并行 CLI
- 若所选并行会话在后续步骤中失活：fail-closed，提示重新选择

### 6.4 兼容原有后续步骤

- `PLAN / YOLO`
- `立即发送 / 排队发送`
- `补充任务描述 / 跳过`
- `done` 状态 `/compact`

以上链路均会复用“已选择的现有会话”。

## 7. Self-Test Gate

最终测试范围：

```bash
python3.11 -m pytest -q \
  tests/test_parallel_flow.py::test_push_model_starts_with_dispatch_target_choice \
  tests/test_parallel_flow.py::test_existing_cli_target_with_multiple_sessions_opens_session_picker \
  tests/test_task_description.py::test_push_model_success \
  tests/test_task_description.py::test_push_model_supplement_uses_caption \
  tests/test_task_description.py::test_push_model_skip_keeps_selected_push_mode \
  tests/test_task_description.py::test_push_model_supplement_falls_back_to_attachment_names \
  tests/test_task_description.py::test_push_model_supplement_binds_attachments \
  tests/test_task_description.py::test_push_model_preview_fallback_on_too_long \
  tests/test_task_description.py::test_push_model_test_push \
  tests/test_task_description.py::test_push_model_choice_for_non_codex_skips_send_mode \
  tests/test_task_description.py::test_push_model_test_push_includes_related_task_context \
  tests/test_task_description.py::test_push_model_done_push \
  tests/test_task_description.py::test_push_model_selected_existing_parallel_session_routes_to_parallel_context \
  tests/test_chat_menu_buttons.py::test_worker_session_live_button_opens_session_list \
  tests/test_parallel_runtime.py::test_parallel_session_store_list_sessions_filters_by_status
```

### 第一轮

```text
15 passed in 0.25s
```

### 第二轮

```text
15 passed in 0.30s
```

### 最小诊断

```bash
python3.11 -m vibego_cli doctor
```

结果：

```json
{
  "python_ok": true
}
```

## 8. 用户可见结果

1. 处理方式按钮名称变为 `现有 CLI 会话处理`
2. 如果当前项目只有主会话：
   - 选择该项后直接继续原流程
3. 如果当前项目存在活动并行会话：
   - 先看到可选会话列表
   - 选择哪个会话，就把提示词推送到哪个会话
4. `done` 状态的 `/compact` 也会遵循同样的会话选择规则

## 9. 风险与回滚

### 风险

- 用户在会话选择后、真正发送前，如果选中的并行会话失活，会看到 fail-closed 提示，需要重新发起一次推送。

### 回滚点

- `tasks/fsm.py`
- `bot.py`
- `tests/test_parallel_flow.py`
- `tests/test_task_description.py`
