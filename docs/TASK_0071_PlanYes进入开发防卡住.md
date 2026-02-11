# TASK_0071 Plan Yes 进入开发防卡住（DEVELOP）

## 1. 背景
- 现象：在 Telegram 点击 Plan 收口按钮 `Yes` 后，原逻辑仅发送 `Implement the plan.`，在默认 PLAN 规约下存在继续停留在 PLAN 的概率。
- 目标：提高 Yes 链路进入开发阶段的成功率，并在失败时提供自动恢复与可见重试入口，避免“无感卡住”。

## 2. 本次决策落地
1. Yes 首发提示词改为：`develop\nImplement the plan.`。
2. 增加执行态检测（短窗口轮询会话增量信号）。
3. 检测失败后自动恢复一次：补发 `进入开发阶段\ndevelop`。
4. 自动恢复仍失败时，下发“🔁 重试进入开发”按钮。
5. 重试按钮点击后再次发送 `develop\nImplement the plan.`，并重新启动执行态检测。

## 3. 代码改动

### 3.1 常量与策略（`bot.py`）
- 新增执行提示词常量：
  - `PLAN_IMPLEMENT_EXEC_PROMPT = "develop\\nImplement the plan."`
  - `PLAN_RECOVERY_DEVELOP_PROMPT = "进入开发阶段\\ndevelop"`
- 新增检测参数：
  - `PLAN_EXECUTION_SIGNAL_TIMEOUT_SECONDS`
  - `PLAN_EXECUTION_SIGNAL_POLL_INTERVAL_SECONDS`
  - `PLAN_EXECUTION_AUTO_RECOVERY_MAX`
- 新增信号关键词：
  - `PLAN_EXECUTION_DEVELOP_KEYWORDS`
  - `PLAN_EXECUTION_STILL_PLAN_KEYWORDS`
- `_prepend_enforced_agents_notice(...)` 扩展白名单：上述开发切换提示词原样透传，不注入强制前缀。

### 3.2 新增运行态会话与任务状态（`bot.py`）
- 新增 `PlanDevelopRetrySession`。
- 新增全局状态：
  - `PLAN_DEVELOP_RETRY_SESSIONS`
  - `CHAT_ACTIVE_PLAN_DEVELOP_RETRY_TOKENS`
  - `CHAT_PLAN_EXECUTION_MONITORS`

### 3.3 新增核心能力（`bot.py`）
- 回调协议：
  - `PLAN_DEVELOP_RETRY_CALLBACK_PREFIX = "pdr:"`
  - `PLAN_DEVELOP_RETRY_ACTION_RETRY = "retry"`
- 新增执行态检测与恢复函数：
  - `_infer_plan_execution_signal`
  - `_initial_plan_execution_cursor`
  - `_scan_plan_execution_signal`
  - `_wait_for_plan_execution_signal`
  - `_monitor_plan_execution_and_recover`
  - `_schedule_plan_execution_monitor`
  - `_send_plan_develop_retry_prompt`

### 3.4 既有链路改造（`bot.py`）
- `on_plan_confirm_callback(...)`
  - `Yes` 从发送 `PLAN_IMPLEMENT_PROMPT` 改为 `PLAN_IMPLEMENT_EXEC_PROMPT`。
  - 推送成功后启动执行态监控任务。
- 新增 `on_plan_develop_retry_callback(...)`
  - 处理“重试进入开发”按钮并复用监控逻辑。
- `_dispatch_prompt_to_model(...)`
  - 新增参数 `reset_plan_execution_monitor`（默认 `True`），用于避免监控协程内部触发恢复时自我取消。
  - 新提示词入模时，清理旧的重试会话与执行态监控状态。

## 4. 测试改动
- `tests/test_plan_confirm_bridge.py`
  - 更新 Yes 断言为发送 `PLAN_IMPLEMENT_EXEC_PROMPT`。
  - 新增“重试进入开发”按钮回调用例。
  - 扩展 fixture，清理重试会话与监控任务状态。
- `tests/test_task_description.py`
  - `test_dispatch_prompt_skips_enforced_agents_notice_for_plan_implement_prompt` 参数化，覆盖：
    - `PLAN_IMPLEMENT_PROMPT`
    - `PLAN_IMPLEMENT_EXEC_PROMPT`
    - `PLAN_RECOVERY_DEVELOP_PROMPT`
  - `_prepend_enforced_agents_notice` 参数化样例同步新增两个提示词。
- `tests/test_request_user_input_flow.py`
  - fixture 补充清理新状态，避免跨用例污染。

## 5. 回归结果
```bash
PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_request_user_input_flow.py tests/test_task_description.py -k "plan_confirm or plan_develop_retry or dispatch_prompt_skips_enforced_agents_notice_for_plan_implement_prompt or prepend_enforced_agents_notice_cases"
# 21 passed, 131 deselected

PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py tests/test_plan_progress.py tests/test_model_quick_reply.py tests/test_codex_jsonl_phase.py tests/test_request_user_input_flow.py tests/test_long_poll_mechanism.py tests/test_auto_compact.py
# 198 passed

PYTHONPATH=. pytest -q
# 584 passed, 6 warnings
```

## 6. 风险与说明
- 执行态检测基于关键词，存在误判概率；本次通过“自动恢复 + 按钮重试”兜底。
- 本次未改外层编排协议（`<collaboration_mode>`），属于最小改造闭环。

## 7. 可验证资料（官方）
- Telegram CallbackQuery：
  https://core.telegram.org/bots/api#callbackquery
- Telegram InlineKeyboardMarkup：
  https://core.telegram.org/bots/api#inlinekeyboardmarkup
- tmux `send-keys` / `capture-pane`（手册）：
  https://man7.org/linux/man-pages/man1/tmux.1.html
- OpenAI Chat/Developer 指令层级（官方文档）：
  https://platform.openai.com/docs/guides/chat-completions
