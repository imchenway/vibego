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

## 7. 项目列表仍无响应的后续根因

Master 保活后，Telegram 入站更新已经能够到达，但发送项目列表仍超时。现场进一步证明：

| 证据 | 事实 | 裁决 |
|---|---|---|
| `master.py`（锚点：旧 `_detect_proxy`）与 `bot.py`（同名锚点） | `TELEGRAM_PROXY` 为空时会继续读取 `https_proxy/http_proxy/all_proxy`。 | Telegram 专用链路错误继承了终端 HTTP 代理。 |
| `scutil --proxy`（2026-07-20 现场） | 用户 macOS 同时配置 HTTP 与 SOCKS，二者端口不同。 | Vibego 必须读取 SOCKS 配置本身，不能推断或写死端口。 |
| `lsof -nP -a -p <Telegram PID> -iTCP` | Telegram App 与本机 SOCKS 端口存在两条已建立连接。 | Telegram App 正常不代表旧 Vibego HTTP 代理链路正确。 |
| `dscacheutil -q host -a name api.telegram.org` + SOCKS TLS 探测 | 本机 DNS 返回非 Telegram 地址；SOCKS CONNECT 成功后 TLS 无响应。 | 切换 SOCKS5 后仍需保护 Bot API 域名解析。 |
| 同一 SOCKS5 + DoH 得到的 Telegram IPv4 + `getMe/sendMessage` | `getMe` 成功，并向管理员发送验证消息。 | SOCKS5 传输正常，剩余故障点是污染 DNS。 |

## 8. Telegram 专用代理修复契约

1. `TELEGRAM_PROXY=system`：运行时读取 macOS 当前启用的 SOCKS5 主机和端口，不内置任何本机端口。
2. `TELEGRAM_PROXY=<完整 URL>`：主机、协议、端口全部由用户提供。
3. `TELEGRAM_PROXY=`：明确表示直连，不继承终端 `http_proxy/https_proxy/all_proxy`。
4. 格式无效或选择 `system` 但系统 SOCKS5 未启用时 fail-closed。
5. SOCKS5 模式通过同一代理执行 DoH，并仅为 `api.telegram.org` 安装进程内 IPv4 映射；不修改 `/etc/hosts`、macOS DNS 或 Mono 配置。
6. Master 与 worker 复用同一个代理解析模块，避免配置语义漂移。

### TDD 记录

- Baseline：`BOT_TOKEN=TEST_TOKEN MODEL_WORKDIR=/Users/david/hypha/tools/vibego python3.11 -m pytest -q` → `1098 passed, 6 warnings`。
- RED 1：`tests/test_telegram_proxy.py` 首次收集失败，原因是 `telegram_proxy` 模块不存在。
- RED 2：`vibego init --telegram-proxy ...` 被 argparse 拒绝，证明 CLI 配置入口不存在。
- GREEN：代理、Master/worker、CLI 与 worker 健康检查聚焦回归 → `47 passed`。

### 影响面

- `telegram_proxy.py`：统一代理解析、macOS SOCKS5 读取、Bot API 受保护 DNS。
- `master.py` / `bot.py`：复用统一解析并配置 aiogram 连接器。
- `vibego_cli/main.py`：新增 `init --telegram-proxy` 配置入口。
- `pyproject.toml`：将新模块纳入 wheel。
- `tests/test_telegram_proxy.py`：正常、边界、异常及 Master/worker 集成回归。

## 9. 安装与真实 Telegram 验收

| 验证项 | 证据 | 结果 |
|---|---|---|
| 无源码固定端口 | `rg -n "6152\|6153" telegram_proxy.py master.py bot.py vibego_cli/main.py pyproject.toml` | 无匹配；本机端口仅存在于用户的 `~/.config/vibego/.env`。 |
| wheel 包含新模块 | `python3.11 -m build --wheel` + `unzip -l` | `telegram_proxy.py`、`master.py`、`bot.py`、`vibego_cli/main.py` 均进入 `vibego-1.5.221` wheel。 |
| pipx 隔离安装 | pipx Python 使用 `-I` 导入 | `telegram_proxy` 来自 pipx `site-packages`，不是源码目录误加载。 |
| CLI/配置诊断 | `vibego init --help`、`vibego doctor` | 新增 `--telegram-proxy`；Python、依赖、配置、数据库均通过。 |
| 代理与 DNS | `/Users/david/.config/vibego/logs/vibe.log`（锚点：`2026-07-20 19:45:53`） | 使用 `TELEGRAM_PROXY` 指定的 SOCKS5，并记录“Telegram Bot API 已启用 SOCKS5 受保护 DNS”。 |
| Bot API 真实调用 | 安装包执行 `getMe` + `sendMessage` | Bot 身份获取成功，验证消息 `message_id=3547` 发送成功。 |
| 原始项目列表动作 | 同一日志（锚点：`2026-07-20 19:46:06` 至 `19:46:07`） | 收到用户“📂 项目列表”，生成 11 个按钮并成功发送项目概览。 |
| Master 存活 | `vibego status` + `lsof -nP -a -p 56894 -iTCP` | `master_running=true`，Master 到用户 SOCKS5 端口的 TCP 为 `ESTABLISHED`。 |

### 当前机器的配置边界

Mono 当前使用 TUN 模式，虽然本机 SOCKS5 服务处于监听状态，但 `scutil --proxy` 没有暴露 SOCKS 字段。因此当前机器使用显式 `TELEGRAM_PROXY=socks5://<本机地址>:<用户端口>`；端口由用户配置文件决定，不存在源码默认值。`TELEGRAM_PROXY=system` 只适用于 macOS 系统代理确实启用了 SOCKS5 的环境，缺失时按设计 fail-closed。

### 全量回归已知问题

- 全量回归共执行 4 轮：两轮 `1112 passed, 6 warnings`；另两轮均只有 `tests/test_wx_remote_debug_flow.py::test_trigger_retries_once_with_new_dynamic_port_after_cli_conflict` 失败，其余 `1111` 个通过。
- 该微信端口竞态用例单独复跑通过；失败表现为前序临时 WebSocket 尚可探测时，本用例直接复用首个端口，未产生预期的第二次 CLI 端口记录。
- 此问题与 Telegram 代理改动无代码交集，本任务未修改微信远程调试逻辑；因此没有达到“连续两轮全量全绿”，但 Telegram 聚焦回归、构建、安装和真实交互均已通过。
