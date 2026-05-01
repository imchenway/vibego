# /TASK_0108 sessionId 绑定主会话继续

## 1. 任务口径

用户诉求：需要可以通过 sessionId 绑定一个会话，然后将那个会话作为主会话继续。

本轮采用已确认方案：

- 仅做 Codex 主会话恢复，避免非 Codex 缺少可证 resume 契约时出现假完成。
- 用户输入 `sessionId` 后，系统应定位对应 Codex 会话文件，校验属于当前工作目录，再重启主 tmux 到该会话。
- 绑定成功后，该会话成为当前 chat 的主会话，后续普通消息继续进入该会话。
- 不新增 DB 表，不新增依赖，不改并行会话提升规则。

## 2. 规约与证据读取

- 已读取 `$HOME/.config/vibego/AGENTS.md`：默认 PLAN / develop TDD 门禁、禁止临时修改交付、最终收尾字段。
- 已读取当前仓库 `AGENTS.md`：Strict Evidence Mode、写入范围、Python + pytest、运行期目录与主/worker 架构证据。
- 受影响范围内未发现更近的 `AGENTS.md` 或 `AGENTS.evidence.json`。

## 3. 仓库现状证据

- `bot.py` 已通过 `sessionId : {session_path.stem}` 给用户展示当前会话标识。
- `bot.py` 当前主会话绑定以内存 `CHAT_SESSION_MAP`、运行期 pointer 文件和 watcher 共同决定。
- `scripts/start_tmux_codex.sh` 当前负责启动主 tmux 与 session binder，并在启动时清空 pointer 与 active session id 文件。
- 本机 `codex resume --help` 可证 Codex CLI 支持 `resume [SESSION_ID]`，可按 UUID 恢复交互会话。

## 4. Class Impact Plan

### 4.1 计划修改单元

| 单元                          | 实现文件                          | 测试文件                                 |
|-----------------------------|-------------------------------|--------------------------------------|
| sessionId 规范化与会话文件定位        | `bot.py`                      | `tests/test_session_binding.py`      |
| 主会话绑定与 watcher 切换           | `bot.py`                      | `tests/test_session_binding.py`      |
| Telegram `/bind_session` 入口 | `bot.py`                      | `tests/test_session_binding.py`      |
| Codex resume 启动命令拼装         | `scripts/start_tmux_codex.sh` | `tests/test_start_tmux_model_cmd.py` |
| 帮助菜单命令说明                    | `bot.py`                      | `tests/test_session_binding.py`      |

### 4.2 直连依赖测试

- `tests/test_task_description.py`：已有主会话 pointer 切换、普通 prompt 绑定与 strict fallback 用例，作为必要回归候选。
- `tests/test_chat_menu_buttons.py`：已有主会话实况与 watcher 恢复用例，作为必要回归候选。

### 4.3 测试范围升级判断

- 命中升级条件：是。
- 原因：本次会改变主会话绑定、主 tmux 启动命令和用户可见命令入口。
- 执行策略：先定向类级测试，最终补充主会话相关直连回归；不默认跑全仓测试。

## 5. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_session_binding.py tests/test_start_tmux_model_cmd.py
```

结果：

- ✅ `5 passed`
- 说明：实现前既有会话绑定与启动脚本用例全绿。

## 6. TDD 红灯

先补测试，覆盖：

1. UUID 与 Telegram 展示的文件 stem 都能定位到同一 Codex 会话。
2. 工作目录不匹配时拒绝绑定。
3. `/bind_session` 成功后会重启主 tmux、更新 pointer、绑定 watcher，并从文件末尾监听。
4. 非 Codex 模型直接 fail-closed。
5. 启动脚本在提供 sessionId 时拼出 `codex ... resume <sessionId>`。

首次执行：

```bash
python3.11 -m pytest -q tests/test_session_binding.py tests/test_start_tmux_model_cmd.py
```

结果：

- ❌ `5 failed, 5 passed`
- 失败原因：尚未实现 sessionId 定位、主会话恢复入口、主 tmux resume 命令拼装。

## 7. 最小实现记录

- `bot.py`
    - 新增 `/bind_session sessionId` 命令。
    - 新增 Codex JSONL 首行 `session_meta.payload.id` 读取。
    - sessionId 同时支持文件 stem 与 Codex UUID。
    - 定位会话时校验当前工作目录，不匹配或无法确认则拒绝绑定。
    - 绑定成功后重启主 tmux 到 `codex resume <UUID>`，更新 pointer/active session id，替换当前 chat 主会话 watcher。
    - 历史会话绑定后从文件末尾监听，避免旧输出重复回推。
    - `/help` 与 `/tasks` 增加命令说明。
- `scripts/start_tmux_codex.sh`
    - 支持 `MODEL_RESUME_SESSION_ID`。
    - 仅 Codex 可使用该参数；其他模型直接失败。
    - 拼接 `resume <sessionId>`，避免只改 pointer 造成假绑定。

## 8. Self-Test Gate

### 8.1 定向实现测试

```bash
python3.11 -m pytest -q tests/test_session_binding.py tests/test_start_tmux_model_cmd.py
```

结果：

- ✅ `10 passed`

### 8.2 直连回归与双轮一致性

```bash
python3.11 -m pytest -q tests/test_session_binding.py tests/test_start_tmux_model_cmd.py tests/test_task_description.py tests/test_chat_menu_buttons.py -k 'bind_session or session_binding or start_tmux_dry_run or dispatch_prompt_rebinds_when_pointer_updates or dispatch_prompt_injects_enforced_agents_notice or dispatch_prompt_strict_fallback or worker_terminal_snapshot_resumes_watcher_when_exited or worker_session_live_button_opens_session_list'
```

结果：

- ✅ 第一轮：`15 passed, 225 deselected`
- ✅ 第二轮：`15 passed, 225 deselected`

### 8.3 最小诊断

```bash
python3.11 -m vibego_cli doctor
```

结果：

- ✅ `python_ok=true`

### 8.4 未执行项

- 当前仓库未找到可证实的统一 typecheck 命令。
- 当前仓库未找到可证实的 coverage 统一命令。
- 未执行正式构建；本次未涉及发布产物。

## 9. 风险与回滚

### 风险

- 绑定 sessionId 会重启主 tmux 中的 Codex CLI；这是“真实恢复主会话”的必要动作。
- 若旧会话文件工作目录无法确认或不匹配，应 fail-closed，避免跨项目串会话。

### 回滚

- 回滚 `bot.py` 中 `/bind_session` 入口与主会话绑定辅助逻辑。
- 回滚 `scripts/start_tmux_codex.sh` 中 resume 命令拼装。
- 回滚新增测试。

## 10. 绑定会话按钮入口设计（2026-05-01 PLAN）

### 10.1 本轮需求理解

用户在确认 `/bind_session sessionId` 文本命令入口后，继续提出“给我做个按钮”。按上下文判断，本轮不是重新设计 sessionId 绑定能力，而是把已有命令能力做成 Telegram 可点入口，降低记忆命令与复制参数成本。

### 10.2 当前现状（用户视角）

- 用户现在能在 `/help`、`/tasks` 中看到 `/bind_session sessionId` 命令。
- 模型监听提示会展示 `sessionId : {session_path.stem}`，用户需要复制该值并手动发送 `/bind_session <sessionId>`。
- Worker 常驻键盘目前只有 `📋 任务列表`、`📟 命令管理`、`💻 会话实况`、`🧭 PLAN MODE/MODE`。

### 10.3 新增目标

- 给用户一个可见、可点击的“绑定会话”入口。
- 不改变既有 `/bind_session sessionId` 命令契约；按钮只是减少操作成本。
- 保持 Codex-only fail-closed：非 Codex 模型不做假恢复。
- 不新增 DB 表、不新增依赖、不改构建/CI。

### 10.4 方案对比

| 方案 | 说明 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. 仅在 Worker 常驻键盘新增 `🔗 绑定会话` | 点击后提示用户发送 `/bind_session <sessionId>` 或粘贴 `sessionId : ...` | 改动最小、入口稳定、测试简单 | 仍需要手动粘贴 sessionId，不是“一键绑定” | 可作为最小方案 |
| B. 在包含 `sessionId : ...` 的模型监听提示下方增加 Inline 按钮 `🔗 绑定此会话` | 按钮携带短 token，回调时定位原 session 并复用现有绑定逻辑 | 真正减少复制粘贴；历史消息也可直接恢复该会话 | 需要新增 token 映射与回调失效策略；bot 重启后旧按钮需 fail-closed 或回退提示 | 推荐方案 |
| C. A + B 同时做 | 常驻键盘提供入口，sessionId 消息提供一键绑定 | 入口最完整，兼顾“我知道 sessionId”和“我看到旧消息”两类用户 | 改动略大，主键盘会多一项，需要确认排布 | 高置信推荐 🌟 |

### 10.5 推荐开发设计

推荐采用方案 C：

1. Worker 常驻键盘新增 `🔗 绑定会话`：
   - 点击后回复操作说明：
     - “请发送 `/bind_session <sessionId>`，也可以直接粘贴 `sessionId : ...` 那一行。”
     - 若后续要做到“粘贴纯 sessionId 自动绑定”，需另起口径，本轮不默认扩大。
2. `_send_session_ack(...)` 在 `sessionId : ...` 消息下增加 Inline 按钮 `🔗 绑定此会话`：
   - 回调 token 映射到 session_path / display_session_id / resume_session_id。
   - 回调复用 `_resolve_main_session_binding_target(...)` 与 `_restart_main_tmux_with_resume_session(...)`，避免复制另一套绑定逻辑。
   - token 查不到、非 Codex、工作目录不匹配、session 文件不存在时 fail-closed，并提示用户使用 `/bind_session <sessionId>` 重试。
3. 回调成功后回复：
   - “已绑定为主会话，后续消息将继续进入该会话。\nsessionId : xxx”
   - 保持 worker 主键盘回显。

### 10.6 受影响目录/文件

| 类型 | 文件 | 影响 |
|---|---|---|
| 实现 | `bot.py` | 新增按钮常量、主键盘排布、按钮 handler、Inline 回调 token 与绑定回调处理 |
| 测试 | `tests/test_chat_menu_buttons.py` | 覆盖常驻键盘新增按钮与点击提示 |
| 测试 | `tests/test_session_binding.py` | 覆盖 sessionId 提示消息带一键绑定按钮、回调成功/失效/非 Codex fail-closed |
| 文档 | `docs/TASK_0108_sessionid绑定主会话继续.md` | 记录本轮按钮设计、验证矩阵、风险与回滚 |
| 脚本 | `scripts/start_tmux_codex.sh` | 不受影响；现有 resume 能力复用 |
| DB | SQLite 表结构 | 不受影响；不新增表字段 |

### 10.7 契约变更

- 新增用户可见按钮文案：`🔗 绑定会话`、`🔗 绑定此会话`。
- 既有命令 `/bind_session sessionId` 保持兼容。
- 既有 sessionId 展示格式 `sessionId : xxx` 保持兼容。
- 非 Codex 模型仍提示“不支持绑定”，不做静默成功。
- 回调 token 为内存态：bot 重启后旧按钮若无法解析，应提示用户复制 sessionId 使用命令重试；不落库。

### 10.8 测试矩阵

| 用例 | 覆盖点 | 预期 |
|---|---|---|
| Worker 常驻键盘结构 | 新增按钮后键盘结构稳定 | 包含 `🔗 绑定会话`，按钮均为 `KeyboardButton` |
| 点击 `🔗 绑定会话` | 入口可发现 | 回复绑定说明，不触发 tmux 重启 |
| `_send_session_ack` | sessionId 消息一键绑定入口 | 回复消息包含 Inline `🔗 绑定此会话` |
| 点击 Inline 绑定成功 | 一键绑定复用现有主会话恢复 | 重启主 tmux、更新 pointer、绑定 watcher，成功提示包含 sessionId |
| Inline token 失效 | bot 重启/旧按钮 | fail-closed，提示使用 `/bind_session <sessionId>` |
| 非 Codex 模型点击 | 可证恢复契约缺失 | fail-closed，提示暂仅支持 Codex |
| 工作目录不匹配 | 防串项目 | 拒绝绑定 |
| 既有命令回归 | `/bind_session` 不破坏 | 原有测试继续通过 |

### 10.9 实施顺序（develop 阶段）

1. Baseline：执行 `python3.11 -m pytest -q tests/test_session_binding.py tests/test_chat_menu_buttons.py`。
2. TDD 红灯：先补按钮相关失败用例。
3. 实现：
   - 新增按钮常量与 Worker 键盘排布；
   - 新增按钮说明 handler；
   - 新增 session ack Inline 绑定按钮与 token 映射；
   - 新增 callback handler，复用现有主会话绑定函数。
4. 定向验证：执行 `python3.11 -m pytest -q tests/test_session_binding.py tests/test_chat_menu_buttons.py`。
5. 直连回归双轮：追加 `tests/test_task_description.py` 中主会话绑定相关 `-k` 子集，连续两次通过。
6. 最小诊断：执行 `python3.11 -m vibego_cli doctor`。

### 10.10 风险与回滚

- 风险 1：主键盘新增按钮可能改变既有测试中键盘行列断言；需要同步更新测试，不应删除原按钮。
- 风险 2：Inline callback_data 长度限制 64 bytes，不能直接塞完整 sessionId/path；应使用短 token 映射。
- 风险 3：token 内存态在 bot 重启后会丢失；本轮接受 fail-closed，不为了按钮新增 DB 表。
- 回滚：删除新增按钮常量/handler/token 映射/Inline 回调处理，并恢复 `tests/test_chat_menu_buttons.py`、`tests/test_session_binding.py` 的新增用例。


## 11. 用户澄清：Resume 入口放在会话实况中（2026-05-01 PLAN 修订）

### 11.1 修订口径

用户明确澄清：“既然你的实现方式是 resume，那就在会话实况里，给我个 resume 的入口，点击后我来输入 sessionId”。因此本轮废弃第 10 节中“主键盘新增绑定按钮 / sessionId 消息下方一键绑定”的默认推荐，改为以下收口：

- 入口位置：只放在 `💻 会话实况` 页面中。
- 入口文案：建议使用 `↩️ Resume 会话`，突出 Codex resume 语义。
- 交互方式：点击入口后，进入输入态，提示用户输入 sessionId。
- 输入内容：支持 `rollout-...`、Codex UUID、以及粘贴整行 `sessionId : ...`。
- 执行动作：用户输入 sessionId 后，复用现有 `/bind_session sessionId` 的恢复逻辑，即校验当前项目 -> `codex resume <sessionId>` -> 绑定为当前主会话。
- 边界：仍然仅支持 Codex；非 Codex fail-closed，不做假恢复。

### 11.2 用户视角主流程

1. 用户点击 Worker 主键盘 `💻 会话实况`。
2. 系统展示当前主会话 / 并行会话列表，同时底部出现 `↩️ Resume 会话`。
3. 用户点击 `↩️ Resume 会话`。
4. 系统提示：`请输入要 resume 的 sessionId，支持 rollout-xxx / UUID / sessionId : xxx。发送“取消”可退出。`
5. 用户输入 sessionId。
6. 系统恢复并绑定为主会话，回复：`已恢复并绑定为主会话，后续消息将继续进入该会话。`

### 11.3 当前仓库证据

- `bot.py` 中 `WORKER_TERMINAL_SNAPSHOT_BUTTON_TEXT = "💻 会话实况"`，入口处理函数是 `on_tmux_snapshot_button(...)`。
- `bot.py` 中 `_build_session_live_list_markup(...)` 当前只展示会话条目与 `🔄 刷新列表`。
- `bot.py` 中已存在 `/bind_session` 命令处理函数 `on_bind_session_command(...)`，并已实现 sessionId 解析、工作目录校验、重启主 tmux 到 `codex resume`、绑定 watcher。
- `tasks/fsm.py` 当前集中定义输入态；新增 resume 输入态应放在这里，便于测试与清理。

### 11.4 受影响目录/文件

| 类型 | 文件 | 影响 |
|---|---|---|
| 实现 | `bot.py` | 新增 session live resume callback、resume 输入提示、输入处理，并复用现有绑定逻辑 |
| 实现 | `tasks/fsm.py` | 新增 `SessionResumeStates.waiting_session_id` |
| 测试 | `tests/test_session_live_view.py` | 覆盖会话实况列表包含 `↩️ Resume 会话` 入口 |
| 测试 | `tests/test_session_binding.py` 或新增定向测试 | 覆盖点击 resume 入口进入输入态、输入 sessionId 后恢复主会话、取消、非 Codex fail-closed |
| 文档 | `docs/TASK_0108_sessionid绑定主会话继续.md` | 记录修订口径、测试矩阵、实施顺序与风险 |
| DB | SQLite 表结构 | 不受影响；不新增表字段 |
| 脚本 | `scripts/start_tmux_codex.sh` | 不受影响；复用已实现的 `MODEL_RESUME_SESSION_ID` |

### 11.5 契约变更

- 新增 Inline 按钮：`↩️ Resume 会话`。
- 新增输入态：点击后等待用户输入 sessionId。
- 保留 `/bind_session sessionId` 命令，作为同一能力的命令入口。
- sessionId 解析继续复用 `_normalize_bind_session_id(...)`，兼容 `sessionId : xxx` 粘贴。
- 成功后仍将目标 Codex 会话绑定为当前主会话，后续普通消息进入该会话。
- 取消输入时恢复 Worker 主键盘，不做任何 tmux 操作。

### 11.6 测试矩阵

| 用例 | 覆盖点 | 预期 |
|---|---|---|
| 会话实况列表渲染 | Resume 入口位置 | `💻 会话实况` 列表底部包含 `↩️ Resume 会话` |
| 点击 Resume 入口 | 输入态启动 | 设置 `SessionResumeStates.waiting_session_id`，提示输入 sessionId |
| 输入 `sessionId : rollout-xxx` | 输入规范化 | 能抽取真实 sessionId 并复用绑定逻辑 |
| 输入 UUID | Codex 原生 id | 能定位 JSONL 并执行 resume |
| 输入取消 | 用户主动退出 | FSM 清理，回复取消，不重启 tmux |
| 非 Codex 模型 | 契约缺失 | fail-closed，提示仅支持 Codex，FSM 清理 |
| 工作目录不匹配 | 防串项目 | 拒绝绑定并提示不属于当前项目 |
| 原命令回归 | `/bind_session` 保持可用 | 既有绑定测试仍通过 |

### 11.7 develop 实施顺序

1. Baseline：`python3.11 -m pytest -q tests/test_session_live_view.py tests/test_session_binding.py`。
2. TDD 红灯：先补会话实况 resume 入口、点击进入输入态、输入恢复、取消等测试，并确认失败。
3. 实现：
   - 在 `tasks/fsm.py` 新增 `SessionResumeStates`；
   - 在 `bot.py` 新增 `SESSION_LIVE_RESUME_CALLBACK`；
   - `_build_session_live_list_markup(...)` 添加 `↩️ Resume 会话`；
   - 新增 callback handler：设置输入态并提示输入 sessionId；
   - 新增输入 handler：复用现有 bind_session 主逻辑；
   - 如有必要，把 `on_bind_session_command(...)` 的核心逻辑抽成可复用 helper，避免复制粘贴。
4. 定向验证：`python3.11 -m pytest -q tests/test_session_live_view.py tests/test_session_binding.py`。
5. 直连回归双轮：补充 `tests/test_chat_menu_buttons.py` 中会话实况入口相关用例与 `tests/test_task_description.py` 主会话绑定子集，连续两次通过。
6. 最小诊断：`python3.11 -m vibego_cli doctor`。

### 11.8 风险与回滚

- 风险 1：新增 FSM 输入态可能被通用文本处理器抢先消费；实现时需确保 handler 顺序与状态过滤正确。
- 风险 2：点击入口后用户输入非法 sessionId，应清晰提示失败并结束输入态，避免卡在流程中。
- 风险 3：resume 会重启主 tmux，这是恢复主会话的真实动作；必须在用户输入 sessionId 后才执行，点击入口本身不能重启。
- 回滚：删除 `SessionResumeStates`、`SESSION_LIVE_RESUME_CALLBACK`、session live resume 按钮与相关 handler，保留既有 `/bind_session` 命令能力。

## 12. Resume 入口 develop 实施记录（2026-05-01）

### 12.1 决策结果

用户选择“待决策项全部按模型推荐”，按第 11 节推荐项执行：只在 `💻 会话实况` 中新增 `↩️ Resume 会话` 入口，点击后由用户输入 sessionId，再执行真实 `codex resume <sessionId>` 并绑定为主会话。

### 12.2 规约复核

- 已重新读取 `$HOME/.config/vibego/AGENTS.md`：确认默认 PLAN、TDD、禁止临时修改交付、docs 沉淀与最终收尾要求。
- 已重新读取仓库根 `AGENTS.md`：确认 Strict Evidence Mode、写入范围、pytest 门禁、双轮回归要求。
- 受影响目录 `bot.py`、`tasks/`、`tests/`、`docs/` 下未发现更近的 `AGENTS.md` 或 `AGENTS.evidence.json`。

### 12.3 Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_session_live_view.py tests/test_session_binding.py
```

结果：

- ✅ `9 passed in 0.26s`
- 说明：实现前会话实况与既有绑定命令相关测试全绿。

### 12.4 TDD 红灯

先补测试，覆盖：

1. 会话实况列表必须包含 `↩️ Resume 会话` callback。
2. 点击 `↩️ Resume 会话` 后进入 `SessionResumeStates.waiting_session_id`。
3. 非 Codex 模型点击入口 fail-closed。
4. 输入 `sessionId : rollout-xxx` 后复用主会话恢复逻辑。
5. 输入 `取消` 时清理状态且不重启 tmux。

首次执行：

```bash
python3.11 -m pytest -q tests/test_session_live_view.py tests/test_session_binding.py
```

结果：

- ❌ `5 failed, 8 passed`
- 红灯原因：尚未实现 `SESSION_LIVE_RESUME_CALLBACK`、`SessionResumeStates`、Resume callback handler 与输入 handler。

### 12.5 最小实现

- `tasks/fsm.py`
  - 新增 `SessionResumeStates.waiting_session_id`，用于会话实况 Resume 输入态。
- `bot.py`
  - 新增 `SESSION_LIVE_RESUME_CALLBACK = "session:resume"`。
  - `_build_session_live_list_markup(...)` 在会话实况列表底部新增 `↩️ Resume 会话`。
  - 新增 `_build_session_resume_input_keyboard()`，输入态仅提供“取消”。
  - 抽出 `_resume_main_session_from_user_input(...)` 作为 `/bind_session` 与会话实况 Resume 的共用执行路径，避免重复实现恢复逻辑。
  - 新增 `on_session_live_resume_callback(...)`：点击入口后校验 Codex 模型，进入 sessionId 输入态。
  - 新增 `on_session_resume_session_id_input(...)`：处理 sessionId 输入，兼容 `sessionId : xxx`，输入后清理 FSM，再执行真实 resume 绑定。
- `tests/test_session_live_view.py`
  - 补充会话实况列表包含 Resume callback 的断言。
- `tests/test_session_binding.py`
  - 补充 Resume 入口、非 Codex、输入恢复、取消等定向测试。

### 12.6 Self-Test Gate

#### 定向测试

```bash
python3.11 -m pytest -q tests/test_session_live_view.py tests/test_session_binding.py
```

结果：

- ✅ `13 passed, 2 warnings in 0.39s`
- 说明：warnings 为既有 MarkdownV2 docstring 转义提示，非本次新增逻辑失败。

#### 直连回归双轮

```bash
python3.11 -m pytest -q tests/test_session_live_view.py tests/test_session_binding.py tests/test_chat_menu_buttons.py tests/test_task_description.py -k 'session_live or bind_session or session_binding or worker_terminal_snapshot or worker_session_live_button_opens_session_list or dispatch_prompt_rebinds_when_pointer_updates or dispatch_prompt_injects_enforced_agents_notice or dispatch_prompt_strict_fallback'
```

结果：

- ✅ 第一轮：`22 passed, 221 deselected in 2.47s`
- ✅ 第二轮：`22 passed, 221 deselected in 2.46s`

#### 最小诊断

```bash
python3.11 -m vibego_cli doctor
```

结果：

- ✅ `python_ok=true`
- ✅ `dependencies=[]`

### 12.7 未执行项

- 未执行全仓 pytest：本次改动集中在会话实况、sessionId resume 与 FSM 输入态，已执行受影响与直连回归双轮。
- 未执行 coverage：仓库 `AGENTS.md` 记录当前未定位统一 coverage 命令。
- 未执行正式构建：本次未涉及发布产物，`python -m build` 会写 `dist/`，不在当前必要范围内。

### 12.8 影响点与回滚

#### 影响点

- 用户在 `💻 会话实况` 页面可看到 `↩️ Resume 会话`。
- 点击后不会立刻重启 tmux；只有用户输入 sessionId 后才执行真实 Codex resume。
- `/bind_session sessionId` 仍保持可用，且与新入口共用同一恢复逻辑。
- 非 Codex 模型保持 fail-closed。
- 不涉及 DB schema、不新增依赖、不改构建/CI。

#### 回滚

- 回滚 `tasks/fsm.py` 中 `SessionResumeStates`。
- 回滚 `bot.py` 中 `SESSION_LIVE_RESUME_CALLBACK`、`_build_session_resume_input_keyboard()`、`_resume_main_session_from_user_input(...)`、session live Resume 按钮与两个 handler；如需保留 `/bind_session`，可把 helper 内逻辑还原回命令 handler。
- 回滚新增/修改测试断言。
