# TASK_20260529_003 Telegram 消息未进入 tmux 模型排查

## 1. 背景与现象

用户反馈：Telegram 发送的普通消息“经常没有发送到 tmux 的模型里”。该问题发生在 Telegram → vibego worker → tmux → Codex/模型 CLI 的直聊链路中。

当前阶段：只读调研与修复方案确认，尚未修改源码。

## 2. 现象 -> 影响 -> 根因 -> 修法 -> 验证

### 2.1 现象

- Telegram 侧消息已发出，vibego 也可能返回“已发送/已绑定会话”的确认。
- 但 tmux 中的模型 CLI 没有进入对应 prompt，或用户观察到模型没有开始处理该消息。
- 运行日志显示多次 `ack sent`，但没有 `tmux错误`；这说明 worker 认为 `tmux send-keys` 已成功，不等价于模型 CLI 已接受输入。
  - 证据：`/Users/david/.config/vibego/logs/codex/vibegobot/run_bot.log:35332-35340`。

### 2.2 影响

- 用户在 Telegram 继续追问或补充信息时，消息可能被注入到“当前正在运行的 turn”或被 Codex TUI 忽略，导致远端交互不可靠。
- 由于 vibego 已发送 ack，用户会误以为模型已收到，实际形成“静默丢消息”的体验。

### 2.3 根因判断

#### 根因 A（高置信度）：普通 Telegram 直聊默认仍走 Enter 立即提交，不具备 Codex 忙碌态排队语义

- 普通 Telegram 文本消息最终进入 `_handle_prompt_dispatch(...)`，该函数构造 `dispatch_kwargs` 时只传 `reply_to` 与 `intended_mode=None`，没有传 `send_mode`。
  - 证据：`bot.py:4694-4767`（锚点：`_handle_prompt_dispatch`、`dispatch_kwargs`、`_dispatch_prompt_to_model`）。
- `_dispatch_prompt_to_model(...)` 对缺省 `send_mode` 调用 `_normalize_push_send_mode(send_mode)`，默认落到 `PUSH_SEND_MODE_IMMEDIATE`，随后执行 `tmux_send_line(...)`。
  - 证据：`bot.py:2984-2997`（参数 `send_mode: Optional[str] = None`）、`bot.py:3209-3223`（`resolved_send_mode` 与 `tmux_send_line/tmux_queue_line` 分支）。
- `tmux_send_line(...)` 使用 `C-m`（Enter）提交当前 prompt；`tmux_queue_line(...)` 才使用模型排队键。
  - 证据：`bot.py:2037-2053`、`bot.py:2119-2133`。
- 既有设计文档明确记录：Codex 的 `Enter` 是注入当前 turn，`Tab` 是排队到下一 turn；此前仅在“任务详情 -> 推送到模型”入口增加排队选项，没有修改普通消息默认语义。
  - 证据：`docs/TASK_0091_在codex中可以按tab发送排队的消息我希望在vibego也可以发送这种消息.md:12-14`、`:35-41`、`:252`。

结论：当 Codex 正在执行、处于计划确认/更新、或 TUI 状态不接受当前 Enter 输入时，tmux 层 `send-keys` 会成功，但模型层不一定接收，形成用户看到的“Telegram 没发进 tmux 模型”。

#### 根因 B（中置信度）：当前缺少 per-chat/tmux 派发串行化与过渡态保护

- `_dispatch_prompt_to_model(...)` 会取消旧 watcher、重绑 session、立即发送 prompt，但未看到对同一 chat/tmux session 的发送锁。
  - 证据：`bot.py:3003-3033`（取消旧 watcher）、`bot.py:3217-3223`（直接调用 tmux 发送）。
- 最近 PlanConfirm native fresh 修复中，fresh 操作会驱动当前 Codex TUI 原生第二项并等待新 session；在新 session 绑定完成前，如果 Telegram 又来普通消息，可能仍按旧 session/当前 tmux 状态发送。
  - 证据：`AGENTS.md:34`（Telegram PlanConfirm 三选项约束记录当前 fresh 实现边界）；`bot.py` 中 `_drive_native_plan_confirm_fresh_context` 为 fresh 驱动入口（本任务下一阶段将以测试固化）。

结论：即使把默认发送改为排队，也应补充“同一 chat/tmux 的发送串行化或过渡态 fail-closed”作为加固项，避免 fresh/dispatch 竞争。

### 2.4 推荐修法

推荐采用“普通 Telegram 直聊默认 queued_when_supported + 显式入口保持原契约 + 过渡态最小保护”的一次性修复：

1. 为普通 Telegram 文本/附件直聊新增默认发送策略：
   - Codex/Copilot 等支持排队的模型默认走 `queued`，即 `tmux_queue_line(...)`。
   - 不支持排队的模型自动回退 `immediate`，保持兼容。
   - 任务详情“立即发送/排队发送”的显式选择不变。
2. 增加可回滚环境变量，例如 `DIRECT_MESSAGE_SEND_MODE`：
   - `queued_when_supported`：推荐默认值。
   - `immediate`：紧急回滚到旧行为。
   - `queued`：强制排队，若模型不支持仍由现有逻辑回退。
3. 增加直接消息派发测试，确保：
   - Codex 普通 Telegram 消息默认带 `send_mode=queued`。
   - 非排队模型不会被强行使用不可用按键。
   - 任务详情显式选择不受影响。
4. 在日志中记录最终 `send_mode`、tmux session、chat id，避免以后只能看到 `ack sent`，无法区分是 Enter 还是 Tab。

## 3. 方案对比

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. 只加日志，不改发送语义 | 记录每次 dispatch 的 send_mode 与 prompt 指纹 | 风险最低 | 不能解决丢消息，只能辅助排查 | 不推荐 |
| B. 普通直聊默认 queued_when_supported | Codex/Copilot 普通消息走排队键，其他模型回退 | 直接解决忙碌态丢消息；影响面小；可 env 回滚 | 空闲态语义会从“立即注入”偏向“下一轮排队”，但更符合 Telegram 异步入口 | 推荐 |
| C. 动态检测忙碌态，仅忙时 queue | 解析 tmux pane/模型状态，忙则 Tab、闲则 Enter | 理论语义最好 | Codex TUI 文案与状态不稳定，容易误判，维护成本高 | 暂不推荐 |
| D. 全局 Enter 替换为 Tab | 所有入口都走排队 | 实现最简单 | 破坏任务推送、plan switch、force exit 等既有契约 | 禁止 |

## 4. 受影响目录与契约变更

### 4.1 受影响目录

- `bot.py`
  - 增加普通 Telegram 直聊默认发送策略解析。
  - `_handle_prompt_dispatch(...)` 传递 `send_mode`。
  - `_dispatch_prompt_to_model(...)` 可补日志字段。
- `tests/test_task_description.py` / `tests/test_tmux_send_line.py`
  - 增加直接消息默认排队策略回归测试。
- `docs/`
  - 本任务文档记录设计、测试矩阵、风险与回滚。
- `AGENTS.md`
  - 实现后需补充/更新 “Telegram 普通消息发送约束” 事实表。

### 4.2 契约变更

- 普通 Telegram 直聊：从“缺省 immediate/Enter”变为“支持排队模型缺省 queued/Tab”。
- 任务详情推送：显式“立即发送 / 排队发送”契约保持不变。
- 非 Codex/Copilot 模型：不支持排队时仍回退 Enter。
- Telegram 回复展示、附件持久化、PlanConfirm 三选项：不改契约。

### 4.3 数据库变更

- 不涉及数据库表、字段、索引、迁移。

### 4.4 接口变更

- 不涉及 HTTP/API 契约。
- 仅新增本地环境变量回滚开关，不改变 Telegram 命令入口。

## 5. 测试矩阵

| 场景 | 入口 | 模型 | 预期 | 测试位置 |
|---|---|---|---|---|
| 普通 Telegram 文本 | `_handle_prompt_dispatch` | codex | `_dispatch_prompt_to_model` 收到 `send_mode=queued` | `tests/test_task_description.py` |
| 普通 Telegram 文本 | `_handle_prompt_dispatch` | claudecode/gemini | 不使用不可用排队键，回退 immediate | `tests/test_task_description.py` |
| 任务详情显式立即发送 | task push | codex | 仍 `send_mode=immediate` | 既有测试 + 必要回归 |
| 任务详情显式排队发送 | task push | codex | 仍 `tmux_queue_line` | 既有测试 |
| 排队发送底层按键 | `tmux_queue_line` | codex | 使用 Tab 且不发 Escape/双 Enter | 既有 `tests/test_tmux_send_line.py` |
| tmux 错误 | `_dispatch_prompt_to_model` | codex | 返回带 Tab 手工提示 | 既有/补充测试 |

## 6. 实施顺序

1. 先跑受影响 baseline：
   - `python3.11 -m pytest -q tests/test_task_description.py tests/test_tmux_send_line.py`
2. 先补失败测试：普通 Telegram 直聊默认 queued_when_supported。
3. 最小修改 `bot.py`：新增默认策略 helper，并在 `_handle_prompt_dispatch` 传入 `send_mode`。
4. 补日志与中文注释。
5. 跑聚焦测试两轮。
6. 更新 `AGENTS.md` 事实表与本文档完成状态。

## 7. 风险与回滚

### 风险

- 空闲 Codex 中普通 Telegram 直聊从 Enter 改为 Tab，可能让用户感觉“不是立即插入当前输入框”，但 Telegram 本身是异步入口，排队语义更安全。
- 如果某个模型宣称支持排队但实际按键变化，可能仍需单独适配 `_queue_send_submit_key()`。

### 回滚

- 若新增 `DIRECT_MESSAGE_SEND_MODE=immediate`，可通过环境变量恢复旧行为，无需回滚代码。
- 如果只需临时绕过，可在 `.env` 设置该变量并重启对应 worker。

## 8. 待确认决策

推荐选择 B：普通 Telegram 直聊默认 `queued_when_supported`，一次性解决“忙碌态静默丢消息”的核心问题，同时保留环境变量回滚能力。

## 9. 2026-05-29 追补：同样消息重发能成功后的判断修正

用户补充：“同样的消息我再发一遍又能发出去”。该信息说明问题不应按“固定路由错误”处理，而应收敛为**瞬时状态/竞态/未确认消费**：同一文本内容本身没有被过滤或永久拒绝，第一次失败与第二次成功的差异更可能来自发送时刻的 tmux/Codex TUI 状态、会话绑定窗口或 Telegram 入站处理状态。

### 9.1 需要区分的两种现场

1. 首条失败消息 **没有收到** “模型思考中/sessionId” ack：
   - 更偏向 Telegram polling/proxy、入站 middleware、长文本聚合等待窗口。
   - 证据链入口：`bot.py:699`（Dispatcher）、`bot.py:24857`（`dp.start_polling`）、`bot.py:714-725`（入站消息先安排补偿轮询与文本聚合判断）。
2. 首条失败消息 **收到了** ack，但 tmux/Codex 没处理：
   - 更偏向 tmux send-keys 已成功但 Codex TUI 未消费输入。
   - 证据链入口：`bot.py:2808-2837`（ack 仅表示 worker 发送确认）、`bot.py:3209-3223`（发送到 tmux）、`bot.py:3235-3379`（ack 后才启动监听）。

### 9.2 修法调整

在原“默认 queued_when_supported”的基础上，推荐补充**投递自校验 + 自动重试**：

1. 发送前记录 session 文件偏移与 prompt 指纹。
2. `tmux_send_line/tmux_queue_line` 返回后，在短窗口内检查 Codex session JSONL 是否出现新的 `role=user` / `event_msg.user_message` 且包含该 prompt 指纹。
3. 若未确认消费：
   - 第一次自动用 queued 方式重试；
   - 重试仍未确认时，不再假装成功，Telegram 明确提示“tmux 已注入但模型未确认消费，请稍后重试/检查终端”。
4. ack 文案应区分：
   - “已注入 tmux，等待模型确认消费”；
   - “模型已确认收到，开始监听回复”。

该调整能解释“同样消息重发能成功”：用户手工第二次发送相当于一次人工重试；系统应把这个重试自动化，并用 session JSONL 作为确认依据，避免静默丢失。

## 10. 2026-05-29 DEVELOP：投递确认 + 自动重试已实现

### 10.1 实现结论

本次按用户确认的方案 B 完成最小修复：普通 Telegram 直聊与 Codex `/goal` 入口不再只相信 `tmux send-keys` 成功，而是在发送后检查模型 session JSONL 是否出现本轮用户输入；若短窗口内未确认，则自动重试一次，优先使用 queued/Tab；重试后仍未确认时，Telegram 明确提示“模型未确认收到”，避免继续假装已提交成功。

### 10.2 受影响文件

| 文件 | 变化 | 证据锚点 |
|---|---|---|
| `bot.py` | 新增投递确认配置与回滚开关：`PROMPT_DELIVERY_CONFIRM_ENABLED`、`PROMPT_DELIVERY_CONFIRM_TIMEOUT_SECONDS`、`PROMPT_DELIVERY_CONFIRM_POLL_INTERVAL_SECONDS`、`PROMPT_DELIVERY_CONFIRM_RETRY_ENABLED` | `bot.py:354-359` |
| `bot.py` | 新增 session JSONL 用户输入确认解析与匹配逻辑 | `bot.py:2871-3036` |
| `bot.py` | 新增 `_confirm_or_retry_prompt_delivery(...)`，未确认时自动重试一次，失败后明确提示 | `bot.py:3039-3121` |
| `bot.py` | `_dispatch_prompt_to_model(...)` 增加 `confirm_delivery` 参数，并在绑定 session 前执行确认/重试；确认成功后用发送前 offset 初始化，降低快速响应被跳过风险 | `bot.py:3243-3257`、`bot.py:3477-3501`、`bot.py:3579-3608` |
| `bot.py` | 普通 Telegram 直聊启用 `confirm_delivery=True` | `bot.py:5049-5062` |
| `bot.py` | `/goal` 设置/查看/暂停/恢复/清除启用 `confirm_delivery=True` | `bot.py:18495-18502` |
| `tests/test_tmux_send_line.py` | 增加“未确认自动排队重试”和“重试后仍未确认则失败提示”回归测试 | `tests/test_tmux_send_line.py:412-505` |
| `tests/test_task_description.py` | 增加普通文本直聊启用投递确认；`/goal` 透传测试补充 `confirm_delivery=True` 断言 | `tests/test_task_description.py:6452-6510` |

### 10.3 `/goal` “像暂停”的处理口径

用户补充：设置目标的消息发送出去后，好像自动处于暂停状态。本次没有在 vibego 内部模拟或改写 Codex goal 状态，因为 Codex active thread 仍是唯一事实源；但已经修复一个关键误报：`on_goal_objective_input(...)` 只有在 `_dispatch_goal_command(...)` 返回成功时才提示“已提交 Codex /goal 目标”，而 `_dispatch_goal_command(...)` 现在会要求 session JSONL 确认本轮 `/goal <objective>` 输入已被 Codex 消费。

这意味着：

- 如果 `/goal <objective>` 没有真正进入 Codex active thread，Telegram 不会再误报“已提交”。
- 如果 Codex 已确认消费，但 Codex 自身显示 paused，则该状态属于 Codex goal 运行态，需要后续以 Codex JSONL 的 `get_goal/update_goal` 或终端状态继续取证，不在本次投递确认修复中伪造状态。

### 10.4 契约变更

- 普通 Telegram 直聊：发送后必须确认 Codex/Copilot session JSONL 出现本轮用户输入；未确认自动重试一次。
- `/goal`：同样要求投递确认，避免“目标没进入 active thread 但 Telegram 提示已提交”。
- 默认只对 Codex/Copilot 启用确认；Gemini/ClaudeCode 暂不启用，避免不同会话格式误判。
- 可回滚：设置 `PROMPT_DELIVERY_CONFIRM_ENABLED=0` 可关闭确认；设置 `PROMPT_DELIVERY_CONFIRM_RETRY_ENABLED=0` 可关闭自动重试。

### 10.5 测试与验证记录

1. Baseline（修改前）：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'goal or dispatch_prompt' tests/test_tmux_send_line.py
# 33 passed, 173 deselected in 11.29s
```

2. 红灯测试（新增测试后，修改源码前）：

```bash
python3.11 -m pytest -q \
  tests/test_tmux_send_line.py::test_dispatch_prompt_retries_with_queue_when_user_prompt_not_confirmed \
  tests/test_tmux_send_line.py::test_dispatch_prompt_reports_unconfirmed_after_retry \
  tests/test_task_description.py::test_on_text_direct_prompt_enables_delivery_confirmation \
  tests/test_task_description.py::test_goal_command_dispatches_objective_to_codex
# 4 failed：缺少 confirm_delivery 参数，普通直聊与 /goal 未启用投递确认
```

3. 新增测试转绿：

```bash
python3.11 -m pytest -q \
  tests/test_tmux_send_line.py::test_dispatch_prompt_retries_with_queue_when_user_prompt_not_confirmed \
  tests/test_tmux_send_line.py::test_dispatch_prompt_reports_unconfirmed_after_retry \
  tests/test_task_description.py::test_on_text_direct_prompt_enables_delivery_confirmation \
  tests/test_task_description.py::test_goal_command_dispatches_objective_to_codex
# 4 passed, 2 warnings in 0.14s
```

4. 聚焦回归：

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py -k 'goal or dispatch_prompt or on_text_direct_prompt'
# 36 passed, 173 deselected in 11.28s
```

5. 受影响测试双轮：

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py
# 第 1 轮：209 passed in 15.03s
# 第 2 轮：209 passed in 14.94s
```

6. 关联回归：

```bash
python3.11 -m pytest -q tests/test_chat_menu_buttons.py tests/test_codex_jsonl_phase.py tests/test_session_binding.py
# 69 passed in 0.68s
```

7. 运行诊断：

```bash
python3.11 -m vibego_cli doctor
# python_ok=true，dependencies=[]
```

### 10.6 风险与回滚

- 风险：如果 Codex/Copilot JSONL 未来改变用户消息结构，可能出现误判未确认；此时 Telegram 会提示失败并要求重发，不会静默吞消息。
- 风险：自动重试可能在“模型已消费但 JSONL 尚未写入/解析失败”时造成重复输入；本次限制为一次，并优先使用 queued，以降低对当前 turn 的干扰。
- 回滚：在 worker `.env` 中设置 `PROMPT_DELIVERY_CONFIRM_ENABLED=0` 并重启 worker，可恢复旧的“只信任 tmux send-keys”行为。
