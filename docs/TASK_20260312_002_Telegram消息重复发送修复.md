# TASK_20260312_002 Telegram 消息重复发送修复

## 1. 背景

- 用户现象：同一轮 Codex 输出在 Telegram 中有时会像“发送了两遍”。
- 本次定位会话：
    - `~/.codex/sessions/2026/03/12/rollout-2026-03-12T14-21-54-019ce0b5-7480-7502-a243-8473fa8d8b8e.jsonl`
- 高置信度根因：
    - 同一轮同时存在：
        - `event_msg.payload.type=agent_message, phase=final_answer`
        - `response_item.payload.type=message, phase=final_answer`
    - 当前 `bot.py::_extract_codex_payload()` 会把两路都投递给 Telegram。
    - 当两路文本不一致时，现有按文本 hash 去重无法拦住双发。

## 2. Class Impact Plan

### 2.1 受影响单元

| 单元                                         | 实现文件     | 测试文件                              |
|--------------------------------------------|----------|-----------------------------------|
| `_extract_codex_payload()`                 | `bot.py` | `tests/test_codex_jsonl_phase.py` |
| `_deliver_pending_messages()` 的 Codex 投递契约 | `bot.py` | `tests/test_plan_progress.py`     |

### 2.2 直连依赖测试

- `tests/test_codex_jsonl_phase.py`
    - 直接验证 Codex `event_msg / response_item` 提取规则。
- `tests/test_plan_progress.py`
    - 直接验证 session 中混合事件进入 Telegram 发送链后只发一次。

### 2.3 测试范围升级判断

- 结论：❌ 未命中升级条件
- 原因：仅调整 Codex JSONL 提取逻辑，不涉及构建链、数据库、共享 DTO 或外部契约。

## 3. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_codex_jsonl_phase.py
python3.11 -m pytest -q tests/test_plan_progress.py -k 'duplicate_messages_sent_once'
```

结果：

- ✅ `7 passed`
- ✅ `1 passed, 18 deselected`

## 4. TDD 红灯

先改测试：

- `tests/test_codex_jsonl_phase.py`
    - 将 `event_msg.agent_message + final_answer` 的期望改为 **忽略**
- `tests/test_plan_progress.py`
    - 新增 `test_codex_mixed_final_answer_prefers_response_item_once`
    - 模拟同一 session 中：
        1. `event_msg.agent_message(final_answer)` 短消息
        2. `response_item.message(final_answer)` 完整消息
    - 期望 Telegram 仅发送一次，且正文来自 `response_item`

首次执行：

```bash
python3.11 -m pytest -q tests/test_codex_jsonl_phase.py -k 'event_msg_final_answer_phase_ignored'
python3.11 -m pytest -q tests/test_plan_progress.py -k 'codex_mixed_final_answer_prefers_response_item_once'
```

结果：

- ❌ `event_msg.agent_message(final_answer)` 仍被投递
- ❌ 同一批次仍发送两次模型输出

## 5. 最小实现

文件：`bot.py`

调整点：

- 在 `_extract_codex_payload()` 中，将 `event_msg.payload.type == "agent_message"` 统一视为 **Codex 镜像流**
- 即使带 `phase=final_answer`，也 **不再直接投递 Telegram**
- Codex 最终正文仅由：
    - `response_item.payload.type=message`
    - `response_item.payload.type=assistant_message`
      负责

这样保留现有：

- `update_plan`
- `request_user_input`
- `_deliver_pending_messages()` offset / hash / PlanConfirm

等逻辑不变，属于最小修复。

## 6. Self-Test Gate

### 6.1 红灯回归

```bash
python3.11 -m pytest -q tests/test_codex_jsonl_phase.py -k 'event_msg_final_answer_phase_ignored'
python3.11 -m pytest -q tests/test_plan_progress.py -k 'codex_mixed_final_answer_prefers_response_item_once'
```

结果：

- ✅ `1 passed, 6 deselected`
- ✅ `1 passed, 19 deselected`

### 6.2 类级回归（连续两轮）

第一轮：

```bash
python3.11 -m pytest -q tests/test_codex_jsonl_phase.py
python3.11 -m pytest -q tests/test_plan_progress.py -k 'duplicate_messages_sent_once or codex_mixed_final_answer_prefers_response_item_once'
```

结果：

- ✅ `7 passed`
- ✅ `2 passed, 18 deselected`

第二轮：

```bash
python3.11 -m pytest -q tests/test_codex_jsonl_phase.py
python3.11 -m pytest -q tests/test_plan_progress.py -k 'duplicate_messages_sent_once or codex_mixed_final_answer_prefers_response_item_once'
```

结果：

- ✅ `7 passed`
- ✅ `2 passed, 18 deselected`

## 7. 用户可见变化

1. Codex 同一轮出现“镜像短消息 + 完整最终答案”时，Telegram 仅收到一条最终消息。
2. `<proposed_plan>` 这类完整收口内容以 `response_item` 为准，不再被前面的镜像短消息抢先发出。
3. 现有普通重复文本去重逻辑仍保留。

## 8. 风险与回滚

### 风险

- 若未来 Codex 上游改成“只有 `event_msg.agent_message(final_answer)`，没有 `response_item`”，本次修复会导致该类最终答案不再投递。
- 目前仓库抽样未发现这种样本，因此本轮先采用最小修复。

### 回滚

- 回滚 `bot.py::_extract_codex_payload()` 对 `event_msg.agent_message` 的忽略逻辑
- 回滚测试：
    - `tests/test_codex_jsonl_phase.py`
    - `tests/test_plan_progress.py`
