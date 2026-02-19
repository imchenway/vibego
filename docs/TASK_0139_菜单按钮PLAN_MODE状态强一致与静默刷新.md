# TASK_0139 菜单按钮 PLAN MODE 状态强一致与静默刷新（DEVELOP）

## 1. 背景
- 问题：`🧭 PLAN MODE` 按钮状态偶发不刷新，且部分链路会产生额外 Telegram 文案回复。
- 目标：
  1) 尽可能保持按钮状态与 tmux 实际状态一致；
  2) 非必要不发送额外 Telegram 聊天气泡文案；
  3) 保持现有流程键盘不被误覆盖。
- 关联任务：`/TASK_0067`、`/TASK_0064`（用户口头关联）。

## 2. 本次决策落地
1. 主菜单渲染默认“强探测”PLAN MODE 状态（实时优先，不依赖旧缓存）。
2. PLAN MODE 切换后新增短轮询探测，降低“刚切换就读到旧状态”的概率。
3. Plan Yes/No 收口去除“主菜单状态已刷新”聊天消息，仅保留按钮回调反馈。
4. 状态探测异常时按钮显示 `?`，并通过日志记录，不额外放大打扰。

## 3. 代码变更

### 3.1 `bot.py`
- 新增环境参数：
  - `WORKER_PLAN_MODE_TOGGLE_STABILIZE_SECONDS`（默认 `0.12`）
  - `WORKER_PLAN_MODE_TOGGLE_RETRY_ROUNDS`（默认 `3`）
  - `WORKER_PLAN_MODE_TOGGLE_RETRY_GAP_SECONDS`（默认 `0.12`）
- 新增函数：
  - `_refresh_worker_plan_mode_state_after_toggle_async(...)`
    - 在按键切换后做短暂稳定等待 + 轮询探测，尽量拿到新状态。
- 调整函数：
  - `_build_worker_main_keyboard(...)`
    - 默认 `refresh_plan_mode_state=True` 时，始终强探测实时状态；
    - 仅 `refresh_plan_mode_state=False` 时允许使用显式状态/缓存。
- 调整链路：
  - `_handle_worker_plan_mode_toggle_request(...)`
    - 使用“切换后轮询探测”；
    - 文案收敛为 `主菜单已刷新，当前 PLAN MODE：X`。
  - `_handle_terminal_snapshot_request(...)`
    - 终端实况仍做状态校准，但菜单回写统一走强探测主菜单构建。
  - `on_plan_confirm_callback(...)`
    - Yes/No 后不再额外发送“主菜单状态已刷新”聊天消息；仅清理 inline 按钮并回调确认。
  - `on_task_push_model_choice(...)`
    - 取消分支不再手动透传缓存状态，统一按主菜单强探测渲染。
  - `on_start(...)`
    - 启动欢迎语直接使用主菜单强探测渲染。

### 3.2 测试更新
- `tests/test_chat_menu_buttons.py`
  - 新增 `test_worker_main_keyboard_force_probe_even_with_explicit_state`
  - 新增 `test_refresh_worker_plan_mode_state_after_toggle_retries_until_changed`
  - 调整 PLAN MODE 切换文案断言与探测桩数量。
- `tests/test_plan_confirm_bridge.py`
  - 调整 Yes/No 断言：不再期待“主菜单状态已刷新”聊天消息。

## 4. 测试记录

### 4.1 修改前基线（相关回归）
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py tests/test_plan_confirm_bridge.py tests/test_task_description.py -k "worker_keyboard or terminal_snapshot or plan_confirm or push_model_choice or plan_mode or collaboration_mode"
# 27 passed, 160 deselected
```

### 4.2 修改后相关回归
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py tests/test_plan_confirm_bridge.py tests/test_task_description.py -k "worker_keyboard or terminal_snapshot or plan_confirm or push_model_choice or plan_mode or collaboration_mode"
# 28 passed, 161 deselected
```

### 4.3 修改后扩展回归
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py tests/test_plan_confirm_bridge.py tests/test_task_description.py
# 189 passed
```

### 4.4 修改后全量回归
```bash
PYTHONPATH=. pytest -q
# 617 passed, 6 warnings
```

## 5. 可验证资料（官方）
- Telegram ReplyKeyboardMarkup：
  https://core.telegram.org/bots/api#replykeyboardmarkup
- Telegram CallbackQuery：
  https://core.telegram.org/bots/api#callbackquery
- tmux `capture-pane` / `send-keys`：
  https://man7.org/linux/man-pages/man1/tmux.1.html
