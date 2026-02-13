# TASK_0136 PLAN MODE 状态缓存与刷新一致性（DEVELOP）

## 1. 背景
- 用户反馈：点击 `Implement this plan` 的 `Yes/No` 后，主菜单 `🧭 PLAN MODE` 的 ON/OFF 文案不刷新；通常要在 Telegram 再触发 `/start` 才会更新。
- 目标：保证 PLAN MODE 文案与 tmux 实际状态一致，同时避免覆盖流程中的临时键盘（如“跳过/取消”“PLAN/YOLO 选择”）。

## 2. 方案决策（已落地）
1. 按 `TMUX_SESSION` 增加 PLAN MODE 全局缓存。
2. 关键触发点刷新缓存：`/start`、Plan `Yes/No`、PLAN/YOLO 选择确认/取消、`🧭 PLAN MODE`、`💻 终端实况`。
3. 流程中不强制回写主菜单；仅在“返回主菜单”时携带主键盘，避免覆盖临时键盘。
4. `💻 终端实况` 使用本次 `capture-pane` 输出强制校准并回写主菜单文案。

## 3. 代码改动

### 3.1 `bot.py`
- 新增缓存与辅助函数：
  - `WORKER_PLAN_MODE_STATE_CACHE`
  - `_worker_plan_mode_cache_key`
  - `_resolve_worker_plan_mode_state_from_output`
  - `_set_worker_plan_mode_state_cache`
  - `_get_worker_plan_mode_state_cache`
  - `_refresh_worker_plan_mode_state_cache`
  - `_refresh_worker_plan_mode_state_cache_async`
- 调整 `_probe_worker_plan_mode_state`：复用统一输出解析函数。
- 调整 `_build_worker_main_keyboard`：
  - 新增 `refresh_plan_mode_state` 参数；
  - 支持“显式状态回写缓存”与“按需刷新缓存”两种模式。
- 调整 `on_start`：发送欢迎语前强制刷新 PLAN MODE 缓存并回写主菜单。
- 调整 `_handle_terminal_snapshot_request`：
  - 读取终端实况时直接校准缓存；
  - 发送终端实况时附带主菜单（短消息与附件两种场景均回写）；
  - tmux 异常时回写 `unknown`。
- 调整 `_handle_worker_plan_mode_toggle_request`：切换前后均刷新缓存。
- 调整 `on_plan_confirm_callback`：
  - `Yes/No` 处理后刷新缓存并发送“主菜单状态已刷新”提示（带主菜单）。
- 调整 `on_task_push_model_choice`：
  - 选择 `PLAN/YOLO` 后刷新缓存但不回写主菜单（避免覆盖流程键盘）；
  - 取消时刷新缓存并回写主菜单。

### 3.2 测试更新
- `tests/test_chat_menu_buttons.py`
  - 终端实况成功用例新增断言：`reply_markup/attachment_reply_markup` 均回写主菜单。
  - 新增缓存用例：
    - `test_worker_main_keyboard_uses_cached_plan_mode_when_refresh_disabled`
    - `test_refresh_worker_plan_mode_state_cache_updates_cache`
- `tests/test_plan_confirm_bridge.py`
  - `DummyMessage` 增加 `answer` 以承接 Yes/No 后的主菜单回写消息；
  - fixture 清理 `WORKER_PLAN_MODE_STATE_CACHE`；
  - Yes/No 用例补充“主菜单状态已刷新”断言。

## 4. 测试记录

### 4.1 修改前基线
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py tests/test_plan_confirm_bridge.py tests/test_task_description.py -k "worker_keyboard or terminal_snapshot or plan_confirm or push_model_choice or push_model"
# 24 passed, 161 deselected
```

### 4.2 修改后相关回归
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py tests/test_plan_confirm_bridge.py tests/test_task_description.py -k "worker_keyboard or terminal_snapshot or plan_confirm or push_model_choice or push_model"
# 24 passed, 163 deselected
```

### 4.3 修改后扩展回归
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py tests/test_plan_confirm_bridge.py tests/test_task_description.py
# 187 passed
```

### 4.4 修改后全量回归
```bash
PYTHONPATH=. pytest -q
# 605 passed, 6 warnings
```

## 5. 可验证资料（官方）
- Telegram ReplyKeyboardMarkup：
  https://core.telegram.org/bots/api#replykeyboardmarkup
- Telegram ReplyKeyboardRemove：
  https://core.telegram.org/bots/api#replykeyboardremove
- Telegram CallbackQuery：
  https://core.telegram.org/bots/api#callbackquery
- tmux `capture-pane` / `send-keys`：
  https://man7.org/linux/man-pages/man1/tmux.1.html
