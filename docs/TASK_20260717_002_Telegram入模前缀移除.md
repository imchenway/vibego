# TASK_20260717_002 Telegram 入模前缀移除

## 1. 目标与用户决策

Telegram worker 向模型投递业务 prompt 时，不再自动追加以下两段内容：

- `以下是用户需求描述：`
- `请求来源：vibego Telegram worker / 移动端。HTML 图交付：...`

验收口径：进入 tmux 的正文必须与调用 `_dispatch_prompt_to_model` 时收到的 `prompt` 完全一致，包括换行与首尾空白；用户主动输入相同文字时不得做内容清洗。

## 2. 主任务记忆与现状证据

采用 `docs/TASK_20260626_002_HTML图Codex预览链接与Telegram来源提示.md` 作为本次现状主依据；该文档记录了 Telegram
来源上下文的引入过程。本任务是对该入模行为的显式反向变更，旧文档保留为历史记录。

| 事实                              | 证据                                                                                                            |
|---------------------------------|---------------------------------------------------------------------------------------------------------------|
| 修改前的两个前缀常量由 worker 运行时代码定义。     | `bot.py`（修改前锚点：`ENFORCED_AGENTS_NOTICE`、`TELEGRAM_SOURCE_CONTEXT_NOTICE`）                                     |
| 修改前所有业务 prompt 在统一投递入口被追加前缀。    | `bot.py`（修改前锚点：`_prepend_enforced_agents_notice`、`_dispatch_prompt_to_model`）                                 |
| 会话漂移补偿也按追加前缀后的文本寻找本轮用户事件。       | `bot.py`（修改前锚点：`_probe_new_model_message_once`、`dispatch_text=_prepend_enforced_agents_notice(proof_prompt)`） |
| HTML 附件补发属于模型输出侧能力，不依赖保留入模前缀函数。 | `bot.py`（锚点：`_collect_model_response_local_documents`、`_send_model_response_local_documents`）                 |

未发现 `PROJECT-STYLE.md`、`CODE-GUIDELINES.md`、`DESIGN.md`。仓库内没有 `CONTEXT.md` / `CONTEXT-MAP.md`；本次没有新增稳定领域术语，也不满足
ADR 的难回退、非直观、真实权衡三项条件，因此不新增领域文档或 ADR。

## 3. 实现与兼容边界

- 删除运行时前缀常量和 `_prepend_enforced_agents_notice()`。
- `_dispatch_prompt_to_model` 直接使用 `dispatch_text = prompt`。
- 补偿轮询以 `proof_prompt` 原文作为 `dispatch_text` 进行同源证明，保持会话重绑、防历史 final 回放和 watcher 恢复语义。
- 立即发送、排队发送、Copilot 自动重试、Plan 模式和并行会话均沿用原有控制逻辑，只改变发送正文。
- 不修改内部测试标记阻断、HTML 文档收集/发送、`vibe-diagram` skill、依赖、配置、数据库或 CI。
- 不修改或回滚本任务开始前已经存在的 `AGENTS-template.md`、`AGENTS.md` 其他行、旧版本模板和既有任务文档改动。

## 4. TDD 与验证记录

### 4.1 红灯

| 命令                                                                                                                                                                                                                                                                                  | 结果                        | 裁决                |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------|-------------------|
| `BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_task_description.py::test_dispatch_prompt_sends_raw_prompt_without_telegram_prefix`                                                                                                  | `1 failed`；实际值比原文多出两段自动前缀 | 测试正确复现需求缺口。       |
| `BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_agents_template_migration.py::test_telegram_prompt_prefix_runtime_contract_is_removed tests/test_task_description.py::test_dispatch_prompt_sends_raw_prompt_without_telegram_prefix` | 修改实现前，运行时代码负向契约失败         | 证明旧常量/helper 仍存在。 |

### 4.2 绿灯

| 命令                                                                                                                                                                                                                                                                                  | 结果                                                                                                       |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| `BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_agents_template_migration.py::test_telegram_prompt_prefix_runtime_contract_is_removed tests/test_task_description.py::test_dispatch_prompt_sends_raw_prompt_without_telegram_prefix` | `2 passed`                                                                                               |
| `BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_agents_template_migration.py tests/test_task_description.py tests/test_tmux_send_line.py tests/test_plan_confirm_bridge.py tests/test_message_recovery_poll.py`                      | `252 passed`                                                                                             |
| `python3.11 -m py_compile bot.py`                                                                                                                                                                                                                                                   | 退出码 `0`                                                                                                  |
| `BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q`                                                                                                                                                                                                | `1164 passed, 6 warnings in 55.12s`；6 条 warning 均来自未修改的 `tests/test_unescape_markdown.py` 测试函数返回 `bool`。 |
| 反向搜索生产代码中的旧常量、helper、来源文案，以及测试中的旧 `bot.<symbol>` 引用                                                                                                                                                                                                                                 | 退出码 `0`；`bot.py` 无旧前缀实现，测试无旧符号调用。                                                                        |
| `git diff --check`                                                                                                                                                                                                                                                                  | 退出码 `0`                                                                                                  |

### 4.3 需求逐条验收

| 要求                    | 实现证据                                               | 验证证据                                                                                   | 判定 |
|-----------------------|----------------------------------------------------|----------------------------------------------------------------------------------------|----|
| 去掉“以下是用户需求描述：”        | `bot.py` 不再定义 `ENFORCED_AGENTS_NOTICE` 或前缀 helper。 | 运行时负向契约测试、反向搜索。                                                                        | 通过 |
| 去掉 Telegram/HTML 来源整段 | `bot.py` 不再定义 `TELEGRAM_SOURCE_CONTEXT_NOTICE`。    | 原文透传测试、反向搜索。                                                                           | 通过 |
| Telegram 正文原样进入 tmux  | `bot.py`（锚点：`dispatch_text = prompt`）。             | `test_dispatch_prompt_sends_raw_prompt_without_telegram_prefix` 覆盖多行、首尾空白和用户主动输入旧分隔文字。 | 通过 |
| 重试与恢复不再依赖旧前缀          | `bot.py`（锚点：`dispatch_text=proof_prompt`）。         | 5 个受影响测试文件共 `252 passed`。                                                              | 通过 |
| 不影响 HTML 附件输出链路       | 本任务未修改 HTML 文档收集/发送实现。                             | 全量 `1164 passed`，包含既有 HTML 输出测试。                                                       | 通过 |

剩余未覆盖点：未重启运行中的 Telegram worker，也未执行真实 Telegram 客户端端到端实发；因此不能把源码与自动化测试结果表述为运行环境已生效。

## 5. 风险与回滚

- 风险：模型不再自动获知 Telegram/移动端来源，可能按 Codex 默认格式组织 HTML 交付文本；输出侧仍能从项目内 HTML 路径或
  `file://` URI 收集并发送附件。
- 风险：会话恢复若仍按旧前缀查找会拒绝新 pointer；已将恢复证明同步改为原始 prompt，并纳入恢复轮询测试。
- 回滚：恢复两个常量、前缀 helper、统一投递与补偿轮询调用点，并恢复对应测试和 `AGENTS.md` 事实行。
- 生效：源码验证不等于运行中 worker 已更新；实际 Telegram worker 需要重启后才使用新代码。本任务不自动修改安装产物或执行运行服务重启。
