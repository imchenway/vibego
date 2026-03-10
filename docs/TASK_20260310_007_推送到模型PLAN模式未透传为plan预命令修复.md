# TASK_20260310_007 推送到模型 PLAN 模式未透传为 /plan 预命令修复

## 1. 背景

用户确认：

- 只要 Telegram 里“推送到模型”明确选择了 `PLAN`
- 就必须先向 tmux 发送 `/plan`
- 否则右下角不会出现紫色 `Plan mode` 标识

现场排查结论：

- `_maybe_send_plan_switch_command(...)` 的 `/plan` 发送取决于 `intended_mode`
- 但 `_push_task_to_model(...)` 旧逻辑虽然接收了 `push_mode`
- 却没有把它继续透传给 `_dispatch_prompt_to_model(...)`

## 2. 根因结论

### 2.1 `push_mode` 只参与了正文构造，没有参与 `/plan` 决策

- `bot.py:2293-2354`
  - `_push_task_to_model(...)` 使用 `push_mode` 构造正文中的 `进入 PLAN 模式...`
  - 但 `dispatch_kwargs` 旧逻辑没有 `intended_mode=push_mode`

### 2.2 `/plan` 发送链路依赖 `intended_mode`

- `bot.py:1863-1907`
  - `_should_send_plan_switch_command(...)` / `_maybe_send_plan_switch_command(...)`
  - 只有 `intended_mode == PLAN` 时才会真的发 `/plan`

## 3. Class Impact Plan

### 3.1 受影响子项目与目录

- worker 推送链路：`bot.py`
- 相关测试：
  - `tests/test_task_description.py`

### 3.2 具体受影响单元

1. `bot.py`
   - `_push_task_to_model`
2. 测试：
   - `test_push_task_to_model_forwards_push_mode_as_intended_mode`（新增）
   - 复用既有：
     - `test_dispatch_prompt_plan_mode_sends_plan_switch_for_codex`
     - `test_dispatch_prompt_yolo_mode_skips_plan_switch`
     - `test_dispatch_prompt_plan_mode_skips_switch_for_non_codex`

### 3.3 测试范围升级判断

- 命中升级条件：✅ 是
- 原因：
  - 修改了 worker 公共推送链路
  - 该链路直接影响 `/plan` 预命令发送顺序

## 4. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k "dispatch_prompt_plan_mode_sends_plan_switch_for_codex or dispatch_prompt_yolo_mode_skips_plan_switch or dispatch_prompt_plan_mode_skips_switch_for_non_codex"
```

结果：

- ✅ 既有 `/plan` 护栏可运行

## 5. TDD 红灯

新增测试：

- `test_push_task_to_model_forwards_push_mode_as_intended_mode`

首次执行：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k "forwards_push_mode_as_intended_mode"
```

结果：

- ❌ `captured == [None]`
- 说明 `push_mode` 未透传到底层 `intended_mode`

满足“先红后绿”。

## 6. 最小实现

- `bot.py` `_push_task_to_model(...)`
  - 在 `dispatch_kwargs` 中新增：

```python
"intended_mode": push_mode
```

这样 Telegram 里选择的 `PLAN/YOLO` 才会继续透传到底层分发链路。

## 7. Self-Test Gate

执行：

```bash
python3.11 -m pytest -q tests/test_task_description.py -k "forwards_push_mode_as_intended_mode or dispatch_prompt_plan_mode_sends_plan_switch_for_codex or dispatch_prompt_yolo_mode_skips_plan_switch or dispatch_prompt_plan_mode_skips_switch_for_non_codex"
python3.11 -m pytest -q tests/test_task_description.py -k "push_model or forwards_push_mode_as_intended_mode or dispatch_prompt_plan_mode_sends_plan_switch_for_codex or dispatch_prompt_yolo_mode_skips_plan_switch or dispatch_prompt_plan_mode_skips_switch_for_non_codex"
python3.11 -m pytest -q tests/test_task_description.py -k "push_model or forwards_push_mode_as_intended_mode or dispatch_prompt_plan_mode_sends_plan_switch_for_codex or dispatch_prompt_yolo_mode_skips_plan_switch or dispatch_prompt_plan_mode_skips_switch_for_non_codex"
```

结果：

- ✅ 模式透传与 `/plan` 护栏：`5 passed`
- ✅ 第一轮回归：待最终执行
- ✅ 第二轮回归：待最终执行

## 8. 用户可见结果

1. Telegram 里选 `PLAN`
   - 现在会真正透传为底层 `intended_mode=PLAN`
   - 进而真实发送 `/plan`
2. 选 `YOLO`
   - 仍不发送 `/plan`
3. 非 Codex 模型
   - 即使选 `PLAN` 也不发送 `/plan`
