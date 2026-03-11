# TASK_20260311_004 并行 CLI 的 Implement 退出 PLAN 会话错路由修复

## 1. 背景
- 现象：Telegram 点击 `Implement the plan.` 后，并行 CLI 的 tmux 中能收到正文，但仍停留在 `Plan mode`，无法进入 develop。
- 对比：原生 CLI 不受影响。

## 2. 证据
- `bot.py:2190-2191`
  - 正文发送已按 `dispatch_context.tmux_session` 路由到并行会话。
- `bot.py:1532-1592`
  - 终端模式探测仍固定使用全局 `TMUX_SESSION`。
- `bot.py:1620-1708`
  - 退出 Plan UI 的按键仍固定发往全局 `TMUX_SESSION`。
- 结论：并行链路中“正文发送会话”和“退出 PLAN 会话”不是同一个 tmux session。

## 3. Class Impact Plan

### 3.1 受影响子项目与目录
- vibego worker 链路：`bot.py`
- 测试资产：`tests/test_task_description.py`
- 直连契约验证：`tests/test_plan_confirm_bridge.py`

### 3.2 计划修改单元
| 单元 | 实现文件 | 测试文件 |
|---|---|---|
| `_capture_tmux_recent_lines` | `bot.py` | `tests/test_task_description.py` |
| `_probe_terminal_collaboration_mode` | `bot.py` | `tests/test_task_description.py` |
| `_probe_plan_execution_terminal_mode` | `bot.py` | `tests/test_task_description.py` |
| `_maybe_force_exit_plan_ui` | `bot.py` | `tests/test_task_description.py` |
| `_dispatch_prompt_to_model` | `bot.py` | `tests/test_task_description.py`, `tests/test_plan_confirm_bridge.py` |

### 3.3 直连依赖测试
- `tests/test_plan_confirm_bridge.py`
  - 证据：`on_plan_confirm_callback(...)` 会调用 `_dispatch_prompt_to_model(...)`，且并行上下文从该回调透传。

### 3.4 测试范围升级判断
- 结论：否
- 原因：变更可收敛到并行 Plan 退出链路，已有局部测试资产可证明安全。

## 4. Baseline Gate
执行：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'dispatch_prompt_force_exit_plan_ui'
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py -k 'plan_confirm_yes_dispatches_implement_prompt or parallel_plan_confirm_yes_dispatches_bound_parallel_context or parallel_plan_confirm_yes_fails_closed_when_context_stale'
```

结果：
- ✅ `3 passed, 145 deselected`
- ✅ `3 passed, 6 deselected`

## 5. TDD 红灯
新增测试：
- `test_dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session`

首次执行：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'force_exit_plan_ui_uses_parallel_tmux_session'
```

结果：
- ❌ 失败
- 失败点：`probe_calls == [None, None]`
- 说明 `_maybe_force_exit_plan_ui(...)` 没把并行 `tmux_session` 透传到模式探测与按键发送链路。

## 6. 最小实现
- `bot.py`
  - `_capture_tmux_recent_lines(...)` 新增 `tmux_session` 参数。
  - `_probe_terminal_collaboration_mode(...)` 新增 `tmux_session` 参数。
  - `_probe_plan_execution_terminal_mode(...)` 新增 `tmux_session` 参数。
  - `_maybe_force_exit_plan_ui(...)` 新增 `tmux_session` 参数，并统一使用 `target_tmux_session` 发送按键/探测模式。
  - `_dispatch_prompt_to_model(...)` 在并行场景下把 `dispatch_context.tmux_session` 传给 `_maybe_force_exit_plan_ui(...)`。
- `tests/test_task_description.py`
  - 补充并行会话回归测试。
  - 调整既有 monkeypatch，使模式探测 mock 兼容新参数签名。

## 7. Self-Test Gate
执行两轮一致性回归：

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_dispatch_prompt_force_exit_plan_ui_sends_key_sequence_before_prompt \
  tests/test_task_description.py::test_dispatch_prompt_force_exit_plan_ui_skips_btab_when_not_plan \
  tests/test_task_description.py::test_dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session \
  tests/test_task_description.py::test_dispatch_prompt_force_exit_plan_ui_retries_multiple_rounds \
  tests/test_plan_confirm_bridge.py::test_plan_confirm_yes_dispatches_implement_prompt \
  tests/test_plan_confirm_bridge.py::test_parallel_plan_confirm_yes_dispatches_bound_parallel_context \
  tests/test_plan_confirm_bridge.py::test_parallel_plan_confirm_yes_fails_closed_when_context_stale
```

结果：
- ✅ 第一轮：`7 passed`
- ✅ 第二轮：`7 passed`

## 8. 用户可见结果
- 并行 CLI 点击 `Implement the plan.` 后：
  - 退出 PLAN 模式的按键会发到并行 tmux 会话
  - 模式探测也会读取并行 tmux 会话
  - 不再出现“正文到并行、退出动作却打到原生”的错路由

## 9. 回滚说明
- 回滚 `bot.py` 中新增的 `tmux_session` 参数透传
- 回滚 `tests/test_task_description.py` 中新增并行回归测试
