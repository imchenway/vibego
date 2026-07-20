# TASK_20260720_001 Vibego Master 代理超时保活修复

## 1. 问题与范围

用户在本机执行 `vibego stop && vibego start` 后，Telegram Master Bot 仍无响应。

本任务只保证 Vibego Master 能正常启动并在 Telegram 网络或代理暂时不可达时保持进程存活、持续重试。项目 worker 仍维持手动 `/run` 的既有语义，不新增自动恢复或自动启动行为。

## 2. 现场证据

| 证据 | 事实 | 结论 |
|---|---|---|
| `/Users/david/.config/vibego/logs/vibe.log`（锚点：`2026-07-20 18:15:47`、`Proxy connection timed out: 60`） | Master 先输出“已启动”，随后 `aiohttp_socks._errors.ProxyTimeoutError` 从 polling 链路穿透并结束进程。 | 这是 Telegram 无响应的第一硬失败。 |
| `/Users/david/.config/vibego/state/master.pid` + `vibego status`（锚点：`master_running=false`） | PID 文件存在，但对应进程已经死亡。 | CLI 的短时启动成功不等于 Master 持续存活。 |
| `master.py`（锚点：`_run_master_polling`） | 旧实现只捕获 `TelegramNetworkError`。 | aiohttp-socks 未包装的代理异常不在重试范围内。 |
| `tests/test_master_network_resilience.py`（锚点：`test_master_polling_retries_after_startup_network_timeout`） | 旧测试只覆盖 aiogram 已包装的 `TelegramNetworkError`。 | 缺少真实异常层级的回归资产。 |
| `master.py`（锚点：`bootstrap_manager`、`worker 需手动启动`） | Master 启动时停止历史 worker。 | 经用户确认，本任务不改变 worker 生命周期语义。 |

## 3. 修复口径

1. `TelegramNetworkError`、aiohttp `ClientError`、超时异常继续按网络故障重试。
2. 对 `aiohttp_socks` / `python_socks` 明确的 `ProxyConnectionError`、`ProxyError`、`ProxyTimeoutError` 执行同一保活重试。
3. 非网络异常继续抛出，禁止用无限重试掩盖程序错误。
4. 不自动启动或恢复任何项目 worker。

## 4. TDD 与验收标准

| 编号 | 验收标准 |
|---|---|
| AC-01 | polling 遇到 aiogram `TelegramNetworkError` 时保持进程并重试。 |
| AC-02 | polling 遇到未包装的 `aiohttp_socks._errors.ProxyTimeoutError` 时保持进程并重试。 |
| AC-03 | polling 遇到非网络 `RuntimeError` 时原样抛出，不进入重试。 |
| AC-04 | `vibego stop && vibego start` 后，代理不可达期间 `vibego status` 仍显示 `master_running=true`。 |
| AC-05 | 项目 worker 状态仍为 `stopped`，不发生自动启动。 |

### 基线

- `python3.11 -m pytest -q tests/test_master_network_resilience.py`：`10 passed`。
- 裸跑 `python3.11 -m pytest -q`：收集阶段因既有 `BOT_TOKEN` 缺失而退出。
- 补齐仓库既有测试环境后执行：`BOT_TOKEN=TEST_TOKEN MODEL_WORKDIR=/Users/david/hypha/tools/vibego PYTHONPATH=. python3.11 -m pytest -q`：`1096 passed, 6 warnings`。

### RED

- 新增 `test_master_polling_retries_after_raw_aiohttp_socks_proxy_timeout`。
- 修复前结果：`1 failed`；原始 `ProxyTimeoutError` 从 `master._run_master_polling` 直接穿透。

## 5. 影响面与回滚

- 实现：`master.py::_run_master_polling`、`master.py::_is_retryable_telegram_polling_error`。
- 测试：`tests/test_master_network_resilience.py`。
- 无数据库、配置格式、公共命令或 worker 自动启动语义变化。
- 如需回滚，只回退上述实现与对应测试；回滚后 AC-02 会重新失败。

## 6. 最终验证

| 验证项 | 命令/证据 | 结果 |
|---|---|---|
| 聚焦回归 | `python3.11 -m pytest -q tests/test_master_network_resilience.py tests/test_vibego_cli_startup.py tests/test_worker_startup_connectivity.py` | `17 passed`。 |
| 真实异常类型 | pipx Python 导入 `aiohttp_socks._errors` 的 `ProxyConnectionError`、`ProxyError`、`ProxyTimeoutError` 并调用 `_is_retryable_telegram_polling_error` | 三类均判定为可重试；`RuntimeError` 保持 fail-closed。 |
| 语法检查 | `python3.11 -m py_compile master.py tests/test_master_network_resilience.py` | 通过。 |
| 全量回归第 1 轮 | `BOT_TOKEN=TEST_TOKEN MODEL_WORKDIR=/Users/david/hypha/tools/vibego PYTHONPATH=. python3.11 -m pytest -q` | `1098 passed, 6 warnings`。 |
| 全量回归第 2 轮 | 同上 | `1098 passed, 6 warnings`。 |
| 构建与本机安装 | `python3.11 -m build --wheel` + `pipx install --force <临时 wheel>` | wheel 构建成功；pipx 已安装 `vibego 1.5.220`。 |
| 配置诊断 | `/Users/david/.local/bin/vibego doctor` | Python、依赖、配置、数据库检查均通过。 |
| 故障窗口保活 | 以 `https_proxy=http://127.0.0.1:6152` 启动，在日志出现 60 秒 `ProxyTimeoutError` 后重复执行 `vibego status` | Master 持续为 `master_running=true`，未复现旧版超时退出。 |
| Telegram 真实入站 | `/Users/david/.config/vibego/logs/vibe.log`（锚点：`2026-07-20 18:54:12`） | 收到管理员“📂 项目列表”更新并生成 11 个项目按钮。 |
| worker 语义 | `/Users/david/.config/vibego/state/master_state.json`；日志锚点：`worker 需手动启动` | 启动验收时全部 worker 为 `stopped`；未自动恢复。随后 FawnStudio 的启动来自用户手动按钮操作。 |

全量测试的 6 个 warning 均为 `tests/test_unescape_markdown.py` 返回非 `None` 的既有 `PytestReturnNotNoneWarning`，与本任务无关。
