# TASK_20260311_008 并行 CLI 显式 ready 与项目启动 trusted 前置校验

## 1. 背景

- 用户现场现象：
  - Telegram 选择 `新建分支 + 新 CLI 并行处理 -> PLAN -> 跳过` 后，提示：
    - `并行 CLI 未启动成功：当前终端仍停留在 shell（zsh）`
    - `并行 CLI 未启动成功，请稍后重试。`
  - 但现场 tmux 中的 Codex 实际已经起来，说明存在“启动判定过早”的竞态。
- 用户补充要求：
  1. 并行 CLI 按模型推荐改为 **显式 ready 契约**；
  2. **项目启动时** 也要先自动校验项目目录 trusted 权限，缺失时自动补齐。

## 2. 关键证据

- 并行 CLI 仅执行 `send-keys`，没有 ready 回执：
  - `scripts/start_tmux_codex.sh`（锚点：`run_tmux send-keys -t "$SESSION_NAME" "$FINAL_CMD" C-m`）
- Python 侧脚本退出后立即继续首次派发：
  - `bot.py`（锚点：`_start_parallel_tmux_session(...)`）
- 首次派发前仍采用一次性 shell 检测：
  - `bot.py`（锚点：`_validate_parallel_tmux_ready_for_dispatch(...)`）
- worker 启动时已有主项目 trusted 兜底，但不是 master 启动前置：
  - `bot.py`（锚点：`main`, `_ensure_primary_workdir_codex_trust`）
- master 项目启动当前直接拉起 `run_bot.sh`：
  - `master.py`（锚点：`run_worker`, `cmd = [str(RUN_SCRIPT), "--model", ...]`）

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- worker 启动与并行链路：`bot.py`
- master 启动链路：`master.py`
- 并行 tmux 启动脚本：`scripts/start_tmux_codex.sh`
- 新增共享 trusted helper：`codex_trust.py`
- 测试资产：
  - `tests/test_parallel_flow.py`
  - `tests/test_master_network_resilience.py`
  - `tests/test_codex_trust_config.py`
  - `tests/test_task_description.py`

### 3.2 计划修改单元

| 单元 | 实现文件 | 测试文件 |
|---|---|---|
| `ensure_codex_project_trust`（新增共享 helper） | `codex_trust.py` | `tests/test_master_network_resilience.py`, `tests/test_codex_trust_config.py` |
| `_ensure_codex_trusted_project_path` | `bot.py` | `tests/test_codex_trust_config.py` |
| `_parallel_tmux_ready_file`（新增） | `bot.py` | `tests/test_parallel_flow.py` |
| `_start_parallel_tmux_session` | `bot.py` | `tests/test_parallel_flow.py` |
| `run_worker` | `master.py` | `tests/test_master_network_resilience.py` |
| tmux ready 握手 | `scripts/start_tmux_codex.sh` | `tests/test_parallel_flow.py`（通过 `_start_parallel_tmux_session` 间接覆盖） |

### 3.3 直连依赖测试

- `tests/test_codex_trust_config.py`
  - 证据：覆盖 `bot.py::_ensure_codex_trusted_project_path`、`_ensure_primary_workdir_codex_trust`
- `tests/test_parallel_flow.py`
  - 证据：覆盖 `on_parallel_branch_confirm_callback(...)` 与并行 tmux 启动入口
- `tests/test_task_description.py`
  - 证据：覆盖并行首次派发前的 `/plan` ready 等待与 shell fail-closed 护栏
- `tests/test_master_network_resilience.py`
  - 证据：覆盖 master 点击启动后的 `run_worker(...)` 调用顺序

### 3.4 测试范围升级判断

- 结论：✅ 命中升级条件
- 原因：
  - 修改了启动链路
  - 修改了脚本契约
  - 修改了共享 trusted 配置逻辑

## 4. Baseline Gate

执行：

```bash
python3.11 -m pytest -q \
  tests/test_codex_trust_config.py::test_ensure_primary_workdir_codex_trust_uses_primary_workdir \
  tests/test_parallel_flow.py::test_parallel_branch_confirm_callback_ensures_workspace_trust_before_starting_tmux \
  tests/test_task_description.py::test_dispatch_prompt_plan_mode_waits_for_parallel_tmux_ready \
  tests/test_task_description.py::test_dispatch_prompt_parallel_first_dispatch_fails_closed_when_tmux_still_shell \
  tests/test_master_network_resilience.py::test_run_action_ack_before_run_worker
```

结果：

- ✅ `5 passed`

## 5. TDD 红灯

先补测试，再首次执行：

```bash
python3.11 -m pytest -q \
  tests/test_master_network_resilience.py::test_run_worker_ensures_project_workdir_trust_before_launch \
  tests/test_master_network_resilience.py::test_run_worker_fails_closed_when_project_workdir_trust_auto_config_fails \
  tests/test_parallel_flow.py::test_start_parallel_tmux_session_requires_ready_file \
  tests/test_parallel_flow.py::test_start_parallel_tmux_session_returns_after_ready_file_written
```

结果：

- ❌ `4 failed`
- 首次失败点：
  - `master.py` 还没有 `ensure_codex_project_trust`
  - `run_worker(...)` 启动前未做 trusted 前置校验
  - `_start_parallel_tmux_session(...)` 未向脚本传递 ready 回执契约
  - 脚本成功返回后，Python 侧没有校验 ready 文件

## 6. 最小实现

### 6.1 共享 trusted helper

- 新增 `codex_trust.py`
  - 抽出 `config.toml` 读写与 `ensure_codex_project_trust(...)`
  - 供 `bot.py` 与 `master.py` 共用

### 6.2 项目启动前置 trusted 校验

- `master.py`
  - `run_worker(...)` 在 `create_subprocess_exec(... run_bot.sh ...)` 前：
    - 先执行 `ensure_codex_project_trust(workdir, config_path=CODEX_CONFIG_PATH)`
    - 失败则 fail-closed，不继续启动 worker

### 6.3 并行 CLI 显式 ready 契约

- `bot.py`
  - 新增 `_parallel_tmux_ready_file(...)`
  - `_start_parallel_tmux_session(...)` 向脚本传入：
    - `SESSION_READY_FILE`
    - `SESSION_READY_TIMEOUT_SECONDS`
    - `SESSION_READY_POLL_INTERVAL_SECONDS`
    - `SESSION_READY_PROBE_LINES`
    - `SESSION_READY_MARKERS`
  - 脚本返回成功后若不存在 ready 回执文件，则直接失败

- `scripts/start_tmux_codex.sh`
  - 新增 `wait_for_tmux_ready`
  - 启动 Codex 后轮询：
    - 前台进程不再是 shell
    - pane 输出命中 ready markers
  - ready 成功才写 `SESSION_READY_FILE` 并退出 0
  - 超时则 stderr 输出明确错误并退出非 0

## 7. Self-Test Gate

### 7.1 定向回归

执行：

```bash
python3.11 -m pytest -q tests/test_codex_trust_config.py
python3.11 -m pytest -q tests/test_master_network_resilience.py
python3.11 -m pytest -q tests/test_parallel_flow.py -k 'start_parallel_tmux_session or ensures_workspace_trust_before_starting_tmux'
python3.11 -m pytest -q tests/test_task_description.py -k 'plan_mode_waits_for_parallel_tmux_ready or parallel_first_dispatch_fails_closed_when_tmux_still_shell or parallel_first_dispatch_does_not_fallback_to_old_session'
python3.11 -m py_compile bot.py master.py codex_trust.py
```

结果：

- ✅ `5 passed`
- ✅ `4 passed`
- ✅ `3 passed, 24 deselected`
- ✅ `3 passed, 148 deselected`
- ✅ `py_compile` 通过

### 7.2 最终两轮一致性回归

同上述命令连续执行 2 轮，结果一致：

- ✅ 第一轮通过
- ✅ 第二轮通过

## 8. 用户可见结果

1. 项目启动前会先自动校验主项目目录的 Codex trusted 权限。
2. 若主项目目录缺少 trusted 配置，会先自动补齐，再继续启动。
3. 并行 CLI 不再只看“脚本是否返回”，而是要求脚本给出显式 ready 回执。
4. 若 Codex UI 迟迟未进入 ready 状态，会明确失败，不再出现“其实起来了但先报死”的误判。

## 9. 未执行项说明

- 当前仓库未找到可证实的统一 typecheck 命令
- 当前仓库未找到可证实的 coverage 命令
- 证据：`AGENTS.md`（锚点：`当前覆盖率工具`、`typecheck：TODO`）
