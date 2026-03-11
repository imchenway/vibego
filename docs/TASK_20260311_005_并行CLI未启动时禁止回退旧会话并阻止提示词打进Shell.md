# TASK_20260311_005 并行 CLI 未启动时禁止回退旧会话并阻止提示词打进 Shell

## 1. 背景

- 用户现场现象：
  - 新建并行 CLI 后，Telegram 收到的是旧会话内容；
  - 并行 tmux pane 实际还停留在 `zsh`，任务提示词被当作 shell 命令执行。
- 运行时证据：
  - `tmux -u display-message -p -t vibe-par-cckgpimbot-task_0010 '#{pane_current_command}'` => `zsh`
  - `tmux -u capture-pane -p -t vibe-par-cckgpimbot-task_0010 -S -120` 命中：
    - `zsh: command not found: 进入`
    - `zsh: command not found: 任务标题：类目编码的前后端校验`
  - `~/.config/vibego/runtime/parallel/cckgpimbot/TASK_0010/_runtime/session_binder.log`
    - 新一轮 binder start 后未绑定 fresh session
  - `~/.config/vibego/logs/codex/cckgpimbot/run_bot.log`
    - 命中 `strict fallback locate latest session ...`

## 2. 根因

### 2.1 并行 tmux session 存在 ≠ Codex CLI 已就绪

- `bot.py:8590-8621`
  - `_start_parallel_tmux_session(...)` 只负责启动脚本并等待进程退出
  - 没有校验 pane 当前前台进程是否已变为 `codex`

### 2.2 首次并行派发时，正文会在 fresh session 绑定前直接发送

- `bot.py:2218-2244`
  - `needs_session_wait = session_path is None`
  - 但正文 `tmux_send_line(...)` 发生在 `_await_session_path(...)` 之前

### 2.3 fresh session 未绑定时，strict 模式会错误兜底到旧 rollout

- `bot.py:2266-2273`
  - 首次并行派发在 pointer 迟迟未写入时，仍允许 `_fallback_locate_latest_session(...)`

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- worker 并行启动与模型派发链路：`bot.py`
- 测试资产：
  - `tests/test_task_description.py`
  - `tests/test_parallel_flow.py`
  - `tests/test_parallel_session_routing.py`

### 3.2 计划修改单元

| 单元 | 实现文件 | 测试文件 |
|---|---|---|
| `_get_tmux_pane_current_command`（新增） | `bot.py` | `tests/test_task_description.py`, `tests/test_parallel_session_routing.py` |
| `_is_tmux_shell_command`（新增） | `bot.py` | `tests/test_parallel_session_routing.py` |
| `_validate_parallel_tmux_ready_for_dispatch`（新增） | `bot.py` | `tests/test_task_description.py` |
| `_dispatch_prompt_to_model` | `bot.py` | `tests/test_task_description.py` |
| `on_parallel_branch_confirm_callback` | `bot.py` | `tests/test_parallel_flow.py` |
| `_parallel_session_runtime_issue` | `bot.py` | `tests/test_parallel_session_routing.py` |

### 3.3 直连依赖测试

- `tests/test_task_description.py`
  - 证据：覆盖 `_dispatch_prompt_to_model(...)` 的 `/plan`、退出 PLAN、并行 tmux 会话路由
- `tests/test_parallel_flow.py`
  - 证据：覆盖 `on_parallel_branch_confirm_callback(...)` 的处理中提示、失败收口、超时收口
- `tests/test_parallel_session_routing.py`
  - 证据：覆盖并行运行态健康检查与 stale 降级

### 3.4 测试范围升级判断

- 结论：✅ 命中升级条件
- 原因：
  - 修改了 worker 公共并行派发基础逻辑
  - 修改了 stale 运行态判定逻辑

## 4. Baseline Gate

### 4.1 基线修复

- 发现既有测试 `tests/test_task_description.py::test_dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session`
  存在运行态依赖缺口，会真实调用 `_send_session_ack(...)`
- 最小修复：
  - 为该测试补 `fake_send_session_ack`
  - 清理 `PARALLEL_TASK_WATCHERS / PARALLEL_TASK_SESSION_MAP / PARALLEL_SESSION_CONTEXTS`

### 4.2 基线执行

```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'dispatch_prompt_plan_mode_waits_for_parallel_tmux_ready or dispatch_prompt_force_exit_plan_ui_sends_key_sequence_before_prompt or dispatch_prompt_force_exit_plan_ui_skips_btab_when_not_plan or dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session'
python3.11 -m pytest -q tests/test_parallel_flow.py -k 'parallel_branch_confirm_callback_edits_same_message_to_processing or parallel_branch_confirm_callback_replaces_processing_message_with_summary or parallel_branch_confirm_callback_acknowledges_callback_before_prepare or parallel_branch_confirm_callback_falls_back_to_chat_when_failure_callback_expires or parallel_branch_confirm_callback_reports_prepare_timeout'
python3.11 -m pytest -q tests/test_parallel_session_routing.py -k 'get_active_parallel_session_marks_stale_session_closed or delete_parallel_session_workspace_allows_cleanup_for_closed_session'
```

结果：

- ✅ `4 passed`
- ✅ `5 passed`
- ✅ `2 passed`

## 5. TDD 红灯

新增测试：

- `tests/test_task_description.py`
  - `test_dispatch_prompt_parallel_first_dispatch_fails_closed_when_tmux_still_shell`
  - `test_dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session`
- `tests/test_parallel_flow.py`
  - `test_parallel_branch_confirm_callback_stops_when_push_task_to_model_fails`
- `tests/test_parallel_session_routing.py`
  - `test_parallel_session_runtime_issue_detects_shell_pane`

首次执行：

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_dispatch_prompt_parallel_first_dispatch_fails_closed_when_tmux_still_shell \
  tests/test_task_description.py::test_dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session \
  tests/test_parallel_flow.py::test_parallel_branch_confirm_callback_stops_when_push_task_to_model_fails \
  tests/test_parallel_session_routing.py::test_parallel_session_runtime_issue_detects_shell_pane
```

结果：

- ❌ 并行首次派发仍会发送 `/plan` / 正文
- ❌ 仍会 `strict fallback` 到旧 rollout
- ❌ `_push_task_to_model=False` 时仍会继续成功收口
- ❌ stale 判定未识别 pane 仍停留在 `zsh`

## 6. 最小实现

### 6.1 新增并行 pane 当前前台进程探测

- `bot.py`
  - `_get_tmux_pane_current_command(session)`
  - `_is_tmux_shell_command(command)`

### 6.2 并行首次派发前增加 shell 门禁

- `bot.py`
  - `_validate_parallel_tmux_ready_for_dispatch(...)`
  - `_dispatch_prompt_to_model(...)`
    - 若并行首次派发时 pane 当前仍是 shell，则直接：
      - 回复 `并行 CLI 未启动成功：当前终端仍停留在 shell（zsh）`
      - 不发送 `/plan`
      - 不发送正文

### 6.3 并行首次派发禁止 strict fallback 到旧 session

- `bot.py`
  - `_dispatch_prompt_to_model(...)`
    - 对 `is_parallel_dispatch and needs_session_wait` 场景禁用 `_fallback_locate_latest_session(...)`
    - fresh session 超时未绑定时直接 fail-closed：
      - `并行 CLI 未生成新的会话日志，请稍后重试。`

### 6.4 并行创建链路在首次推送失败时立即收口失败

- `bot.py`
  - `on_parallel_branch_confirm_callback(...)`
    - `_push_task_to_model(...)` 返回 `success=False` 时：
      - `PARALLEL_SESSION_STORE.update_status(..., status="closed")`
      - 回传 `并行 CLI 未启动成功，请稍后重试。`
      - 不再展示成功摘要 / prompt 预览 / session ack

### 6.5 stale 运行态判定补强

- `bot.py`
  - `_parallel_session_runtime_issue(...)`
    - 若 pane 当前命令仍是 shell（如 `zsh`），直接视为失活

## 7. Self-Test Gate

### 7.1 定向回归

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_dispatch_prompt_plan_mode_sends_plan_switch_for_codex \
  tests/test_task_description.py::test_dispatch_prompt_plan_mode_waits_for_parallel_tmux_ready \
  tests/test_task_description.py::test_dispatch_prompt_force_exit_plan_ui_sends_key_sequence_before_prompt \
  tests/test_task_description.py::test_dispatch_prompt_force_exit_plan_ui_skips_btab_when_not_plan \
  tests/test_task_description.py::test_dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session \
  tests/test_task_description.py::test_dispatch_prompt_parallel_first_dispatch_fails_closed_when_tmux_still_shell \
  tests/test_task_description.py::test_dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session \
  tests/test_parallel_flow.py::test_parallel_branch_confirm_callback_edits_same_message_to_processing \
  tests/test_parallel_flow.py::test_parallel_branch_confirm_callback_replaces_processing_message_with_summary \
  tests/test_parallel_flow.py::test_parallel_branch_confirm_callback_acknowledges_callback_before_prepare \
  tests/test_parallel_flow.py::test_parallel_branch_confirm_callback_falls_back_to_chat_when_failure_callback_expires \
  tests/test_parallel_flow.py::test_parallel_branch_confirm_callback_reports_prepare_timeout \
  tests/test_parallel_flow.py::test_parallel_branch_confirm_callback_stops_when_push_task_to_model_fails \
  tests/test_parallel_session_routing.py::test_get_active_parallel_session_marks_stale_session_closed \
  tests/test_parallel_session_routing.py::test_delete_parallel_session_workspace_allows_cleanup_for_closed_session \
  tests/test_parallel_session_routing.py::test_parallel_session_runtime_issue_detects_shell_pane
```

结果：

- ✅ 第一轮：`16 passed`
- ✅ 第二轮：`16 passed`

### 7.2 未执行项说明

- 当前仓库未找到可证实的局部 typecheck 统一命令
- 当前仓库未找到可证实的 coverage 统一命令
- 证据：`AGENTS.md -> 7) Testing & Quality Gates`

## 8. 用户可见结果

1. 并行 tmux 如果还停留在 `zsh`
   - vibego 会直接报错并停止
   - 不再把 prompt 打进 shell
2. 并行首次派发若没有 fresh session
   - 会 fail-closed
   - 不再回退到旧 rollout
3. 并行首次推送失败时
   - Telegram 不再展示“已创建并行开发副本”成功摘要
   - 而是明确提示启动失败

## 9. 回滚说明

- 回滚 `bot.py`：
  - `_get_tmux_pane_current_command`
  - `_is_tmux_shell_command`
  - `_validate_parallel_tmux_ready_for_dispatch`
  - `_dispatch_prompt_to_model` 中的并行 fail-closed 逻辑
  - `on_parallel_branch_confirm_callback` 的 push failure 收口
  - `_parallel_session_runtime_issue` 的 shell 判定
- 回滚测试文件中的新增用例与基线修复
