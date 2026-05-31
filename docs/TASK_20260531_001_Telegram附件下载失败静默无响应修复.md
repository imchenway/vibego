# TASK_20260531_001 Telegram附件下载失败静默无响应修复

## 1. 背景与现象

用户反馈“体验很差”：在 Telegram 向 `vibegoBot` 发送带截图/附件的消息后，前几次没有任何机器人响应；随后发送纯文本 `?` 时才收到 `codex 思考中`。

只读取证结果：

- `vibegobot` 运行日志在 `2026-05-31 09:18~09:25` 出现两次 `ProxyTimeoutError: Proxy connection timed out: 60`。
- 堆栈显示失败发生在 `on_media_message -> _enqueue_media_group_message/_collect_saved_attachments -> _download_telegram_file -> bot.get_file`。
- 纯文本消息不需要下载附件，因此 `09:40` 能进入 `_handle_prompt_dispatch` 并返回 session ack。

## 2. 根因

当前普通附件消息链路先下载 Telegram 文件，再拼接附件提示词推送模型。附件下载依赖 Telegram 代理链路；当代理超时时，异常会从 `_collect_saved_attachments` 冒泡到 aiogram dispatcher：

1. 单图/文件：`on_media_message` 直接异常退出。
2. 相册/媒体组：`_enqueue_media_group_message` 在已创建聚合状态后异常退出，用户无可见提示，且聚合状态可能残留。

因此用户看到的不是模型慢，而是消息没有进入模型。

## 3. 受影响目录

| 目录/文件 | 影响 |
|---|---|
| `bot.py` | 普通 Telegram 附件消息处理；附件下载失败时新增可见回执和 caption 文字兜底推送。 |
| `tests/test_task_description.py` | 新增附件下载失败的 TDD 回归测试。 |
| `AGENTS.md` | 补充 Telegram 附件下载失败可见回执的仓库事实证据。 |
| `docs/` | 记录本次根因、设计、验证与回滚。 |

不涉及：数据库 schema、CLI 参数、命令中心、微信开发命令脚本、模型会话文件格式。

## 4. 契约变更

| 场景 | 旧行为 | 新行为 |
|---|---|---|
| 普通附件下载失败且有 caption | 异常退出，用户无响应，caption 也不会进模型 | 先提示“附件下载失败”，再把 caption 文字发送给模型 |
| 普通附件下载失败且无 caption | 异常退出，用户无响应 | 提示附件下载失败，并要求重发附件/检查代理 |
| 媒体组附件下载失败且有 caption | 异常退出，聚合状态可能残留 | 清理媒体组聚合状态，提示失败，并把 caption 文字发送给模型 |
| 附件下载成功 | 正常下载附件并拼接附件提示词 | 保持不变 |

## 5. 实现方案

### 5.1 最小修复方案（本次采用）

在普通聊天附件入口 `on_media_message` 外层捕获附件下载相关异常：

1. 单附件与媒体组统一进入兜底处理。
2. 若是媒体组，先清理 `MEDIA_GROUP_STATE`，避免失败相册残留。
3. 给用户发送明确失败提示，包含简短错误原因。
4. 若消息存在 caption/text，则继续调用 `_handle_prompt_dispatch` 将文字说明发送给模型。

优点：改动范围小，直接解决“没反应”和“文字说明丢失”。

缺点：附件本体仍需用户重发；代理不稳定时不能保证附件下载成功。

### 5.2 不采用的方案

| 方案 | 不采用原因 |
|---|---|
| 在 `_download_telegram_file` 内无限重试 | 会拖慢消息处理，且代理长时间不可用时仍然卡住用户。 |
| 失败后自动把 Telegram file_id 发给模型 | 模型无法直接读取 Telegram file_id，用户仍会误以为附件已处理。 |
| 改为全局吞掉 aiogram 异常 | 会隐藏其他代码缺陷，定位成本更高。 |

## 6. 测试矩阵

| 用例 | 预期 |
|---|---|
| 单附件下载失败 + caption | 用户收到“附件下载失败”提示；caption 被发送给模型。 |
| 媒体组下载失败 + caption | 用户收到失败提示；caption 被发送给模型；媒体组状态被清理。 |
| 既有媒体组正常聚合 | 仍只派发一次，包含所有附件。 |
| 请求输入/附件提示词相关回归 | 不受影响。 |

## 7. TDD 记录

### 7.1 baseline

```bash
python3.11 -m pytest -q tests/test_task_description.py tests/test_plan_progress.py tests/test_command_execution_flow.py
```

结果：`236 passed in 14.69s`。

### 7.2 红灯

```bash
python3.11 -m pytest -q tests/test_task_description.py::test_on_media_message_attachment_download_failure_dispatches_caption tests/test_task_description.py::test_on_media_group_download_failure_clears_state_and_dispatches_caption
```

结果：`2 failed`，失败点为附件下载异常直接冒泡，符合预期。

### 7.3 绿灯

```bash
python3.11 -m pytest -q tests/test_task_description.py::test_on_media_message_attachment_download_failure_dispatches_caption tests/test_task_description.py::test_on_media_group_download_failure_clears_state_and_dispatches_caption
```

结果：`2 passed, 2 warnings`。

```bash
python3.11 -m pytest -q tests/test_task_description.py tests/test_request_user_input_flow.py tests/test_attachment_prompt_format.py
```

结果：`228 passed in 16.78s`。

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| broad except 误吞未知异常 | 捕获范围只在普通附件入口，用日志记录异常，并保留用户可见失败提示；任务/缺陷专用流程未改。 |
| caption 发送给模型但附件未发送，模型误判附件已读取 | 提示语明确“附件下载失败，文字说明已先发送给模型”，要求用户重发附件。 |
| 媒体组状态清理影响后续相册分片 | 仅在下载已失败时清理当前媒体组；正常相册聚合逻辑不变，并保留既有回归测试。 |

回滚方式：回退 `bot.py` 中 `_clear_failed_media_group_state`、`_format_attachment_download_failure_notice`、`_handle_media_attachment_download_failure` 和 `on_media_message` 的 try/except 分支；删除新增测试；恢复 `AGENTS.md` 事实表。

### 7.4 最终验证

```bash
python3.11 -m py_compile bot.py
python3.11 -m pytest -q tests/test_task_description.py tests/test_request_user_input_flow.py tests/test_attachment_prompt_format.py
python3.11 -m pytest -q
python3.11 -m pytest -q
python3.11 -m vibego_cli doctor
bash scripts/test_deps_check.sh
git diff --check
```

结果：

- `py_compile` 通过。
- 附件/请求输入影响面：`228 passed in 16.73s`。
- 全量 pytest 第 1 轮：`937 passed, 6 warnings in 31.60s`。
- 全量 pytest 第 2 轮：`937 passed, 6 warnings in 30.32s`。
- `vibego_cli doctor` 通过，运行期配置与依赖探测正常。
- `scripts/test_deps_check.sh` 通过。
- `git diff --check` 通过。
- 6 个 warning 均为既有 `tests/test_unescape_markdown.py` 中测试函数返回 bool 的 `PytestReturnNotNoneWarning`，不属于本次变更引入。
