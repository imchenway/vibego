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
