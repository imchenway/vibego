# TASK_0132 Telegram 触发 Implement the plan 自动切开发执行态（DEVELOP）

## 1. 背景
- 现象：Telegram 点击 `Implement the plan.` 后，模型有概率仍回到 `Plan Mode` 锁定提示，导致无法进入实际开发执行。
- 目标：在不改 Telegram 用户可见文案的前提下，让 Implement 链路优先自动退出 Plan UI，再执行 develop 指令。

## 2. 已落地决策
1. `Implement the plan.` 语义升级为：强制进入开发执行态 + 立即执行。
2. 若当前会话被 Plan 锁定：优先自动切换**当前会话**（不新建会话）。
3. 用户可见文案保持现状。
4. 切换动作：`Shift+Tab(BTab)` 一次后立即发 develop 提示。
5. 判定顺序：先看终端模式标识，再看模型回复信号。
6. 失败回退：自动再执行一次“BTab + develop”（复用现有自动恢复链路）。

## 3. 代码改动

### 3.1 常量与关键词（`bot.py`）
- 新增：
  - `PLAN_EXECUTION_EXIT_PLAN_KEY`（默认 `BTab`）
  - `PLAN_EXECUTION_EXIT_PLAN_DELAY_SECONDS`
  - `PLAN_EXECUTION_MODE_PROBE_LINES`
- 扩展 Plan 锁定关键词：
  - `强制处于 plan mode`
  - `plan mode 锁定`

### 3.2 tmux 与模式探测能力（`bot.py`）
- 新增 `tmux_send_key(session, key)`：发送单键（如 `BTab`）。
- 新增模式解析与探测：
  - `TERMINAL_COLLABORATION_MODE_RE`
  - `_extract_terminal_collaboration_mode(...)`
  - `_probe_terminal_collaboration_mode()`
  - `_probe_plan_execution_terminal_mode()`

### 3.3 发送链路增强（`bot.py`）
- `_dispatch_prompt_to_model(...)` 新增参数：
  - `force_exit_plan_ui: bool = False`
- 新增：
  - `_should_force_exit_plan_ui(...)`
  - `_maybe_force_exit_plan_ui(...)`
- 行为：当 `force_exit_plan_ui=True` 时，发送正文前先探测模式；若仍是 Plan（或未知）则先发 `BTab` 再继续推送。

### 3.4 执行态判定顺序调整（`bot.py`）
- 新增 `_resolve_plan_execution_signal(...)`：
  - 先终端模式探测（Plan/非Plan/未知）
  - 再模型回复扫描
- `_monitor_plan_execution_and_recover(...)` 改为使用该判定函数。
- 自动恢复推送 `PLAN_RECOVERY_DEVELOP_PROMPT` 时开启 `force_exit_plan_ui=True`。

### 3.5 Plan 回调接入（`bot.py`）
- `on_plan_confirm_callback(...)` 的 Yes 分支：
  - 调用 `_dispatch_prompt_to_model(..., force_exit_plan_ui=True)`
- `on_plan_develop_retry_callback(...)`：
  - 同步启用 `force_exit_plan_ui=True`

## 4. 测试改动

### 4.1 `tests/test_plan_confirm_bridge.py`
- Yes / Retry 回调断言更新：验证 `force_exit_plan_ui=True`。
- 新增：
  - `test_resolve_plan_execution_signal_prioritizes_terminal_plan`
  - `test_resolve_plan_execution_signal_uses_model_reply_after_non_plan_probe`

### 4.2 `tests/test_task_description.py`
- 新增：
  - `test_dispatch_prompt_force_exit_plan_ui_sends_btab_before_prompt`
  - `test_dispatch_prompt_force_exit_plan_ui_skips_btab_when_not_plan`
  - `test_extract_terminal_collaboration_mode`（含 ANSI/大小写场景）

## 5. 回归结果
```bash
# 修改前基线（相关）
PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py -k "plan_confirm or plan_develop_retry or dispatch_prompt"
# 16 passed

# 修改后相关用例
PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py -k "plan_confirm or plan_develop_retry or dispatch_prompt or terminal_collaboration_mode or resolve_plan_execution_signal"
# 24 passed

# 全量回归
PYTHONPATH=. pytest -q
# 592 passed, 6 warnings
```

## 6. 可验证资料（官方）
- tmux `send-keys` / 键名（含 `BTab`）：
  https://man7.org/linux/man-pages/man1/tmux.1.html
- Telegram CallbackQuery：
  https://core.telegram.org/bots/api#callbackquery
- Telegram InlineKeyboardMarkup：
  https://core.telegram.org/bots/api#inlinekeyboardmarkup
