# TASK_0140 PLAN MODE 按钮状态与终端一致性修复（DEVELOP）

## 1. 背景
- 用户反馈：点击 `🧭 PLAN MODE` 后，终端已在 ON/OFF 间切换，但 Telegram 主菜单按钮与提示偶发持续显示 `ON`。
- 本次范围：仅修复 `🧭 PLAN MODE` 按钮链路，不改 Plan Yes/No、终端实况、自动恢复链路。
- 验收口径：按钮显示与终端状态 **100% 一致**。

## 2. 决策落地
1. Worker PLAN MODE 状态解析改为“尾部状态行优先”，避免历史消息里的 `Plan mode` 文案误判。
2. 点击按钮后的回复键盘使用本次切换探测结果回写，避免再次强探测把新状态覆盖成旧状态。
3. 保留 1 条用户可见提示：`主菜单已刷新，当前 PLAN MODE：X`。

## 3. 代码变更

### 3.1 `bot.py`
- 新增配置：
  - `WORKER_PLAN_MODE_STATUS_TAIL_LINES`（默认 `8`）
- 新增状态行正则：
  - `WORKER_PLAN_MODE_STATUS_LINE_RE`
- 调整 `_resolve_worker_plan_mode_state_from_output(...)`：
  - 只扫描尾部若干行；
  - 仅接受“状态行特征”匹配（如 `·` 分隔、百分比、或纯 `Plan/Default mode` 行）；
  - 无有效模式标识时返回 `off`（保持原口径）。
- 调整 `_handle_worker_plan_mode_toggle_request(...)`：
  - 回复键盘改为 `_build_worker_main_keyboard(plan_mode_state=after_state, refresh_plan_mode_state=False)`；
  - 统一使用本次切换后的状态回写按钮，避免二次探测抖动。

### 3.2 `tests/test_chat_menu_buttons.py`
- 新增：
  - `test_probe_worker_plan_mode_state_ignores_non_status_plan_mode_phrase`
  - `test_probe_worker_plan_mode_state_detects_status_line_plan_mode`
- 调整：
  - `test_worker_plan_mode_button_toggles_and_refreshes_keyboard`
    - 将探测序列从 3 次收敛为 2 次，确保按钮回写不再触发额外探测覆盖。

## 4. 测试记录

### 4.1 修改前基线（相关）
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py -k "worker_plan_mode_button or probe_worker_plan_mode_state or worker_main_keyboard or refresh_worker_plan_mode_state_after_toggle"
# 7 passed, 34 deselected
```

### 4.2 修改后相关回归
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py -k "worker_plan_mode_button or probe_worker_plan_mode_state or worker_main_keyboard or refresh_worker_plan_mode_state_after_toggle"
# 9 passed, 34 deselected
```

### 4.3 修改后扩展回归
```bash
PYTHONPATH=. pytest -q tests/test_chat_menu_buttons.py tests/test_plan_confirm_bridge.py tests/test_task_description.py -k "worker_keyboard or terminal_snapshot or plan_confirm or push_model_choice or plan_mode or collaboration_mode"
# 30 passed, 161 deselected
```

### 4.4 修改后全量回归
```bash
PYTHONPATH=. pytest -q
# 619 passed, 6 warnings
```

## 5. 可验证资料（官方）
- Telegram ReplyKeyboardMarkup：  
  https://core.telegram.org/bots/api#replykeyboardmarkup
- Telegram CallbackQuery：  
  https://core.telegram.org/bots/api#callbackquery
- tmux `capture-pane` / `send-keys`：  
  https://man7.org/linux/man-pages/man1/tmux.1.html
