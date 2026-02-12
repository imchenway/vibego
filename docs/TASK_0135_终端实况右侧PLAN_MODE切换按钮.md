# TASK_0135 终端实况右侧增加 PLAN MODE 切换按钮（DEVELOP）

## 1. 背景与目标
- 用户诉求：在 Worker 主键盘中，把 `PLAN MODE` 开关放到 `💻 终端实况` 右侧，并显示当前会话状态。
- 关键约束（已确认）：
  1. 切换方式复用现有实现：发送 `BTab`。
  2. 仅 `Plan mode` 会在终端底部显示；`Default mode` 不显示。
  3. 无 `Plan mode` 标识且 tmux 可读时，视为 `OFF`。
  4. `UNKNOWN` 时仍尝试切换一次再刷新。
  5. 每次回主菜单都要刷新按钮状态。

## 2. 主要改动

### 2.1 `bot.py`
- 新增常量：
  - `WORKER_PLAN_MODE_BUTTON_PREFIX`
  - `WORKER_PLAN_MODE_BUTTON_TEXT_ON/OFF/UNKNOWN`
  - `WORKER_PLAN_MODE_TOGGLE_KEY`
  - `WORKER_PLAN_MODE_PROBE_LINES`
  - `WORKER_PLAN_MODE_PROBE_TIMEOUT_SECONDS`
- 新增探测函数：
  - `_probe_worker_plan_mode_state() -> Literal["on","off","unknown"]`
  - 逻辑：  
    - 匹配 `Plan mode` => `on`  
    - 其余（无标识/Default）=> `off`  
    - tmux 失败 => `unknown`
- 更新主键盘函数：
  - `_build_worker_main_keyboard(...)` 第二行改为：
    - `[💻 终端实况] [🧭 PLAN MODE: ON/OFF/?]`
  - 默认每次构建键盘都会实时探测状态（满足“每次回主菜单刷新”）。
- 新增按钮处理链路：
  - `_handle_worker_plan_mode_toggle_request(message)`
  - `@router.message(F.text.regexp(r"^🧭 PLAN MODE:"))`
  - 行为：发送一次 `WORKER_PLAN_MODE_TOGGLE_KEY`（默认 `BTab`），再探测并回显结果。

### 2.2 测试 `tests/test_chat_menu_buttons.py`
- 更新原有断言：
  - 主键盘第二行从 1 个按钮改为 2 个按钮。
  - 增加 PLAN 按钮文案断言。
- 新增测试：
  - `test_probe_worker_plan_mode_state_returns_off_when_no_mode_marker`
  - `test_probe_worker_plan_mode_state_returns_unknown_on_tmux_error`
  - `test_worker_plan_mode_button_toggles_and_refreshes_keyboard`
  - `test_worker_plan_mode_button_unknown_still_attempts_toggle`

## 3. 测试记录

### 3.1 修改前（相关基线）
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py
# 175 passed in 3.11s
```

### 3.2 修改后（相关回归）
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py tests/test_task_description.py
# 179 passed, 2 warnings in 3.73s
```

### 3.3 修改后（全量回归）
```bash
BOT_TOKEN=dummy-token PYTHONPATH=. pytest -q
# 599 passed, 6 warnings in 10.39s
```

## 4. 可验证资料（官方）
- tmux 手册（`send-keys` / `capture-pane`）：  
  https://man7.org/linux/man-pages/man1/tmux.1.html
- Telegram Bot API - ReplyKeyboardMarkup：  
  https://core.telegram.org/bots/api#replykeyboardmarkup

