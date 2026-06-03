# TASK_20260603_004 Telegram 普通消息自动确保 PLAN 模式（DEVELOP）

## 1. 背景与目标

用户最新要求：每次用户发送 Telegram 普通消息时，先检查当前是否处于 PLAN 模式；如果不是，则先自动切换到 PLAN 模式，再将消息发送给模型。

本次属于 `TASK_0137_移除普通消息自动Plan预命令.md` 的后续反向变更：普通消息不再完全依赖用户手动切换 PLAN，而是恢复“普通直聊默认以 PLAN 入口投递”，并补充当前按钮/排队发送后的新约束。

## 2. 当前链路现状

### 2.1 功能现状
- 普通文本、图文聚合、长文本聚合最终都会进入 `bot.py::_handle_prompt_dispatch(...)`。
- `_handle_prompt_dispatch(...)` 原先固定传 `intended_mode=None`，不会触发 PLAN 预命令。
- 普通消息发送方式由底部 `✉️ 立即/排队` 控制，通过 `_get_worker_direct_send_mode()` 透传到 `_dispatch_prompt_to_model(...)`。
- `_dispatch_prompt_to_model(...)` 内部在发送正文前调用 `_maybe_send_plan_switch_command(...)`，但旧规则对 `send_mode=queued` 会跳过 `/plan`。

### 2.2 开发设计现状
- 入口：`on_text(...)` / 附件聚合结束后调用 `_handle_prompt_dispatch(...)`。
- 模式判断：`_resolve_dispatch_mode(...)` 通过 `intended_mode` 判断 PLAN/YOLO。
- PLAN 预命令：`_maybe_send_plan_switch_command(...)` 通过 `tmux_send_line(..., PLAN_MODE_SWITCH_COMMAND)` 发送 `/plan`。
- 排队发送：正文使用 `tmux_queue_line(...)`，默认不发送 PLAN 预命令，避免影响任务推送里显式排队语义。

## 3. 新增/修改契约

1. 普通 Telegram 直聊消息必须传 `intended_mode=PUSH_MODE_PLAN`。
2. 普通直聊即使当前发送方式为 `queued`，也必须允许先发送 PLAN 预命令，再排队正文。
3. 普通直聊发送 PLAN 预命令前，先探测主 worker 当前 PLAN 状态：
   - `on`：跳过重复 `/plan`，直接发送/排队正文。
   - `off` 或 `unknown`：发送 `/plan` 后再发送/排队正文。
4. 任务推送、PlanConfirm、并行任务等非普通直聊入口保持原有显式模式契约；默认 `queued + PLAN` 仍不自动插入 `/plan`，除非调用方显式开启强制开关。
5. slash 命令继续不插入 `/plan`，避免破坏命令语义。

## 4. 受影响目录与文件

| 范围 | 文件 | 影响 |
| --- | --- | --- |
| Worker 投递逻辑 | `bot.py` | `_handle_prompt_dispatch(...)` 恢复普通消息 PLAN 投递；`_dispatch_prompt_to_model(...)` 增加强制排队前 PLAN 与发送前探测参数；`_maybe_send_plan_switch_command(...)` 支持已在 PLAN 时跳过重复预命令。 |
| 回归测试 | `tests/test_task_description.py` | 覆盖普通消息 PLAN 参数、排队直聊先 PLAN、已处于 PLAN 不重复发送、任务排队默认不变。 |
| 任务文档 | `docs/TASK_20260603_004_Telegram普通消息自动确保PLAN模式.md` | 记录本次契约、风险、验证矩阵。 |
| 历史任务文档 | `docs/TASK_0137_移除普通消息自动Plan预命令.md` | 追加后续变更说明，避免后续 AI 误用旧结论。 |
| 证据索引 | `AGENTS.md` | 新增普通消息自动 PLAN 约束证据。 |

## 5. 数据库/接口/配置影响

- 数据库：不涉及 SQLite 表结构、迁移或回滚脚本。
- 外部接口：不新增 Telegram Bot API 参数，不改变命令菜单契约。
- 配置：不新增依赖、不改构建、不改 CI。
- 运行期状态：只读取当前 PLAN 状态缓存/探测结果，不新增状态文件。

## 6. 实施顺序

1. 先运行既有相关 baseline，确认当前旧契约测试通过。
2. 修改/新增测试，确认红灯：
   - 普通消息仍传 `None`。
   - 排队直聊暂不支持强制先 PLAN。
   - 已处于 PLAN 时仍重复发送 `/plan`。
3. 最小实现：
   - 给 `_dispatch_prompt_to_model(...)` 增加 `force_plan_switch_for_queued` 与 `probe_plan_mode_before_switch`。
   - 普通消息入口传 `PUSH_MODE_PLAN`、强制排队前 PLAN、发送前探测。
   - `_maybe_send_plan_switch_command(...)` 在主 worker 已处于 PLAN 时跳过重复 `/plan`。
4. 跑聚焦测试与相关回归。
5. 更新 docs 与 AGENTS 证据。

## 7. 测试矩阵

| 用例 | 预期 | 覆盖测试 |
| --- | --- | --- |
| 普通文本直聊 | `_handle_prompt_dispatch` 传 `intended_mode=PLAN`，并开启排队前 PLAN 强制开关 | `test_handle_prompt_dispatch_auto_ensures_plan_mode` |
| 普通文本直聊 + 排队发送 | 保留 `send_mode=queued`，同时传 `PLAN` 与强制开关 | `test_on_text_direct_prompt_uses_worker_queued_send_mode` |
| PLAN 已开启 | 不重复发送 `/plan`，只发送正文 | `test_dispatch_prompt_plan_mode_skips_plan_switch_when_worker_already_plan` |
| 普通直聊 + 排队 + PLAN 未开启 | 先 `/plan`，再 `tmux_queue_line` 排队正文 | `test_dispatch_prompt_plan_mode_queued_force_sends_plan_switch_for_direct_message` |
| 非普通直聊任务推送 + queued | 默认保持不发 `/plan`，直接排队正文 | `test_dispatch_prompt_plan_mode_queued_skips_plan_switch_for_codex` |
| 即时 PLAN 旧链路 | 仍支持 `/plan` -> 正文 | `test_dispatch_prompt_plan_mode_sends_plan_switch_for_codex` |

## 8. 风险与回滚

### 风险
1. 如果 Codex CLI 对 `/plan` 的语义变化为非幂等，本次“off/unknown 时发送 `/plan`”可能产生额外影响；当前实现通过“已探测 on 时跳过”降低重复发送风险。
2. `unknown` 状态仍会发送 `/plan`，这是为了 fail-open 地保证普通消息尽量进入 PLAN；如果 tmux 探测长期异常，按钮状态可能显示 `?`，但消息仍会尝试进入 PLAN。
3. 排队发送模式下会先立即发送 `/plan`，再排队正文；这会改变普通直聊排队场景下的终端输入顺序，但符合用户“先切 PLAN 再发送”的最新要求。

### 回滚
- 回滚 `bot.py` 中普通消息入口的 `intended_mode=PUSH_MODE_PLAN`、`force_plan_switch_for_queued=True`、`probe_plan_mode_before_switch=True`。
- 删除 `_dispatch_prompt_to_model(...)` 与 `_maybe_send_plan_switch_command(...)` 的新增参数，恢复 `queued + PLAN` 默认跳过 `/plan`。
- 回滚对应测试断言到 `TASK_0137` 的旧契约。

## 9. 验证记录

### baseline（修改前）
```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'handle_prompt_dispatch_uses_manual_mode_control or on_text_direct_prompt_uses_worker_queued_send_mode or dispatch_prompt_plan_mode_queued_skips_plan_switch_for_codex or dispatch_prompt_plan_mode_sends_plan_switch_for_codex'
# 4 passed, 201 deselected
```

### RED（测试先行）
```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'handle_prompt_dispatch_auto_ensures_plan_mode or on_text_direct_prompt_uses_worker_queued_send_mode or dispatch_prompt_plan_mode_skips_plan_switch_when_worker_already_plan or dispatch_prompt_plan_mode_queued_force_sends_plan_switch_for_direct_message'
# 4 failed, 203 deselected
```

### GREEN（最小实现后）
```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'handle_prompt_dispatch_auto_ensures_plan_mode or on_text_direct_prompt_uses_worker_queued_send_mode or dispatch_prompt_plan_mode_skips_plan_switch_when_worker_already_plan or dispatch_prompt_plan_mode_queued_force_sends_plan_switch_for_direct_message'
# 4 passed, 203 deselected
```

## 10. 补充验证状态

- 已在第 11 节补充执行包含旧 PLAN/queued 用例的更宽聚焦回归、受影响文件全量与全量 pytest。
- 若用户要让当前 live Telegram worker 生效，需要按当前部署方式重新安装/重启 worker；源码变更本身不自动替换 pipx 已安装包。

---

## 11. 最终验证记录（本轮）

### 11.1 聚焦回归
```bash
python3.11 -m pytest -q tests/test_task_description.py -k 'handle_prompt_dispatch or on_text_direct_prompt or dispatch_prompt_plan_mode or push_send_mode'
# 12 passed, 195 deselected
```

### 11.2 受影响文件全量
```bash
python3.11 -m pytest -q tests/test_task_description.py
# 207 passed
```

### 11.3 全量 pytest
```bash
python3.11 -m pytest -q
# 3 failed, 959 passed, 6 warnings
```

失败项均为既有 `tests/test_agents_template_migration.py`：
- `test_enforced_notice_points_to_agents_md`
- `test_enforced_notice_adds_user_requirement_header_before_prompt`
- `test_agents_template_requires_comet_for_complex_workflows`

本次改动未修改 `ENFORCED_AGENTS_NOTICE` 或 `AGENTS-template.md`，该全量失败与本次普通消息 PLAN 行为变更隔离。

### 11.4 CLI doctor
```bash
python3.11 -m vibego_cli doctor
# exit 0，python_ok=true，dependencies=[]
```

## 12. 2026-06-03 后续变更提示：普通直聊默认 queued

后续 `TASK_20260603_005_普通消息默认排队并移除发送方式按钮.md` 已更新普通直聊发送方式来源：

1. 普通消息仍按本文件约定进入 `_handle_prompt_dispatch` 后以 `intended_mode=PLAN` 投递。
2. Codex/Copilot 普通直聊不再由底部 `✉️ 立即/排队` 按钮或状态文件决定发送方式，而是默认 `send_mode=queued`。
3. 不支持 queued 的模型自动回退 `send_mode=immediate`。
4. 即使普通直聊默认 queued，也继续保留本文件的“先确保 PLAN，再排队正文”契约；任务推送等非普通直聊入口仍不受影响。
