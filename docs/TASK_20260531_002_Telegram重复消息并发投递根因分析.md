# TASK_20260531_002 Telegram 重复消息并发投递根因分析

## 1. 任务背景

用户询问：“为什么会发送重复的消息到 telegram？”

本次处于 PLAN / 只读调研阶段，未修改实现代码。Comet/OpenSpec 检测结果：当前仓库执行 `openspec list --json` 返回 `No OpenSpec changes directory found. Run 'openspec init' first.`，因此按 AGENTS 的 plan 流程降级执行只读排障。

## 2. 现象确认

运行期日志显示同一个 chat、同一个 Codex session 在极短时间内出现两组并发投递日志：

- `2026-05-31 19:49:26` 同一 session 连续两次 `检测到待发送的模型事件` 与 `准备发送模型输出`，随后 `2026-05-31 19:49:27` 连续两次 `完成单条消息发送` 与 `模型输出发送成功`。
- `2026-05-31 20:16:49`、`2026-05-31 20:26:06~20:26:08` 也出现同类双发。

运行期证据：`~/.config/vibego/logs/vibe.log`（锚点：`205417`~`205425`、`205559`~`205567`、`205605`~`205614`）。

## 3. 代码证据链

| 证据点 | 结论 | 锚点 |
| --- | --- | --- |
| 多个入口会调用同一个投递函数 | 即时轮询、watcher、补偿轮询、绑定后即时检查、并行 watcher 均可能进入 `_deliver_pending_messages` | `bot.py`（锚点：`_handle_prompt_dispatch` 中 `SESSION_POLL_TIMEOUT` 循环调用 `_deliver_pending_messages`；`_watch_and_notify`；`_probe_new_model_message_once`；`_resume_session_from_selected_target`；`_watch_parallel_session_and_notify`） |
| 投递函数没有 per chat/session 互斥锁 | `CHAT_DELIVERED_OFFSETS` / `CHAT_DELIVERED_HASHES` 是普通内存 set，当前只在发送成功后更新 | `bot.py`（锚点：`_deliver_pending_messages`、`delivered_offsets = _get_delivered_offsets(...)`、`if event_offset in delivered_offsets`、`reply_large_text(...)`、`delivered_offsets.add(event_offset)`） |
| 去重发生在发送前检查、发送后登记 | 两个协程并发时都能在登记前通过 hash/offset 检查，然后各自发送一次 | `bot.py`（锚点：`initial_hash in delivered_hashes`、`reply_large_text`、`delivered_hashes.add(...)`、`delivered_offsets.add(event_offset)`） |
| watcher 中断是软中断 | `_interrupt_long_poll` 只标记 `CHAT_LONG_POLL_STATE[chat_id]['interrupted'] = True`，不取消正在执行或已进入 `_deliver_pending_messages` 的协程 | `bot.py`（锚点：`_interrupt_long_poll`、`CHAT_WATCHERS[chat_id] = watcher_task`） |
| 网络异常会放大重入窗口 | 日志显示 Telegram 代理超时触发 `_update_plan_progress` 失败，随后同 session 重试/轮询继续 | `~/.config/vibego/logs/vibe.log`（锚点：`205327`~`205416`）；`bot.py`（锚点：`_send_with_retry`、`_update_plan_progress`） |

## 4. 根因判断

高置信度根因不是 Codex 同一答案写了两份，也不是 Markdown 降级导致重发；这些旧问题已有保护：

- `event_msg.agent_message(final_answer)` 已被忽略，仅 `response_item.message / assistant_message` 进入正式投递。
  - 证据：`bot.py`（锚点：`_extract_codex_payload` 中 `event_msg.agent_message` 统一 `return None`）；`tests/test_codex_jsonl_phase.py`（锚点：`test_extract_codex_event_msg_final_answer_phase_ignored`）。
- 同文本重复与同时间戳 `assistant_message/message` 双写已有测试覆盖。
  - 证据：`tests/test_plan_progress.py`（锚点：`test_duplicate_messages_sent_once`、`test_codex_mixed_final_answer_prefers_response_item_once`、`test_codex_response_item_and_assistant_message_same_timestamp_only_delivered_once`）。

本次日志更符合 **并发投递竞态**：两个投递协程同时处理同一个 session offset；因为 offset/hash 在 Telegram 发送成功后才登记，两个协程都看到“未发送”，于是都调用 `reply_large_text`，最终 Telegram 收到两条相同消息。

触发条件通常是以下因素叠加：

1. 同一 chat/session 存在多个投递入口同时活跃：快速轮询、延迟 watcher、补偿轮询或重复绑定。
2. Telegram 代理超时/网络异常导致上一次计划进度或模型事件没有稳定提交 offset。
3. `_deliver_pending_messages` 缺少 per `(chat_id, session_key)` 级别的互斥与“发送前 claim”。

## 5. 影响范围

| 范围 | 是否受影响 | 原因 |
| --- | --- | --- |
| `bot.py` 模型回复投递 | 是 | `_deliver_pending_messages` 是重复发送的核心入口。 |
| `bot.py` 计划进度更新 | 间接受影响 | 计划进度发送失败会让 offset 停留在旧位置，放大后续重复投递窗口。 |
| `bot.py` watcher / 补偿轮询 | 是 | 多入口并发调用同一投递函数。 |
| `tests/test_plan_progress.py` | 是 | 需要新增并发回归测试，现有测试只覆盖串行去重。 |
| `tests/test_task_description.py` / media group | 暂不作为主因 | 该路径解决的是入站图文被拆成多次 prompt 的问题，不是本次日志中的同 session 输出双发。 |
| 数据库 / SQLite schema | 不受影响 | 重复发送状态为内存态，当前未发现 DB schema 参与。 |
| 前端/UI | 不受影响 | 这是 Telegram worker 后端投递链路问题。 |

## 6. 修复方案设计

### 方案 A：在 `_deliver_pending_messages` 外层增加 per chat/session 异步锁（推荐）

- 为 `(chat_id, session_key)` 建立 `asyncio.Lock`。
- 任一入口进入 `_deliver_pending_messages` 前先获取锁；同一 chat/session 串行处理。
- 保留现有 offset/hash 逻辑，改动小，能覆盖 watcher/补偿轮询/即时轮询全部入口。

优点：最小改动、风险低、直接消灭并发双发窗口。
缺点：同一 chat/session 的消息投递会串行；但 Telegram 本来就是有序对话，影响可接受。

### 方案 B：发送前 claim offset/hash，失败再回滚

- 在调用 Telegram API 前就把 event offset 标记为 in-flight。
- 成功后转为 delivered；失败后释放或回滚。

优点：即使没有锁，也能避免同 offset 双发。
缺点：异常分支复杂，Telegram “请求已到达但响应失败”的不确定性更难裁决，容易引入漏投递。

### 方案 C：只调整 watcher/补偿轮询触发顺序

- 避免同一时刻启动多个 watcher 或补偿轮询。

优点：表面上能降低概率。
缺点：不能保证所有入口不会并发，属于治标，不推荐。

推荐：**方案 A + 必要时补一个 in-flight 保护集合**。先用 per chat/session lock 关闭主竞态，再根据测试暴露情况决定是否需要 in-flight 状态。

## 7. 测试矩阵

| 测试点 | 预期 |
| --- | --- |
| 并发两次调用 `_deliver_pending_messages` 处理同一 session 同一 event | Telegram 只发送一次；第二个调用看到 offset/hash 已登记后跳过。 |
| 计划进度发送失败后恢复轮询 | 不因失败产生两条相同最终答案。 |
| 现有 Codex JSONL 双流保护 | `event_msg.agent_message(final_answer)` 继续不投递。 |
| 同时间戳 `assistant_message/message` | 仅保留正式 `message`。 |
| 串行重复文本 | 仍只发送一次。 |
| Telegram 网络异常 | 不因异常通知/重试造成无限刷屏；失败提示仍有冷却。 |

本次已执行的只读验证命令：

```bash
python3.11 -m pytest -q \
  tests/test_plan_progress.py::test_duplicate_messages_sent_once \
  tests/test_plan_progress.py::test_codex_mixed_final_answer_prefers_response_item_once \
  tests/test_plan_progress.py::test_codex_response_item_and_assistant_message_same_timestamp_only_delivered_once \
  tests/test_codex_jsonl_phase.py::test_extract_codex_event_msg_final_answer_phase_ignored
```

结果：`4 passed in 0.04s`。

## 8. 实施顺序（待用户确认进入 develop）

1. Baseline：运行 `tests/test_plan_progress.py` 中现有去重/投递测试。
2. TDD 红灯：新增并发调用 `_deliver_pending_messages` 的失败测试，模拟两个协程同时处理同一个 JSONL 事件。
3. 最小实现：新增 per `(chat_id, session_key)` delivery lock，并确保异常后释放。
4. 回归：运行受影响测试文件，至少两轮一致。
5. 文档/证据：若行为变更成立，更新 AGENTS Facts Table 中 Telegram 模型回复投递约束。

## 9. 风险与回滚

- 风险：锁粒度过大可能让同一 chat/session 的计划更新和最终答案顺序更严格，但这符合 Telegram 对话预期。
- 风险：如果锁没有在异常路径释放，会阻塞后续投递；实现必须用 `async with`。
- 回滚：移除 delivery lock 与新增测试即可恢复旧行为；不涉及数据库迁移。

## 10. Develop 实施记录（2026-05-31）

用户确认“待决策项全部按模型推荐”，因此按推荐方案 A 进入 develop。Comet hotfix 技能已读取，但当前仓库 `openspec list --json` 仍提示未初始化 OpenSpec changes 目录；本次按仓库 AGENTS 的 `vibe -> design -> develop` 与 TDD 门禁直接实施。

### 10.1 Baseline Gate

```bash
python3.11 -m pytest -q tests/test_plan_progress.py tests/test_codex_jsonl_phase.py
```

结果：`43 passed in 0.10s`。

### 10.2 TDD 红灯

新增测试：`tests/test_plan_progress.py::test_deliver_pending_messages_concurrent_calls_send_once`。

测试构造：

1. 同一 session 写入 1 条 Codex final answer。
2. 第一条 `_deliver_pending_messages` 在 `reply_large_text` 中挂起，模拟 Telegram 发送尚未完成、offset/hash 尚未登记。
3. 第二条 `_deliver_pending_messages` 并发进入同一 chat/session。
4. 期望 Telegram 只发送 1 次。

首次运行结果：失败，`len(replies) == 2`，日志同时出现两次“准备发送模型输出”和两次“模型输出发送成功”，复现线上根因。

```bash
python3.11 -m pytest -q tests/test_plan_progress.py::test_deliver_pending_messages_concurrent_calls_send_once
```

结果：`1 failed`。

### 10.3 最小实现

| 文件 | 修改 |
| --- | --- |
| `bot.py` | 新增 `CHAT_DELIVERY_LOCKS`，用 `(chat_id, session_key)` 作为投递锁 key；新增 `_get_delivery_lock(...)`；将原 `_deliver_pending_messages(...)` 正文下沉为 `_deliver_pending_messages_locked(...)`，外层 `_deliver_pending_messages(...)` 通过 `async with` 串行化同一 chat/session 投递。 |
| `tests/test_plan_progress.py` | 新增并发重复投递回归测试；测试 fixture 清理 `CHAT_DELIVERY_LOCKS`，避免跨用例状态污染。 |
| `AGENTS.md` | Facts Table 增补“Telegram 模型回复并发投递互斥约束”。 |

### 10.4 Green Gate

```bash
python3.11 -m pytest -q tests/test_plan_progress.py::test_deliver_pending_messages_concurrent_calls_send_once
```

结果：`1 passed, 2 warnings in 0.11s`。

### 10.5 回归验证

第一轮：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py tests/test_codex_jsonl_phase.py
```

结果：`44 passed in 0.09s`。

### 10.6 契约变化

- 同一 `chat_id + session_key` 的模型输出投递从“多入口可并发执行”改为“串行执行”。
- 不改变 Telegram 文案、Markdown 表格转换、memory citation 剥离、PlanConfirm、request_user_input 等既有外部展示契约。
- 不涉及 SQLite schema、配置项、新依赖、构建链或 CI。

### 10.7 风险与回滚

- 风险：同一 chat/session 中模型输出投递严格串行，极端情况下后一轮补偿轮询会等待前一轮 Telegram API 调用完成；这是为避免重复发送做出的可接受取舍。
- 回滚：移除 `CHAT_DELIVERY_LOCKS`、`_get_delivery_lock` 与 `_deliver_pending_messages_locked` 外层包装，删除新增测试与 AGENTS Facts 行。

### 10.8 最终验证补充

受影响测试第二轮：

```bash
python3.11 -m pytest -q tests/test_plan_progress.py tests/test_codex_jsonl_phase.py
```

结果：`44 passed in 0.09s`。

全量测试第一轮：

```bash
python3.11 -m pytest -q
```

结果：`938 passed, 6 warnings in 31.75s`。6 个 warnings 均来自既有 `tests/test_unescape_markdown.py` 测试函数返回 bool 的 PytestReturnNotNoneWarning，本次未修改该文件。

全量测试第二轮：

```bash
python3.11 -m pytest -q
```

结果：`938 passed, 6 warnings in 30.38s`，与第一轮一致。

运行诊断：

```bash
python3.11 -m vibego_cli doctor
```

结果：`python_ok=true`，配置根、env、projects、master_db 均存在。

依赖自检：

```bash
bash scripts/test_deps_check.sh
```

结果：依赖检查通过，`aiogram`、`aiohttp`、`aiosqlite` 均已安装。
