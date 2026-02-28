# TASK_20260228_001 Telegram 补偿轮询与“永远落后一条”修复

## 1. 背景

当 Telegram 侧落后于终端输出时，用户再次发送一条消息会触发补发 backlog。  
但此前存在两个问题：

1. 即时轮询命中 backlog 后提前 `return`，导致当前在途回复未继续监听（表现为“永远落后一条”）。
2. 缺少长周期兜底检测，当 watcher 异常退出后，后续输出可能长期无人补发。

## 2. 已确认决策（来自终端确认）

- 修复策略：保留即时轮询 + 补建监听（不中断现有能力）。
- 触发范围：仅 Telegram `Message` 入站事件（文本/命令/媒体等都算）。
- 检测节奏：固定 5 次，分别在 **1/3/10/30/90 分钟**。
- 命中行为：一旦检测到并成功回传新消息，立即停止后续检测。
- 与 watcher 关系：作为 watcher 兜底补偿并行运行（靠 offset/hash 去重）。
- 运行态：仅内存态；进程重启不续跑。
- 并发策略：同一 `chat_id` 新消息覆盖旧补偿任务。

## 3. 代码变更

### 3.1 `bot.py`

1. 新增补偿轮询常量：
   - `MESSAGE_RECOVERY_POLL_DELAYS_SECONDS = (60, 180, 600, 1800, 5400)`

2. 新增补偿轮询任务管理：
   - `CHAT_MESSAGE_RECOVERY_POLL_TASKS`
   - `_schedule_message_recovery_poll(...)`
   - `_cancel_message_recovery_poll(...)`
   - `_run_message_recovery_poll(...)`
   - `_probe_new_model_message_once(...)`

3. 在 `TextPasteAggregationMiddleware` 中统一触发补偿调度：
   - 任意真实 `Message` 入站都会安排补偿轮询；
   - 同 chat 自动覆盖旧任务；
   - 内部合成消息自动跳过。

4. 修复“永远落后一条”主因：
   - `_dispatch_prompt_to_model(...)`：
     - 即时轮询命中后不再提前返回；
     - 仍会补建 watcher；
     - 使用 `start_in_long_poll=True` 避免重复完成前缀。
   - `_ensure_session_watcher(...)`：
     - 即时补发命中后不再提前返回；
     - 保持补建 watcher 逻辑一致。

5. 内部合成消息统一治理（避免误触发补偿轮询）：
   - 新增 `_build_internal_synthetic_message_id(...)`，统一采用大偏移 message_id；
   - `_dispatch_task_new_command(...)`、`_fallback_task_detail_back(...)` 注入消息前打标；
   - 继续复用 `TEXT_PASTE_SYNTHETIC_GUARD` 跳过二次处理。

### 3.2 测试

新增 `tests/test_message_recovery_poll.py`：

- 补偿轮询命中后提前停止；
- 同 chat 新消息覆盖旧补偿任务；
- 内部合成消息不触发补偿轮询；
- `_dispatch_prompt_to_model` 即时命中后仍补建 watcher；
- `_ensure_session_watcher` 即时命中后仍补建 watcher。

## 4. 自测记录

开发前基线（相关测试）：

```bash
.venv314/bin/python -m pytest -q tests/test_long_poll_mechanism.py tests/test_tmux_send_line.py
```

结果：`14 passed`

开发后回归（本次改动相关）：

```bash
.venv314/bin/python -m pytest -q \
  tests/test_message_recovery_poll.py \
  tests/test_long_poll_mechanism.py \
  tests/test_tmux_send_line.py \
  tests/test_task_list_entry.py \
  tests/test_task_detail_back.py \
  tests/test_task_description.py -k "dispatch_prompt or ensure_session_watcher"
```

结果：`24 + 39 + 15 = 78 passed`（分批执行，均通过）

## 5. 参考文档（官方）

- Telegram Bot API `Update`：<https://core.telegram.org/bots/api#update>
- Telegram Bot API `getUpdates`：<https://core.telegram.org/bots/api#getupdates>
- Python `asyncio` Task：<https://docs.python.org/3/library/asyncio-task.html>
