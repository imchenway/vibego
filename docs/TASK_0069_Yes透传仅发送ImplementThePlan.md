# TASK_0069 Yes 透传仅发送 Implement the plan（DEVELOP）

## 1. 背景
- 现象：点击 Plan 收口确认 `Yes` 后，发送到 tmux 的文本被自动拼接了“强制规约”前缀。
- 目标：该场景必须仅发送 `Implement the plan.`，保证模型正确进入执行链路。

## 2. 决策（已按推荐执行）
1. 仅修复 Plan 确认 `Yes` 场景，不扩大到其他推送入口。
2. 采用“精确匹配固定文案”策略：仅当 prompt 为 `Implement the plan.` 时跳过前缀注入。
3. 保持原有 `_dispatch_prompt_to_model` 流程不变（会话绑定/监听/ack/重试逻辑不改）。

## 3. 代码改动

### 3.1 `bot.py`
- 新增常量：
  - `PLAN_IMPLEMENT_PROMPT = "Implement the plan."`
- 调整 `_prepend_enforced_agents_notice(raw_prompt)`：
  - 增加特判：当 prompt 为 `PLAN_IMPLEMENT_PROMPT` 时，原样返回，不追加 `ENFORCED_AGENTS_NOTICE`。
- 调整 `on_plan_confirm_callback(...)`：
  - `Yes` 回调调用 `_dispatch_prompt_to_model` 时改用 `PLAN_IMPLEMENT_PROMPT` 常量。

### 3.2 `tests/test_task_description.py`
- 新增用例：
  - `test_dispatch_prompt_skips_enforced_agents_notice_for_plan_implement_prompt`
  - 断言 `_dispatch_prompt_to_model(..., PLAN_IMPLEMENT_PROMPT, ...)` 最终写入 tmux 的内容严格等于 `Implement the plan.`。
- 扩展参数化用例：
  - `test_prepend_enforced_agents_notice_cases` 增加 `PLAN_IMPLEMENT_PROMPT` 场景，确保函数级行为稳定。

### 3.3 `tests/test_plan_confirm_bridge.py`
- 更新断言：
  - `Yes` 回调应以 `bot.PLAN_IMPLEMENT_PROMPT` 作为派发 prompt。

## 4. 测试结果
```bash
PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py -k "plan_confirm_yes_dispatches_implement_prompt or dispatch_prompt_injects_enforced_agents_notice or dispatch_prompt_skips_enforced_agents_notice_for_plan_implement_prompt or prepend_enforced_agents_notice_cases"
# 14 passed

PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py
# 133 passed
```

## 5. 风险与回滚
- 风险：若未来 `Yes` 按钮文案变更，需同步 `PLAN_IMPLEMENT_PROMPT` 常量，否则不会命中特判。
- 回滚：移除 `_prepend_enforced_agents_notice` 中该特判即可恢复原行为。

## 6. 可验证资料（官方）
- Telegram Bot API `sendMessage`：
  https://core.telegram.org/bots/api#sendmessage
- Telegram Bot API `CallbackQuery`：
  https://core.telegram.org/bots/api#callbackquery
- Telegram Bot API `InlineKeyboardMarkup`：
  https://core.telegram.org/bots/api#inlinekeyboardmarkup
