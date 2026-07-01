# TASK_20260701_001：Vibego 启动失败 Telegram 连通性排查

## 1. 结论

- 本次用户看到的失败不是此前“worker 已活但 Master 误判”的假失败；`Vibego` worker 在 2026-06-30 23:13:37 的确因为启动前 Telegram 握手超过 30 秒未成功而退出。
- 程序内直接故障点已确认：`bot.py::ensure_telegram_connectivity()` 在 worker 启动阶段调用 `bot.get_me()`；超时会抛出 `RuntimeError("在 30.0 秒内未能与 Telegram 成功握手")`，随后 `main()` 关闭 bot session 并 `SystemExit(1)`。
- 外部根因最高可疑点：本机访问 Telegram 必须走 `http://127.0.0.1:6152` 代理；历史运行日志中同一代理链路多次出现 `ProxyConnectionError`、`ProxyTimeoutError`、`Request timeout`、`ServerDisconnected`，本次失败窗口内也记录“使用代理(https_proxy): http://127.0.0.1:6152”。但 2026-06-30 23:13:07 当刻的代理端口监听状态没有留存，因此外部层面按 Strict Evidence 标为“高置信可疑”，不写成已确认根因。
- 当前状态已现场验证：2026-07-01 08:45:45 `Vibego` worker 已成功完成 Telegram 握手，`master_state.json` 中 `vibego.status=running`，`bot.pid=87299` 对应进程存活；当前代理端口可连接，且通过该代理访问 `https://api.telegram.org` 成功返回 HTTP 302。

## 2. 证据链

| 编号 | 证据 | 观察结果 | 指向判断 |
| --- | --- | --- | --- |
| E1 | `/Users/david/.config/vibego/logs/codex/vibego/run_bot.log:3242-3246` | 本次失败 boot_id=`1d79e5fdf820402a9c5609b4d5d57e95`；启动后使用 `https_proxy=http://127.0.0.1:6152`；30 秒后记录 Telegram 连通性检查失败。 | 失败发生在本次 worker 启动前 Telegram 握手阶段。 |
| E2 | `/Users/david/.config/vibego/logs/vibe.log:285790-285795` | Master 日志同一时间记录 `worker[vibego]` 启动、使用代理、随后 Telegram 连通性检查失败。 | Master 报错不是旧日志误判，而是本次 boot_id 对应 worker 失败。 |
| E3 | `bot.py:26791-26813` | `ensure_telegram_connectivity()` 用 `asyncio.timeout(30.0)` 包住 `bot.get_me()`；超时转成“未能与 Telegram 成功握手”。 | 程序内直接故障点是启动前 get_me 握手超时。 |
| E4 | `bot.py:26861-26872` | `main()` 捕获连通性异常后记录错误、关闭 session 并 `SystemExit(1)`。 | worker 进程退出是当前代码设计的 fail-closed 行为。 |
| E5 | `master.py:2439-2461`、`master.py:2463-2530`、`master.py:2635-2656` | Master 按 boot_id 查本次日志里的握手成功标记；健康检查失败时返回最近日志。 | 本次提示中的失败日志来自本次启动片段，不是追加日志里的旧失败。 |
| E6 | `/Users/david/.config/vibego/logs/codex/vibego/run_bot.log:3128-3236` | 历史运行期间同一代理链路多次出现 `ProxyTimeoutError`、`ProxyConnectionError: Couldn't connect to proxy 127.0.0.1:6152`、`Request timeout`、`ServerDisconnected`。 | 代理链路在近期存在间歇性不可用/超时。 |
| E7 | 本次命令：`nc -vz 127.0.0.1 6152` | 当前代理端口连接成功。 | 当前代理监听已恢复。 |
| E8 | 本次命令：`curl -I --max-time 12 -x http://127.0.0.1:6152 https://api.telegram.org` | 当前经代理返回 `HTTP/1.1 200 Connection Established` 和 `HTTP/2 302`。 | 当前经代理访问 Telegram API 域名可达。 |
| E9 | 本次命令：`curl -I --max-time 8 https://api.telegram.org` | 直连 8 秒超时。 | 本机当前访问 Telegram 依赖代理，不应绕过代理判断。 |
| E10 | `/Users/david/.config/vibego/logs/codex/vibego/run_bot.log:3250-3260` | 2026-07-01 08:45:44 后新 boot_id 成功记录 `Telegram 连接正常，Bot=vibegoBot`，并同步命令/菜单。 | 同一项目在代理恢复后可以启动成功。 |
| E11 | `/Users/david/.config/vibego/state/master_state.json`（锚点：顶层 `vibego.status`） | 本次读取到 `{'model': 'codex', 'status': 'running', 'chat_id': 726858153, 'actual_username': 'vibegoBot', 'telegram_user_id': 8497245661}`。 | Master 当前状态已是 running。 |
| E12 | `/Users/david/.config/vibego/logs/codex/vibego/bot.pid` + 本次 `ps -p 87299` | `bot.pid=87299`，进程命令为 pipx venv 中的 `bot.py`，状态存活。 | 当前 worker 进程真实存在。 |

## 3. 根因裁决

### 已确认程序内直接故障点 R1

因为 E1/E2 显示本次启动后 30 秒内没有成功握手，E3/E4 显示该超时会直接导致 worker 退出，所以本次 Master 报“worker 进程 60295 已退出”的程序内直接原因是：worker 启动前 Telegram `get_me()` 握手超时后 fail-closed 退出。

### 外部根因最高可疑点 H1

因为 E6 显示同一代理链路近期存在多种网络/代理错误，E7/E8/E9 显示当前 Telegram 访问依赖 `127.0.0.1:6152` 且代理恢复后可达，H1 判定为：本地代理或其上游链路在失败窗口内间歇性不可用/超时。

限制：没有 2026-06-30 23:13:07 当刻的 `nc/curl` 现场输出，因此 H1 不能升级为“已确认外部根因”。

## 4. 处理状态

- 本轮未修改源码；没有执行 git commit/push/merge。
- 已新增排查文档与 HTML 诊断图，作为本次现场证据沉淀。
- 当前服务状态已验证为运行中，但这代表“当前现场恢复”，不代表代理链路未来不会再次瞬断。

## 5. 可执行处理建议

1. 若再次看到同类错误，先验证代理：`nc -vz 127.0.0.1 6152`，再验证 Telegram 经代理可达：`curl -I --max-time 12 -x http://127.0.0.1:6152 https://api.telegram.org`。
2. 若代理不通，先恢复代理/VPN，再从 Master 面板重新启动项目。
3. 若代理通但 worker 仍失败，收集对应 boot_id 后的 `run_bot.log` 和 `vibe.log`，避免用旧日志判断。
4. 如需降低偶发代理抖动导致的启动失败，可以后续做代码硬化：给 `ensure_telegram_connectivity()` 增加启动期短重试/退避，并在错误里显式输出代理来源、代理端口连通性和最后一次底层异常；该变更需要先写失败测试再改源码。

## 6. 本次验证命令

```bash
python3.11 -m vibego_cli status
nc -vz 127.0.0.1 6152
curl -I --max-time 12 -x http://127.0.0.1:6152 https://api.telegram.org
curl -I --max-time 8 https://api.telegram.org
python3.11 - <<'PY'
import json
from pathlib import Path
p=Path('/Users/david/.config/vibego/state/master_state.json')
data=json.loads(p.read_text())
print(data.get('vibego'))
PY
ps -p 87299 -o pid,ppid,stat,etime,command
```

结果摘要：Master 运行；代理端口当前可连接；经代理访问 Telegram 成功；直连 Telegram 超时；`vibego.status=running`；worker PID 87299 存活。

## 7. 未覆盖项 / TODO

- TODO：无法回放 2026-06-30 23:13:07 当刻代理端口是否监听、上游代理是否可用。
- TODO：未做源码硬化；若要把“偶发代理瞬断”从启动失败变成启动期重试，需要另开实现任务并执行 TDD。
