# TASK_0070 request_user_input 防漏选与交互重构（DEVELOP）

## 1. 背景
- 现状痛点：用户在多题按钮交互中容易不确定“当前点的是哪一题”，并且重复点击同题按钮时可能误以为已经切换到其他题。
- 目标：提升题目归属清晰度，防止漏选与误判；保持“全部作答后自动提交”。

## 2. 本次决策落地
1. 逐题独立消息（每题完整文案 + 本题选项）。
2. 仅保留按钮：A/B/C... + `D. 输入自定义决策`。
3. 题目按模型返回顺序出题。
4. 题目一旦作答即锁定，不可修改。
5. 全部题目作答后自动提交（无二次确认页）。
6. 自动提交失败自动重试 1 次，仍失败下发“重试提交”按钮。

## 3. 代码改动

### 3.1 会话状态扩展（`bot.py`）
- `RequestInputSession` 新增：
  - `question_message_ids: Dict[str, int]`（题目与消息 ID 绑定）
  - `submission_state: str`（`idle/submitting/failed/submitted`）
  - `submit_retry_count: int`

### 3.2 回调协议增强（`bot.py`）
- `_build_request_input_callback_data` 支持多数值参数。
- `_parse_request_input_callback_data` 支持解析多段数字参数。
- 选项按钮回调从旧格式 `opt:<option>` 升级为 `opt:<question_index>:<option_index>`。
- 新增动作：`REQUEST_INPUT_ACTION_RETRY_SUBMIT`。

### 3.3 题目渲染与按钮重构（`bot.py`）
- `_render_request_input_question_text(session, question_index=...)`：按指定题目渲染完整文案。
- `_build_request_input_keyboard(session, question_index=...)`：仅渲染“选项 + 自定义决策”。
- `_send_request_input_question(...)`：每题独立发送消息并记录 `message_id`。

### 3.4 防重复/防误点控制（`bot.py`）
- 新增题目解析辅助：
  - `_resolve_request_input_question_index_for_callback`
  - `_resolve_request_input_option_selection`
  - `_find_request_input_question_index_by_message_id`
- 同题重复点击时返回“已锁定不可修改”，不覆盖答案。
- 尝试移除已完成题目按钮，减少重复点击噪音。

### 3.5 自动提交与失败恢复（`bot.py`）
- 新增 `_submit_request_input_session_with_auto_retry(...)`：
  - 自动提交失败后按配置重试 1 次。
  - 仍失败则下发“🔁 重试提交”按钮。
- 新增 `_build_request_input_retry_submit_keyboard(...)` 与 `_send_request_input_retry_prompt(...)`。

### 3.6 自定义输入流程（`bot.py`）
- 自定义输入取消后返回当前题（未作答）。
- 自定义文本提交后同样进入“锁题 + 下一题/自动提交”主流程。

## 4. 测试改动
- 更新：`tests/test_request_user_input_flow.py`
- 关键覆盖新增：
  1) 新回调格式包含题目索引。
  2) 同题重复点击锁定拦截。
  3) 自动提交失败后展示“重试提交”按钮。
  4) 旧提交入口未答完时仅提示，不再跳题。
  5) 自定义输入提示文案包含题号。
  6) request_user_input 提交成功后不再发送“中间工具结果代码块”回显。

## 5. 回归结果
```bash
PYTHONPATH=. pytest -q tests/test_request_user_input_flow.py
# 14 passed

PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py tests/test_plan_progress.py tests/test_model_quick_reply.py tests/test_codex_jsonl_phase.py tests/test_request_user_input_flow.py tests/test_long_poll_mechanism.py tests/test_auto_compact.py
# 193 passed

PYTHONPATH=. pytest -q
# 579 passed, 6 warnings
```

## 6. 可验证资料（官方）
- Telegram InlineKeyboardMarkup：
  https://core.telegram.org/bots/api#inlinekeyboardmarkup
- Telegram CallbackQuery：
  https://core.telegram.org/bots/api#callbackquery
- Telegram EditMessageReplyMarkup：
  https://core.telegram.org/bots/api#editmessagereplymarkup

## 7. 2026-02-11 追加变更（去除重复回显）

### 7.1 变更目标
- 用户已明确要求：保留“✅ 已推送到模型 + 决策摘要”，去掉其后重复的“已推送到模型 + request_user_input 工具结果代码块”。

### 7.2 代码改动
- 文件：`bot.py`
- 位置：`_submit_request_input_session(...)`
- 调整内容：
  - 保留：模型派发、决策摘要发送、session ack。
  - 删除：`_send_model_push_preview(...)` 在 request_user_input 提交成功链路中的调用。

### 7.3 测试改动
- 文件：`tests/test_request_user_input_flow.py`
- 更新 3 个用例断言为“**不再发送中间推送代码块预览**”：
  1) `test_request_input_submit_dispatches_structured_payload`
  2) `test_request_input_option_auto_submits_when_all_answered`
  3) `test_request_input_custom_text_auto_submits`

### 7.4 回归结果
```bash
PYTHONPATH=. pytest -q tests/test_request_user_input_flow.py
# 14 passed, 2 warnings

PYTHONPATH=. pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py tests/test_plan_progress.py tests/test_model_quick_reply.py tests/test_codex_jsonl_phase.py tests/test_request_user_input_flow.py tests/test_long_poll_mechanism.py tests/test_auto_compact.py
# 193 passed

PYTHONPATH=. pytest -q
# 579 passed, 6 warnings
```

## 8. 2026-02-11 追加修复（自定义决策后“取消”菜单残留）

### 8.1 问题现象
- 在 `request_user_input` 中点击 `D. 输入自定义决策` 后，用户发送自定义文本并自动提交成功，Telegram 底部菜单仍可能停留在“取消”按钮。

### 8.2 根因分析
- 自定义输入成功收口链路最终走 `_submit_request_input_session(...)`；
- 该路径此前发送“决策摘要”时未附带 `ReplyKeyboardRemove`，导致 ReplyKeyboard 未被清理。

### 8.3 代码修复（`bot.py`）
- `_submit_request_input_session(...)` 新增参数：
  - `remove_reply_keyboard: bool = False`
  - 当该参数为 `True` 时，决策摘要消息附带 `ReplyKeyboardRemove()`，统一清理底部菜单。
- `_submit_request_input_session_with_auto_retry(...)` 新增参数：
  - `remove_reply_keyboard: bool = False`
  - 成功重试链路向下透传该参数；
  - 失败链路（含自动重试失败）在提示“重试提交”后，补发一条带 `ReplyKeyboardRemove()` 的清理消息，避免菜单残留。
- `_handle_request_input_custom_text_message(...)`：
  - 在“最后一题自定义文本自动提交”调用中启用 `remove_reply_keyboard=True`，将菜单清理收敛到统一提交收口逻辑。

### 8.4 测试更新（`tests/test_request_user_input_flow.py`）
- 现有用例增强：
  - `test_request_input_custom_text_auto_submits`：新增断言，成功收口消息带 `ReplyKeyboardRemove`。
- 新增用例：
  - `test_request_input_custom_text_submit_failure_clears_reply_keyboard`：覆盖自定义文本提交失败场景，断言先出现“重试提交”按钮，再清理 ReplyKeyboard。

### 8.5 回归结果
```bash
PYTHONPATH=. pytest -q tests/test_request_user_input_flow.py
# 15 passed, 2 warnings

PYTHONPATH=. pytest -q
# 596 passed, 6 warnings
```

### 8.6 可验证资料（官方）
- ReplyKeyboardRemove：
  https://core.telegram.org/bots/api#replykeyboardremove
- InlineKeyboardMarkup：
  https://core.telegram.org/bots/api#inlinekeyboardmarkup
