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

---

## 8. 四次修复（2026-03-04）：Plan Yes 并发点击导致重复发送

### 8.1 问题现象

- 用户反馈在同一会话中，Telegram 会重复收到“💭 codex思考中，正在持续监听模型响应结果中”。
- 日志证据（同一 session 同秒重复）：
    - `~/.config/vibego/logs/codex/hyphamall/run_bot.log`（锚点：`23:46:27 ... ack sent` 连续两条）
    - `~/.config/vibego/logs/codex/hyphamall/run_bot.log`（锚点：`23:46:47 ... 检测到待发送的模型事件` 连续两条）

### 8.2 根因判断

- `on_plan_confirm_callback` 在 Yes 分支中，`_dispatch_prompt_to_model(...)` 与 `_drop_plan_confirm_session(token)`
  之间存在并发窗口；
- 同一 token 并发点击可同时通过校验并触发两次派发，导致重复 ack 与重复监听。

### 8.3 代码改动

- `bot.py`
    1) 新增并发幂等状态：
        - `PLAN_CONFIRM_PROCESSING_TOKENS: set[str]`
    2) 新增令牌函数：
        - `_claim_plan_confirm_processing_token(token)`
        - `_release_plan_confirm_processing_token(token)`
    3) `on_plan_confirm_callback(...)`（Yes）：
        - 派发前先 claim，失败则回包：`正在处理中，请勿重复点击。`
        - 派发流程置于 `try/finally`，确保 release；
    4) `_drop_plan_confirm_session(token)`：
        - 同步 `discard` processing token，避免脏状态残留。

### 8.4 测试改动（TDD）

- 先红：
    - `tests/test_plan_confirm_bridge.py::test_plan_confirm_yes_is_idempotent_under_concurrent_clicks`
    - 改代码前失败：并发触发出现两次 dispatch。
- 再绿：
    - 新增用例通过，确保并发点击仅一次派发，另一条返回“正在处理中，请勿重复点击。”
- 同步清理：
    - `tests/test_plan_confirm_bridge.py` fixture 增加 `PLAN_CONFIRM_PROCESSING_TOKENS.clear()`。

### 8.5 Baseline Repair（本次触发）

- 全量回归首次执行发现基线失败：
    - `tests/test_agents_template_migration.py::test_enforced_notice_points_to_agents_template`
- 原因：当前 `bot.ENFORCED_AGENTS_NOTICE` 文案已明确要求读取 `当前根目录 AGENTS.md`。
- 修复：
    - 将该用例更新为 `test_enforced_notice_points_to_agents_md`，断言与当前强制规约一致。

### 8.6 回归结果

```bash
# TDD 红灯（先失败）
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py -k "idempotent_under_concurrent_clicks"
# 1 failed

# 受影响用例
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py tests/test_agents_template_migration.py
# 11 passed

# 全量回归（两轮一致）
python3.11 -m pytest -q
# 631 passed, 6 warnings

python3.11 -m pytest -q
# 631 passed, 6 warnings
```

### 8.7 风险与回滚

- 风险：
    - 幂等状态为内存态，进程重启后不保留（可接受，属瞬时交互态）。
- 回滚点：
    - 回滚 `bot.py` 中 `PLAN_CONFIRM_PROCESSING_TOKENS` 与 claim/release 逻辑；
    - 回滚 `tests/test_plan_confirm_bridge.py` 并发幂等用例；
    - 如需恢复旧断言，再回滚 `tests/test_agents_template_migration.py` 对应测试。
