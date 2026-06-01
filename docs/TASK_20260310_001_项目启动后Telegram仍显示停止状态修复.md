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

---

## 9. 2026-06-01 追补：项目列表点击启动后实际进程存在但列表仍显示未启动

### 9.1 现象 -> 影响 -> 根因 -> 修法 -> 验证

- 现象：在 Master 项目列表点击“启动”后，worker 后台进程可能已经存在，但原项目列表仍保留“▶️ 启动”按钮，用户会误以为未启动。
- 影响：用户可能重复点击启动，造成 `run_bot.sh` 再次检测到同项目 `bot.pid` 后失败，或误判 worker 状态，属于高风险状态一致性问题。
- 根因：`master.py` 的默认健康检查等待时间为 20 秒，而 `bot.py` 内部 Telegram 连通性检查默认等待 30 秒；当 worker 仍在启动或稍后握手成功时，Master 先超时并把 state 回写为 `stopped`，且异常分支不会刷新原项目列表。
  - Evidence: `master.py`（锚点：`WORKER_HEALTH_TIMEOUT`、`run_worker`、`_health_check_worker`、`on_project_action`）
  - Evidence: `bot.py`（锚点：`ensure_telegram_connectivity(bot: Bot, timeout: float = 30.0)`）
  - 现场证据：`~/.config/vibego/logs/vibe.log` 在 `2026-06-01 11:37:38` 出现 `worker 进程 ... 未在 20.0s 内完成 Telegram 握手`，同一 worker 日志在 `2026-06-01 11:37:50` 才输出 `Telegram 连通性检查失败：在 30.0 秒内未能...`。
- 修法：
  1. 将 Master 默认健康检查等待时间提高到 45 秒，保证默认值覆盖 worker 的 30 秒 Telegram 握手窗口。
  2. worker 脚本启动成功后立即把项目状态写为 `starting`，并持久化本轮 `boot_id`；握手成功后再转为 `running`。
  3. 健康检查超时但 `bot.pid` 仍存活时，不再回写 `stopped`，保持 `starting`，项目列表展示“⏳ 启动中/停止”，阻止重复启动并提供停止入口。
  4. 项目列表渲染前根据 `bot.pid + run_bot.log` 做运行态 reconcile：对 stale `stopped` 状态，如 pid 存活且有握手日志，自动纠正为 `running`；如 pid 存活但未握手，纠正为 `starting`。
  5. 启动按钮异常分支也刷新项目列表，避免原 inline message 继续显示旧的“启动”按钮。

### 9.2 受影响目录与文件

| 类型 | 文件 | 影响 |
|---|---|---|
| 实现 | `master.py` | Worker 启动状态机、项目列表展示、异常分支刷新、运行态 reconcile |
| 测试 | `tests/test_worker_health_boot_id.py` | 覆盖健康检查超时但 pid 存活、starting UI、stale 状态纠正、默认超时口径 |
| 测试 | `tests/test_master_network_resilience.py` | 覆盖启动失败但状态变为 starting 后仍刷新项目列表 |
| 文档 | `docs/TASK_20260310_001_项目启动后Telegram仍显示停止状态修复.md` | 追补本次根因、方案、验证与风险 |

### 9.3 契约变更

- `ProjectState.status` 增加运行中间态：`starting`。
- `StateStore` 持久化新增可选字段：`boot_id`。
- 项目列表按钮契约：
  - `running`：展示 `⛔️ 停止`。
  - `starting`：展示 `⏳ 启动中/停止`，callback 仍走 `project:stop:<slug>`。
  - 其他状态：展示 `▶️ 启动`。
- `/run` 与项目列表启动逻辑不会把“已存在且仍存活的 worker”误当作可重复启动对象。
- 不涉及数据库表结构、外部 API、Bot Token、Telegram command 契约变更。

### 9.4 测试矩阵

| 场景 | 用例/命令 | 结果 |
|---|---|---|
| TDD 红灯：健康检查超时但 pid 存活 | `python3.11 -m pytest -q tests/test_worker_health_boot_id.py` | 首次新增用例失败：旧逻辑回写 `stopped` |
| TDD 红灯：starting 状态 UI 不允许重复启动 | `python3.11 -m pytest -q tests/test_worker_health_boot_id.py` | 首次新增用例失败：旧 UI 仍展示启动按钮 |
| TDD 红灯：stale stopped 需 reconcile 为 running | `python3.11 -m pytest -q tests/test_worker_health_boot_id.py` | 首次新增用例失败：无 `reconcile_worker_states` |
| TDD 红灯：Master 默认健康检查覆盖 worker 30s | `python3.11 -m pytest -q tests/test_worker_health_boot_id.py` | 首次新增用例失败：旧默认 20s |
| TDD 红灯：异常分支也刷新项目列表 | `python3.11 -m pytest -q tests/test_master_network_resilience.py::test_run_action_refreshes_overview_when_worker_left_starting_after_failure` | 首次新增用例失败：旧异常分支直接 return |
| 聚焦验证第一轮 | `python3.11 -m pytest -q tests/test_worker_health_boot_id.py tests/test_master_network_resilience.py tests/test_master_project_management.py` | ✅ `46 passed` |
| 聚焦验证第二轮 | 同上 | ✅ `46 passed` |
| CLI 诊断 | `python3.11 -m vibego_cli doctor` | ✅ `python_ok=true`，依赖缺失列表为空 |
| 依赖脚本 | `bash scripts/test_deps_check.sh` | ✅ aiogram/aiohttp/aiosqlite 均已安装 |
| 全量 pytest | `python3.11 -m pytest -q` | 🔴 `3 failed, 944 passed`；失败集中在 `tests/test_agents_template_migration.py`，与本次启动状态修复无直接实现耦合 |

### 9.5 实施顺序

1. 复核现状链路：`project:run` -> `MasterManager.run_worker` -> `scripts/run_bot.sh` -> `bot.ensure_telegram_connectivity` -> `StateStore` -> `_projects_overview`。
2. 先补失败测试，覆盖“pid 存活但健康检查超时不能显示 stopped”的主路径与异常刷新分支。
3. 最小实现：新增 `starting` 中间态、`boot_id` 持久化、运行态 reconcile、健康超时 pid 存活保护、异常分支刷新。
4. 执行聚焦测试双轮、CLI doctor、依赖脚本；全量 pytest 记录现有不相关失败。

### 9.6 风险与回滚

- 风险 1：`starting` 是新增状态，旧 state 文件没有该字段不受影响；新增 `boot_id` 为可选字段，旧文件兼容。
- 风险 2：stale `stopped` reconcile 在无 `boot_id` 的旧状态下会使用“pid 存活 + 任意握手日志”纠正为 `running`，这是为了兼容旧版本已遗留的错误状态；若 pid 文件错误指向无关进程，可能误判。现有 stop 脚本仍可通过“停止”入口清理。
- 风险 3：默认健康检查从 20s 提升到 45s，会让真实网络故障的按钮等待时间更长，但可避免 20s/30s 超时倒挂造成的错误状态；如需临时回滚，可通过环境变量 `WORKER_HEALTH_TIMEOUT=20` 恢复旧等待，但不建议。
- 回滚方式：还原 `master.py` 中 `starting/boot_id/reconcile` 相关改动，并删除本次新增测试；注意回滚后会重新暴露“实际 worker 存活但项目列表显示启动”的风险。

### 9.7 未解决/独立问题

- 全量 pytest 的 3 个失败均在 `tests/test_agents_template_migration.py`：
  - `bot.ENFORCED_AGENTS_NOTICE` 当前实际值为 `以下是用户需求描述：`，测试期望完整 AGENTS 提示。
  - `AGENTS-template.md` 当前未包含测试期望的 `## Comet 自动调用规则`。
- 该问题属于 AGENTS 模板/提示词迁移契约，不在本次 worker 启动状态最小修复范围内；如要修复需单开任务，避免混入启动状态变更。
