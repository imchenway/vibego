# TASK_20260310_001 项目启动后 Telegram 仍显示停止状态修复

## 1. 背景

- 用户现象：启动项目后，Telegram 项目列表仍显示 `stopped`，但 `tmux ls` 已存在会话。
- 本次直接证据：
  - `~/.config/vibego/logs/codex/vibegobot/run_bot.log`
  - `~/.config/vibego/logs/codex/hyphamall/run_bot.log`
  - 均出现 `ModuleNotFoundError: No module named 'parallel_runtime'`

## 2. 现状结论

- `master.py` 的状态写入并不依赖 tmux 是否存在，而依赖 worker 健康检查是否通过。
- 健康检查成功标记是 `run_bot.log` 中出现 `Telegram 连接正常`。
- 本次失败的真实根因不是 Telegram 展示错误，而是发布包漏收 `parallel_runtime.py`，导致 worker 启动即退出。

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- 打包配置：`pyproject.toml`
- 回归测试：`tests/test_packaging_manifest.py`
- 直连验证测试：
  - `tests/test_parallel_flow.py`
  - `tests/test_vibego_cli_runtime_venv.py`

### 3.2 受影响单元

1. 打包清单 `tool.setuptools.py-modules`
   - 实现文件：`pyproject.toml`
   - 测试文件：`tests/test_packaging_manifest.py`
2. 并行运行时源码存在性回归
   - 实现文件：`parallel_runtime.py`
   - 测试文件：`tests/test_parallel_flow.py`
3. 运行时虚拟环境准备逻辑
   - 实现文件：`vibego_cli/main.py`
   - 测试文件：`tests/test_vibego_cli_runtime_venv.py`

### 3.3 直连依赖测试纳入依据

- `tests/test_parallel_flow.py:22` 直接 `from parallel_runtime import build_parallel_commit_message`
- `vibego_cli/main.py:345-348` 会把 `PACKAGE_ROOT / requirements` 注入 worker 运行环境，属于本次打包链路直连验证

### 3.4 测试范围升级判断

- 命中升级条件：✅ 是
- 原因：本次修改了 `pyproject.toml`，属于构建/打包链路变更，必须追加 build + wheel smoke 验证。

## 4. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_parallel_flow.py tests/test_vibego_cli_runtime_venv.py
```

结果：

- ✅ `6 passed`

## 5. TDD 红灯

先新增 `tests/test_packaging_manifest.py`，断言 `parallel_runtime` 必须进入发布包。

首次执行：

```bash
python3.11 -m pytest -q tests/test_packaging_manifest.py
```

结果：

- ❌ 失败：`AssertionError: assert 'parallel_runtime' in {'bot', 'logging_setup', 'master', 'project_repository'}`

满足先红后绿。

## 6. 最小实现

- 修改 `pyproject.toml`
  - `py-modules` 从
    - `["bot", "master", "logging_setup", "project_repository"]`
  - 调整为
    - `["bot", "master", "logging_setup", "project_repository", "parallel_runtime"]`

## 7. Self-Test Gate

### 7.1 类级测试

```bash
python3.11 -m pytest -q tests/test_packaging_manifest.py tests/test_parallel_flow.py tests/test_vibego_cli_runtime_venv.py
python3.11 -m pytest -q tests/test_packaging_manifest.py tests/test_parallel_flow.py tests/test_vibego_cli_runtime_venv.py
```

结果：

- ✅ 第一轮：`7 passed`
- ✅ 第二轮：`7 passed`

### 7.2 build 验证

```bash
python3.11 -m build
```

结果：

- ✅ 生成：
  - `dist/vibego-1.5.57.tar.gz`
  - `dist/vibego-1.5.57-py3-none-any.whl`

### 7.3 wheel smoke

执行临时 venv 安装 wheel，并验证：

```bash
import parallel_runtime
import bot
```

结果：

- ✅ `parallel_runtime_ok= True`
- ✅ `bot_ok= True`

额外核验：

- wheel 内包含 `parallel_runtime.py`

## 8. 风险与后续观察

1. 当前修复的是“发布包漏模块”主因；**线上本机若仍使用旧 pipx 安装副本，还需要重新安装/升级 vibego 才会实际生效**。
2. `python3.11 -m build` 输出显示 sdist 带入了 `scripts/.venv/...` 内容，这不是本次故障主因，但建议后续单开任务清理发布包噪音。
3. `run_bot.log` 中仍可见历史 `ProxyConnectionError: 127.0.0.1:8234`，这属于独立网络/代理问题，不在本次最小修复范围内。
