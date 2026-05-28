# TASK_20260528_002 Telegram长文本拆分未自动转附件修复

## 1. 背景与现象

用户反馈：同一段日志内容如果作为 Telegram 文件附件发送，模型能按附件读取；但如果直接把文件内容粘贴到 Telegram，会被拆成多条消息发送到 tmux。用户记得系统已有“自动转成文件发送”的逻辑。

本次附件证据：`/Users/david/.config/vibego/data/telegram/vibegobot/2026-05-28/20260528_102531936-54022c8d1d2a.md`，文件大小约 8.7KB，正文约 8779 字符。

现场 JSONL 证据：`/Users/david/.codex/sessions/2026/05/27/rollout-2026-05-27T20-27-49-019e6967-c64e-7843-94bc-6fc9c2719a0e.jsonl` 中同一次日志被拆成多条用户消息，例如 `len=4066`、`len=676`、`len=4069`。这说明进入模型前已经被 Telegram/客户端拆为多个消息事件。

## 2. 现状与根因

### 2.1 已有逻辑

- 入站长文本聚合：`bot.py`（锚点：`TextPasteAggregationMiddleware`、`_maybe_enqueue_text_paste_message`、`_finalize_text_paste_after_delay`）。
- 超长文本转附件：`bot.py`（锚点：`_handle_prompt_dispatch`）中当 `len(dispatch_prompt) > TELEGRAM_MESSAGE_LIMIT` 时，会调用 `_persist_text_paste_as_attachment` 并用 `_build_prompt_with_attachments` 改写为附件提示词。
- 附件落盘：`bot.py`（锚点：`_persist_text_paste_as_attachment`、`_write_text_payload_as_attachment`）。

### 2.2 根因

根因不是“没有自动转附件逻辑”，而是：

1. 自动转附件位于 `_handle_prompt_dispatch`，前提是进入该函数时已经是一个超过 `TELEGRAM_MESSAGE_LIMIT=4096` 的完整提示词。
2. Telegram 直接粘贴大段文本时，客户端可能先拆成多条消息；本次每段基本都没有超过 4096。
3. 旧的聚合等待窗口复用 `TEXT_PASTE_AGGREGATION_DELAY=0.8s`，但现场分片间隔约数秒，第一段提前被当作完整请求进入 tmux，后续段又分别成为独立请求。

## 3. 用户确认方案

用户选择“待决策项全部按模型推荐”，按推荐方案一次性落地：

- 保留短前缀场景的短等待窗口，避免普通聊天被长时间阻塞。
- 对“接近 Telegram 单条上限”的长文本分片启用独立长窗口：`TEXT_PASTE_LONG_CHUNK_AGGREGATION_DELAY`，默认 8 秒。
- 只要聚合状态中出现接近上限的分片，后续每次分片到达都用长窗口重新计时。
- 聚合完成后仍走现有 `_handle_prompt_dispatch` 的转附件逻辑，避免重复实现附件处理。

## 4. 受影响范围

| 范围 | 文件 | 说明 | 证据锚点 |
|---|---|---|---|
| Worker 入站文本聚合 | `bot.py` | 新增长分片独立聚合窗口配置与状态标记 | `TEXT_PASTE_LONG_CHUNK_AGGREGATION_DELAY`、`PendingTextPasteState.long_chunk_seen`、`_text_paste_finalize_delay_seconds` |
| Worker 入站文本聚合 | `bot.py` | `_maybe_enqueue_text_paste_message` 在 near-limit 分片出现时使用长窗口重置 finalize task | `_maybe_enqueue_text_paste_message`、`_finalize_text_paste_after_delay` |
| 自动转附件 | `bot.py` | 复用既有超长转附件逻辑，无新增附件格式 | `_handle_prompt_dispatch`、`_persist_text_paste_as_attachment`、`_build_prompt_with_attachments` |
| 测试资产 | `tests/test_task_description.py` | 新增慢到达分片回归，调整既有 text_paste 测试的窗口参数 | `test_text_paste_near_limit_chunks_wait_longer_than_prefix_window` |
| 协作证据 | `AGENTS.md` | 增补 Telegram 长文本聚合与转附件约束 | `Telegram 长文本粘贴聚合约束` |

## 5. 契约变更

### 5.1 用户侧行为

- 对普通短消息：不触发聚合，行为不变。
- 对短前缀（如“见如下日志：”）：仍使用短窗口，避免误合并普通对话。
- 对接近 Telegram 上限的长文本分片：等待更长时间收集后续分片；若总长度超过 4096，会自动保存为本地附件，并向模型发送“附件提示词 + 附件路径”。

### 5.2 配置契约

新增可选环境变量：

- `TEXT_PASTE_LONG_CHUNK_AGGREGATION_DELAY`：长文本分片聚合窗口，默认 `8.0` 秒；实际值不会低于 `TEXT_PASTE_AGGREGATION_DELAY`。

### 5.3 数据库/API/构建契约

- 数据库：不涉及 SQLite 表结构、索引、迁移。
- HTTP API：本仓库无 REST Controller 证据，本次不涉及。
- 依赖：不新增依赖。
- 构建/CI：不修改构建链或 CI。

## 6. 测试矩阵

| 用例 | 覆盖点 | 结果 |
|---|---|---|
| `test_text_paste_near_limit_chunks_wait_longer_than_prefix_window` | 首个 near-limit 分片到达后，超过短窗口仍不提前注入；后续分片到达后合并一次注入 | 先红后绿 |
| `test_text_paste_aggregation_injects_single_combined_message` | 原长文本分片聚合能力兼容 | 通过 |
| `test_text_paste_aggregation_merges_prefix_and_log_parts` | 短前缀 + 日志合并兼容 | 通过 |
| `test_text_paste_prefix_only_falls_back_to_injection_after_delay` | 只有短前缀时仍按短窗口回退注入 | 通过 |
| `test_text_paste_prefix_followed_by_short_message_flushes_prefix` | 短前缀后跟普通短消息时仍及时释放 | 通过 |
| `test_text_paste_prefix_captures_short_log_fragment_before_near_limit_chunk` | 短前缀后先来短日志片段再来 near-limit 分片，仍保留日志头 | 通过 |
| `test_on_text_skips_text_paste_aggregation_for_short_messages` | 普通短消息不误触发聚合 | 通过 |

## 7. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| 单条 near-limit 文本没有后续分片 | 用户会多等待默认 8 秒再推送 | 仅 near-limit 文本受影响；普通短消息不变；可通过 `TEXT_PASTE_LONG_CHUNK_AGGREGATION_DELAY` 调整 |
| 分片间隔超过 8 秒 | 仍可能被拆成多次请求 | 配置可增大；不建议默认过大以免影响交互反馈 |
| 短前缀误等待 | 普通短前缀可能延迟 | 本次未增大短前缀窗口，保持原短窗口 |

回滚方式：移除 `TEXT_PASTE_LONG_CHUNK_AGGREGATION_DELAY`、`PendingTextPasteState.long_chunk_seen`、`_text_paste_finalize_delay_seconds` 及 `_maybe_enqueue_text_paste_message` 对长窗口的调度；还原 `tests/test_task_description.py` 新增与调整的 text_paste 测试。本次无数据迁移，无需数据回滚。

## 8. 实施记录

- 基线：`python3.11 -m pytest -q tests/test_task_description.py -k 'text_paste or overlong_task_prompt'`：6 passed, 186 deselected。
- 红灯：`python3.11 -m pytest -q tests/test_task_description.py -k 'text_paste_near_limit_chunks_wait_longer_than_prefix_window'`：旧实现提前注入第一段，测试失败。
- 绿灯：同命令修复后通过。

## 9. 完成状态 Checklist

- [x] 已复现 near-limit 分片慢到达时旧实现提前注入的问题。
- [x] 已为 long chunk 增加独立长窗口。
- [x] 已保持短前缀短窗口不变。
- [x] 已复用既有超长转附件逻辑，无重复附件实现。
- [x] 已补充/调整聚焦测试。
- [x] 未新增依赖、未改数据库、未改构建链/CI。
- 聚焦回归：`python3.11 -m pytest -q tests/test_task_description.py -k 'text_paste or overlong_task_prompt'`：7 passed, 186 deselected。
- 扩展回归：`python3.11 -m pytest -q tests/test_message_recovery_poll.py tests/test_task_description.py -k 'text_paste or message_recovery or overlong_task_prompt'`：12 passed, 186 deselected。
- 诊断：`python3.11 -m vibego_cli doctor`：通过，`python_ok=true`，关键运行配置存在。
