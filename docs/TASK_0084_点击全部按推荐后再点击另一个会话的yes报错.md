# /TASK_0084 点击全部按推荐后，再点击另一个会话的 Yes，报错了

## 1. 背景

用户现象：

- 先点击某条消息上的 `✅ 全部按推荐`
- 再点击另一个会话里的 `Implement this plan? -> Yes`
- Telegram 弹出：

```text
该确认已失效，请重新触发。
```

已确认决策：

1. **旧原生会话消息上的 quick reply 直接报失效**
2. 修复范围：
   - `✅ 全部按推荐`
   - `🧩 部分按推荐（需补充）`
   - `Plan Yes` 联动检查

## 2. 证据

- 截图：
  - `/Users/david/.config/vibego/data/telegram/vibegobot/2026-03-12/20260312_075111500-92fa6c4691a5.jpg`
- 原生 quick reply 当前无 session-scoped 路由：
  - `bot.py`（锚点：`_build_model_quick_reply_keyboard`）
- 原生 `全部按推荐` 直接走 chat 级 `_dispatch_prompt_to_model(...)`：
  - `bot.py`（锚点：`on_model_quick_reply_all`）
- `_dispatch_prompt_to_model(...)` 会按目标 session 清理对应 PlanConfirm：
  - `bot.py`（锚点：`_drop_plan_confirm_sessions_for_session`）
- `Yes` 报“该确认已失效”只会命中：
  - `bot.py`（锚点：`on_plan_confirm_callback`）

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- Worker 快捷回复与 PlanConfirm 联动：`bot.py`
- 测试：
  - `tests/test_model_quick_reply.py`
  - `tests/test_plan_confirm_bridge.py`

### 3.2 受影响单元

- `bot.py`
  - `MODEL_QUICK_REPLY_ALL_SESSION_PREFIX`（新增）
  - `MODEL_QUICK_REPLY_PARTIAL_SESSION_PREFIX`（新增）
  - `SessionQuickReplyBinding`（新增）
  - `SESSION_QUICK_REPLY_CALLBACK_BINDINGS`（新增）
  - `_build_model_quick_reply_keyboard`
  - `_deliver_pending_messages`
  - `on_model_quick_reply_all`
  - `on_model_quick_reply_partial`
  - `on_model_quick_reply_partial_supplement`
  - 原生 quick reply 解析与 fail-closed 辅助函数（新增）

### 3.3 直连依赖测试

- `tests/test_model_quick_reply.py`
  - 原生 quick reply 正常派发
  - 原生旧消息 fail-closed
  - `部分按推荐` 在 session 切换后 fail-closed
- `tests/test_plan_confirm_bridge.py`
  - 旧原生 quick reply fail-closed 后，不误伤另一个会话的 `Plan Yes`

### 3.4 测试范围升级判断

- 结论：**有限升级**
- 原因：
  - 修改了 Worker 公共 quick reply 链路
  - 且需要验证不会误删 PlanConfirm token

## 4. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_model_quick_reply.py tests/test_plan_confirm_bridge.py -k "quick_reply_all or quick_reply_partial or plan_confirm"
```

结果：

- ✅ `19 passed`

## 5. TDD 红灯

先补测试，覆盖：

1. 原生会话模型答案底部，`全部按推荐 / 部分按推荐` 使用 session-scoped callback
2. 旧原生会话消息上的 `全部按推荐` 直接 fail-closed
3. 旧原生会话消息上的 `部分按推荐` 直接 fail-closed
4. `部分按推荐` 进入补充态后，如当前活动 session 切走，提交时 fail-closed
5. quick reply fail-closed 后，不应误删另一个活动会话的 `Plan Yes`

首次执行：

```bash
python3.11 -m pytest -q tests/test_model_quick_reply.py tests/test_plan_confirm_bridge.py -k "old_native_quick_reply_fail_closed or quick_reply_all_old_native_message_fails_closed or quick_reply_partial_old_native_message_fails_closed or quick_reply_partial_native_submit_fails_closed_after_session_switch or deliver_pending_messages_for_bound_native_session_includes_commit_button"
```

结果：

- ❌ 失败
- 失败原因：
  - 缺少 `SESSION_QUICK_REPLY_CALLBACK_BINDINGS`
  - 缺少 `SessionQuickReplyBinding`
  - 原生 quick reply 未区分所属 session，仍会按 chat 当前活动会话派发

## 6. 最小实现

### 6.1 给原生 quick reply 增加 session-scoped payload

- 新增：
  - `MODEL_QUICK_REPLY_ALL_SESSION_PREFIX`
  - `MODEL_QUICK_REPLY_PARTIAL_SESSION_PREFIX`
- `SessionQuickReplyBinding`
  - 记录 `token / task_id / session_key`
- `_deliver_pending_messages(...)`
  - 对原生绑定任务的模型答案，生成原生 quick reply token
  - 将 `全部按推荐 / 部分按推荐` 改为 session-scoped callback

### 6.2 旧原生 quick reply fail-closed

- `on_model_quick_reply_all(...)`
- `on_model_quick_reply_partial(...)`

逻辑：

1. 如果点击的是新的 session-scoped 原生 quick reply：
   - 仅当 `binding.session_key == CHAT_SESSION_MAP[chat_id]` 时允许继续
   - 否则直接提示：

```text
该消息所属会话已失效，请在最新会话中重试。
```

2. 如果点击的是历史旧按钮（无 session token）：
   - 在存在活动 task-bound session / PlanConfirm 的风险场景下，直接 fail-closed

### 6.3 `部分按推荐` 提交前再次校验

- `on_model_quick_reply_partial(...)`
  - 进入补充态时把 `native_quick_reply_session_key` 写入 FSM state
- `on_model_quick_reply_partial_supplement(...)`
  - 提交前再次比对 `CHAT_SESSION_MAP[chat_id]`
  - 若当前活动 session 已切走：
    - 清理 state
    - 直接 fail-closed
    - 不调用 `_dispatch_prompt_to_model(...)`

## 7. Self-Test Gate

### 第一轮（新增/受影响测试）

```bash
python3.11 -m pytest -q tests/test_model_quick_reply.py tests/test_plan_confirm_bridge.py -k "old_native_quick_reply_fail_closed or quick_reply_all_old_native_message_fails_closed or quick_reply_partial_old_native_message_fails_closed or quick_reply_partial_native_submit_fails_closed_after_session_switch or deliver_pending_messages_for_bound_native_session_includes_commit_button"
```

结果：

- ✅ `5 passed`

### 最终范围第一轮

```bash
python3.11 -m pytest -q tests/test_model_quick_reply.py tests/test_plan_confirm_bridge.py
```

结果：

- ✅ `26 passed`

### 最终范围第二轮

```bash
python3.11 -m pytest -q tests/test_model_quick_reply.py tests/test_plan_confirm_bridge.py
```

结果：

- ✅ 双跑一致通过

### 最小诊断

```bash
python3.11 -m vibego_cli doctor
```

结果：

- ✅ `python_ok=true`

## 8. 用户可见结果

1. 旧原生会话消息上的 `全部按推荐 / 部分按推荐`，如果已不是当前活动会话：
   - 不再误发到别的会话
   - 直接提示重新在最新会话中操作
2. `部分按推荐` 若进入补充态后会话发生切换：
   - 提交时会直接 fail-closed
3. 另一个会话里的 `Implement this plan? -> Yes`
   - 不会再被 quick reply 误伤为“已失效”

## 9. 风险与回滚

### 风险

- 历史旧原生 quick reply 按钮现在会更严格地 fail-closed，不再自动回落到 chat 当前会话。

### 回滚点

- `bot.py`
- `tests/test_model_quick_reply.py`
- `tests/test_plan_confirm_bridge.py`
