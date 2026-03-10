# TASK_20260310_006 并行任务路由前缀仅用于 Telegram 不入模

## 1. 背景

用户反馈：

- 在并行会话中点击 `↩️ 回复 /TASK_0093` 后，下一条消息发送到 Codex 时出现：

```text
Unrecognized command '/TASK_0093'. Type "/" for a list of supported commands.
```

用户确认口径：

- `TASK_0093` 只是 Telegram 侧的路由前缀
- 真正发送到模型时，仍应复用原有 `【强制规约】...以下是用户需求描述：` 前缀
- 不应把 `/TASK_0093` 原样送入 Codex CLI

## 2. 根因结论

### 2.1 手动任务前缀路由会把 `/TASK_xxxx` 原样入模

- `bot.py:8281-8289`
  - `_extract_task_prefixed_prompt(...)` 旧逻辑返回整个原始字符串
- `bot.py:16747-16757`
  - 手动输入 `/TASK_0093 xxx` 后，会把整段 `/TASK_0093 xxx` 交给 `_handle_prompt_dispatch(...)`

### 2.2 回复模式也会主动拼 `/TASK_xxxx`

- `bot.py:16759-16773`
  - 旧逻辑在并行回复模式下发送：
    - `f"/{reply_task_id} {prompt}"`

### 2.3 `/` 开头 prompt 会绕过强制规约前缀

- `bot.py:1818-1836`
  - `_prepend_enforced_agents_notice(...)` 对 slash command 直接透传
- 结果：
  - `/TASK_0093 xxx` 变成裸 slash command 送给 Codex
  - 被 Codex 识别成非法命令

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- worker 文本路由：`bot.py`
- 相关测试：
  - `tests/test_parallel_flow.py`
  - `tests/test_parallel_session_routing.py`

### 3.2 具体受影响单元

1. `bot.py`
   - `_extract_task_prefixed_prompt`
   - `on_text`
2. 测试：
   - `tests/test_parallel_flow.py`
   - `tests/test_parallel_session_routing.py`

### 3.3 直连依赖测试纳入依据

- `on_text(...)` 是 Telegram 文本主入口
- 并行回复模式与手动 `/TASK_xxxx ...` 都直接依赖这里的路由逻辑

### 3.4 测试范围升级判断

- 命中升级条件：✅ 是
- 原因：
  - 修改了 worker 公共消息路由逻辑

## 4. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_parallel_flow.py tests/test_parallel_session_routing.py -k "parallel_reply_mode_auto_prefixes_next_message or manual_task_prefix_routes_parallel_session_without_leaking_slash_prefix or parallel_reply_callback_and_followup_use_bound_dispatch_context"
```

结果：

- ✅ 基线相关护栏可运行

## 5. TDD 红灯

先修改测试期望：

- 并行回复模式不再把 `/TASK_xxxx` 原样入模
- 手动 `/TASK_xxxx xxx` 仅用于路由，真正入模文本只保留 `xxx`

首次执行：

```bash
python3.11 -m pytest -q tests/test_parallel_flow.py tests/test_parallel_session_routing.py -k "parallel_reply_mode_auto_prefixes_next_message or manual_task_prefix_routes_parallel_session_without_leaking_slash_prefix or parallel_reply_callback_and_followup_use_bound_dispatch_context"
```

结果：

- ❌ 旧逻辑仍把 `/TASK_0001 继续完善方案`
- ❌ 旧逻辑仍把 `/TASK_0093 继续补充`
- ❌ 手动输入 `/TASK_0093 继续补充` 仍把整段原样入模

满足“先红后绿”。

## 6. 最小实现

### 6.1 手动路由前缀只保留正文

- `_extract_task_prefixed_prompt(...)`
  - 从返回 `task_id + 全量原文`
  - 调整为返回 `task_id + 去掉前缀后的正文`

### 6.2 并行回复模式不再主动拼 `/TASK_xxxx`

- `on_text(...)`
  - 回复模式下改为直接发送用户正文
  - 路由依赖 `dispatch_context`
  - 不再依赖 slash 前缀

## 7. Self-Test Gate

执行两轮一致性回归：

```bash
python3.11 -m pytest -q tests/test_parallel_flow.py tests/test_parallel_session_routing.py -k "parallel_reply_mode_auto_prefixes_next_message or manual_task_prefix_routes_parallel_session_without_leaking_slash_prefix or parallel_reply_callback_and_followup_use_bound_dispatch_context"
python3.11 -m pytest -q tests/test_parallel_session_routing.py tests/test_model_quick_reply.py tests/test_parallel_flow.py tests/test_task_description.py -k "parallel_session_routing or quick_reply or parallel_reply or dispatch_prompt_rebinds_when_pointer_updates or stale or delete_parallel"
python3.11 -m pytest -q tests/test_parallel_session_routing.py tests/test_model_quick_reply.py tests/test_parallel_flow.py tests/test_task_description.py -k "parallel_session_routing or quick_reply or parallel_reply or dispatch_prompt_rebinds_when_pointer_updates or stale or delete_parallel"
```

结果：

- ✅ 路由定向用例：`3 passed`
- ✅ 第一轮回归：`19 passed`
- ✅ 第二轮回归：`19 passed`

## 8. 用户可见结果

1. 手动输入：

```text
/TASK_0093 继续补充
```

- Telegram 内仍能用 `/TASK_0093` 做路由
- 真正发给模型的文本是：

```text
继续补充
```

- 之后仍会由 `_prepend_enforced_agents_notice(...)` 追加原有强制规约前缀

2. 点击 `↩️ 回复 /TASK_0093`

- 下一条普通文本不再变成 `/TASK_0093 ...`
- 仅通过并行上下文路由到对应会话
