# TASK_20260601_002 Telegram 图文混合消息拆分与 Codex 自动重试重复修复

## 1. 任务背景

用户反馈：刚才从 Telegram 发出的输入实际是“两张图片 + 1 条文字消息”，但 vibego 把它拆成了多条消息推送到终端；同时 Codex 终端里出现同一条消息被排队两次，Telegram 侧也出现错误反馈。

本次按 bug 修复处理，目标是一次性修复两个独立但相互放大的问题：

1. **入站聚合 bug**：普通图文消息在没有 `media_group_id` 时被拆成多次 `_handle_prompt_dispatch`。
2. **投递确认 bug**：Codex 当前轮忙碌时，prompt 已进入 `Queued follow-up inputs`，但 JSONL 尚未写入用户消息，vibego 误判“未确认收到”并自动补发，导致终端出现重复 queued 输入。

## 2. 现象与证据

### 2.1 JSONL 证据

当前 vibegoBot 会话：

- `/Users/david/.codex/sessions/2026/06/01/rollout-2026-06-01T15-21-14-019e820e-e33e-7241-aa2f-c2d10cc6ac38.jsonl`

证据：

| 行号 | 现象 | 说明 |
| --- | --- | --- |
| 6-7 | 第一条 user message 只包含 `20260601_072157840-e575727be3f7.jpg` | 第一张图被单独推送到 Codex。 |
| 13-14 | 第二条 user message 包含用户文字和 `20260601_072204025-cf78dc3e09cd.jpg` | 第二张图与文字又被作为另一条 prompt 推送。 |

这证明问题不是 Codex 回答重复，而是 vibego 入站阶段已经把同一用户意图拆成了两次终端输入。

### 2.2 日志证据

运行日志：`/Users/david/.config/vibego/logs/vibe.log`

| 行号 | 现象 | 说明 |
| --- | --- | --- |
| 213413-213415 | CckgWmsAppCoreBot 在 15:20:10 重新绑定会话后，15:20:18 记录“模型未确认收到 prompt，开始自动重试一次” | 触发自动补发逻辑。 |
| 213435-213442 | vibegoBot 在 15:22:03 与 15:22:09 对同一 session 连续绑定/ack | 用户随后切到 vibegoBot 复现/反馈问题。 |

结合截图中 Codex TUI 已显示 `Queued follow-up inputs`，说明 prompt 已被 Codex 接收到队列，只是 JSONL 在当前轮完成前不会立刻追加 user_message。

## 3. 根因

### 3.1 Bug A：普通图文消息只聚合 Telegram `media_group_id`

当前实现已有相册聚合：

- `bot.py`（锚点：`MEDIA_GROUP_STATE`、`_enqueue_media_group_message`、`_finalize_media_group_after_delay`、`on_media_message`）

但它只处理 `message.media_group_id` 存在的场景。实际 Telegram 客户端/代理链路会把“两张图 + 文字”拆成多条普通消息，且不一定带同一个 `media_group_id`。旧逻辑对非媒体组附件会立即构造 `_build_prompt_with_attachments(...)` 并调用 `_handle_prompt_dispatch(...)`，所以第一张图先进入终端，后续图文又进入终端。

### 3.2 Bug B：JSONL 投递确认不能作为 Codex 自动重试依据

当前普通直聊启用了 session JSONL 投递确认：

- `bot.py`（锚点：`PROMPT_DELIVERY_CONFIRM_ENABLED`、`_wait_for_prompt_delivery_confirmation`、`_confirm_or_retry_prompt_delivery`）

这个机制能防止 tmux `send-keys` 成功但 Codex 没消费的静默丢消息。但当 Codex 当前 turn 正忙时，新的输入会先显示在 TUI 的 `Queued follow-up inputs` 区域，JSONL 只有等后续 turn 真正开始时才写入 user_message。旧逻辑只看 JSONL，在短超时内没看到 user_message 就自动补发，于是把同一 prompt 又排队了一次。

## 4. 修复方案

### 4.1 入站图文聚合

新增普通直聊附件聚合窗口：

- 附件消息没有 `media_group_id` 时，不再立即推送模型，而是进入 chat 级 `DIRECT_PROMPT_BATCH_STATE`。
- 窗口内后续附件和文字都追加到同一个批次。
- 窗口结束后统一调用 `_build_prompt_with_attachments(text, attachments)`，只推送一次模型。
- 普通纯文本在没有待聚合附件时仍即时发送，不改变日常直聊体验。

默认窗口：`TELEGRAM_DIRECT_PROMPT_BATCH_DELAY=8.0s`。

取舍：单张无 caption 图片会多等最多 8 秒，但换来“两图 + 文字”这类真实输入不再被拆成多次入模。这个方向优先保证语义正确，而不是抢 1-2 秒响应速度。

### 4.2 Codex JSONL 确认语义修正

修正后的根因口径：Codex 的 session JSONL 不是传输层 ACK，它只表示 Codex 已开始消费输入。当前 turn 正忙时，JSONL 延迟写入是正常状态。

因此本次最终方案不是读取 tmux/Codex TUI 文案，而是调整重试契约：

- `tmux_send_line` / `tmux_queue_line` 成功返回，即认为 vibego 到 tmux 的传输成功。
- 只有 tmux 命令失败抛出 `CalledProcessError` 时，才向用户报告投递失败。
- Codex JSONL 暂未确认时不自动重试，避免同一 prompt 被重复排队。
- Copilot 仍保留原有 JSONL 未确认后的自动重试保护。

保留原有保护边界：

- tmux 发送命令失败时仍立即返回错误。
- Copilot 未确认时仍按旧逻辑自动重试一次；重试后仍未确认时提示用户失败。

## 5. 受影响目录与文件

| 文件 | 影响 |
| --- | --- |
| `bot.py` | 新增普通直聊图文聚合状态与 quiet window；修正 Codex JSONL 未确认后的重试策略；调整 `on_media_message` / `on_text` / `_confirm_or_retry_prompt_delivery`。 |
| `tests/test_task_description.py` | 新增两图一文字只入模一次的回归测试。 |
| `tests/test_tmux_send_line.py` | 新增 Codex JSONL 未确认不自动补发、Copilot 仍保留自动重试的回归测试。 |
| `AGENTS.md` | Facts Table 增补普通图文直聊聚合约束与 Codex 投递确认不自动重试约束。 |

不影响：

- SQLite schema：未改表结构、索引、迁移。
- Telegram 命令中心：未改命令定义、历史表。
- 模型输出投递去重：本次修的是用户输入入模，不改模型回复 `_deliver_pending_messages`。
- 前端/UI：无浏览器端或小程序端。

## 6. 测试矩阵

| 用例 | 覆盖点 | 结果 |
| --- | --- | --- |
| `test_direct_photo_photo_text_burst_dispatches_once` | 两张非媒体组图片 + 一条文字聚合为一次 prompt | 先红后绿 |
| `test_dispatch_prompt_does_not_retry_codex_when_jsonl_not_confirmed` | Codex JSONL 未确认时不自动补发 | 先红后绿 |
| `test_on_media_message_album_with_small_gap_dispatches_once` | 既有 `media_group_id` 相册聚合不回退 | 通过 |
| `test_on_media_message_attachment_download_failure_dispatches_caption` | 附件下载失败仍有用户可见回执 | 通过 |
| `test_on_text_direct_prompt_enables_delivery_confirmation` | 普通文本仍启用投递确认 | 通过 |
| `test_dispatch_prompt_retries_with_queue_for_copilot_when_user_prompt_not_confirmed` | Copilot JSONL 未确认时仍自动重试一次 | 通过 |
| `test_dispatch_prompt_reports_unconfirmed_after_retry_for_copilot` | Copilot 自动重试仍失败时仍明确提示 | 通过 |

## 7. TDD 执行记录

### 7.1 Baseline

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_on_media_message_album_with_small_gap_dispatches_once \
  tests/test_task_description.py::test_on_media_message_attachment_download_failure_dispatches_caption \
  tests/test_task_description.py::test_on_text_direct_prompt_enables_delivery_confirmation \
  tests/test_tmux_send_line.py::test_dispatch_prompt_retries_with_queue_for_copilot_when_user_prompt_not_confirmed \
  tests/test_tmux_send_line.py::test_dispatch_prompt_reports_unconfirmed_after_retry_for_copilot
```

结果：`5 passed in 2.64s`。

### 7.2 RED

新增两个失败测试后执行：

```bash
python3.11 -m pytest -q \
  tests/test_task_description.py::test_direct_photo_photo_text_burst_dispatches_once \
  tests/test_tmux_send_line.py::test_dispatch_prompt_does_not_retry_when_codex_has_queued_followup
```

首次有效红灯：

- 图文聚合测试失败：`len(dispatched_prompts) == 3`，说明旧逻辑会拆成多次入模。
- Codex JSONL 未确认测试失败：旧逻辑进入自动重试路径。

### 7.3 GREEN

实现后同一命令结果：`2 passed, 2 warnings in 0.30s`。

### 7.4 聚焦回归

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py
```

结果：`218 passed in 14.85s`。

## 8. 风险与回滚

### 风险

1. 单张无 caption 图片会最多等待 `TELEGRAM_DIRECT_PROMPT_BATCH_DELAY` 后再入模。
2. 如果用户发完图片后马上发一个真正独立的命令/任务编号，且发生在聚合窗口内，会被视为图片说明合并。这是为了优先修复“图文被拆”问题的取舍。
3. Codex 不再因 JSONL 暂未确认自动补发；实际 tmux 发送失败仍由 `CalledProcessError` 返回给用户，若 Codex TUI 自身接收后丢弃输入，需要用户显式重发。

### 回滚

- 删除 `DIRECT_PROMPT_BATCH_*` 状态、`_enqueue_direct_prompt_batch_message`、`_finalize_direct_prompt_batch_after_delay`，并恢复 `on_media_message` 直接 `_handle_prompt_dispatch`。
- 恢复 `_confirm_or_retry_prompt_delivery` 中 Codex JSONL 未确认后的自动重试分支（不推荐，会重新带来重复排队风险）。
- 删除本次新增测试与 AGENTS Facts 行。

## 9. 后续观察

建议上线后观察两类日志：

- 是否还出现同一 chat 短时间内连续两条图片 prompt 进入同一 Codex session。
- 是否还出现 `模型未确认收到 prompt，开始自动重试一次` 后，终端 `Queued follow-up inputs` 中重复同一文本。

## 最终验证记录（2026-06-01 15:38 CST）

- ✅ `python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py`
  - 结果：`218 passed in 14.87s`
  - 覆盖：本次修改的 Telegram 图文聚合路径、普通文本路径、附件失败回执、tmux/Codex 投递确认与重试路径。
- ✅ `python3.11 -m vibego_cli doctor`
  - 结果：Python 3.11、依赖、配置根、环境文件、项目配置与 master DB 均正常。
- ✅ `bash scripts/test_deps_check.sh`
  - 结果：runtime venv 与关键依赖 `aiogram/aiohttp/aiosqlite` 正常。
- ⚠️ `python3.11 -m pytest -q`
  - 结果：`3 failed, 946 passed, 6 warnings in 31.94s`
  - 失败项：`tests/test_agents_template_migration.py::{test_enforced_notice_points_to_agents_md,test_enforced_notice_adds_user_requirement_header_before_prompt,test_agents_template_requires_comet_for_complex_workflows}`。
  - 判定：失败点不在本次修改链路；当前仓库 `bot.ENFORCED_AGENTS_NOTICE` 仅为 `以下是用户需求描述：`，且 `AGENTS-template.md` 未包含测试期望的 Comet 规则，属于既有模板/规约测试不一致问题。本次未扩大范围修复，避免把图文聚合/投递确认热修带入无关规约变更。

## 本次落地结论

- 两个用户可见问题均已在源码侧修复并有回归测试保护：
  1. 普通直聊“两张图片 + 一条文字”不会再因缺少 `media_group_id` 被立即拆成多次模型输入。
  2. Codex 当前轮忙碌导致 JSONL 尚未落盘时，不会再把“未消费”误判成“未投递”并自动补发同一 prompt。
- 上线前需要重启对应 vibego worker 才能加载 `bot.py` 变更。


## 10. 方案更正：撤销 tmux/Codex TUI 内容探测

用户复核指出：通过读取 tmux/Codex TUI 的 `Queued follow-up inputs` 来判断是否已接收，属于高成本兜底，不是根因修复。该判断已撤销。

最终根因修复口径：

- `tmux_send_line` / `tmux_queue_line` 成功返回，是 vibego 到 tmux 的传输层确认；失败会抛 `CalledProcessError` 并返回给用户。
- Codex session JSONL 写入用户消息，只表示 Codex 开始消费该输入；当前轮忙碌时延迟写入是正常现象。
- 因此 Codex 不得再用“JSONL 暂未写入”触发自动重试；否则会把同一 prompt 放进 `Queued follow-up inputs` 两次。
- Copilot 仍保留原 JSONL 未确认后的自动重试契约，相关测试独立覆盖。

## 11. 更正后验证记录（2026-06-01 16:04 CST）

- ✅ `python3.11 -m pytest -q tests/test_tmux_send_line.py::test_dispatch_prompt_does_not_retry_codex_when_jsonl_not_confirmed tests/test_tmux_send_line.py::test_dispatch_prompt_retries_with_queue_for_copilot_when_user_prompt_not_confirmed tests/test_tmux_send_line.py::test_dispatch_prompt_reports_unconfirmed_after_retry_for_copilot tests/test_task_description.py::test_direct_photo_photo_text_burst_dispatches_once`
  - 结果：`4 passed, 2 warnings in 0.29s`
  - 覆盖：Codex JSONL 未确认不自动重试、Copilot 自动重试保留、普通图文聚合。
- ✅ `python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py`
  - 结果：`218 passed in 14.87s`
  - 覆盖：本次受影响的 tmux 投递与 Telegram 入站消息处理回归集合。

## 12. 最终催办后验证记录（2026-06-01 16:05 CST）

- ✅ `python3.11 -m pytest -q tests/test_tmux_send_line.py::test_dispatch_prompt_does_not_retry_codex_when_jsonl_not_confirmed tests/test_tmux_send_line.py::test_dispatch_prompt_retries_with_queue_for_copilot_when_user_prompt_not_confirmed tests/test_tmux_send_line.py::test_dispatch_prompt_reports_unconfirmed_after_retry_for_copilot tests/test_task_description.py::test_direct_photo_photo_text_burst_dispatches_once`
  - 结果：`4 passed in 0.20s`
- ✅ `python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py`
  - 结果：`218 passed in 14.85s`
- ✅ `python3.11 -m vibego_cli doctor`
  - 结果：Python/依赖/配置根/master DB 正常。
- ✅ `bash scripts/test_deps_check.sh`
  - 结果：runtime venv 与关键依赖正常。

## 13. 追加故障：Telegram typing 超时阻断 tmux 投递（2026-06-01 21:30 CST）

### 13.1 现象

用户在 HyphaFawnStudioBot 发送一条包含 Client 信息与集成文档链接的普通文本后，Telegram 侧显示已发送，但终端无输入、模型无回复。

### 13.2 影响

该消息没有进入 tmux/Codex TUI，属于入站消息已被 worker 收到但在投递前被 Telegram API 辅助动作异常打断。已经失败的这条 Telegram update 不会自动补投，需要用户在 worker 加载修复后重新发送。

### 13.3 根因证据

- `tmux capture-pane -p -t vibe-hyphafawnstudiobot:0.0 -S -220` 显示 Codex TUI 仍停在初始提示 `› Improve documentation in @filename`，未出现用户文本，证明 tmux 未收到。
- `~/.config/vibego/logs/codex/hyphafawnstudiobot/run_bot.log` 显示 update 处理过程中在 `bot.py::_handle_prompt_dispatch` 的 `await bot.send_chat_action(message.chat.id, "typing")` 抛出 `aiohttp_socks._errors.ProxyTimeoutError: Proxy connection timed out: 60`。
- 该 `typing` 动作发生在 `_dispatch_prompt_to_model` 之前，因此异常直接中断处理链路，导致没有执行 tmux 投递。

### 13.4 修法

- 将 `_handle_prompt_dispatch` 中的 Telegram `send_chat_action(..., "typing")` 改为 best-effort：
  - 成功：保留原 typing 提示体验；
  - 失败：仅记录 warning，并继续执行 `_dispatch_prompt_to_model`；
  - 不再让 Telegram 代理/API 抖动影响核心 tmux 投递。
- 新增回归测试 `test_handle_prompt_dispatch_ignores_chat_action_failure`，强制覆盖 typing 抛异常时 prompt 仍进入 `_dispatch_prompt_to_model`。
- 更新 `AGENTS.md` Facts：新增 “Telegram typing 动作非阻断约束”。

### 13.5 验证记录

- ✅ 红灯复现：新增测试后、修复前，`test_handle_prompt_dispatch_ignores_chat_action_failure` 因 `RuntimeError("Proxy connection timed out: 60")` 在 `send_chat_action` 处失败。
- ✅ 绿灯聚焦：`python3.11 -m pytest -q tests/test_task_description.py::test_handle_prompt_dispatch_ignores_chat_action_failure tests/test_task_description.py::test_handle_prompt_dispatch_uses_manual_mode_control`
  - 结果：`2 passed, 2 warnings in 0.11s`
- ✅ 受影响集合：`python3.11 -m pytest -q tests/test_task_description.py tests/test_tmux_send_line.py`
  - 结果：`219 passed in 14.87s`
- ✅ 运行诊断：`python3.11 -m vibego_cli doctor`
  - 结果：Python/依赖/配置根/master DB 正常。

### 13.6 风险与回滚

- 风险：`typing` 失败后用户不会看到 Telegram “正在输入”状态，但核心消息会继续入模；这是正确取舍。
- 回滚：恢复 `await bot.send_chat_action(...)` 直接抛错的旧逻辑会重新引入“Telegram API 抖动导致 tmux 完全收不到”的故障，不建议回滚。

### 13.7 运行环境落地记录

- ✅ 已将当前仓库源码重新安装到 pipx 运行环境：`/Users/david/.local/pipx/venvs/vibego`，版本仍为 `1.5.157`。
- ✅ 已重启 `hyphafawnstudiobot` worker，当前 `bot.pid=8108`。
- ✅ 运行环境变量已校验：`PROJECT_NAME=hyphafawnstudiobot`，`MODEL_WORKDIR=/Users/david/hypha/fawnStudio`，`CODEX_WORKDIR=/Users/david/hypha/fawnStudio`。
- ✅ tmux 会话已重新拉起：`vibe-hyphafawnstudiobot`，pane 命令为 `codex-aarch64-a`。
- ✅ `run_bot.log` 已出现 `Telegram 连接正常，Bot=HyphaFawnStudioBot`。
- ⚠️ 原 21:26 那条用户文本已经在旧 worker 中断于 `send_chat_action`，没有进入 tmux；修复后不会自动回放该 update，需要用户重新发送。
