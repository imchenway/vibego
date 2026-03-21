# /TASK_0104 telegram 消息

## 1. 背景

- 任务标题：`telegram 消息`
- 当前效果：超出长度会发送 `.md`
- 期望效果：直接拆分消息发送
- 本轮用户确认口径：
  - 范围：**仅模型回复**
  - 按钮位置：**仅最后一条**
  - 格式策略：**优先保留 Markdown**

## 2. 规约读取结果

已按要求读取：

- `$HOME/.config/vibego/AGENTS.md`
- `./AGENTS.md`
- 扫描命令：`find . -name 'AGENTS.md' -o -name 'AGENTS.evidence.json' | sort`
  - 结果仅有：`./AGENTS.md`
  - 结论：本任务没有更近子项目规约，按根目录 `AGENTS.md` 执行

## 3. 证据

- 当前超长回复走附件：`bot.py`（锚点：`async def reply_large_text(`、`attachment_name = f"model-response-... .md"`、`bot.send_document(`）
- 模型回复投递链路：`bot.py`（锚点：`async def _deliver_pending_messages(`、`delivered_payload = await reply_large_text(`）
- 历史设计曾明确采用 `.md` 附件：
  - `docs/TASK_0041_模型回复底部快捷回复按钮.md`（锚点：`当模型答案过长，当前实现会降级为 .md 附件发送`）
  - `docs/TASK_0086_消息有重复看起来很奇怪.md`（锚点：`重复消息处理：只保留附件消息`）
- 非本次范围但需保持不变的链路：
  - `bot.py`（锚点：`async def _send_model_push_preview(`）
  - `bot.py`（锚点：`async def _send_task_detail_as_attachment(`）

## 4. Class Impact Plan

### 4.1 受影响子项目与目录

- Worker 主链路：`bot.py`
- 类级测试：`tests/test_plan_progress.py`
- 文档：`docs/TASK_0104_telegram消息.md`

### 4.2 本次计划修改的单元

- `bot.py::reply_large_text`
- `bot.py::_split_text_for_telegram_messages`（新增）
- `bot.py::_split_code_block_for_telegram`（新增）
- `bot.py::_split_plain_text_for_telegram`（新增）
- `bot.py::_deliver_pending_messages`

### 4.3 对应测试文件

- `tests/test_plan_progress.py`
  - `test_reply_large_text_attachment`
  - `test_reply_large_text_short_message`
  - `test_reply_large_text_split_mode_sends_multiple_messages_and_only_last_chunk_has_markup`（新增）
  - `test_reply_large_text_split_mode_preserves_fenced_code_blocks`（新增）
  - `test_deliver_pending_messages_uses_split_mode_for_model_output`（新增）

### 4.4 直连依赖测试与证据

- 仅纳入 `tests/test_plan_progress.py`
  - 证据：该文件已直接覆盖 `reply_large_text` 的短消息/附件契约（锚点：`test_reply_large_text_attachment`、`test_reply_large_text_short_message`）
  - 本次新增测试也直接覆盖 `_deliver_pending_messages -> reply_large_text` 的模型回复分流契约

### 4.5 测试范围升级判断

- 结论：**不升级到模块级/子项目级测试**
- 原因：
  - 仅修改 Worker 的 Telegram 超长发送私有链路
  - 无数据库、无 DTO、无 CLI/配置/CI 变更
  - 影响面可稳定收敛到 `bot.py` 与直连测试 `tests/test_plan_progress.py`

## 5. Baseline Gate

### 5.1 绿色基线

执行：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py -k 'reply_large_text_attachment or reply_large_text_short_message'
```

结果：

- ✅ `2 passed`

### 5.2 额外侦查（未纳入本次 baseline）

执行：

```bash
python3.11 -m pytest -q tests/test_model_quick_reply.py -k 'deliver_pending_messages_for_bound_native_session_includes_commit_button or deliver_pending_messages_for_batch_bound_native_session_uses_per_message_task_binding'
```

结果：

- ⚠️ `2 failed`

说明：这两条旧用例当前已红，更像旧 fixture 与现事件解析不匹配；本次未把它们纳入类级 baseline，避免把“修旧基线”和“做新需求”混在一起。

## 6. TDD Gate

### 6.1 新增/修改测试

先补测试：

- 超长模型回复在 `split` 模式下改为多条正文消息发送
- 仅最后一条挂按钮
- fenced code block 超长时，分片后每条仍保留代码块 fence
- `_deliver_pending_messages` 对模型回复显式走 `split` 策略

### 6.2 红灯记录

补测后首次调试中，`test_deliver_pending_messages_uses_split_mode_for_model_output` 初版为红；原因是测试未设置 `ACTIVE_MODEL/MODEL_CANONICAL_NAME`，导致事件未按 Codex 路径投递。修正测试前置条件后继续实现。

## 7. 实现

### 7.1 生产代码

- `reply_large_text(...)` 新增私有策略参数 `overflow_mode`
  - `attachment`：保持现有 `.md` 附件行为
  - `split`：拆成多条正文消息发送
- 新增 Telegram 分片辅助函数：
  - `_measure_telegram_text_length`
  - `_max_telegram_prefix_index`
  - `_choose_telegram_split_index`
  - `_split_plain_text_for_telegram`
  - `_split_code_block_for_telegram`
  - `_split_text_into_telegram_blocks`
  - `_split_text_for_telegram_messages`
- `_deliver_pending_messages(...)` 仅在模型回复链路调用 `reply_large_text(..., overflow_mode="split")`
- 其它长文本场景继续保持附件策略不变

### 7.2 关键实现口径

- 分片基于**原始文本块**，不是直接截断已转义 payload
- 优先按段落/换行切分
- fenced code block 超长时按多段重新补齐 fence
- 单段 Markdown 解析失败时，仅该段降级为纯文本；不再回退 `.md`（仅模型回复范围内）
- 最后一条才挂 `reply_markup`

## 8. Self-Test Gate

### 8.1 类级自测第一轮

```bash
python3.11 -m pytest -q tests/test_plan_progress.py -k 'reply_large_text or deliver_pending_messages_uses_split_mode_for_model_output'
```

结果：

- ✅ `5 passed, 19 deselected`

### 8.2 类级自测第二轮

```bash
python3.11 -m pytest -q tests/test_plan_progress.py -k 'reply_large_text or deliver_pending_messages_uses_split_mode_for_model_output'
```

结果：

- ✅ `5 passed, 19 deselected`

### 8.3 语法/收集校验

```bash
python3.11 -m pytest -q --collect-only tests/test_auto_compact.py tests/test_request_user_input_flow.py tests/test_task_description.py
```

结果：

- ✅ `210 tests collected`

### 8.4 最小诊断

```bash
python3.11 -m vibego_cli doctor
```

结果：

- ✅ `python_ok=true`

## 9. 结果

- 模型回复超长时，不再发送 `.md` 附件
- Telegram 会直接收到多条正文消息
- 快捷按钮只出现在最后一条
- 任务详情 / 推送预览 / request_input 等非模型回复场景仍保持原有附件策略

## 10. 修改文件清单

- `bot.py`
- `tests/test_plan_progress.py`
- `tests/test_auto_compact.py`
- `tests/test_request_user_input_flow.py`
- `tests/test_task_description.py`
- `tests/test_model_quick_reply.py`
- `docs/TASK_0104_telegram消息.md`
