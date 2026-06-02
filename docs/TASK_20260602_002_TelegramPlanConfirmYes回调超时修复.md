# TASK_20260602_002 Telegram PlanConfirm Yes 回调超时修复（PLAN）

## 1. 现象

用户在 Telegram PlanConfirm 消息中点击 `✅ Yes, implement this plan` 后：

- 终端已经收到并触发了 `Implement the plan.`；
- Telegram 按钮仍显示无响应/转圈；
- 再次点击时用户侧看到超时提示。

截图证据：`/Users/david/.config/vibego/data/telegram/vibegobot/2026-06-02/20260602_104443654-63f55f9a2a49.jpg`，画面为 `Implement this plan?` 三选项按钮。

## 2. 影响

- 用户误判为 Telegram 按钮没有生效，容易重复点击。
- 终端实际已经进入执行链路时，Telegram 侧可能没有及时清除按钮或给成功态。
- 网络抖动时，回调 handler 可能在已经向 tmux 发送 prompt 后被 Telegram ack 消息发送失败打断，导致 watcher/按钮状态清理不完整。

## 3. 当前实现证据

| 事实 | 证据 |
|---|---|
| PlanConfirm 三选项由 `bot.py` 自建 Telegram keyboard 渲染 | `bot.py`（锚点：`_build_plan_confirm_keyboard`、`_maybe_send_plan_confirm_prompt`） |
| Yes 回调进入 `on_plan_confirm_callback`，抢占 token 后先执行 `_dispatch_prompt_to_model`，成功后才 `callback.answer("已确认并推送到模型")` | `bot.py`（锚点：`on_plan_confirm_callback`、`_claim_plan_confirm_processing_token`、`PLAN_CONFIRM_ACTION_YES`） |
| `_dispatch_prompt_to_model` 在 tmux send 后会发送 Telegram session ack；ack 失败会向上传播 | `bot.py`（锚点：`_dispatch_prompt_to_model`、`_send_session_ack`、`_reply_to_chat`） |
| 项目已有安全答复 helper，可忽略过期/网络失败的 callback answer | `bot.py`（锚点：`_answer_callback_safely`） |
| 当前 PlanConfirm 测试覆盖 Yes 派发、并发幂等、三选项 fresh，但未覆盖“先 ack callback 再长耗时派发/Telegram ack 失败不阻断 tmux 后续” | `tests/test_plan_confirm_bridge.py`（锚点：`test_plan_confirm_yes_dispatches_implement_prompt`、`test_plan_confirm_yes_is_idempotent_under_concurrent_clicks`、`test_plan_confirm_fresh_context_drives_native_tui_and_binds_new_main_session`） |

## 4. 运行时证据

`/Users/david/.config/vibego/logs/codex/cckgwmsappcore/run_bot.log` 在 2026-06-02 18:43:22~18:43:25 记录：

1. `[session-map] chat=726858153 cancel previous watcher`
2. `Plan 切换预命令已发送`
3. `[session-map] chat=726858153 bound to ...rollout-2026-06-02T18-08-57...jsonl`
4. 随后 update 处理异常：`ProxyTimeoutError: Proxy connection timed out: 60`
5. traceback 定位：`on_plan_confirm_callback -> _dispatch_prompt_to_model -> _send_session_ack -> _reply_to_chat -> reply_to.answer`

这说明：终端/tmux 链路已经执行到模型派发后，Telegram 侧 session ack 发送超时导致整个 callback handler 异常退出，最终没有稳定执行 `callback.answer` 与按钮清理。

## 5. 根因判断

### 根因 A（高置信）

`on_plan_confirm_callback` 对 Yes/Fresh 这类长耗时动作没有先快速确认 callback query，而是等 `_dispatch_prompt_to_model` 或 native fresh 完成后才 `callback.answer`。

- Telegram 网络/代理抖动时，按钮 spinner 会持续等待。
- 后续 callback query 可能过期，用户侧表现为“按钮没反应/再次点击超时”。

### 根因 B（高置信）

`_dispatch_prompt_to_model` 在 tmux 已发送 prompt 后，仍把 Telegram session ack 发送失败当作致命异常，导致：

- prompt 已进入终端，但 handler 被中断；
- PlanConfirm session 可能未清理；
- watcher 可能未启动；
- Telegram 成功态/按钮清理没有执行。

## 6. 推荐修法

### 方案 A：只提前 callback.answer（最小改造，不推荐单独采用）

- 在 `on_plan_confirm_callback` 抢占 token 后立即 `_answer_callback_safely(callback, "已收到，正在推送到模型…")`。
- 成功/失败后通过消息发送或清按钮反馈结果。

优点：按钮立即止转。  
缺点：不能解决 `_send_session_ack` 失败导致 watcher/按钮清理中断的问题。

### 方案 B：只让 `_send_session_ack` 非致命（最小后端稳定性修复，不推荐单独采用）

- 在 `_dispatch_prompt_to_model` 中对 `_send_session_ack` 做非致命保护：失败只记 warning，继续启动 watcher 并返回 success。

优点：能保证“tmux 已发送后”后续绑定/watcher 不被 Telegram ack 失败打断。  
缺点：按钮仍可能在 callback.answer 前等待较久。

### 方案 C：组合修复（推荐）

一次性修复两个断点：

1. PlanConfirm Yes/Fresh 抢占 token 后立即安全答复 callback，避免按钮 spinner 卡住。
2. `_dispatch_prompt_to_model` 中 session ack 失败降级为 warning，不能阻断已经完成的 tmux 派发与 watcher 启动。
3. 成功后继续清理 PlanConfirm session 和按钮；失败后保留按钮并通过普通消息给用户可见失败提示。

优点：同时解决用户可感知无响应与 tmux 已触发后的状态中断。  
缺点：改动面比单点修复稍大，需要补两类回归测试。

## 7. 受影响目录

| 目录/文件 | 是否影响 | 原因 |
|---|---:|---|
| `bot.py` | 是 | PlanConfirm 回调处理、模型派发 ack 容错 |
| `tests/test_plan_confirm_bridge.py` | 是 | 增加 Yes/Fresh 回调先 ack、session ack 失败不阻断的测试 |
| `docs/` | 是 | 记录本次根因、契约、验证矩阵 |
| SQLite/DB | 否 | 不涉及表结构、任务/命令数据读写 |
| CLI/脚本/CI | 否 | 不新增依赖，不改构建链，不改启动脚本 |

## 8. 契约变更

1. Telegram PlanConfirm Yes/Fresh 点击后，callback query 必须先被快速确认；长耗时动作不得让按钮一直转圈。
2. tmux send 已成功后，Telegram session ack 发送失败不得回滚或中断模型派发结果。
3. PlanConfirm 成功后必须清理按钮；失败后必须保留可重试入口，并给用户可见失败说明。
4. 旧按钮、并行 PlanConfirm、Fresh native TUI 的 fail-closed 语义保持不变。

## 9. 测试矩阵

| 用例 | 预期 |
|---|---|
| Yes 点击时 `_dispatch_prompt_to_model` 尚未返回 | callback 已先收到“正在推送/处理中”答复 |
| `_dispatch_prompt_to_model` 中 `_send_session_ack` 抛 `TelegramNetworkError` | 函数仍返回 success，watcher 正常启动，PlanConfirm session 可被清理 |
| Yes 成功 | 仍发送 `Implement the plan.`，仍强制退出 Plan UI，按钮被清理 |
| Yes 派发失败 | 保留按钮，普通消息提示失败，避免仅依赖过期 callback alert |
| Fresh native 成功/失败 | 先 ack；成功清按钮；失败保留按钮并给普通消息 |
| 并发点击 | 仍最多派发一次，第二次提示处理中 |

## 10. 实施顺序（待用户确认后进入 DEVELOP）

1. 运行现有基线：`python3.11 -m pytest -q tests/test_plan_confirm_bridge.py`。
2. TDD 红灯：新增 PlanConfirm callback 先 ack 测试；新增 `_send_session_ack` 失败不阻断派发测试。
3. 最小实现：修改 `bot.py` 的 PlanConfirm 回调和 `_dispatch_prompt_to_model` ack 容错。
4. 聚焦回归：`python3.11 -m pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py -k 'plan_confirm or dispatch_prompt_to_model or dispatch_prompt_force_exit_plan_ui'`。
5. 基础诊断：`python3.11 -m vibego_cli doctor`。
6. 必要时全量 pytest，视基线耗时与用户确认执行。

## 11. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| callback 只能展示一次提示 | 后续成功态不能再通过 callback 弹出 | 成功态依赖按钮清理和 session ack；失败态用普通消息提示 |
| ack 失败被忽略后用户看不到“思考中” | 用户侧短时缺少反馈 | watcher 仍运行，模型输出可继续回传；日志记录 warning |
| 扩散到普通直聊派发 | `_dispatch_prompt_to_model` 是通用路径 | 测试限定“tmux 已发送后 ack 失败不阻断”；不改变 tmux 失败/会话缺失失败语义 |

回滚方式：还原 `bot.py` 本次两处修改与对应测试；本次无数据库迁移，无需数据回滚。

## 12. 当前状态

- [x] 已完成截图与运行日志只读取证。
- [x] 已定位高置信根因。
- [x] 已给出推荐修法与测试矩阵。
- [ ] 待用户确认后进入 TDD 实现。

---

## 13. DEVELOP 实施记录（2026-06-02）

### 13.1 TDD 红灯

基线：

```bash
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py
# 16 passed, 2 warnings
```

新增失败测试：

1. `tests/test_plan_confirm_bridge.py::test_plan_confirm_yes_acknowledges_callback_before_dispatch`
   - 目标：进入 `_dispatch_prompt_to_model` 前必须已经答复 Telegram callback。
   - 红灯：旧实现中 `callback.answers == []`。
2. `tests/test_plan_confirm_bridge.py::test_dispatch_prompt_to_model_continues_when_session_ack_fails`
   - 目标：tmux 已发送后 `_send_session_ack` 抛异常不得中断 `_dispatch_prompt_to_model`。
   - 红灯：旧实现抛出 `RuntimeError("telegram proxy timeout")`。

红灯命令：

```bash
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py -k 'acknowledges_callback_before_dispatch or continues_when_session_ack_fails'
# 2 failed, 16 deselected
```

### 13.2 最小实现

| 文件 | 修改点 | 说明 |
|---|---|---|
| `bot.py` | `on_plan_confirm_callback` | Yes/Fresh 抢占处理 token 后立即 `_answer_callback_safely(...)`，再执行 tmux 派发或 native fresh；后续成功/失败 callback 答复均改为安全答复，避免 query 过期异常打断流程。 |
| `bot.py` | `_dispatch_prompt_to_model` | `_send_session_ack(...)` 改为 best-effort：失败只记 warning，继续启动 watcher 并返回派发成功。 |
| `tests/test_plan_confirm_bridge.py` | 新增 2 个回归测试 | 固化 callback 先确认与 session ack 非致命契约。 |
| `AGENTS.md` | 新增证据约束 | 记录 PlanConfirm 回调响应约束。 |

### 13.3 已执行验证

```bash
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py -k 'acknowledges_callback_before_dispatch or continues_when_session_ack_fails'
# 2 passed, 16 deselected, 2 warnings

python3.11 -m pytest -q tests/test_plan_confirm_bridge.py
# 18 passed
```

### 13.4 Checklist

- [x] Yes 点击进入 tmux 派发前，Telegram callback 已先收到确认。
- [x] tmux send 已成功后，Telegram session ack 失败不再中断 watcher 启动。
- [x] PlanConfirm Yes 原有 `Implement the plan.` 派发语义保持不变。
- [x] PlanConfirm Fresh 原生 TUI fail-closed 语义保持不变。
- [x] 并发点击 token 幂等保护保持不变。
- [x] 无数据库变更、无新增依赖、无构建/CI 变更。

> 后续最终聚焦回归与 `vibego_cli doctor` 结果将在收尾前补充。

### 13.5 最终验证补充

聚焦回归：

```bash
python3.11 -m pytest -q tests/test_plan_confirm_bridge.py tests/test_task_description.py -k 'plan_confirm or dispatch_prompt_to_model or dispatch_prompt_force_exit_plan_ui'
# 23 passed, 199 deselected
```

基础诊断：

```bash
python3.11 -m vibego_cli doctor
# python_ok=true，dependencies=[]，config_root=/Users/david/.config/vibego
```

语法校验：

```bash
python3.11 -m py_compile bot.py
# exit code 0
```

全量 pytest 当前未通过，失败项集中在既有 AGENTS 模板迁移测试，和本次 PlanConfirm 修改无直接交集：

```bash
python3.11 -m pytest -q
# 3 failed, 953 passed, 6 warnings
# failed:
# - tests/test_agents_template_migration.py::test_enforced_notice_points_to_agents_md
# - tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt
# - tests/test_agents_template_migration.py::test_agents_template_requires_comet_for_complex_workflows

python3.11 -m pytest -q tests/test_agents_template_migration.py
# 3 failed, 4 passed
```

本次不修改上述模板测试，原因：失败断言指向 `ENFORCED_AGENTS_NOTICE` / `AGENTS-template.md` 的 Comet/规约文案口径，属于独立测试资产与模板同步问题；当前任务只修复 PlanConfirm Yes/Fresh 回调超时与 session ack 非致命链路。

### 13.6 本次最终状态

- [x] PlanConfirm 相关聚焦测试通过。
- [x] `_dispatch_prompt_to_model` 相关聚焦测试通过。
- [x] `vibego_cli doctor` 通过。
- [x] `bot.py` 语法校验通过。
- [ ] 全量 pytest 未通过：阻断项为既有 `tests/test_agents_template_migration.py` 模板口径失败，已记录为未验证通过点。
