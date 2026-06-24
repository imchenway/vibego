# TASK_20260624_001 request_user_input 未答二次确认提交

## 1. 背景与目标

### 现象

用户通过截图指出：Codex 终端在存在未回答问题时会出现原生确认菜单：

```text
Submit with unanswered questions?
1 unanswered question

1. Proceed  Submit with 1 unanswered question.
2. Go back  Return to the first unanswered question.
```

但 vibego 的 Telegram `request_user_input` 交互此前不支持这个能力：题目按钮只包含选项与“输入自定义决策”，不提供“提交/跳过未答”入口；即使触发历史 `submit` callback，也会被拦截为“请先完成全部题目后再提交”。

### 目标

在 Telegram 侧补齐“未答题提交前二次确认”：

1. 每道 `request_user_input` 题目底部增加“提交/跳过未答”按钮。
2. 如果仍有未答题，先发送 Telegram 二次确认，展示 `Proceed / Go back` 两项。
3. 用户选择 `Proceed` 后，将已答内容推回模型，未答题不写入 `answers`，但在 `question_context` 中标记 `selected_kind=unanswered`。
4. 用户选择 `Go back` 后，返回首个未答题继续作答，不向 tmux 投递任何提示词。

### 本次不做

- 不改 `answers` / `ask_user` 既有 JSON schema。
- 不改变业务提示默认 queued 的发送方式。
- 不新增数据库表、字段、迁移。
- 不新增依赖、不改构建链、不改 CI。
- 不把终端 TUI 原生菜单截图解析为状态源；本次只补 Telegram 自身交互能力。

## 2. 当前实现证据

| 事实 | 证据 |
| --- | --- |
| `request_user_input` 使用 `RequestInputSession` 保存题目、答案、当前下标与提交状态 | `bot.py`（锚点：`class RequestInputSession`） |
| 题目按钮由 `_build_request_input_keyboard` 构造 | `bot.py`（锚点：`_build_request_input_keyboard`） |
| 提交动作常量已有 `REQUEST_INPUT_ACTION_SUBMIT` | `bot.py`（锚点：`REQUEST_INPUT_ACTION_SUBMIT = "submit"`） |
| 提交回推统一走 `_submit_request_input_session_with_auto_retry` | `bot.py`（锚点：`_submit_request_input_session_with_auto_retry`） |
| 未答题上下文已可通过 `question_context` 表达为 `selected_kind=unanswered` | `bot.py`（锚点：`_build_request_input_question_context`） |
| 历史测试曾要求键盘不展示提交按钮、未答完提交必须阻断 | `tests/test_request_user_input_flow.py`（旧锚点：`test_request_input_keyboard_hides_submit_button`、`test_request_input_submit_requires_all_answers`） |

## 3. 方案

### 推荐方案（已按用户确认实施）

采用 Telegram 二次确认，而不是直接允许跳过：

- 新增二次确认 callback：
  - `REQUEST_INPUT_ACTION_CONFIRM_SUBMIT`：确认带未答题提交。
  - `REQUEST_INPUT_ACTION_GO_BACK_UNANSWERED`：返回首个未答题。
- 题目键盘新增“⏭ 提交/跳过未答”。
- 点击提交且仍有未答题时，不直接投递模型，只发送确认消息。
- 确认消息文案与 Codex 原生菜单语义对齐：`Submit with unanswered questions? / Proceed / Go back`。
- `Proceed` 继续复用 `_submit_request_input_session_with_auto_retry`，保持 queued 业务提示与 session ack 逻辑。
- `Go back` 只调用 `_send_request_input_question` 发送首个未答题，并锁定确认消息按钮，避免重复点击噪音。

### 方案对比

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| Telegram 二次确认 | 与 Codex 原生体验一致；误触风险低；用户能显式选择继续或返回 | 比直接提交多一次点击 | 采用 |
| 直接允许跳过 | 操作最快，改动更少 | 容易误触，把不完整决策发给模型 | 不采用 |
| 只同步终端菜单 | 不改 Telegram 交互 | 用户仍无法在 Telegram 完成未答提交 | 不采用 |

## 4. 受影响目录与边界

| 范围 | 是否影响 | 说明 |
| --- | ---: | --- |
| `bot.py` | 是 | 新增提交确认/返回未答题 action、确认文案与键盘、未答提交回调逻辑。 |
| `tests/test_request_user_input_flow.py` | 是 | 覆盖未答提交确认、Proceed 投递、Go back 返回、键盘 callback 数据。 |
| `docs/` | 是 | 新增本任务文档。 |
| `AGENTS.md` | 是 | 增加仓库事实与证据锚点。 |
| SQLite/DB | 否 | 不新增表字段，不改迁移。 |
| tmux/模型 CLI | 否 | 仍通过既有 `_dispatch_prompt_to_model` 投递；不改 tmux 发送实现。 |
| Telegram 命令菜单 | 否 | 不改 `/plan`、`/goal`、`/status`、命令中心。 |
| 构建/依赖/CI | 否 | 不新增依赖，不改构建配置。 |

## 5. 契约变更

### Telegram 交互契约

1. 每个 `request_user_input` 题目消息按钮增加：`⏭ 提交/跳过未答`。
2. 点击该按钮且存在未答题时，发送二次确认消息：
   - `1. Proceed`
   - `2. Go back`
3. `Proceed`：允许提交未答题。
4. `Go back`：发送首个未答题。
5. 仍保持：仅会话发起人可操作；过期、取消、已提交会话仍按既有 fail-soft 提示。

### 回推模型 prompt 契约

- `answers`：只包含已答题，不为未答题生成占位答案。
- `question_context`：保留全部题目；未答题使用：

```json
{"selected_kind":"unanswered","selected_option_label":"","selected_option_description":""}
```

- `send_mode`：继续通过 `_resolve_business_prompt_send_mode()`，Codex/Copilot 默认 queued。

### 数据库与 API

- 无数据库变更。
- 无 HTTP API 或外部接口变更。

## 6. 测试矩阵

| 用例 | 覆盖点 | 预期 |
| --- | --- | --- |
| `test_request_input_keyboard_includes_submit_button_for_unanswered_confirmation` | 题目键盘新增提交入口 | 按钮包含“提交/跳过未答”。 |
| `test_request_input_submit_with_unanswered_requires_telegram_confirmation` | 未答提交第一步 | 不投递模型；发送 Proceed / Go back 二次确认。 |
| `test_request_input_submit_confirm_proceed_dispatches_unanswered_context` | Proceed | 只提交已答 `answers`；未答题在 `question_context` 标记 `unanswered`；业务提示 queued。 |
| `test_request_input_submit_confirm_go_back_sends_first_unanswered_question` | Go back | 不投递模型；返回首个未答题；确认按钮被锁定。 |
| `test_request_input_keyboard_includes_question_index_in_callback_data` | callback 兼容 | 选项/自定义保留题目下标，submit 使用会话级 callback。 |
| `tests/test_request_user_input_flow.py` | request_input 全链路 | 27 条通过。 |

## 7. 实施顺序

1. 读取 AGENTS 与相关历史任务文档。
2. 运行 baseline：`PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py`。
3. 先修改/新增未答提交相关测试并确认红灯。
4. 在 `bot.py` 最小实现二次确认 action、文案、键盘与回调。
5. 运行聚焦测试与 request_input 全文件测试。
6. 更新 docs 与 AGENTS 证据。
7. 执行受影响测试、语法检查、doctor 与全量 pytest，记录结果。

## 8. 风险与回滚

### 风险

- Telegram 按钮增加后，题目消息更长；但只新增一个底部按钮，不增加复杂布局。
- 用户可重复点击原题目上的“提交/跳过未答”产生多个确认消息；当前确认消息自身会在 Proceed/Go back 后锁定，后续如需进一步降噪可在原题 submit 点击时也锁定原题按钮，但会影响用户返回当前题继续选项的便利性。
- 未答题提交给模型后，模型需要正确理解 `question_context.selected_kind=unanswered`；这是既有上下文契约的自然扩展。

### 回滚

1. 移除 `REQUEST_INPUT_ACTION_CONFIRM_SUBMIT` 与 `REQUEST_INPUT_ACTION_GO_BACK_UNANSWERED`。
2. 从 `_build_request_input_keyboard` 移除“提交/跳过未答”按钮。
3. 将 `REQUEST_INPUT_ACTION_SUBMIT` 分支恢复为未答题时直接提示“请先完成全部题目后再提交”。
4. 回滚本任务新增/调整测试与 AGENTS 事实。
5. 无数据库或配置回滚。

## 9. 验证记录

### Baseline

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py
# 25 passed in 2.31s
```

### TDD 红灯

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py -k "submit_with_unanswered_requires_telegram_confirmation or confirm_proceed or confirm_go_back or keyboard_includes_submit_button"
# 4 failed, 23 deselected
# 失败原因：旧实现无二次确认 action、未答 submit 直接阻断、键盘无提交按钮。
```

### 当前绿灯

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py -k "submit_with_unanswered_requires_telegram_confirmation or confirm_proceed or confirm_go_back or keyboard_includes_submit_button"
# 4 passed, 23 deselected, 2 warnings in 0.11s
```

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py
# 27 passed in 2.29s
```

> 最终验证以任务最终回复中的最新命令输出为准。

### 最终验证补充

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py -k "submit_with_unanswered_requires_telegram_confirmation or confirm_proceed or confirm_go_back or keyboard_includes_submit_button"
# 4 passed, 23 deselected in 0.04s
```

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py tests/test_plan_confirm_bridge.py tests/test_tmux_send_line.py
# 61 passed in 2.60s
```

```bash
PYTHONPATH=. python3.11 -m py_compile bot.py
# exit 0
```

```bash
PYTHONPATH=. python3.11 -m vibego_cli doctor
# exit 0；python_ok=true；dependencies=[]；config_root=/Users/david/.config/vibego
```

```bash
PYTHONPATH=. python3.11 -m pytest -q
# 3 failed, 989 passed, 6 warnings in 40.48s
# 失败仍为既有 tests/test_agents_template_migration.py 三项：
# - test_enforced_notice_points_to_agents_md
# - test_enforced_notice_adds_user_requirement_header_before_prompt
# - test_agents_template_requires_comet_for_complex_workflows
```
