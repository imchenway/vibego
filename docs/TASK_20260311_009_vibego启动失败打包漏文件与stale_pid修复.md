# TASK_20260311_009 vibego 启动失败打包漏文件与 stale PID 修复

## 1. 背景

- 用户现场命令：

```bash
vibego stop && vibego start
```

- 现场现象：
  - `stop` 正常结束
  - `start` 输出：`执行失败： master 进程启动失败，请检查日志。`

## 2. 关键证据

- 启动报错来自 CLI：
  - `vibego_cli/main.py:560-574`
  - `command_start(...)` 在写入 `master.pid` 后等待 2 秒；若子进程已退出，则直接抛 `RuntimeError("master 进程启动失败，请检查日志。")`
- 运行日志直接报缺模块：
  - `~/.config/vibego/logs/vibe.log`
  - 命中：

```text
ModuleNotFoundError: No module named 'codex_trust'
```

- `master.py` / `bot.py` 已新增对 `codex_trust` 的直接导入：
  - `master.py:63`
  - `bot.py:64`
- 但打包清单漏掉该模块：
  - `pyproject.toml:35-37`
  - `py-modules = ["bot", "master", "logging_setup", "project_repository", "parallel_runtime"]`
- 构建产物也能复现遗漏：
  - 修复前 `dist/vibego-1.5.76-py3-none-any.whl` 中无 `codex_trust.py`
- 启动失败后还会残留 stale PID：
  - `vibego_cli/main.py:570-574` 失败路径未清理 `config.MASTER_PID_FILE`
  - `python3.11 -m vibego_cli status` 现场输出：
    - `master_pid = 2449`
    - `master_running = false`

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- 打包配置：`pyproject.toml`
- CLI 启动链路：`vibego_cli/main.py`
- 测试资产：
  - `tests/test_packaging_manifest.py`
  - `tests/test_vibego_cli_runtime_venv.py`
  - `tests/test_vibego_cli_startup.py`（新增）

### 3.2 计划修改单元

| 单元 | 实现文件 | 测试文件 |
|---|---|---|
| `tool.setuptools.py-modules` | `pyproject.toml` | `tests/test_packaging_manifest.py` |
| `command_start(...)` stale PID 处理 | `vibego_cli/main.py` | `tests/test_vibego_cli_startup.py` |

### 3.3 直连依赖测试

- `tests/test_vibego_cli_runtime_venv.py`
  - 证据：同属 `vibego_cli/main.py` 运行时启动准备链路，覆盖 `_ensure_virtualenv(...)`
- build + wheel smoke
  - 证据：本次命中打包/构建链路升级条件，仅类级 pytest 不足以证明发布产物安全

### 3.4 测试范围升级判断

- 结论：✅ 命中升级条件
- 原因：
  - 修改了 `pyproject.toml`，属于构建/打包链路变更
  - 需要对 wheel 产物做额外验证

## 4. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_packaging_manifest.py tests/test_vibego_cli_runtime_venv.py
```

结果：

- ✅ `3 passed`

## 5. TDD 红灯

先补测试：

- `tests/test_packaging_manifest.py`
  - `test_codex_trust_is_packaged_for_distribution`
- `tests/test_vibego_cli_startup.py`
  - `test_command_start_clears_stale_pid_file_when_master_exits_early`
  - `test_command_start_ignores_dead_master_pid_before_launch`

首次执行：

```bash
python3.11 -m pytest -q tests/test_packaging_manifest.py tests/test_vibego_cli_startup.py
```

结果：

- ❌ `3 failed, 1 passed`

失败点：

1. `codex_trust` 不在 `py-modules` 中
2. `master` 启动即退出后，`master.pid` 未清理
3. 启动前若存在死掉的 `master.pid`，`command_start(...)` 仍误判“已启动”

## 6. 最小实现

### 6.1 修复发布包漏收 `codex_trust.py`

- `pyproject.toml`
  - `py-modules` 新增 `codex_trust`

### 6.2 修复 start 的 stale PID 收口

- `vibego_cli/main.py`
  - 启动前读取到 `master.pid` 时：
    - 若进程仍存活，保持原逻辑直接返回
    - 若 PID 已失效，自动删除 pid 文件后继续启动
  - 子进程在 2 秒内退出时：
    - 先删除刚写入的 `master.pid`
    - 再抛启动失败异常

## 7. Self-Test Gate

### 7.1 类级测试双轮

执行两轮：

```bash
python3.11 -m pytest -q tests/test_packaging_manifest.py tests/test_vibego_cli_startup.py tests/test_vibego_cli_runtime_venv.py
python3.11 -m pytest -q tests/test_packaging_manifest.py tests/test_vibego_cli_startup.py tests/test_vibego_cli_runtime_venv.py
```

结果：

- ✅ 第一轮：`6 passed`
- ✅ 第二轮：`6 passed`

### 7.2 build 验证

执行：

```bash
python3.11 -m build
```

结果：

- ✅ 成功生成：
  - `dist/vibego-1.5.76.tar.gz`
  - `dist/vibego-1.5.76-py3-none-any.whl`

### 7.3 wheel smoke

验证 wheel 内容：

```bash
python3.11 -m zipfile -l dist/vibego-1.5.76-py3-none-any.whl | rg 'codex_trust\.py|master\.py|bot\.py'
```

结果：

- ✅ wheel 内包含：
  - `bot.py`
  - `codex_trust.py`
  - `master.py`

临时 venv 安装 wheel 后，从 **非仓库目录** 导入验证：

```python
import codex_trust
import master
```

结果：

- ✅ `codex_trust_ok = True`
- ✅ `master_ok = True`
- ✅ 导入路径位于临时 venv 的 `site-packages`

## 8. 用户可见结果

1. 重新发布/安装后，`vibego start` 不会再因 `ModuleNotFoundError: codex_trust` 立即退出。
2. 若上一次启动失败留下 `master.pid`，再次 `vibego start` 会自动清理失效 PID，而不是误报“master 已启动”。
3. 若本次启动在早期失败，也不会再遗留新的假 PID。

## 9. 风险与后续观察

1. **本次修复的是仓库代码与发布产物**；本机现有 pipx 安装副本若仍是旧包，仍需重新安装/升级后才会真正生效。
2. `python3.11 -m build` 仍带入 `scripts/.venv/...` 到 sdist，这不是本次故障主因，但属于发布包噪音，建议后续单开任务治理。
