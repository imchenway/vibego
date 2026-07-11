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

## 6. 2026-07-11 PLAN：模型回复未回传 Telegram 的会话绑定竞态

### 6.1 现象与影响

- 现象：Codex TUI 已显示最终回复，但 Telegram 只收到“模型思考中”确认，没有收到最终正文。
- 影响：普通主会话的新 session 首次创建较慢时，worker 可能把 chat 的 watcher 绑定到同工作目录下的旧 Codex Desktop JSONL；当前回复不会进入 Telegram 发送阶段，补偿轮询也会持续检查错误文件。
- 本轮边界：只读取证、定位根因并沉淀故障图；未修改源码、测试、配置或运行态。

本轮采用本文作为主任务文档，因为它是 `/docs` 中最直接覆盖“模型已输出但 Telegram watcher 未回传 / 永远落后一条”的通用任务记忆。相关历史证据还包括：

- `docs/TASK_20260510_001_codex_goal模式支持方案.md`（锚点：`## 15. 2026-05-11 PLAN`）：记录同 CWD 会话串绑与 offset 初始化风险；
- `docs/TASK_20260312_002_Telegram消息重复发送修复.md`（锚点：`event_msg.agent_message`）：记录 JSONL 事件镜像流过滤边界。

### 6.2 运行现场证据

| 编号 | 证据 | 观察结果 | 裁决 |
|---|---|---|---|
| E1 | `/Users/david/.config/vibego/logs/codex/vibego/run_bot.log:3570-3579` | 12:16:38 worker 已连接 Telegram。 | Telegram 通道在故障前已恢复。 |
| E2 | 同文件 `:3580-3585` | 12:17:26 pointer 尚未绑定，strict fallback 选中 `2026-07-09` 旧 JSONL，并写入 chat 映射。 | chat 从启动早期就缓存了错误会话。 |
| E3 | `/Users/david/.codex/sessions/2026/07/11/rollout-2026-07-11T12-22-44-019f4f69-d635-7682-95a8-1a3f95706197.jsonl:9-10` | 12:23:26.646 本轮用户消息进入新的 Codex TUI JSONL。 | tmux → Codex 入模成功。 |
| E4 | `/Users/david/.config/vibego/logs/codex/vibego/session_binder.log:56`；`current_session.txt:1` | 12:23:26 binder 才把新 JSONL 写入 pointer；新 JSONL 含 worker marker，旧 JSONL 来源为 `Codex Desktop` 且不含 marker。 | pointer 最终正确，但更新晚于 dispatch 的首次会话选择。 |
| E5 | `run_bot.log:3619-3623` | worker 同秒取消旧 watcher 后，12:23:27 仍把 chat 绑定到 7 月 9 日旧 JSONL；12:23:28 ack 发送成功。 | watcher 错绑；同时排除 Telegram 出站整体不可用。 |
| E6 | 新 JSONL `:24-27` | 12:24:02–12:24:05 同时存在合法 `event_msg.agent_message`、`response_item.message phase=final_answer` 和 `task_complete`。 | 排除“模型未产出”和“final 事件形态被过滤”。 |
| E7 | `run_bot.log:3624-3625` | 12:24:25、12:27:25 补偿轮询仍检查 7 月 9 日旧 JSONL；没有“检测到待发送 / 准备发送模型输出 / 模型输出发送成功”。 | 最终正文根本没有进入 Telegram `sendMessage` 前置阶段。 |

补充一致性证据：仓库 `bot.py` 与当前 pipx 运行副本 SHA-256 均为 `a1d7c39ace3314f740a4a9b69824dcb27f24dde11185bf8f56f21e485b0e721c`，当前运行版本为 `vibego 1.5.215`，因此本轮代码锚点可用于解释现场运行行为。

### 6.3 候选根因裁决

| 候选 | 状态 | 证据 |
|---|---|---|
| H1：Telegram 网络导致最终正文发送失败 | 排除 | E1、E5：同一 chat 的 ack 已成功；最终正文前没有任何发送尝试或发送异常。 |
| H2：Codex 没有生成正式 final，或 phase/role 被过滤 | 排除 | E6：存在 `response_item type=message role=assistant phase=final_answer`，随后 `task_complete`。 |
| H3：JSONL 读取半行导致 offset 越过 final | 本次排除，保留为独立测试缺口 | watcher 没有读取新 JSONL；断点早于 reader。当前 `_read_session_events_jsonl` 对半行推进 offset 的行为仍缺回归测试，但不能解释本现场。 |
| H4：chat watcher 仍绑定旧 JSONL | **成立（已确认根因）** | E2、E4、E5、E7 形成完整时间链。 |

### 6.4 已确认根因

**根因 R1：新 Codex session 首次创建时，`_dispatch_prompt_to_model` 与 `session_binder` 发生会话绑定竞态。dispatch 先复用缓存的旧 `CHAT_SESSION_MAP`；binder 随后把正确的新 session 写入 pointer，但 dispatch 在创建 watcher 前没有再次读取并裁决 pointer，最终 watcher 和补偿轮询继续监听旧 JSONL。**

代码链路：

1. `bot.py:3857-3862`：优先从 `CHAT_SESSION_MAP` 复用仍存在的旧 session；
2. `bot.py:3908-3944`：只在 dispatch 前段读取一次 pointer；如果 pointer 此时尚未更新，不会切换；
3. `bot.py:4093-4101`、`bot.py:17904-17959`：strict fallback 仅按 CWD/mtime 找最新 rollout，普通消息路径未要求 worker marker，因此可能选中同 CWD 的 Codex Desktop 会话；
4. `bot.py:4187-4195`、`bot.py:4233-4243`：把前述旧路径写回 chat 映射并据此创建 watcher；
5. `bot.py:16049-16080`：补偿轮询继续从相同 `CHAT_SESSION_MAP` 取路径，无法发现 pointer 已切换。

现有测试 `tests/test_task_description.py:3560-3630` 只覆盖“调用 dispatch 前 pointer 已经切到新 session”；`tests/test_task_description.py:3490-3540` 只覆盖 chat 尚无旧映射时从 pointer 建 watcher。两者都没有覆盖“首次读取 pointer 后、最终创建 watcher 前 pointer 才切换”的竞态窗口。

### 6.5 推荐修复方向（尚未实施）

1. 在 tmux 发送和 delivery-confirm 等待之后、初始化 `SESSION_OFFSETS` / `CHAT_SESSION_MAP` 并创建 watcher 之前，再次读取 pointer；若 pointer 已切到可证明同源的新 JSONL，必须原子切换 session path。
2. Codex 主会话的 strict fallback 不得仅凭“同 CWD + 最新 mtime”接受会话；至少校验 worker marker、创建时序与本轮输入证据，缺证据时 fail-closed，不能绑定 Codex Desktop 会话。
3. 若新 session 在本轮发送后才创建，应从文件头或可证明的发送前 offset 读取，避免快速 final 在 rebind 前已经写入而再次漏失。
4. 补偿轮询或 watcher 应具备 pointer 漂移自愈：旧 mapped 文件仍存在时，也需要比较当前 pointer，而不是永久信任缓存路径。

### 6.6 develop 阶段 TDD 验收口径

| AC | 失败测试建议 | 验收结果 |
|---|---|---|
| AC1：pointer 在 tmux 发送后、watcher 创建前从 old 切到 new | 在 `tests/test_task_description.py::test_dispatch_prompt_rebinds_when_pointer_updates` 附近新增竞态测试 | 返回 path、`CHAT_SESSION_MAP` 与 watcher 参数都必须是 new。 |
| AC2：同 CWD、无 worker marker 的 Codex Desktop session 不得作为普通主会话 strict fallback | 扩展 `tests/test_session_binder_codex.py` 与 dispatch 聚焦测试 | old session 不得被绑定或发送历史输出。 |
| AC3：新 JSONL 已快速写入 final 时仍回传一次 | 竞态测试在 new JSONL 预置 user + `response_item final_answer` | final 恰好发送一次，old 不发送。 |
| AC4：补偿轮询发现 pointer 已切换时可自愈 | 扩展 `tests/test_message_recovery_poll.py` | 轮询 new session，不持续检查 old。 |
| AC5：现有正常 pointer 绑定、显式 Resume、并行会话和 `/goal` 同源保护不回归 | 运行相关聚焦测试与全量测试 | 所有受影响测试通过；未验证前不得宣称修复。 |

### 6.7 临时恢复与边界

- 当前 `current_session.txt` 已指向正确的新 JSONL。再从 Telegram 发送一条普通业务消息，会让新 dispatch 在入口比较旧 map 与新 pointer，从而恢复**后续** watcher 绑定。
- 已漏掉的上一条 final 不保证自动补发：新 dispatch 通常从本轮发送前 offset 初始化，可能跳过它；不要把“后续恢复”表述为“历史消息已补发”。
- 本轮未重启 worker、未手工改 pointer、未调用 Telegram API 补发，也未修改任何实现或测试。

### 6.8 可视化交付

- `docs/TASK_20260711_001_Telegram模型回复未回传会话绑定竞态排查.html`

## 7. 2026-07-11 DEVELOP：会话绑定竞态修复计划

用户已明确回复 `develop`，本阶段只修复第 6 节确认的会话错绑链路，不扩展到无关的 Telegram UI、模型输出解析或 tmux 发送语义。

### 7.1 变更边界

| 文件 | 责任 |
|---|---|
| `bot.py` | 在 prompt 发送后重新裁决 pointer；Codex 有 worker marker 时拒绝无 marker 会话；watcher / 补偿轮询发现 pointer 漂移时切换到新会话。 |
| `tests/test_task_description.py` | 复现“发送前 old、tmux 发送后 pointer 切到 new”的 dispatch 竞态，并覆盖 strict fallback 的 marker 保护。 |
| `tests/test_message_recovery_poll.py` | 复现补偿轮询仍读取 old、pointer 已切到 new 的场景，验证自愈和单次投递。 |
| 本文与故障 HTML | 回写 RED / GREEN / 回归 / review / 剩余风险证据。 |

### 7.2 TDD 顺序

1. **RED-1**：新增 dispatch 竞态测试；在 fake `tmux_send_line` 内把 pointer 从 old 改为 new，断言返回 path、`CHAT_SESSION_MAP`、ack 和 watcher 参数全部指向 new。
2. **RED-2**：新增 recovery pointer 漂移测试；初始 map=old、pointer=new，断言只读取 new，并把 map / watcher 修正为 new；同一 final 不得重复投递。
3. **RED-3**：新增 marker fallback 测试；同 CWD 的较新 Desktop session 无 marker、较旧 worker session 有 marker 时，只允许 worker session；没有合格会话时 fail-closed。
4. **GREEN**：在 `bot.py` 提取最小 pointer 校验 / 重绑定逻辑；发送后、ensure watcher、recovery poll 三处复用，不改 Telegram 发送接口和 JSONL 解析器。
5. **回归**：先运行三个新增测试，再运行 `test_task_description.py`、`test_message_recovery_poll.py`、`test_tmux_send_line.py`、`test_session_binder_codex.py`，最后运行全量 `pytest -q`。
6. **review / verification**：独立 reviewer 按本任务原始需求、当前 `AGENTS.md`、diff 与测试证据审查；修正 Critical / Important 后重新执行全量验证。

### 7.3 验收与回滚

- 验收：AC1-AC5 全部有自动化证据；运行现场对应的 old/new 时间顺序能由回归测试稳定复现；Telegram ack 成功但 final 不回传的断点被消除。
- 兼容：显式 Resume（marker 文件为空）、`/goal` 的 fail-closed、并行 session、Copilot 自动重试语义保持不变。
- 回滚：仅需恢复 `bot.py` 与本轮新增/修改测试；不涉及依赖、配置、数据库迁移、运行期数据或远端历史操作。

## 8. 2026-07-11 DEVELOP 执行与验收

本节是对第 6 节 PLAN 现场结论和第 7 节实施计划的执行回写。结论边界是：**仓库源码修复已通过自动化验证；当前 pipx 运行副本尚未升级/重启，Telegram 移动端实况仍待部署后验收。**

### 8.1 实施变更与证据锚点

| 范围 | 已实施内容 | 仓库证据 |
|---|---|---|
| dispatch 会话裁决 | 记录 tmux 发送前 EOF 和发送成功的 prompt/时间证明；发送后再读 pointer。仅当新 JSONL 同时通过 CWD、当前 worker marker 和本轮 prompt 证明时重绑，offset 从本轮 user 行开始。 | `bot.py`（锚点：`_session_event_epoch`、`_iter_session_jsonl_event_offsets`、`_find_user_prompt_event_offset`、`_dispatch_prompt_to_model`、`CHAT_RECENT_MODEL_DISPATCH_PROOFS`） |
| watcher / recovery 自愈 | watcher 可检测 pointer 漂移；补偿轮询只在“最近 tmux 发送成功证明晚于 Telegram 触发且新 JSONL 含对应 prompt”时替换旧 watcher。即时投递异常也会补建新 watcher；无证明则 fail-closed。 | `bot.py`（锚点：`_rebind_chat_session_from_pointer`、`_ensure_session_watcher`、`_probe_new_model_message_once`、`_schedule_message_recovery_poll`） |
| marker 与并行隔离 | 只要当前 worker marker 非空，cache/pointer/fallback 候选都必须含 marker；全部候选不合格时拒绝绑定。并行任务改用任务私有 marker，同时保留主会话显式自定义 marker 路径及 marker 为空的 Resume 语义。 | `bot.py`（锚点：`_parallel_session_binder_token_file`、`_session_marker_file_for_pointer`、`_fallback_locate_latest_session`、`_find_latest_rollout_for_cwd`） |
| event loop 性能与时间精度 | 同 session 常规路径直接使用 pre-send EOF，不全量扫描长 JSONL；发送后新 session 和 recovery 的文件扫描交给 `asyncio.to_thread`。对 Codex JSONL 毫秒时间截断允许 1 ms 公差。 | `bot.py`（锚点：`_find_user_prompt_event_offset`、`_dispatch_prompt_to_model`、`_probe_new_model_message_once`） |
| 测试隔离 | 每个测试清理本机真实 `SESSION_BINDER_TOKEN_FILE` 与 dispatch proof，避免开发机 worker marker 泄漏进临时 pointer 用例。 | `tests/conftest.py`（锚点：`_isolate_worker_session_marker`） |

主要回归证据：

- `tests/test_task_description.py`（锚点：`test_dispatch_prompt_rebinds_when_pointer_switches_during_tmux_send`、`test_dispatch_prompt_strict_fallback_rejects_newer_session_without_worker_marker`、`test_dispatch_prompt_strict_fallback_fails_closed_when_all_sessions_lack_worker_marker`、`test_dispatch_prompt_same_session_does_not_full_scan_jsonl`、`test_session_prompt_offset_accepts_codex_millisecond_timestamp_truncation`）；
- `tests/test_message_recovery_poll.py`（锚点：`test_probe_message_recovery_rebinds_to_new_pointer_and_delivers_once`、`test_probe_message_recovery_restarts_watcher_when_rebound_delivery_fails`、`test_probe_message_recovery_rejects_pointer_without_current_dispatch_prompt`）；
- `tests/test_parallel_flow.py`（锚点：`test_start_parallel_tmux_session_requires_ready_file`）。

### 8.2 RED → GREEN 与审查闭环

1. 首轮四个核心用例在旧实现下得到 `4 failed`；最小实现后同组得到 `4 passed`，覆盖 dispatch pointer 竞态、marker fallback、recovery 漂移和并行 marker 隔离。
2. review 后又分别以失败用例暴露并修正：watcher 即时投递异常未补建、本机 marker 污染测试、同 session/recovery 在 event loop 全量扫描、JSONL 毫秒时间截断、recovery 缺少本轮 prompt 证明、全部会话缺 marker 时未 fail-closed。
3. 独立 reviewer 读取并遵守当前 `AGENTS.md`、任务文档、完整 diff 与验证证据后，最终结论为：源码层面未发现剩余 Critical / Important / Minor 问题。

### 8.3 自动化验证记录

#### 受影响回归集

```bash
.venv314/bin/python -m pytest -q \
  tests/test_task_description.py \
  tests/test_message_recovery_poll.py \
  tests/test_tmux_send_line.py \
  tests/test_session_binder_codex.py \
  tests/test_session_binding.py \
  tests/test_parallel_flow.py \
  tests/test_start_tmux_model_cmd.py
```

结果：`306 passed in 21.68s`。

#### 全量回归

```bash
.venv314/bin/python -m pytest -q
```

结果：`1149 passed, 6 warnings in 42.46s`。六条 warning 均为既有 `tests/test_unescape_markdown.py` 测试函数返回非 `None` 的 `PytestReturnNotNoneWarning`，本轮未弱化测试资产来隐藏它们。

#### 静态、环境与 HTML 验证

```bash
.venv314/bin/python -m compileall -q bot.py tests
git diff --check
.venv314/bin/vibego doctor
.venv314/bin/python vibego_cli/data/skills/vibe-diagram/scripts/vibe_diagram_lint.py \
  --type fault-debugging \
  docs/TASK_20260711_001_Telegram模型回复未回传会话绑定竞态排查.html
```

结果：

- `compileall` 与 `git diff --check` 退出码为 0；`compileall` 只显示既有 `tests/verify_flow.py` 的 `\`` 转义 `SyntaxWarning`。
- `vibego doctor` 退出码为 0，`python_ok=true`、`dependencies=[]`，配置/环境/项目/DB 检查存在。
- 故障排查 HTML lint 通过；已在 1440×1000 桌面视口和 390×844 移动视口检查，未见节点重叠、文字裁切或横向溢出。
- 为恢复本地全量验证环境，只在 `.venv314` 中安装了项目已声明的 `openpyxl 3.1.5` 及其依赖 `et-xmlfile 2.0.0`；未修改 `pyproject.toml`、锁文件或运行中 pipx 环境。

### 8.4 需求验收映射

| AC | 验收结果 | 证据 |
|---|---|---|
| AC1：发送后 pointer 由 old 切到 new | 通过 | dispatch 返回路径、map、ack、watcher 全部指向 new；参见 `test_dispatch_prompt_rebinds_when_pointer_switches_during_tmux_send`。 |
| AC2：无 worker marker 的 Desktop session 不得成为 strict fallback | 通过 | 较新无 marker 会话被拒绝；所有候选均不合格时 fail-closed。 |
| AC3：新 JSONL 已快速写入 final | 通过 | offset 定位本轮 user 行；只读取 `current final` 一次，不回放 `historical final`。 |
| AC4：recovery 发现 pointer 漂移 | 通过 | 本轮 prompt 证明成立时重绑、替换 watcher 并只投递一次；缺少当前 prompt 则拒绝。 |
| AC5：Resume、parallel、`/goal`、Codex/Copilot 投递语义不回归 | 通过（自动化） | 306 条受影响回归与 1149 条全量回归全部通过。Codex 仍不因 JSONL 暂未确认而重发，Copilot 仍保留 queued retry。 |

### 8.5 部署边界、剩余风险与回滚

- **部署边界**：本轮未执行 `git commit/push/merge`，未升级 pipx 运行副本，未重启 worker，也未调用真实 Telegram API 进行最终回传验收。因此不把“源码已验证”表述为“运行环境已修复”。
- **剩余风险 R1**：第 6.3 节记录的 JSONL 半行读取 offset 风险本轮未处理；它不是本次运行现场根因。
- **剩余风险 R2**：相同 prompt 在极短时间窗内重复时，仍依赖时间、CWD、marker、pointer 和文本共同裁决；自动化已覆盖主竞态，但未做多 chat 高并发运行态压力测试。
- **待执行验收**：发布/升级并重启 worker 后，从 Telegram 发送一条会触发新 Codex session 的普通业务消息；应同时观察 pointer 切换、watcher 绑定新 JSONL、final 只回传一次，且历史 final 不被补发。
- **回滚**：如部署后验收失败，仅恢复 `bot.py`、`tests/conftest.py`、`tests/test_task_description.py`、`tests/test_message_recovery_poll.py`、`tests/test_parallel_flow.py` 及本轮文档变更；不需要数据库、配置或依赖回滚。
