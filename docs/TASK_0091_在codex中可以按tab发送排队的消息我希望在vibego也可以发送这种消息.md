# /TASK_0091 在 Codex 中可以按 Tab 发送排队的消息，我希望在 vibego 也可以发送这种消息

## 1. 背景

用户希望 vibego 也支持类似 Codex 的“Tab 排队发送”能力，并要求先分析两种方案：

1. 仅在任务详情推送链路中增加显式选择
2. 将所有消息发送从 Enter 改成 Tab

最终按推荐方案确认：

- 仅在 **任务详情 -> 推送到模型** 链路增加“立即发送 / 排队发送（Codex）”
- 不修改全局默认发送语义
- 仅对 **Codex** 暴露排队发送入口

## 2. 关键证据

### 2.1 仓库现状

- `bot.py:tmux_send_line(...)` 是公共 tmux 注入入口，当前固定使用 `C-m`（Enter）提交。
- `bot.py:_dispatch_prompt_to_model(...)` 会在入模前统一调用 `tmux_send_line(...)`。
- `bot.py:TaskPushStates` 当前仅有：
  - `waiting_dispatch_target`
  - `waiting_choice`
  - `waiting_supplement`
- `bot.py` 任务详情推送现状链路为：
  - 当前 CLI / 并行 CLI
  - PLAN / YOLO
  - 补充任务描述

### 2.2 官方文档

OpenAI 官方 Codex CLI 文档明确说明：

- `Enter`：向**当前 turn**注入新指令
- `Tab`：将 prompt **排队到下一 turn**

参考：
- https://developers.openai.com/codex/cli/features/#tips-and-shortcuts

因此 `Tab` 不是 `Enter` 的等价替换，不能直接全局替换现有 Enter 发送语义。

## 3. 设计决策

### 3.1 采纳方案

仅在任务详情推送链路中新增一层发送方式选择：

```text
任务详情 -> 推送到模型
  -> 当前 CLI / 并行 CLI
  -> PLAN / YOLO
  -> 立即发送 / 排队发送（Codex）
  -> 补充任务描述
  -> 发送
```

### 3.2 不采纳方案

不将全局 `Enter` 提交改为 `Tab`，原因：

- 会影响 `/plan`、`/compact`、普通 prompt 等公共链路
- 与官方语义冲突：`Tab` 是“排队下一轮”，不是“立即发送”
- 风险面过大，难以证明安全

### 3.3 额外实现口径

- 非 Codex 模型（ClaudeCode / Gemini）不显示“排队发送”入口
- 当发送方式为 `queued` 时，不再预先发送 `/plan`，避免破坏“排队下一轮”的核心语义
- `done` 状态的 `/compact` 链路保持不变

## 4. Class Impact Plan

### 4.1 受影响实现单元

1. `tasks/fsm.py`
   - `TaskPushStates`
2. `bot.py`
   - `tmux_send_line`
   - `tmux_queue_line`（新增）
   - `_dispatch_prompt_to_model`
   - `_should_send_plan_switch_command`
   - `_push_task_to_model`
   - `_begin_parallel_launch`
   - `ParallelLaunchSession`
   - `_build_push_send_mode_prompt`
   - `_build_push_send_mode_keyboard`
   - `_prompt_push_send_mode_input`
   - `_prompt_model_supplement_input`
   - `on_task_push_model_choice`
   - `on_task_push_model_send_mode`（新增）
   - `on_task_push_model_skip`
   - `on_task_push_model_supplement`

### 4.2 受影响测试单元

1. `tests/test_tmux_send_line.py`
2. `tests/test_task_description.py`

### 4.3 测试范围升级判断

- 未扩大到全项目测试
- 原因：改动集中在任务详情推送链路与 tmux 发送策略；可可靠收敛到类级测试

## 5. TDD 执行记录

### 5.1 Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py
```

结果：

```text
164 passed in 13.32s
```

### 5.2 TDD Gate（先红）

先补测试，覆盖：

1. `tmux_queue_line` 使用 `Tab` 且不补发第二次 Enter
2. 排队发送失败时提示“手动按 Tab”
3. 任务详情推送在 Codex 下新增发送方式选择
4. `queued` 模式会透传到底层 `_push_task_to_model`
5. `queued + PLAN` 时不再预发 `/plan`
6. 非 Codex 模型选择 PLAN/YOLO 后直接进入补充阶段

首次执行：

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py -k 'push_model or tmux_queue_line or queued_skips_plan_switch or non_codex_skips_send_mode'
```

结果：

```text
10 failed, 4 passed
```

失败原因符合预期：

- 尚未定义 `tmux_queue_line`
- 尚未新增 `TaskPushStates.waiting_send_mode`
- 尚未新增发送方式常量与 handler
- `_dispatch_prompt_to_model` 尚未支持 `send_mode`

### 5.3 最小实现

已完成：

1. 新增发送方式常量：
   - `PUSH_SEND_MODE_IMMEDIATE`
   - `PUSH_SEND_MODE_QUEUED`
   - 对应展示文案常量
2. 新增 `TaskPushStates.waiting_send_mode`
3. 新增 `tmux_queue_line(...)`
   - 使用 `Tab` 提交
   - 不走双 Enter 兜底
4. `_dispatch_prompt_to_model(...)`
   - 新增 `send_mode`
   - `queued` 时走 `tmux_queue_line`
   - `queued` 失败时提示“手动按 Tab”
5. `_should_send_plan_switch_command(...)`
   - `queued` 时不发送 `/plan`
6. 任务详情推送 FSM
   - Codex：PLAN/YOLO 之后进入“发送方式”选择
   - 非 Codex：保持原流程，直接进入补充阶段
7. 并行 CLI 路径
   - `send_mode` 可透传到并行会话推送

## 6. Self-Test Gate

### 第 1 次类级自测

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py
```

结果：

```text
168 passed in 15.60s
```

### 第 2 次类级自测（同范围双跑）

```bash
python3.11 -m pytest -q tests/test_tmux_send_line.py tests/test_task_description.py
```

结果：

```text
168 passed in 15.51s
```

### 本地诊断

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

### 7.1 Codex 下

任务详情点击“推送到模型”后变为：

```text
当前 CLI / 并行 CLI
-> PLAN / YOLO
-> 立即发送 / 排队发送（Codex）
-> 补充任务描述
-> 发送
```

其中：

- **立即发送**：使用 Enter，立刻注入当前轮
- **排队发送（Codex）**：使用 Tab，排到下一轮

### 7.2 非 Codex 下

仍保持：

```text
当前 CLI / 并行 CLI
-> PLAN / YOLO
-> 补充任务描述
-> 发送
```

不会展示“排队发送”。

### 7.3 不受影响的行为

- `/plan` 预命令默认行为未全局改变
- `/compact` 保持立即发送
- 普通消息发送链路保持立即发送
- 未显式选择排队发送的场景仍使用 Enter
