# TASK_0133 request_user_input 自定义决策后主菜单恢复（DEVELOP）

## 1. 背景
- 用户反馈：点击 `D. 输入自定义决策` 并发送文本后，底部菜单栏会消失，未恢复常驻主菜单（如 `📋 任务列表`）。
- 已确认策略：
  - 成功提交后，直接在“决策摘要”消息恢复主菜单。
  - 自动提交失败后，保留“🔁 重试提交”按钮，并额外恢复主菜单。
  - 仅修 `request_user_input` 自定义输入链路，最小改动。

## 2. 实施变更

### 2.1 `bot.py`
- 函数：`_submit_request_input_session(...)`
  - 新增 `summary_reply_markup` 逻辑：
    - 当 `remove_reply_keyboard=True` 时，改为 `reply_markup=_build_worker_main_keyboard()`；
    - 不再使用 `ReplyKeyboardRemove()` 作为成功收口的最终菜单态。

- 函数：`_submit_request_input_session_with_auto_retry(...)`
  - 自动提交失败且 `remove_reply_keyboard=True` 时：
    - 保留原有“重试提交”Inline按钮消息；
    - 追加发送一条主菜单恢复消息，`reply_markup=_build_worker_main_keyboard()`；
    - 文案调整为：`已恢复主菜单，可点击“📋 任务列表”继续。`

### 2.2 `tests/test_request_user_input_flow.py`
- 更新 `test_request_input_custom_text_auto_submits`
  - 断言成功收口消息为 `ReplyKeyboardMarkup`，且包含 `WORKER_MENU_BUTTON_TEXT`。
- 重命名并更新 `test_request_input_custom_text_submit_failure_restores_main_keyboard`
  - 断言失败时第一条消息仍是“重试提交”Inline按钮；
  - 最后一条消息恢复主菜单（`ReplyKeyboardMarkup`，含 `WORKER_MENU_BUTTON_TEXT`）。

## 3. 回归结果

```bash
PYTHONPATH=. pytest -q tests/test_request_user_input_flow.py
# 15 passed

PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py tests/test_plan_progress.py tests/test_model_quick_reply.py tests/test_codex_jsonl_phase.py tests/test_request_user_input_flow.py tests/test_long_poll_mechanism.py tests/test_auto_compact.py
# 210 passed

PYTHONPATH=. pytest -q
# 596 passed, 6 warnings
```

## 4. 可验证资料（官方）
- ReplyKeyboardMarkup：  
  https://core.telegram.org/bots/api#replykeyboardmarkup
- ReplyKeyboardRemove：  
  https://core.telegram.org/bots/api#replykeyboardremove
- InlineKeyboardMarkup：  
  https://core.telegram.org/bots/api#inlinekeyboardmarkup

