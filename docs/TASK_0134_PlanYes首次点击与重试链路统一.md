# TASK_0134 Plan Yes 首次点击与重试链路统一（DEVELOP）

## 1. 背景
- 用户反馈：点击 `1. Yes, implement this plan` 后，tmux 内出现 PLAN mode 闪跳，最终仍在 PLAN MODE 并报错。
- 但点击“重试进入开发”后可立刻退出 PLAN 并开始开发，说明重试链路策略有效。
- 目标：让 Yes 首次链路与重试链路行为对齐，提升一次成功率。

## 2. 决策落地
1. 修复范围：仅改 Plan Yes 首次点击链路（最小改动）。
2. 退出 Plan 策略：Yes 首次链路复用重试链路同款按键序列与轮次。
3. Yes 提示词：改为仅发送 `Implement the plan.`。
4. 测试范围：新增/更新 `tests/test_plan_confirm_bridge.py` 一致性断言。

## 3. 代码改动

### 3.1 `bot.py`
- 位置：`on_plan_confirm_callback(...)` 的 Yes 分支。
- 调整：
  - prompt 从 `PLAN_IMPLEMENT_EXEC_PROMPT` 改为 `PLAN_IMPLEMENT_PROMPT`；
  - 补充与重试链路一致的参数：
    - `force_exit_plan_ui_key_sequence=_build_plan_develop_retry_exit_plan_key_sequence()`
    - `force_exit_plan_ui_max_rounds=PLAN_DEVELOP_RETRY_EXIT_PLAN_MAX_ROUNDS`

### 3.2 `tests/test_plan_confirm_bridge.py`
- 更新 `test_plan_confirm_yes_dispatches_implement_prompt`：
  - 断言 Yes 链路发送 `PLAN_IMPLEMENT_PROMPT`；
  - 断言 Yes 链路携带与重试链路一致的退出 Plan 参数（按键序列 + 轮次）。

## 4. 回归结果

```bash
PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py
# 7 passed

PYTHONPATH=. pytest -q tests/test_task_description.py tests/test_plan_confirm_bridge.py
# 149 passed

PYTHONPATH=. pytest -q
# 596 passed, 6 warnings
```

## 5. 可验证资料（官方）
- Telegram CallbackQuery：  
  https://core.telegram.org/bots/api#callbackquery
- Telegram InlineKeyboardMarkup：  
  https://core.telegram.org/bots/api#inlinekeyboardmarkup
- tmux 手册：  
  https://man7.org/linux/man-pages/man1/tmux.1.html

---

## 6. 二次优化（2026-02-11）

### 6.1 变更背景
- 用户反馈：`Implement the plan.` 触发后若中断，系统会自动再发一条 `进入开发阶段\ndevelop`，希望去掉这次自动发送。

### 6.2 决策
1. 禁用 Yes 链路自动恢复补发（不再自动发送 `PLAN_RECOVERY_DEVELOP_PROMPT`）。
2. 保留失败后的“🔁 重试进入开发”按钮，交由用户手动触发重试。

### 6.3 实现点
- `bot.py`：`_monitor_plan_execution_and_recover(...)`
  - 删除自动恢复循环（`_dispatch_prompt_to_model(...PLAN_RECOVERY_DEVELOP_PROMPT...)`）。
  - 失败后直接下发重试按钮提示。
- `tests/test_plan_confirm_bridge.py`
  - 新增测试：失败时不触发自动补发，仅下发重试按钮。
  - 新增测试：成功进入 develop 时不下发重试按钮。
