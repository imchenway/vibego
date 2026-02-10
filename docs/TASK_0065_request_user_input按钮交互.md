# /TASK_0065 request_user_input 按钮交互（DEVELOP）

## 1) 目标
- 解析 Codex 会话 JSONL 中的 `request_user_input` function_call。
- 在 Telegram 侧渲染可点击按钮并支持逐题交互。
- 用户提交后，以结构化 JSON（含 `call_id`）回推模型继续执行。

## 2) 本次实现

### 2.1 JSONL 解析扩展
- 文件：`bot.py`
- 新增可投递类型：`DELIVERABLE_KIND_REQUEST_INPUT`
- 在 `_extract_codex_payload()` 中新增：
  - 识别 `response_item.payload.type=function_call && name=request_user_input`
  - 解析 `arguments.questions`、`call_id`
  - 生成交互元数据（问题、选项）并投递到 Telegram

### 2.2 Telegram 按钮交互会话
- 文件：`bot.py`
- 新增会话模型：
  - `RequestInputOption`
  - `RequestInputQuestion`
  - `RequestInputSession`
- 新增运行态缓存：
  - `REQUEST_INPUT_SESSIONS`
  - `CHAT_ACTIVE_REQUEST_INPUT_TOKENS`
  - `CHAT_ACTIVE_USERS`
- 新增回调协议（短 token，适配 `callback_data` 限制）：
  - `rui:<token>:opt:<idx>`
  - `rui:<token>:prev`
  - `rui:<token>:next`
  - `rui:<token>:submit`
  - `rui:<token>:cancel`
- 新增处理器：
  - `@router.callback_query(F.data.startswith("rui:"))`
  - 支持：选择、上一题、下一题、提交、取消

### 2.3 提交回推模型
- 文件：`bot.py`
- 提交时生成结构化 payload：
```json
{"answers":{"question_id":{"answers":["选项文案"]}}}
```
- 并构建回推提示词（含 `call_id`）发送给模型：
  - `request_user_input 工具结果（来自 Telegram 按钮交互）`
  - `call_id=<...>`
  - `{"answers":...}`

### 2.4 安全与健壮性
- 仅允许会话发起人点击按钮（防误触/串会话）。
- 默认 15 分钟过期（`REQUEST_INPUT_SESSION_TTL_SECONDS`）。
- 同 chat 新交互会替换旧 token，旧按钮自动失效。
- `request_user_input` 解析失败时降级提示，不中断主流程。
- 任务推送读取备注/附件失败时降级为空列表（避免 SQLite 局部表缺失导致推送中断）。

## 3) 兼容性与行为保持
- 保持现有 `final_answer`、`update_plan`、快捷回复按钮等逻辑不变。
- `request_user_input` 事件单独走交互通道，不复用“全部按推荐”按钮。

## 4) 测试与验证

### 4.1 新增测试
- `tests/test_request_user_input_flow.py`
  - 解析 `request_user_input` 事件
  - 投递题目按钮
  - 非发起人拒绝点击
  - 提交生成结构化 payload 并回推
  - 未答完阻止提交
  - 过期会话失效

### 4.2 回归结果
```bash
PYTHONPATH=. pytest -q tests/test_request_user_input_flow.py tests/test_plan_progress.py tests/test_model_quick_reply.py tests/test_codex_jsonl_phase.py tests/test_task_description.py
# 160 passed

PYTHONPATH=. pytest -q tests/test_long_poll_mechanism.py tests/test_auto_compact.py
# 14 passed
```

## 5) 可验证资料（官方）
- Telegram Inline Keyboard：  
  https://core.telegram.org/bots/api#inlinekeyboardmarkup
- Telegram InlineKeyboardButton（`callback_data` 1-64 bytes）：  
  https://core.telegram.org/bots/api#inlinekeyboardbutton
- Telegram CallbackQuery：  
  https://core.telegram.org/bots/api#callbackquery

