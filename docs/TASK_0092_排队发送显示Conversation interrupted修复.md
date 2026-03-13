# /TASK_0092 排队发送显示 Conversation interrupted 修复

## 1. 背景

用户反馈：在任务详情中选择“排队发送”后，Codex 终端会出现：

```text
Conversation interrupted - tell the model what to do differently.
Something went wrong? Hit /feedback to report the issue.
```

补充确认：

- 手动在 Codex 中直接按 `Tab` 本身可正常发送/排队
- 不能影响其他消息发送链路
- “排队发送”按钮文案无需刻意声明 `Codex`

## 2. 关键证据

1. 当前排队发送复用了公共发送逻辑  
   - `bot.py`（锚点：`def tmux_queue_line(session: str, line: str):`）  
   - 旧实现：`_tmux_submit_line(session, line, submit_key="Tab", double_submit=False)`

2. 公共发送逻辑在发送前固定注入 `Escape`  
   - `bot.py`（锚点：`def _tmux_submit_line(`）  
   - 关键片段：`subprocess.call(... "Escape")`

3. 当前排队发送按钮文案显式带 `Codex`  
   - `bot.py`（锚点：`PUSH_SEND_MODE_QUEUED_LABEL`）  
   - 旧值：`排队发送（Codex）`

4. 官方 Codex CLI 文档说明 `Enter` / `Tab` 语义不同  
   - OpenAI 官方文档：<https://developers.openai.com/codex/cli/features/#tips-and-shortcuts>
   - 结论：`Enter` 注入当前 turn，`Tab` 排队到下一 turn

## 3. 设计决策

### 3.1 采纳方案

仅修复 **queued** 链路，不影响其他发送路径：

1. `tmux_send_line(...)` 保持既有逻辑不变
2. `tmux_queue_line(...)` 改为专用路径
   - 不再复用“立即发送”的前置 `Escape`
   - 保留正文注入与 `Tab` 提交
3. 用户可见文案统一为：
   - `立即发送`
   - `排队发送`

### 3.2 不采纳方案

1. 不改全局 Enter 发送逻辑
2. 不放大到普通聊天/其他推送链路
3. 不修改非 queued 分支的发送语义

## 4. Class Impact Plan

### 4.1 受影响子项目与目录

- 仓库根目录 Python 机器人逻辑
  - 实现：`bot.py`
  - 测试：`tests/test_tmux_send_line.py`、`tests/test_task_description.py`

### 4.2 计划修改的具体单元

1. `bot.py`
   - `PUSH_SEND_MODE_QUEUED_LABEL`
   - `_build_push_send_mode_prompt`
   - `_tmux_submit_line`
   - `tmux_queue_line`
   - 新增辅助函数：
     - `_tmux_prepare_immediate_submit`
     - `_tmux_send_text_chunks`

2. `tests/test_tmux_send_line.py`
   - 新增 queued 专用链路测试
   - 新增 immediate 保持原行为的表征测试

3. `tests/test_task_description.py`
   - 新增排队发送文案去除 `Codex` 的回归测试

### 4.3 直连依赖测试

- 仅纳入以下直连单元测试：
  - `tests/test_tmux_send_line.py`
  - `tests/test_task_description.py`

原因：变更集中于 tmux 发送策略与任务详情推送文案，影响面可可靠收敛。未命中测试范围升级条件。

## 5. TDD 执行记录

### 5.1 Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py
```

结果：

```text
168 passed in 14.89s
```

### 5.2 TDD Gate（先红）

先补测试：

1. `test_tmux_send_line_keeps_escape_preflight`
2. `test_tmux_queue_line_skips_escape_preflight`
3. `test_push_send_mode_prompt_uses_generic_queue_label`

首次执行：

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py -k 'keeps_escape_preflight or skips_escape_preflight or push_send_mode_prompt_uses_generic_queue_label'
```

结果：

```text
2 failed, 1 passed
```

失败原因符合预期：

- queued 仍会发送前置 `Escape`
- 排队发送文案仍为 `排队发送（Codex）`

### 5.3 最小实现

已完成：

1. 将立即发送的前置 `Escape + copy-mode cancel` 下沉到 `_tmux_prepare_immediate_submit(...)`
2. 抽取正文注入逻辑 `_tmux_send_text_chunks(...)`
3. `tmux_queue_line(...)` 改为 queued 专用实现：
   - 不发送前置 `Escape`
   - 仅注入文本后发送 `Tab`
4. `PUSH_SEND_MODE_QUEUED_LABEL` 改为 `排队发送`
5. `_build_push_send_mode_prompt()` 去除 `（Codex）`

## 6. Self-Test Gate

### 6.1 定向新增测试

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py -k 'keeps_escape_preflight or skips_escape_preflight or push_send_mode_prompt_uses_generic_queue_label'
```

结果：

```text
3 passed
```

### 6.2 类级自测（第 1 次）

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py
```

结果：

```text
171 passed in 14.85s
```

### 6.3 类级自测（第 2 次）

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py
```

结果：

```text
171 passed in 14.85s
```

### 6.4 本地诊断

```bash
python3.11 -m vibego_cli doctor
```

结果：

```json
{
  "python_ok": true
}
```

## 7. 用户可见结果

1. 任务详情中的发送方式文案变为：
   - `立即发送`
   - `排队发送`
2. `排队发送` 不再复用立即发送的前置 `Escape`
3. 其他消息发送链路保持既有行为不变

## 8. 2026-03-13 回归复核（/TASK_0092）

### 8.1 复核结论

经当前仓库代码与类级测试复核，**当前实现中未保留 `Ctrl + C` 发送逻辑**；排队发送链路也**不会再注入前置 `Escape`**。

因此，若线上仍看到：

```text
Conversation interrupted - tell the model what to do differently.
Something went wrong? Hit /feedback to report the issue.
```

更高概率是**运行中的实例未使用包含 /TASK_0092 修复的代码**，而不是当前仓库里仍残留 `Ctrl + C` 逻辑。

### 8.2 关键证据

1. 仓库内检索 `C-c / ctrl+c / send-keys.*C-c`，未命中排队发送链路  
   - 只读检索：`rg -n 'C-c|ctrl\\+c|send-keys.*C-c' bot.py tests master.py vibego_cli`  
   - 结果仅见：`master.py:4895` 的 `KeyboardInterrupt`，未见 tmux 发送 `C-c`

2. 当前排队发送实现仅注入正文后发送 `Tab`  
   - `bot.py`（锚点：`def tmux_queue_line(session: str, line: str):`）
   - 关键片段：`_tmux_send_text_chunks(tmux, session, line)` + `send-keys ... "Tab"`

3. 当前立即发送的预处理仅存在于 immediate 专用路径  
   - `bot.py`（锚点：`def _tmux_prepare_immediate_submit(tmux: str, session: str) -> None:`）
   - 关键片段：`send-keys ... "Escape"` 仅由 `_tmux_submit_line(...)` 调用

4. 当前排队发送的类级回归测试仍为绿色  
   - `tests/test_tmux_send_line.py`（锚点：`def test_tmux_queue_line_skips_escape_preflight`）
   - `tests/test_task_description.py`（锚点：`def test_dispatch_prompt_plan_mode_queued_skips_plan_switch_for_codex`）

### 8.3 本次复核执行记录

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py
```

结果：

```text
171 passed in 14.76s
```

```bash
python3.11 -m vibego_cli doctor
```

结果：

```json
{
  "python_ok": true
}
```
