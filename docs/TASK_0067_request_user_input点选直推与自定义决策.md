# TASK_0067 request_user_input 点选直推与自定义决策（DEVELOP）

## 1. 目标
- 去除“必须点提交”主路径，支持题目作答后自动推送。
- 每题追加 `D. 输入自定义决策`，点击后进入文本输入态。
- 自定义文本发送后直接保存并推进流程；全部题目作答完成后一次性推送。
- 推送后回显“已推送 + 决策摘要”。

## 2. 代码改动

### 2.1 request_user_input 动作与会话状态扩展（`bot.py`）
- 新增动作常量：
  - `REQUEST_INPUT_ACTION_CUSTOM`
- 新增语义常量：
  - `REQUEST_INPUT_CUSTOM_OPTION_INDEX = -1`
  - `REQUEST_INPUT_CUSTOM_LABEL = "输入自定义决策"`
- `RequestInputSession` 新增字段：
  - `custom_answers: Dict[str, str]`
  - `input_mode_question_id: Optional[str]`

### 2.2 题目渲染与按钮交互升级（`bot.py`）
- 选项序号改为 A/B/C 显示。
- 每题追加 `D. 输入自定义决策` 按钮。
- 默认隐藏“提交”按钮（保留底层 submit action 兼容旧消息回调）。
- 新增自定义输入态菜单：仅提供“取消”按钮。

### 2.3 自动推送与摘要回显（`bot.py`）
- 新增 `_submit_request_input_session(...)`：统一封装推送、预览、session ack。
- 新增 `_build_request_input_submission_summary(...)`：推送成功后回显每题答案摘要。
- 选项点击后自动推进到下一未答题；当全部题目已答时自动推送。

### 2.4 自定义输入态处理（`bot.py`）
- 新增 `_handle_request_input_custom_text_message(...)`：
  - 仅会话发起人可输入。
  - 支持“取消”返回当前题目。
  - 非空校验，空文本拦截。
  - 保存自定义答案后自动推进；全部完成则自动推送。
- 在 `on_text` 与 `on_media_message` 前置接入该处理器，确保输入态优先消费消息。

### 2.5 answers 结构（已按决策落地）
- A/B/C：沿用 `{"answers":["选项文案"]}`。
- D（自定义）：仅提交用户输入文本，不加前缀。

## 3. 测试改动
- 更新：`tests/test_request_user_input_flow.py`
- 新增覆盖：
  1) 键盘隐藏提交按钮 + 展示 D 选项
  2) 单题点击选项自动推送
  3) 点击 D 进入自定义输入态
  4) 自定义文本发送后自动推送（payload 为纯自定义文本）
  5) 自定义输入“取消”可返回题目

## 4. 回归结果

### 4.1 相关测试
```bash
PYTHONPATH=. pytest -q tests/test_request_user_input_flow.py tests/test_task_description.py tests/test_plan_progress.py tests/test_model_quick_reply.py tests/test_codex_jsonl_phase.py tests/test_long_poll_mechanism.py tests/test_auto_compact.py
# 184 passed
```

### 4.2 全量测试
```bash
PYTHONPATH=. pytest -q
# 570 passed, 6 warnings
```

## 5. 可验证资料（官方）
- Telegram InlineKeyboardButton（callback_data 限制 1-64 bytes）：
  https://core.telegram.org/bots/api#inlinekeyboardbutton
- Telegram CallbackQuery：
  https://core.telegram.org/bots/api#callbackquery
- Telegram ReplyKeyboardMarkup：
  https://core.telegram.org/bots/api#replykeyboardmarkup
