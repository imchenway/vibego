# TASK_20260602_001 Telegram 中长日志首段原文与附件重复入模修复

## 1. 现象

用户在 HyphaFawnStudioBot 发送一段启动/错误日志后，Codex 终端里出现两次 `以下是用户需求描述：`：

1. 第一次是未压缩的日志首段原文。
2. 第二次是 `收到一段超长文本，已自动保存为附件，请阅读附件获取全文。` 以及本地 txt 附件路径。

证据：

- 截图附件：`/Users/david/.config/vibego/data/telegram/vibegobot/2026-06-02/20260602_092527998-05954297998e.jpg`，可见原文日志 prompt 后又出现附件 prompt。
- Codex 会话：`/Users/david/.codex/sessions/2026/06/01/rollout-2026-06-01T22-45-09-019e83a5-4f02-7b53-b091-a1d47334c2be.jsonl`。
  - line 2687：09:24:10.994 收到 2181 字符的原始日志首段 prompt。
  - line 2694：09:24:16.255 又收到超长文本转附件 prompt。
- 转附件文件：`/Users/david/.config/vibego/data/telegram/hyphafawnstudiobot/2026-06-02/20260602_092415119-1880d85adbb6.txt`，大小 10922 字符，属于同一次日志上下文后续分片。

## 2. 影响

- 同一轮日志输入被拆成两次模型输入，用户会看到两次执行/两次打断风险。
- 第一段原文先进入 tmux 后，用户可能以为模型已经拿到完整日志并打断；后续附件 prompt 再进入，造成上下文混乱。
- 该问题发生在 Telegram 入站聚合阶段，不是 Codex 输出重复，也不是模型 JSONL 投递重复。

## 3. 根因

当前长文本聚合触发条件过窄：

- `bot.py`（锚点：`TEXT_PASTE_NEAR_LIMIT_THRESHOLD`、`_maybe_enqueue_text_paste_message`）：只有单条文本达到 near-limit，或命中“短前缀 + 长日志”时才进入聚合。
- 真实 Telegram 粘贴中，首段可能只有 2K 左右，低于默认 `TEXT_PASTE_NEAR_LIMIT_THRESHOLD=3500`，但它仍然是后续超长日志的一部分。
- 这类首段既不是“短前缀”，也未达到 near-limit，因此旧实现返回 `False`，交给 `on_text -> _handle_prompt_dispatch` 立即投递原文。
- 后续真正超长分片再触发聚合，最终在 `_handle_prompt_dispatch` 中转成附件 prompt，形成“原文 + 附件”两次入模。

## 4. 修法

新增中长多行粘贴首段识别：

- 新增 `TEXT_PASTE_LEADING_FRAGMENT_MIN_CHARS`，默认 1200。
- 新增 `_is_text_paste_leading_fragment_candidate(text)`：低于 near-limit、但长度达到阈值且包含换行的文本，视为可能被拆分的粘贴首段。
- `_maybe_enqueue_text_paste_message` 在首次看到该类首段时，将其放入 `PendingTextPasteState.parts`，并设置 `long_chunk_seen=True`，复用现有长窗口等待后续分片。
- 若后续分片到达，则合并为一次合成消息；若没有后续分片，则延迟后只发送一次原文。

## 5. 受影响目录与边界

| 文件 | 影响 |
| --- | --- |
| `bot.py` | 新增长文本首段识别阈值与 helper；调整 `_maybe_enqueue_text_paste_message` 首次入队分支。 |
| `tests/test_task_description.py` | 新增“低于 near-limit 的日志首段 + 后续超长分片只合并一次”的回归测试。 |
| `AGENTS.md` | Facts Table 更新 Telegram 长文本粘贴聚合约束。 |
| `docs/TASK_20260602_001_Telegram中长日志首段原文与附件重复入模修复.md` | 本任务取证、设计、验证记录。 |

不影响：

- SQLite schema：无表结构/迁移变化。
- Telegram 附件下载：不改 `_collect_saved_attachments` 与普通媒体处理。
- 模型输出投递：不改 `_deliver_pending_messages`。
- tmux/Codex 投递确认：不改 `_dispatch_prompt_to_model` 与 JSONL ack 规则。
- 前端/小程序：无影响。

## 6. 契约变更

- 低于 near-limit 但达到 `TEXT_PASTE_LEADING_FRAGMENT_MIN_CHARS` 且包含换行的文本，会最多等待 `TEXT_PASTE_LONG_CHUNK_AGGREGATION_DELAY` 后再入模。
- 如果后续分片到达：只入模一次，必要时转本地 txt 附件。
- 如果后续分片未到达：只入模一次原始内容，不会丢消息。
- 普通短消息仍即时发送；单行中长文本不进入该新分支，降低误等待。

## 7. 测试矩阵

| 用例 | 覆盖点 | 结果 |
| --- | --- | --- |
| `test_text_paste_log_leading_fragment_waits_for_overlong_followup` | 首段低于 near-limit 但是中长多行日志，后续超长分片到达时只注入一次合并内容 | RED：旧实现返回 `False`；GREEN：通过 |
| `test_text_paste_near_limit_chunks_wait_longer_than_prefix_window` | 既有 near-limit 长窗口不回退 | 通过 |
| `test_text_paste_aggregation_injects_single_combined_message` | 已聚合状态下连续分片合并一次 | 通过 |
| `test_text_paste_aggregation_merges_prefix_and_log_parts` | 短前缀 + 日志分片继续合并 | 通过 |
| `test_text_paste_prefix_only_falls_back_to_injection_after_delay` | 只有短前缀时仍回退发送一次 | 通过 |
| `test_text_paste_prefix_followed_by_short_message_flushes_prefix` | 短前缀后跟短消息不误合并 | 通过 |
| `test_text_paste_prefix_captures_short_log_fragment_before_near_limit_chunk` | 短日志头 + near-limit 分片不丢头 | 通过 |
| `test_on_text_skips_text_paste_aggregation_for_short_messages` | 普通短文本即时发送 | 通过 |

## 8. TDD 记录

### 8.1 Baseline

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_text_paste_aggregation_injects_single_combined_message \
  tests/test_task_description.py::test_text_paste_near_limit_chunks_wait_longer_than_prefix_window \
  tests/test_task_description.py::test_text_paste_aggregation_merges_prefix_and_log_parts \
  tests/test_task_description.py::test_on_text_skips_text_paste_aggregation_for_short_messages
```

结果：`4 passed in 0.30s`。

### 8.2 RED

```bash
python3.11 -m pytest -q tests/test_task_description.py::test_text_paste_log_leading_fragment_waits_for_overlong_followup
```

结果：失败，`_maybe_enqueue_text_paste_message(first, first.text)` 返回 `False`，证明旧实现会让首段原文继续进入正常 handler。

### 8.3 GREEN

```bash
python3.11 -m pytest -q tests/test_task_description.py::test_text_paste_log_leading_fragment_waits_for_overlong_followup
```

结果：`1 passed, 2 warnings in 0.28s`。

## 9. 风险与回滚

### 风险

1. 中长多行消息会延迟最多 `TEXT_PASTE_LONG_CHUNK_AGGREGATION_DELAY`（默认 8 秒）再入模。
2. 如果用户连续发送两个独立的中长多行文本且间隔小于长窗口，会被视为一次粘贴分片合并。这是为避免日志拆分重复入模的取舍。
3. 默认阈值 1200 主要覆盖日志/代码/启动栈一类多行长贴；普通短问题不受影响。

### 回滚

- 删除 `TEXT_PASTE_LEADING_FRAGMENT_MIN_CHARS`。
- 删除 `_is_text_paste_leading_fragment_candidate`。
- 删除 `_maybe_enqueue_text_paste_message` 中的 leading fragment 分支。
- 删除 `test_text_paste_log_leading_fragment_waits_for_overlong_followup`。
- 恢复 `AGENTS.md` 中 Telegram 长文本粘贴聚合约束描述。

## 10. 最终验证记录（2026-06-02）

- ✅ `python3.11 -m pytest -q tests/test_task_description.py`
  - 第一轮结果：`204 passed in 14.97s`。
  - 第二轮结果：`204 passed in 15.04s`。
  - 覆盖：Telegram 文本/附件入站、长文本聚合、短前缀聚合、普通文本路径、附件下载失败回执等同文件回归集合。
- ✅ `python3.11 -m vibego_cli doctor`
  - 结果：Python 3.11、依赖、配置根、环境文件、项目配置与 master DB 正常。
- ✅ `bash scripts/test_deps_check.sh`
  - 结果：runtime venv 与关键依赖 `aiogram/aiohttp/aiosqlite` 正常。

未执行全量 `python3.11 -m pytest -q`：本次改动集中在 `bot.py` 长文本入站聚合与 `tests/test_task_description.py`，已跑受影响测试文件双轮；历史文档已记录全量测试存在与本链路无关的模板/规约失败，避免扩大修复范围。
