# TASK_20260529_002_YesClear触发Homebrew更新排查

## 1. 背景与现象

用户在 Telegram 中点击 PlanConfirm 的 `🧹 Yes, clear context and implement` 后，再点击“会话实况”，看到终端最近输出进入 Homebrew 自动更新阶段：

- `==> Auto-updating Homebrew...`
- `HOMEBREW_AUTO_UPDATE_SECS` / `HOMEBREW_NO_AUTO_UPDATE=1` / `HOMEBREW_NO_ENV_HINTS=1` 提示
- `==> Auto-updated Homebrew!`

附件证据：`/Users/david/.config/vibego/data/telegram/vibegobot/2026-05-29/20260529_062702799-44004a021921.jpg`。

## 2. 现状链路取证

### 2.1 Telegram 三选项语义

`docs/TASK_20260528_001_Telegram计划确认三选项一致性修复.md` 已定义 Codex 模型下 Telegram PlanConfirm 三项：

- `✅ Yes, implement this plan`
- `🧹 Yes, clear context and implement`
- `📝 No, stay in Plan mode`

其中 `Yes, clear context and implement` 的既定语义不是普通确认，而是 fresh context：使用上一轮 `<proposed_plan>` 正文启动全新 Codex 线程，并强制同源绑定校验。

证据锚点：

- `docs/TASK_20260528_001_Telegram计划确认三选项一致性修复.md`：`## 3. 已确认方案`，第 22-31 行。
- `bot.py`：`PlanConfirmSession.plan_text`，第 663-674 行。

### 2.2 fresh context 会重启主 tmux/Codex CLI

`bot.py::_restart_main_tmux_fresh_session` 的函数注释明确写明：重启主 tmux 并启动一个不带 resume 参数的全新 Codex 会话。该函数会：

1. 构造 `scripts/start_tmux_codex.sh` 路径；
2. 从环境中移除 `MODEL_RESUME_SESSION_ID`；
3. 设置 `MODEL_NAME`、`TMUX_SESSION`、`VIBEGO_AGENTS_SYNCED`、`SESSION_POINTER_FILE`、`MODEL_WORKDIR`；
4. 通过 `asyncio.create_subprocess_exec(str(script), "--kill", ...)` 调用启动脚本。

证据锚点：`bot.py`：`_restart_main_tmux_fresh_session`，第 15282-15310 行。

### 2.3 启动脚本只启动 Codex，不显式调用 brew update

`scripts/start_tmux_codex.sh` 中 Codex 默认命令为：

- `codex --dangerously-bypass-approvals-and-sandbox -c trusted_workspace=true`

脚本随后将其追加 `model_instructions_file`、`project_doc_max_bytes`、`features.goals=true` 等参数，最终通过 tmux `send-keys` 启动。

关键点：当前 fresh 启动链路没有显式 `brew update` 调用；仓库内 `rg "brew|Homebrew|HOMEBREW"` 命中的内容主要是安装提示、Homebrew Python venv 断链测试说明，以及 tmux 安装提示，不是 PlanConfirm fresh 分支主动更新 Homebrew。

证据锚点：

- `scripts/start_tmux_codex.sh`：`CODEX_BASE_CMD` / `MODEL_CMD`，第 174-193 行。
- `scripts/start_tmux_codex.sh`：`run_tmux send-keys ... "$FINAL_CMD" C-m`，第 306-314 行。
- `scripts/models/codex.sh`：Codex 默认命令，`model_configure`，第 4-27 行。

## 3. 根因判断

### 3.1 高置信度根因

用户点击的是第二项 `Yes, clear context and implement`，该按钮按设计会清空上下文并启动 fresh Codex 线程。fresh 启动会重新拉起终端中的 Codex CLI；在新的 CLI 启动过程中，Homebrew 自动更新机制被触发，于是会话实况中显示 `Auto-updating Homebrew...`。

这不是模型收到计划后主动执行了 `brew update`，也不是 vibego PlanConfirm 逻辑显式调用了 `brew update`；更准确地说，它是“fresh CLI 启动”带出来的 Homebrew 自动更新副作用。

### 3.2 为什么普通 Yes 通常不会触发

`✅ Yes, implement this plan` 复用当前会话继续发送 `Implement the plan.`；它不需要 kill/restart tmux，也不会重新启动 Codex CLI，因此通常不会走到 Homebrew 的启动期 auto-update。

`🧹 Yes, clear context and implement` 为了获得干净上下文，必须启动新 CLI；所以更容易暴露启动期副作用。

## 4. 影响范围

| 范围 | 当前影响 | 说明 |
|---|---|---|
| Telegram UI | 用户误以为点击确认后模型在执行 brew 更新 | 实际是 fresh 启动链路触发 CLI 启动期输出 |
| 终端/tmux | fresh context 会 kill 并重新启动主会话 | 符合既定 fresh 语义 |
| 模型 JSONL | 新线程会生成新的 Codex 会话文件 | 符合 worker marker 绑定要求 |
| 代码实现 | 本次排查未修改源码 | 仅记录原因与修复选项 |
| 数据库/API | 不涉及 | 无 SQLite 表结构、REST API 变化 |
| 构建/依赖 | 不涉及新增依赖 | 若后续加环境变量，仅为启动环境配置变化 |

## 5. 可选修复方案

### 方案 A：不改代码，仅解释语义

- 做法：继续保留现状；告知用户第二个按钮是 fresh 启动，Homebrew 自动更新属于 CLI 启动期副作用。
- 优点：零风险，不改变现有行为。
- 缺点：后续仍可能在 fresh 启动时看到 Homebrew auto-update 输出。

### 方案 B：在 Codex tmux 启动环境中禁用 Homebrew 自动更新（推荐）

- 做法：在 fresh/通用 Codex tmux 启动环境中注入：
  - `HOMEBREW_NO_AUTO_UPDATE=1`
  - `HOMEBREW_NO_ENV_HINTS=1`
- 优点：能直接消除启动期 Homebrew auto-update 与提示噪声；不改变 PlanConfirm 语义；对用户感知最稳定。
- 缺点：会改变 Homebrew 自动更新策略，后续需要用户自行周期性执行 `brew update`。
- 影响文件候选：`scripts/start_tmux_codex.sh`；必要时补充 `bot.py::_restart_main_tmux_fresh_session` 环境覆盖。

### 方案 C：只调低 Homebrew 自动更新频率

- 做法：设置 `HOMEBREW_AUTO_UPDATE_SECS` 为较大值。
- 优点：仍保留自动更新能力。
- 缺点：不能完全避免；到了时间窗口仍会触发。

### 方案 D：隐藏/删除 fresh context 按钮

- 做法：不再提供 `Yes, clear context and implement`。
- 优点：避免用户误触发 fresh 启动。
- 缺点：削弱已确认的 Codex 三选项一致性契约；不推荐。

## 6. 推荐结论

推荐选择方案 B：保留 `Yes, clear context and implement` 的 fresh 语义，同时在 Codex tmux 启动环境中显式禁用 Homebrew 自动更新与环境提示。

理由：问题的根因不是按钮设计错误，而是 fresh 启动带出的终端环境噪声。修复点应收敛在启动环境，不应删除 fresh 能力。

## 7. 后续实施顺序（如用户确认修复）

1. 基线验证：运行 PlanConfirm 相关测试，确认当前行为绿灯。
2. TDD 红灯：新增/调整启动脚本 dry-run 或环境构造测试，断言 Codex 启动命令包含 Homebrew 环境变量。
3. 最小实现：只在 `scripts/start_tmux_codex.sh` 或 fresh env 注入 `HOMEBREW_NO_AUTO_UPDATE=1`、`HOMEBREW_NO_ENV_HINTS=1`。
4. 回归验证：执行受影响测试、`python3.11 -m vibego_cli doctor`、必要的脚本 dry-run。
5. 文档更新：更新本文与必要的 AGENTS 证据。

## 8. 测试矩阵

| 场景 | 验收点 | 预期 |
|---|---|---|
| 普通 Yes | 不重启 Codex CLI | 不应触发 Homebrew 启动期输出 |
| Yes clear | 重启 fresh Codex CLI | 新线程绑定成功，计划正文被派发 |
| Yes clear + 禁用 Homebrew 自动更新 | fresh 启动环境包含 `HOMEBREW_NO_AUTO_UPDATE=1` | 不再出现 Auto-updating Homebrew |
| 环境变量已有用户值 | 不覆盖或按约定覆盖 | 行为需要在实施前确认 |
| 非 Codex 模型 | 不伪造 fresh 能力 | 保持原约束 |
| 重启失败 | fail-closed | Telegram 给出失败提示，不盲派 |

## 9. 风险与回滚

- 风险：禁用 Homebrew 自动更新后，Codex 或相关 CLI 版本不会在启动时自动获得 Homebrew 更新。
- 回滚：删除启动脚本中的 `HOMEBREW_NO_AUTO_UPDATE` / `HOMEBREW_NO_ENV_HINTS` 注入即可恢复现状。
- 生产影响：仅影响本地 tmux 启动环境；不影响 Telegram API、SQLite 数据、任务数据、模型 JSONL 审计文件。

## 10. 终端原生选择与 Telegram 模拟选择的差异补充

用户追问：正常在终端里选择 `Yes, clear context and implement` 是否也是同样效果。

结论需要区分“语义效果”和“实现机制”：

1. 语义效果一致：终端原生选项与 Telegram 选项都表示“不要继续污染当前上下文，用一个 fresh context 携带计划进入实现”。这也是 `docs/TASK_20260528_001_Telegram计划确认三选项一致性修复.md` 中要求 Telegram 三选项与 Codex 终端一致的原因。
2. 实现机制不完全一致：终端原生选择由 Codex CLI 自己处理；Telegram 无法直接按 Codex TUI 内部选项，只能在外部模拟该语义。因此当前 vibego 的实现是通过 `bot.py::_restart_main_tmux_fresh_session` 调用 `scripts/start_tmux_codex.sh --kill`，明确 kill/restart tmux 中的 Codex CLI。
3. Homebrew auto-update 不属于该选项的业务语义：它只是在“重新启动 Codex 可执行文件”这一实现路径上可能出现的启动期副作用。若 Codex 终端原生选择不重新拉起可执行文件，则未必触发 Homebrew；若用户手动新开 Codex 或终端路径同样重新执行了 Homebrew 管理的 CLI，则仍可能触发。

因此，对用户侧最准确的解释是：

- `clear context and implement` 的“清上下文并继续实现”语义是正常且一致的；
- Telegram 这边为了模拟终端语义，采用了重启 Codex CLI 的外部实现，所以更容易触发 Homebrew 自动更新输出；
- 需要修复的是启动环境噪声，而不是删除 fresh context 语义。

## 11. 纠偏：目标必须等同终端原生选择，而不是外部重启 CLI

用户指出：Telegram 点击 `Yes, clear context and implement` 的效果必须与终端里选择同一项一致。此前“重启 Codex CLI + 把计划正文发到新会话”的实现虽然能实现 fresh 语义，但不等同于终端原生选择，且会引入 Homebrew 启动期副作用。该判断成立，本节记录纠偏后的调研结论。

### 11.1 Codex 官方能力线索

OpenAI Codex 官方文档说明：

- `/clear`：清空终端并在同一个 CLI session 中开始 fresh chat；不同于 Ctrl+L，只清屏不换会话。
- `/new`：在同一个 CLI session 中开始 new conversation，不离开 CLI。
- `/fork`：把当前 conversation fork 成一个新 thread，原 transcript 不变。
- app-server 生命周期中也存在 `thread/start`、`thread/resume`、`thread/fork`、`turn/start` 等线程级 API。
- `check_for_update_on_startup` 是 Codex 启动配置项，说明更新检查发生在 Codex 启动阶段；避免重启 CLI 才能从根上避免启动期更新副作用。

证据锚点：

- OpenAI Codex docs：`Slash commands in Codex CLI`，锚点 `Built-in slash commands`、`/clear`、`/new`、`/fork`。
- OpenAI Codex docs：`Codex App Server`，锚点 `Lifecycle overview`、`API overview`。
- OpenAI Codex docs：`Configuration Reference`，`check_for_update_on_startup`。

本地 Codex 二进制只读取证也能看到 `ThreadFork`、`thread/fork`、`forked_from_id`、`check_for_update_on_startup` 等字符串，说明当前安装版 Codex 具备线程 fork / startup update 相关内部路径。

证据锚点：`/opt/homebrew/Caskroom/codex/0.135.0/codex-aarch64-apple-darwin`，只读 `strings` 取证。

### 11.2 当前 vibego 实现与终端原生效果的差异

当前 `bot.py` 的 fresh 分支：

1. 构造 fresh prompt：`_build_plan_fresh_context_prompt(...)`。
2. 主会话调用 `_restart_main_tmux_fresh_session()`。
3. `_restart_main_tmux_fresh_session()` 通过 `scripts/start_tmux_codex.sh --kill` kill/restart tmux 中的 Codex CLI。
4. 再通过 `_dispatch_prompt_to_model(...)` 把 fresh prompt 发到新启动的 CLI。

证据锚点：

- `bot.py`：`_build_plan_fresh_context_prompt`，第 13776-13782 行。
- `bot.py`：`on_plan_confirm_callback` fresh 分支，第 19394-19454 行。
- `bot.py`：`_restart_main_tmux_fresh_session`，第 15282-15310 行。
- `scripts/start_tmux_codex.sh`：`--kill` 与 `run_tmux send-keys ... "$FINAL_CMD"`，第 256-314 行。

差异：终端原生选择发生在已运行的 Codex TUI 内部；当前 Telegram fresh 重新启动了 Codex 可执行文件。两者不是同一机制。

### 11.3 正确目标

Telegram 的三个按钮不应“自己伪造语义”，而应尽可能驱动当前 Codex TUI 做同一件事：

- `✅ Yes, implement this plan`：等同终端确认第一项。
- `🧹 Yes, clear context and implement`：等同终端确认第二项。
- `📝 No, stay in Plan mode`：等同终端选择第三项，或至少不让终端停在不一致状态。

这样才是真正的“与终端里点击这一项的效果一样”。

## 12. 实现方案调研与对比

### 12.1 方案 A：tmux 原生按键驱动当前 Codex TUI（推荐）

做法：

1. Telegram 收到 PlanConfirm callback 后，不再 restart CLI。
2. 先抓取 tmux 最近输出，确认当前 TUI 仍处于 `Implement this plan?` 选择界面。
3. 根据 Telegram 用户点击的按钮，把对应按键发给当前 tmux pane：
   - 第一项：`Enter` / `C-m`。
   - 第二项：`Down` + `Enter`（可配置为 `PLAN_CONFIRM_NATIVE_FRESH_KEYS`）。
   - 第三项：`Down` + `Down` + `Enter`（可配置）。
4. 点击第二项后，不再手动发送 fresh prompt；由 Codex TUI 原生逻辑负责创建 fresh thread / fork / new conversation 并进入实现。
5. vibego 只做旁路监听：等待新的 Codex session JSONL 出现，更新 `CHAT_SESSION_MAP`、pointer、offset/hash，然后启动 watcher 回传 Telegram。
6. 如果限定时间内未检测到新 session，则 fail-closed，提示“终端原生 fresh 已触发但未发现新会话，请查看终端”。

优点：

- 与终端行为最一致。
- 不重启 Codex CLI，因此不会触发 startup update / Homebrew auto-update。
- 不需要依赖未稳定的 app-server 私有连接。
- 改造范围集中在 `bot.py` 的 PlanConfirm callback 与 session 绑定等待逻辑。

缺点：

- 依赖 TUI 当前光标默认在第一项、`Down/Enter` 键位稳定；需要加环境变量兜底。
- 如果用户在 Telegram 按钮出现后已经手动操作终端，必须 fail-closed，不能盲按。
- 新 session 发现逻辑需要补强，因为 `scripts/session_binder.py` 成功绑定一次后会退出，无法自动更新 pointer。

### 12.2 方案 B：通过 Codex App Server 直接 thread/fork + turn/start

做法：启动或接入 `codex app-server`，用 JSON-RPC 调用 `thread/fork` / `thread/start` / `turn/start`。

优点：

- 结构化 API，理论上比按键更稳定。
- 能直接拿到 thread id 与 streamed events。

缺点：

- 需要把 vibego 从“tmux + TUI + JSONL 监听”改成“app-server client”，影响面很大。
- 不一定能控制当前已经运行的 TUI 会话；可能会变成另一套 Codex 会话面。
- 涉及新增长期进程、协议客户端、鉴权/初始化/事件流处理，不适合作为本次修复。

### 12.3 方案 C：发送 `/new` 或 `/fork` slash command 后再注入计划

做法：不重启 CLI，向当前 TUI 输入 `/new` 或 `/fork`，再输入 fresh prompt。

优点：

- 同一个 CLI 进程内完成，避免 Homebrew startup。
- 比重启 CLI 更接近终端语义。

缺点：

- 仍不是“点击终端原生第二项”；它是用 slash command 拼出近似效果。
- 在 PlanConfirm UI 已经弹出时，直接注入 slash command 可能与菜单态冲突。
- 仍需手工携带 plan prompt，和当前“伪造 fresh prompt”问题本质相近。

## 13. 推荐开发设计

推荐采用 **方案 A：tmux 原生按键驱动当前 Codex TUI**。

### 13.1 关键模块边界

| 模块 | 职责 | 影响 |
|---|---|---|
| `bot.py` PlanConfirm callback | 将 Telegram 按钮映射为当前 TUI 原生选择 | 替换 fresh 分支重启 CLI 行为 |
| `bot.py` tmux 操作工具 | 发送可配置按键序列、抓取菜单状态 | 复用已有 `tmux_send_key`、`_capture_tmux_recent_lines` |
| `bot.py` session 绑定 | 原生 fresh 后等待新 JSONL，更新 chat->session | 新增“等待 native fresh session”逻辑 |
| `tests/test_plan_confirm_bridge.py` | 覆盖三按钮 native key path | 调整旧的 restart mock 预期 |
| `docs/` | 记录纠偏与实施结果 | 本文持续更新 |

### 13.2 契约变更

- Telegram `Yes, clear context and implement` 不再承诺“重启 CLI”，而是承诺“驱动当前 Codex TUI 选择原生第二项”。
- 成功后应绑定 Codex 原生创建的新 session，而不是由 vibego 自建 prompt 会话。
- 如果当前 TUI 不在 PlanConfirm 菜单态，必须 fail-closed，不允许盲发 `Down Enter`。
- 数据库、REST API、依赖、构建链不变。

### 13.3 伪代码

```python
async def _trigger_native_plan_confirm_choice(chat_id, action, session):
    tmux_session = resolve_tmux_session(session)
    output = capture_tmux_recent_lines(tmux_session)
    if not is_codex_plan_confirm_menu(output):
        return failure("终端不在 Implement this plan? 菜单态")

    before_session = session.session_key
    before_time = monotonic_or_epoch()
    keys = native_keys_for_action(action)
    for key in keys:
        tmux_send_key(tmux_session, key)
        sleep(gap)

    if action == YES:
        # 原生第一项通常继续当前或切出 plan，仍监听当前 session。
        return bind_existing_and_watch(before_session)

    if action == FRESH:
        new_session = await wait_latest_codex_session_after(
            cwd=MODEL_WORKDIR,
            not_equal=before_session,
            after=before_time,
            contains_required_marker_if_available=True,
        )
        if not new_session:
            return failure("未检测到 Codex 原生 fresh 会话")
        update_pointer(new_session)
        reset_offsets_and_hashes(chat_id, old=before_session, new=new_session)
        start_watcher(new_session)
        return success

    if action == NO:
        drop_plan_confirm_only()
        refresh_plan_mode_state()
        return success
```

### 13.4 测试矩阵

| 编号 | 场景 | 预期 |
|---|---|---|
| T1 | Telegram 点 fresh 且 tmux 输出包含 PlanConfirm 菜单 | 发送配置化 fresh key sequence，不调用 `_restart_main_tmux_fresh_session` |
| T2 | fresh 后出现新的 Codex JSONL | 更新 `CHAT_SESSION_MAP` / pointer / offset，并启动 watcher |
| T3 | fresh 后未出现新 JSONL | fail-closed，Telegram 提示失败，不发送伪造 prompt |
| T4 | tmux 已不在 PlanConfirm 菜单 | fail-closed，不发送 Down/Enter |
| T5 | 普通 Yes | 走 native 第一项或保持兼容路径（实施时需二选一），不能重启 CLI |
| T6 | No | 不遗留 Telegram 按钮，并与终端菜单状态一致 |
| T7 | 并行 PlanConfirm | 对应并行 tmux 发送 native keys，不回落主会话 |
| T8 | 旧按钮重复点击 | processing token 防重仍有效 |

### 13.5 风险与回滚

- 风险：TUI 文案/默认选中项/键位变化会导致按键不准。缓解：菜单态检测 + key sequence 环境变量可配置 + fail-closed。
- 风险：原生 fresh session 文件发现失败。缓解：按 cwd、mtime、旧 session 排除、marker 过滤多重判断；失败时提示用户查看终端。
- 回滚：如需恢复旧行为，应通过代码回退恢复重启分支；不建议保留运行期开关，以免再次引入启动期 Homebrew 自动更新副作用。

## 14. 开发实施记录（2026-05-29）

### 14.1 用户决策

用户选择方案 B：**Telegram 的 `Yes, clear context and implement` 必须与终端里选择同一项的效果一致**。本次实现将 fresh 分支从“重启 Codex CLI + 粘贴 fresh prompt”调整为“驱动当前 Codex TUI 原生第二项”。

### 14.2 修改范围

| 文件 | 变更 | 证据锚点 |
|---|---|---|
| `bot.py` | 新增 `PLAN_CONFIRM_NATIVE_FRESH_KEYS` 等配置、PlanConfirm 菜单态识别、原生按键发送、新 session 等待与绑定逻辑 | `PLAN_CONFIRM_NATIVE_FRESH_KEYS`、`_is_codex_plan_confirm_menu`、`_drive_native_plan_confirm_fresh_context`、`_await_native_plan_confirm_fresh_session`、`_bind_native_plan_confirm_fresh_session` |
| `bot.py` | `on_plan_confirm_callback` 的 fresh 分支不再调用 `_restart_main_tmux_fresh_session` / `_restart_parallel_tmux_fresh_session` / `_dispatch_prompt_to_model` 粘贴 fresh prompt，而是调用 `_drive_native_plan_confirm_fresh_context` | `on_plan_confirm_callback` |
| `tests/test_plan_confirm_bridge.py` | 调整 fresh 主会话、菜单态 fail-closed、并行 fresh 三个测试，覆盖不重启 CLI、不额外派发 prompt、发送 `Down,C-m`、绑定新 session | `test_plan_confirm_fresh_context_drives_native_tui_and_binds_new_main_session`、`test_plan_confirm_fresh_context_fails_closed_when_terminal_not_in_native_menu`、`test_parallel_plan_confirm_fresh_context_drives_bound_parallel_native_tui` |
| `AGENTS.md` | 更新 PlanConfirm 行为证据，避免后续维护误以为 fresh 仍应重启 CLI | `Telegram PlanConfirm 三选项约束` |

### 14.3 行为契约

1. Telegram `🧹 Yes, clear context and implement`：
   - 先抓取对应 tmux 会话最近输出；
   - 只有识别到 Codex 原生 `Implement this plan?` 菜单时才继续；
   - 默认发送 `Down,C-m`，等价于终端里选择第二项；
   - 等待 Codex 原生创建的新 JSONL session，更新 pointer、offset、watcher。
2. 主会话与并行会话分端处理：
   - 主会话驱动 `TMUX_SESSION`，绑定 `CHAT_SESSION_MAP`；
   - 并行会话驱动 `ParallelDispatchContext.tmux_session`，绑定 `PARALLEL_TASK_SESSION_MAP` / `PARALLEL_SESSION_CONTEXTS`。
3. fail-closed：如果终端不在 PlanConfirm 菜单，直接提示“终端不在 Plan 确认菜单，已中止以避免误操作”，不发送 `Down/Enter`。
4. 数据库、CLI 启动脚本、依赖、构建链均不变。

### 14.4 测试与验证记录

| 阶段 | 命令 | 结果 |
|---|---|---|
| baseline | `python3.11 -m pytest -q tests/test_plan_confirm_bridge.py` | 16 passed |
| TDD 红灯 | `python3.11 -m pytest -q tests/test_plan_confirm_bridge.py -k "fresh_context"` | 预期失败：缺少 `_await_native_plan_confirm_fresh_session` |
| TDD 绿灯 | `python3.11 -m pytest -q tests/test_plan_confirm_bridge.py -k "fresh_context"` | 3 passed |
| 回归 | `python3.11 -m pytest -q tests/test_plan_confirm_bridge.py` | 16 passed |

### 14.5 风险与回滚

- 风险：Codex TUI 文案或菜单默认选中项未来变化，可能导致菜单态识别或 `Down,C-m` 不再匹配。缓解：已提供 `PLAN_CONFIRM_NATIVE_FRESH_KEYS`、`PLAN_CONFIRM_NATIVE_MENU_PROBE_LINES`、等待超时/轮询间隔环境变量，并在菜单态不匹配时 fail-closed。
- 风险：Codex 原生 fresh 已经创建新 session 但 vibego 未能在超时内发现。缓解：按 pointer、cwd、mtime、旧 session 排除和 worker marker 进行搜索；失败时不伪造 prompt，提示用户查看终端状态。
- 回滚：本次没有保留 `restart_cli` 运行期开关，避免再次引入启动期 Homebrew 自动更新副作用；如需回滚，应通过代码回退恢复旧 fresh 分支，并同步回退测试与 AGENTS 证据。

### 14.6 追加验证记录

| 命令 | 结果 | 说明 |
|---|---|---|
| `python3.11 -m pytest -q tests/test_plan_confirm_bridge.py && python3.11 -m pytest -q tests/test_plan_confirm_bridge.py` | 两轮均 16 passed | 受影响 PlanConfirm 回归双轮一致 |
| `python3.11 -m py_compile bot.py` | passed | 语法校验通过 |
| `python3.11 -m vibego_cli doctor` | passed | 本地运行诊断通过 |
| `bash scripts/test_deps_check.sh` | passed | runtime venv 与关键依赖检查通过 |
| `python3.11 -m pytest -q` | 890 passed, 26 failed, 6 warnings | 全量测试存在与本次 PlanConfirm fresh 影响面无直接关系的失败：`tests/test_agents_template_migration.py` 的 `ENFORCED_AGENTS_NOTICE` 断言，以及 `tests/test_parallel_flow.py` 中 `ParallelLaunchSession.send_mode` 必填参数不兼容；本次未修改这些模块，按 fail-closed 记录，不作为本任务完成口径。 |
