# /TASK_0062 codex 的 jsonl 文件有更新（PLAN）

## 1. vibe（需求与现状）

### 1.1 问题描述
- Codex 升级后，JSONL 新增 `phase` 字段（出现 `commentary` 与 `final_answer`）。
- 当前 vibego 会把两类消息都推送到 Telegram。
- 目标：只保留 `final_answer` 推送，`commentary` 不推送。

### 1.2 可验证证据
- 本机会话样例（可复现命令）：

```bash
rg -n --max-count 40 '"phase"\s*:\s*"(commentary|final_answer)"' ~/.codex/sessions -S
```

- 命中样例显示 `response_item.payload.phase` 结构：
  - `..."phase":"commentary"...`
  - `..."phase":"final_answer"...`

### 1.3 验收口径（AC）
1. `phase=commentary` 的 Codex 消息不再发送到 Telegram。  
2. `phase=final_answer` 的 Codex 消息继续正常发送。  
3. 历史无 `phase` 的旧 JSONL 仍保持兼容并可发送。

## 2. design（方案与决策）

### 2.1 代码定位
- 事件提取入口：`bot.py` 的 `_extract_codex_payload()`。
- Telegram 实际发送入口：`bot.py` 的 `_deliver_pending_messages()`。

### 2.2 方案对比
- 方案 A（下游过滤）：在 `_deliver_pending_messages()` 发送前按原始事件再过滤 `phase`。  
  - 优点：发送前统一拦截。  
  - 缺点：耦合更深，需要把 `phase` 元数据一路透传。
- 方案 B（上游过滤，推荐🌟）：在 `_extract_codex_payload()` 提取阶段直接按 `phase` 过滤。  
  - 优点：改动小、风险低，不改变下游发送流程。  
  - 缺点：需覆盖 `agent_message/event_msg/response_item` 多种分支。

### 2.3 最终决策
- 采用 **方案 B（上游过滤）**。

## 3. develop（实现与验证）

### 3.1 代码变更
- `bot.py`
  - 新增常量：
    - `CODEX_MESSAGE_PHASE_COMMENTARY = "commentary"`
    - `CODEX_MESSAGE_PHASE_FINAL_ANSWER = "final_answer"`
  - 在 `_extract_codex_payload()` 增加 phase 过滤函数 `_should_deliver_message()`：
    - 有 `phase` 且为 `final_answer` -> 允许投递
    - 有 `phase` 且非 `final_answer`（含 `commentary`）-> 忽略
    - 无 `phase` -> 兼容旧行为，继续投递
  - 过滤覆盖分支：
    - `agent_message`
    - `event_msg.payload.type == agent_message`
    - `response_item.payload.type in {message, assistant_message}`
    - `payload.event == final`

- `tests/test_codex_jsonl_phase.py`（新增）
  - `commentary` 忽略
  - `final_answer` 投递
  - 无 `phase` 兼容
  - 未知 `phase` 忽略

### 3.2 测试结果
- `PYTHONPATH=. pytest -q tests/test_codex_jsonl_phase.py`  
  - `4 passed`
- `PYTHONPATH=. pytest -q tests/test_claudecode_jsonl.py`  
  - `5 passed`
- `PYTHONPATH=. pytest -q tests/test_task_description.py`  
  - `121 passed`

## 4. 参考资料（官方/可验证）
- Telegram Bot API `sendMessage`：  
  https://core.telegram.org/bots/api#sendmessage
- JSON Lines 格式说明：  
  https://jsonlines.org/

## 5. 案例补充（2026-02-06）

### 5.1 案例信息
- 用户提供会话：`rollout-2026-02-06T09-19-16-019c3088-2a77-7081-959d-06f67225c161`
- 现象：模型输出中出现“**PLAN 模式已进入（当前为 vibe 阶段）**”

### 5.2 证据与结论
- 该会话原始 `user_message`（JSONL 行 6/7）明确包含：
  - `进入 PLAN 模式，最后列出当前所触发的 agents.md 的阶段...`
- 该会话最终输出（JSONL 行 330/331）返回：
  - `PLAN 模式已进入（当前为 vibe 阶段）`

结论：
- 这是**提示词文本触发**进入 PLAN（由推送内容中明确写入“进入 PLAN 模式”导致），并非 CLI `/plan` 斜杠命令触发。
- 因此会出现“有些会话自动进入 PLAN、有些不进入”的体感差异：关键在于推送到模型的实际首段文本是否包含该触发语句。

### 5.3 方案记录（已确认）
- 在 vibego 的“推送到模型=PLAN”流程中，优先尝试两段式：
  1. 先发送 `/plan`（若可用）；
  2. 再发送任务正文；
  3. 若 `/plan` 不可用，自动回退为“文本触发 PLAN”（保留现有 `进入 PLAN 模式...` 前缀）。
- 同时配合 vibego 侧阶段门禁（FSM）实现“未确认不切换阶段”的硬约束。
