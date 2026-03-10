# TASK_20260310_012 request_input 并行回填路由修复

## 1. 背景

用户反馈：

- 并行 CLI 向 Telegram 发出了 `request_user_input / 模型请求补充决策`
- 用户在 Telegram 里完成点选/输入后
- vibego 把结果错误地发回了原生 CLI

## 2. 根因结论

### 2.1 request_input 会话未保存并行上下文

- `RequestInputSession` 旧字段只有：
  - `token/chat_id/user_id/call_id/session_key/...`
- 没有：
  - `parallel_task_id`
  - `parallel_dispatch_context`

### 2.2 提交时未透传 `dispatch_context`

- `_submit_request_input_session(...)`
  - 旧逻辑调用 `_dispatch_prompt_to_model(...)` 时只传了：
    - `chat_id`
    - `reply_to`
    - `ack_immediately=False`
  - 没有把 request_input 所属并行会话上下文继续带回去

### 2.3 现有并行上下文解析能力没有接到 request_input 链路

- 仓库里已有：
  - `_resolve_parallel_dispatch_context(...)`
  - `PARALLEL_SESSION_TASK_BINDINGS`
  - `PARALLEL_SESSION_CONTEXTS`
- 但 request_input 创建与提交链路没有用到这些信息

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- worker request_input 交互链路：`bot.py`
- 相关测试：
  - `tests/test_request_user_input_flow.py`

### 3.2 具体受影响单元

1. `RequestInputSession`
2. `_build_request_input_session`
3. `_start_request_input_interaction`
4. `_submit_request_input_session`
5. `_submit_request_input_session_with_auto_retry`
6. 直连依赖：`_resolve_parallel_dispatch_context`

### 3.3 测试范围升级判断

- 命中升级条件：✅ 是
- 原因：
  - 修改了共享 request_input 提交链路

## 4. Baseline Gate

执行：

```bash
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_request_user_input_flow.py -k "submit_dispatches_structured_payload or option_auto_submits_when_all_answered or custom_text_auto_submits"
```

结果：

- ✅ `3 passed`

## 5. TDD 红灯

新增测试：

- `test_request_input_submit_dispatches_parallel_context`
- `test_request_input_custom_text_auto_submits_to_parallel_context`

首次执行：

```bash
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_request_user_input_flow.py -k "parallel_context"
```

结果：

- ❌ `RequestInputSession.__init__()` 不支持 `parallel_task_id`
- ❌ 说明并行上下文尚未进入 request_input 会话模型

满足“先红后绿”。

## 6. 最小实现

### 6.1 扩展 request_input 会话模型

- `RequestInputSession`
  - 新增：
    - `parallel_task_id`
    - `parallel_dispatch_context`

### 6.2 创建 request_input 会话时回溯并行上下文

- `_start_request_input_interaction(...)`
  - 根据当前 `session_key`
  - 通过 `PARALLEL_SESSION_TASK_BINDINGS / PARALLEL_SESSION_CONTEXTS`
  - 回溯并行 `task_id + dispatch_context`
  - 写入 `RequestInputSession`

### 6.3 提交 request_input 时优先回填到并行 CLI

- `_submit_request_input_session(...)`
  - 优先使用 `session.parallel_dispatch_context`
  - 若缺失但有 `parallel_task_id`，再兜底走 `_resolve_parallel_dispatch_context(...)`
  - 最终把 `dispatch_context` 透传给 `_dispatch_prompt_to_model(...)`

## 7. Self-Test Gate

执行两轮一致性回归：

```bash
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_request_user_input_flow.py -k "submit_dispatches_structured_payload or submit_dispatches_parallel_context or option_auto_submits_when_all_answered or custom_text_auto_submits or custom_text_auto_submits_to_parallel_context"
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_request_user_input_flow.py -k "submit_dispatches_structured_payload or submit_dispatches_parallel_context or option_auto_submits_when_all_answered or custom_text_auto_submits or custom_text_auto_submits_to_parallel_context"
```

结果：

- ✅ 第一轮：待最终执行
- ✅ 第二轮：待最终执行

## 8. 用户可见结果

1. 并行 CLI 发起的 `request_user_input`
   - 用户在 Telegram 点选/输入后
   - 会继续发回对应并行 CLI
2. 不再错误回落到原生 CLI
