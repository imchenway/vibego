# TASK_20260706_001 request_user_input 原生菜单回填修复

## 背景

用户反馈：Telegram 已显示 `✅ 已推送到模型。决策摘要：... -> 用户强制同步 (Recommended)`，但终端仍停留在 `request_user_input` 选项待选择阶段。该现象会让用户误以为 Telegram 决策已经被 Codex 接收，实际 Codex TUI 仍在等待本地菜单选择。

## 现场证据

| 编号 | 证据 | 观察 | 裁决 |
| --- | --- | --- | --- |
| E1 | 用户截图附件：`/var/folders/2m/dswmfzbd7wx_39zxs4kvd3fc0000gn/T/codex-clipboard-44a495ed-9986-45cc-9b08-9d9eecba4418.png` | Telegram 在 08:39 左右展示“已推送到模型”，但用户说明终端还在选项待选择阶段。 | 用户可见确认与终端真实状态不一致。 |
| E2 | Codex session：`/Users/david/.codex/sessions/2026/07/04/rollout-2026-07-04T13-19-03-019f2b90-e241-74b0-aeaf-02660c7aaf91.jsonl`（锚点：line 235、line 236、line 248） | `request_user_input` function_call 出现在 `2026-07-06T08:38:05+08:00`；对应 `function_call_output` 到 `2026-07-06T09:27:54+08:00` 才写入；本轮 `task_complete` 到 `09:28:44+08:00`。 | Telegram 08:39 的“已推送”没有立即形成 Codex 原生工具结果，延迟约 49 分钟。 |
| E3 | worker 日志：`/Users/david/.config/vibego/logs/codex/fawnstudio/run_bot.log`（锚点：line 2626-line 2635、line 2655-line 2661） | 08:38 发现并发送 request_input 事件；08:39 用户操作触发 watcher 重绑；直到 09:28 才检测到后续模型输出。 | watcher 侧也证明 08:39 后没有立即继续模型执行。 |
| E4 | tmux capture：`vibe-fawnstudio` | 当前 transcript 显示 `Questions 1/1 answered` 后才继续 `Explored` 与 `Proposed Plan`，并显示整轮 `Worked for 51m 37s`。 | 终端最终是原生菜单被回答后才继续，不是 Telegram 旧 prompt 回填立即生效。 |
| E5 | 历史设计文档：`docs/TASK_20260604_001_request_user_input回填问题上下文.md`（锚点：`提交流程在 _submit_request_input_session 中调用 _dispatch_prompt_to_model`） | 旧链路把 Telegram 答案构造成普通 prompt，发送到 tmux。 | 这对普通输入可用，但对 Codex 原生 `request_user_input` 菜单是错误层级。 |

## 根因

已确认根因：Codex 原生 `request_user_input` 正在 TUI 菜单态等待 `function_call_output`。旧实现却把 Telegram 按钮答案包装成普通业务 prompt 送入 tmux，并在 `_dispatch_prompt_to_model` 成功后回显“已推送到模型”。由于 TUI 此时不是普通输入框，普通 prompt 不能闭合原生工具生命周期，导致用户看到“已推送”，终端仍卡在选项菜单。

## 修复方案

1. Codex + `request_user_input` 走原生菜单驱动：
   - 提交时先截取 tmux 最近输出，确认仍在当前 `request_user_input` 选项菜单。
   - 将 Telegram 选项序号映射为 `Down * index + Enter`。
   - 发送按键后等待 session JSONL 写入同一 `call_id` 的 `function_call_output`。
   - 只有确认 JSONL 写入后，才回显决策摘要与 session ack。
2. fail-closed：
   - 终端菜单未匹配、session 文件不存在、未答/自定义决策、按键发送失败或未确认 `function_call_output` 时，不再假装成功。
   - 发送错误提示并保留重试按钮。
   - 已回答 transcript（`Questions 1/1 answered`）不会被误判成待选择菜单。
3. 保留非原生回填：
   - Copilot `ask_user` 与非 Codex fallback 仍使用原来的 `question_context + answers/schema JSON` prompt 回填。

## 代码影响

| 文件 | 变更 |
| --- | --- |
| `bot.py` | 新增原生 request_input 菜单探测、按键驱动、`function_call_output` 等待与 watcher 即时恢复；`_submit_request_input_session` 对 Codex 原生 request_input 改走原生菜单驱动。 |
| `tests/test_request_user_input_flow.py` | 新增原生菜单直驱、fail-closed、已回答 transcript 防误判、按键序列与 JSONL 确认测试；旧 prompt 回填测试显式覆盖非原生 fallback。 |
| `AGENTS.md` | 更新 request_user_input 行为事实，区分 Codex 原生菜单直驱与非原生 prompt fallback。 |
| `docs/TASK_20260706_001_request_user_input原生菜单回填修复.html` | 单文件故障排查图，承载证据链、根因、修法、验证与回滚。 |

## TDD 记录

### Baseline

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py
# 27 passed in 2.33s
```

### RED

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py -k 'codex_native_without_prompt_dispatch or native_request_input_selection'
# 3 failed, 27 deselected
# 失败点：Codex 原生 request_user_input 仍调用 _dispatch_prompt_to_model；_drive_native_request_input_selection 尚不存在。
```

### GREEN

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py
# 31 passed, 2 warnings in 2.56s
```

## 最终验证记录

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py
# 31 passed, 2 warnings in 2.40s
```

```bash
PYTHONPATH=. python3.11 -m pytest -q \
  tests/test_plan_confirm_bridge.py \
  tests/test_tmux_send_line.py::test_dispatch_prompt_does_not_retry_codex_when_jsonl_not_confirmed
# 首次未设置 MODEL_WORKDIR 时失败：环境门禁报“未配置工作目录 (MODEL_WORKDIR)”。
# 该失败是测试运行环境缺少工作目录，不是本次 request_input 逻辑失败。
```

```bash
MODEL_WORKDIR=/Users/david/hypha/tools/vibego PYTHONPATH=. python3.11 -m pytest -q \
  tests/test_plan_confirm_bridge.py \
  tests/test_tmux_send_line.py::test_dispatch_prompt_does_not_retry_codex_when_jsonl_not_confirmed
# 21 passed in 0.31s
```

```bash
python3.11 -m py_compile bot.py
# exit 0
```

```bash
PYTHONPATH=. python3.11 -m vibego_cli doctor
# exit 0；python_ok=true；dependencies=[]；config_root=/Users/david/.config/vibego
```

```bash
MODEL_WORKDIR=/Users/david/hypha/tools/vibego PYTHONPATH=. python3.11 -m pytest -q
# 第 1 轮：1132 passed, 6 warnings in 42.05s
# 第 2 轮：1132 passed, 6 warnings in 40.85s
# warnings 均为既有 tests/test_unescape_markdown.py 返回 bool 的 pytest warning，本轮未改。
```

## 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| Codex TUI 菜单文本变化导致探测失败 | fail-closed，不盲发 Enter；可通过 `REQUEST_INPUT_NATIVE_MENU_PROBE_LINES` 调整截取范围。 |
| 自定义决策无法通过原生选项菜单直驱 | 当前显式拒绝并提示终端手动选择，避免假成功；后续可单独设计原生 Other 输入。 |
| worker 未重启导致线上仍用旧代码 | 需要重新安装/重启对应 worker 后生效。 |
| 按键发送后 JSONL 未确认 | 不回显成功，保留重试入口。 |

回滚：恢复 `_submit_request_input_session` 为旧的 `_dispatch_prompt_to_model` prompt 回填；删除新增原生菜单驱动函数与对应测试。回滚后会重新暴露本次“已推送但终端仍待选”的问题。
