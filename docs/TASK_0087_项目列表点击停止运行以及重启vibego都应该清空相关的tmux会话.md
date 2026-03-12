# /TASK_0087 项目列表点击停止运行以及重启 vibego 都应该清空相关的 tmux 会话

## 1. 背景与确认口径

- 用户确认：
  - 停止单项目时，清理 **该项目主 worker tmux + 该项目相关并行 tmux**
  - “重启 vibego”覆盖 **Telegram 的「重启 Master」/`/restart`** 与 **CLI `vibego stop/start`**
- 仓库现状证据：
  - 项目列表停止按钮走 `MasterManager.stop_worker(...)`
    - `master.py`（锚点：`async def stop_worker`）
  - `stop_bot.sh` 的单项目停止只清理主 worker tmux
    - `scripts/stop_bot.sh`（锚点：`stop_single_worker`）
  - 并行 tmux 命名规则是 `vibe-par-<project>-<task>`
    - `bot.py`（锚点：`def _parallel_tmux_session`）
  - CLI `vibego stop` 已有全局 worker/残留进程清理链路
    - `vibego_cli/main.py`（锚点：`def command_stop`）

## 2. Class Impact Plan

### 2.1 受影响子项目与目录

- Master 控制面：`master.py`
- CLI 管理入口测试：`tests/test_vibego_cli_startup.py`
- Master 重启/停止测试：`tests/test_master_restart.py`

### 2.2 受影响单元

| 单元 | 实现文件 | 测试文件 |
| --- | --- | --- |
| `_list_tmux_session_names`（新增） | `master.py` | `tests/test_master_restart.py` |
| `_parallel_tmux_prefix_for_project`（新增） | `master.py` | `tests/test_master_restart.py` |
| `_clear_related_tmux_sessions`（新增） | `master.py` | `tests/test_master_restart.py` |
| `MasterManager.stop_worker` | `master.py` | `tests/test_master_restart.py` |
| `_perform_restart` | `master.py` | `tests/test_master_restart.py` |
| `command_stop`（验证现有 CLI 收口） | `vibego_cli/main.py` | `tests/test_vibego_cli_startup.py` |

### 2.3 直连依赖测试纳入依据

- `tests/test_master_restart.py`
  - 直接覆盖项目按钮停止、重启 master 的行为回归
- `tests/test_vibego_cli_startup.py`
  - 直接覆盖 `vibego stop` 的清理收口

### 2.4 测试范围升级判断

- 结论：❌ 未升级
- 原因：
  - 未改数据库 schema / 外部接口 / 构建链
  - 仅补强 tmux 清理收口与对应类级测试

## 3. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_master_restart.py tests/test_vibego_cli_startup.py
```

结果：

- ✅ `12 passed`

## 4. TDD 红灯

新增测试：

- `tests/test_master_restart.py::test_stop_worker_clears_project_related_tmux_sessions`
- `tests/test_master_restart.py::test_perform_restart_clears_all_related_tmux_sessions_before_start_script`
- `tests/test_vibego_cli_startup.py::test_command_stop_triggers_worker_and_process_cleanup`

首次执行：

```bash
python3.11 -m pytest -q \
  tests/test_master_restart.py::test_stop_worker_clears_project_related_tmux_sessions \
  tests/test_master_restart.py::test_perform_restart_clears_all_related_tmux_sessions_before_start_script \
  tests/test_vibego_cli_startup.py::test_command_stop_triggers_worker_and_process_cleanup
```

结果：

- ❌ 2 个失败
- 失败原因：
  - `master.py` 中尚不存在 `_clear_related_tmux_sessions`
  - `stop_worker` / `_perform_restart` 尚未挂接统一 tmux 清理

## 5. 最小实现

### 5.1 新增统一 tmux 清理入口

- `master.py::_list_tmux_session_names()`
  - 统一枚举当前 tmux 会话
- `master.py::_parallel_tmux_prefix_for_project(project_slug)`
  - 复用并行会话前缀规则：`vibe-par-<project[:12]>-`
- `master.py::_clear_related_tmux_sessions(project_slug=None)`
  - `project_slug=None`：清理全部 vibego 相关 tmux
  - `project_slug=xxx`：仅清理该项目主 worker tmux + 该项目并行 tmux

### 5.2 停止单项目时补清理

- `master.py::MasterManager.stop_worker`
  - 保留既有 `stop_bot.sh` 主 worker 停止逻辑
  - 在脚本返回后补调用 `_clear_related_tmux_sessions(cfg.project_slug)`

### 5.3 重启 master 时补清理

- `master.py::_perform_restart`
  - 在拉起 `scripts/start.sh` 前先调用 `_clear_related_tmux_sessions()`
- `master.py::bootstrap_manager`
  - 启动兜底阶段也改为统一走 `_clear_related_tmux_sessions()`

## 6. Self-Test Gate

执行两轮一致性回归：

```bash
python3.11 -m pytest -q tests/test_master_restart.py tests/test_vibego_cli_startup.py
python3.11 -m pytest -q tests/test_master_restart.py tests/test_vibego_cli_startup.py
```

结果：

- ✅ 第一轮：`15 passed`
- ✅ 第二轮：`15 passed`

## 7. 用户可见结果

1. 在项目列表点击“停止”时，会额外清理该项目相关的并行 tmux，会话不会继续残留。
2. 点击「重启 Master」/发送 `/restart` 时，会在拉起重启脚本前先清空 vibego 相关 tmux。
3. `vibego stop` 的 CLI 清理链路仍保持有效，并有回归测试兜底。
