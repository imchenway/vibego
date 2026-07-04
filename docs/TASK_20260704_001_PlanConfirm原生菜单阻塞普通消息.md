# TASK_20260704_001 PlanConfirm 原生菜单阻塞普通消息

## 背景

用户在 Telegram 移动端反馈：`终端好像会卡在 yes 这个选项这里`，并附图显示 FawnStudio worker 在输出 `<proposed_plan>` 后，Telegram 又出现 Codex 原生 `Implement this plan?` 菜单说明；用户随后发送 `?`，Bot 回复“codex 思考中，正在持续监听模型响应结果中”。

## 现场证据

- 截图附件：`/Users/david/.config/vibego/data/telegram/vibego/2026-07-04/20260704_034636687-36d0d234e366.jpg`。
  - 可见模型计划输出后，Telegram 展示了 `Implement this plan?` 三项说明。
  - 用户在 11:46 发送 `?` 后收到“codex 思考中，正在持续监听模型响应结果中”。
- Codex 会话文件：`/Users/david/.codex/sessions/2026/07/04/rollout-2026-07-04T11-38-06-019f2b34-7895-7033-86a6-e2fb70a3bca7.jsonl`。
  - 03:43:45Z 出现 `item_completed`，类型为 `Plan`。
  - 同秒出现 assistant `<proposed_plan>...` 最终输出。
  - 同秒出现 `task_complete`，说明该轮模型已结束，不是仍在思考。
- worker 日志：`/Users/david/.config/vibego/logs/codex/fawnstudio/run_bot.log`。
  - `2026-07-04 11:43:47`：模型输出发送成功，并记录 `已发送 Plan 结束确认按钮`。
  - `2026-07-04 11:46:04`：收到用户后续消息，取消 previous watcher。
  - `2026-07-04 11:46:06`：`Codex session JSONL 未确认消费 prompt，但 tmux send 已成功；跳过自动重试以避免重复排队`。
- tmux capture：`vibe-fawnstudio:0.0`。
  - 当前仍停在 Codex 原生 `Implement this plan?` 菜单，光标位于 `No, stay in Plan mode`，底部提示 `Press enter to confirm or esc to go back`。

## 根因判断

已确认根因：Telegram 普通文本 / 快捷回复在 PlanConfirm 未处理时仍会按业务 prompt 送入 tmux；但 Codex TUI 此时不在普通输入框，而在原生 `Implement this plan?` 菜单，因此输入不会写入 JSONL，用户侧却收到“模型思考中”的 ack，表现为“卡住”。

代码证据：

- `bot.py` 锚点：`_handle_prompt_dispatch` 原先普通直聊总是进入 `_dispatch_prompt_to_model(... confirm_delivery=True)`，没有先检查 `CHAT_ACTIVE_PLAN_CONFIRM_TOKENS`。
- `bot.py` 锚点：`_deliver_pending_messages_locked` 原先即使输出含 `<proposed_plan>`，仍会给该模型消息挂“✅ 全部按推荐 / 🧩 部分按推荐”通用快捷回复，和后续 PlanConfirm 按钮形成误导。
- `bot.py` 锚点：`on_model_quick_reply_all` / `on_model_quick_reply_partial` 原先快捷回复在存在当前 session binding 时仍会派发业务 prompt。

## 修复方案

1. `<proposed_plan>` 模型输出不再挂通用“全部按推荐 / 部分按推荐”快捷回复；后续只让 PlanConfirm 按钮承担进入开发 / fresh / 留在 plan 的决策。
2. 增加 PlanConfirm 待处理 guard：当 `CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id]` 仍有有效会话，普通业务 prompt fail-closed，不发送 typing，不投递 tmux，只提示用户先处理 Plan 确认菜单。
3. 快捷回复入口同样检查该 guard，避免历史按钮或旧消息把“待决策项全部按模型推荐”送入 Codex 原生 Yes/No 菜单。

## TDD 记录

RED（先失败）：

```bash
python3.11 -m pytest -q \
  tests/test_plan_confirm_bridge.py::test_deliver_pending_messages_triggers_plan_confirm \
  tests/test_plan_confirm_bridge.py::test_direct_prompt_blocks_while_plan_confirm_pending
```

失败点：

- `<proposed_plan>` 仍带 `reply_markup=InlineKeyboardMarkup(...)`。
- PlanConfirm 待处理时，`_handle_prompt_dispatch` 仍调用 `_dispatch_prompt_to_model`。

补充 RED：

```bash
python3.11 -m pytest -q \
  tests/test_plan_confirm_bridge.py::test_quick_reply_all_blocks_while_plan_confirm_pending
```

失败点：

- 旧快捷回复仍调用 `_dispatch_prompt_to_model(chat_id, "待决策项全部按模型推荐", ...)`。

GREEN（修复后通过）：

```bash
python3.11 -m pytest -q \
  tests/test_plan_confirm_bridge.py::test_deliver_pending_messages_triggers_plan_confirm \
  tests/test_plan_confirm_bridge.py::test_direct_prompt_blocks_while_plan_confirm_pending \
  tests/test_plan_confirm_bridge.py::test_quick_reply_all_blocks_while_plan_confirm_pending
```

结果：`3 passed, 2 warnings`。warnings 是既有 Markdown 转义 docstring DeprecationWarning，本轮未处理。

## 影响面

- 影响文件：`bot.py`、`tests/test_plan_confirm_bridge.py`。
- 用户可见变化：
  - 计划收口后不再出现与 PlanConfirm 并列的通用“全部按推荐/部分按推荐”按钮。
  - 若用户直接发文字或点旧快捷回复，Bot 会明确提示“当前终端停在 Codex Plan 确认菜单”，不会再伪装成“思考中”。
- 不改动：PlanConfirm Yes / Fresh / No 的原有按钮语义与执行链路。

## 后续事项

- 实际 FawnStudio worker 需要升级 / 重启后才会应用本仓修复。
- 当前 tmux 现场仍停在原生菜单，需要用户通过终端或 Telegram PlanConfirm 按钮作出 Yes / Fresh / No 决策。

## 本轮追加验证

```bash
python3.11 -m pytest -q \
  tests/test_plan_confirm_bridge.py \
  tests/test_task_description.py::test_handle_prompt_dispatch_uses_current_terminal_mode \
  tests/test_task_description.py::test_handle_prompt_dispatch_ignores_chat_action_failure \
  tests/test_tmux_send_line.py::test_dispatch_prompt_does_not_retry_codex_when_jsonl_not_confirmed \
  && python3.11 -m py_compile bot.py
```

结果：`23 passed, 2 warnings`，随后 `py_compile` 退出码 0。warnings 为既有 `bot.py` MarkdownV2 docstring 转义提示。

```bash
python3.11 -m pytest -q tests/test_plan_progress.py
```

结果：`41 passed`。
