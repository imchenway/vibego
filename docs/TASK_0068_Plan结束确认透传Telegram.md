# TASK_0068 Plan 结束确认透传 Telegram（DEVELOP）

## 背景
- 现象：终端出现 `Implement this plan?` 交互，但 Telegram 侧看不到确认入口，导致用户误以为卡在 PLAN。
- 目标：在不改现有业务主流程的前提下，将 Plan 收口确认透传为 Telegram 按钮。

## 本次实现（最小改造）
1. 新增 Plan 确认会话状态：
   - `PLAN_CONFIRM_SESSIONS`
   - `CHAT_ACTIVE_PLAN_CONFIRM_TOKENS`
2. 新增 Plan 确认按钮协议：
   - 回调前缀：`pcf:`
   - 动作：`yes/no`
3. 新增收口识别逻辑：
   - 当模型输出包含 `<proposed_plan>...</proposed_plan>` 时，发送 Telegram 确认按钮。
4. 新增按钮回调处理：
   - `Yes`：自动推送 `Implement the plan.` 给模型并切入执行链路。
   - `No`：保持 Plan 模式，仅关闭本次确认按钮。
5. 幂等与清理：
   - 同一 `chat+session` 不重复发送确认按钮。
   - 新提示词入模时，清理旧确认状态，避免跨轮次污染。

## 变更文件
- `bot.py`
  - 新增 Plan 确认常量、会话结构、回调构建/解析、发送与处理逻辑。
  - 在 `_deliver_pending_messages` 中接入 `<proposed_plan>` 触发点。
  - 在 `_dispatch_prompt_to_model` 中增加旧确认状态清理。
- `tests/test_plan_confirm_bridge.py`（新增）
  - 覆盖收口触发、非收口不触发、Yes/No 回调行为。
- `tests/test_request_user_input_flow.py`
  - 补充运行态清理，防止新状态污染用例。

## 测试结果
```bash
PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_request_user_input_flow.py
# 15 passed

PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py tests/test_plan_progress.py tests/test_model_quick_reply.py tests/test_codex_jsonl_phase.py tests/test_request_user_input_flow.py tests/test_long_poll_mechanism.py tests/test_auto_compact.py
# 188 passed
```

## 备注
- 按需求采用“无限等待用户处理”策略：不做超时自动取消/重发。
- 本次仅做最小闭环，不额外引入日志增强与超时策略变更。
