# TASK_20260312_001 PlanConfirm 同 chat 并存避免过早失效

## 1. 背景
- 用户现象：Telegram 中旧的 `Implement this plan?` 卡片在点击 `Yes` 时，直接提示“该确认已失效，请重新触发”。
- 高置信度根因：
  - `bot.py:10558-10567`：同 chat 新建 PlanConfirm 时，会直接 `_drop_plan_confirm_session(active_token)`，删掉旧 token。
  - `bot.py:13782-13787`：按钮回调还要求 token 必须等于 `CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id]`，旧卡片会被“新会话替换”门禁挡掉。
  - `bot.py:2264-2268`：任意新 prompt 入模时，还会先 `_drop_chat_plan_confirm_session(chat_id)`，导致无关会话的 PlanConfirm 被一起删掉。
- 对照先例：
  - `docs/TASK_0081_request_user_input同chat并存避免过早失效.md` 已把 request_input 从“同 chat 单活”改为“同 chat 并存”。

## 2. Class Impact Plan

### 2.1 受影响子项目与目录
- worker Telegram 交互链路：`bot.py`
- 测试资产：
  - `tests/test_plan_confirm_bridge.py`
  - `tests/test_task_description.py`

### 2.2 计划修改单元
| 单元 | 实现文件 | 测试文件 |
|---|---|---|
| `CHAT_ACTIVE_PLAN_CONFIRM_TOKENS` 注释/语义 | `bot.py` | `tests/test_plan_confirm_bridge.py` |
| `_find_plan_confirm_tokens`（新增） | `bot.py` | `tests/test_plan_confirm_bridge.py` |
| `_drop_plan_confirm_sessions_for_session`（新增） | `bot.py` | `tests/test_task_description.py` |
| `_maybe_send_plan_confirm_prompt` | `bot.py` | `tests/test_plan_confirm_bridge.py` |
| `on_plan_confirm_callback` | `bot.py` | `tests/test_plan_confirm_bridge.py` |
| `_dispatch_prompt_to_model` | `bot.py` | `tests/test_task_description.py` |

### 2.3 直连依赖测试
- `tests/test_plan_confirm_bridge.py`
  - 直接覆盖 PlanConfirm 按钮创建、Yes/No 回调、并发点击、并行上下文 fail-closed。
- `tests/test_task_description.py`
  - 直接覆盖 `_dispatch_prompt_to_model(...)` 的公共派发契约与 force-exit-plan 行为。

### 2.4 测试范围升级判断
- 结论：✅ 命中升级条件（有限升级）
- 原因：
  - `_dispatch_prompt_to_model(...)` 是共享派发函数，属于高复用公共逻辑；
  - 因此在类级测试外，额外纳入 `tests/test_task_description.py` 的 `dispatch_prompt_*` 直连契约回归。

## 3. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py
python3.11 -m pytest -q tests/test_task_description.py -k 'dispatch_prompt_injects_enforced_agents_notice or dispatch_prompt_skips_enforced_agents_notice_for_plan_implement_prompt or dispatch_prompt_force_exit_plan_ui_sends_key_sequence_before_prompt'
```

结果：
- ✅ `9 passed`
- ✅ `5 passed, 146 deselected`

## 4. TDD 红灯

新增测试：
- `tests/test_plan_confirm_bridge.py::test_plan_confirm_old_callback_still_works_after_newer_session_created`
- `tests/test_plan_confirm_bridge.py::test_maybe_send_plan_confirm_prompt_keeps_older_session_for_same_chat`
- `tests/test_task_description.py::test_dispatch_prompt_to_model_does_not_drop_other_session_plan_confirm_for_same_chat`

首次执行：

```bash
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py -k 'old_callback_still_works_after_newer_session_created or maybe_send_plan_confirm_prompt_keeps_older_session_for_same_chat'
python3.11 -m pytest -q tests/test_task_description.py -k 'does_not_drop_other_session_plan_confirm_for_same_chat'
```

结果：
- ❌ 旧 PlanConfirm 会被“最新 token”门禁拦截
- ❌ 同 chat 新建 PlanConfirm 会删掉旧 session
- ❌ `_dispatch_prompt_to_model(...)` 会误删无关会话的 PlanConfirm

## 5. 最小实现

### 5.1 PlanConfirm 改为“同 chat 可并存”
- `bot.py::_maybe_send_plan_confirm_prompt(...)`
  - 改为按 `chat_id + session_key` 去重；
  - 仅阻止“同会话重复发卡片”；
  - 不再删除同 chat 的其他 PlanConfirm。

### 5.2 回调不再受“最新 token”门禁限制
- `bot.py::on_plan_confirm_callback(...)`
  - 移除 `active_token != token` 的拦截；
  - 改为只校验：
    - token/session 是否存在
    - chat 是否匹配
    - user 是否匹配
    - 并行上下文是否真实 stale
    - 并发点击是否重复

### 5.3 prompt 派发只清理“当前目标会话”的 PlanConfirm
- `bot.py::_dispatch_prompt_to_model(...)`
  - 去掉函数入口处“按 chat 全删”的 `_drop_chat_plan_confirm_session(chat_id)`；
  - 新增 `_drop_plan_confirm_sessions_for_session(chat_id, session_key)`；
  - 仅在识别出本次真正目标 `session_path` 后，清理该会话自己的 PlanConfirm；
  - 其他并存会话保留。

## 6. Self-Test Gate

执行两轮一致性回归：

```bash
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py -k 'dispatch_prompt_'
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py -k 'dispatch_prompt_'
```

结果：
- ✅ 第一轮：`19 passed, 144 deselected`
- ✅ 第二轮：`19 passed, 144 deselected`

## 7. 用户可见结果
1. 同一个 Telegram chat 中，旧的 `Implement this plan?` 卡片不会因为新会话出现而立即失效。
2. 点击旧卡片的 `Yes` 仍会回到它自己的会话上下文。
3. 只有“同一个 session 自己继续推进”时，才会清理对应 PlanConfirm，避免真正的跨轮次误触发。
4. 并发重复点击同一 `Yes` 仍保持幂等。

## 8. 回滚说明
- 回滚 `bot.py` 中：
  - `_find_plan_confirm_tokens`
  - `_drop_plan_confirm_sessions_for_session`
  - `_maybe_send_plan_confirm_prompt(...)` 的同 chat 并存逻辑
  - `_dispatch_prompt_to_model(...)` 的按 session 定向清理逻辑
  - `on_plan_confirm_callback(...)` 去除“最新 token”门禁的改动
- 回滚新增测试：
  - `tests/test_plan_confirm_bridge.py`
  - `tests/test_task_description.py`
