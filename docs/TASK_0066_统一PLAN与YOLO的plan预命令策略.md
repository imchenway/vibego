# /TASK_0066 统一 PLAN 与 YOLO 的 `/plan` 预命令策略（DEVELOP）

## 1. 背景
用户提出两类 Telegram 入口的模式切换诉求：

1) 点击“推送到模型”并选择模式时：
- 选 `PLAN`：先发 `/plan`，再发提示词
- 选 `YOLO`：不发 `/plan`，直接发提示词

2) 直接在 Telegram 发送文本消息时：
- 默认视为 `PLAN`，先发 `/plan`，再发提示词

---

## 2. 代码改动

### 2.1 新增配置项（`bot.py`）
- `PLAN_MODE_SWITCH_COMMAND`（默认 `/plan`）
- `PLAN_MODE_SWITCH_DELAY_SECONDS`（默认 `0.25` 秒）
- `ENABLE_AUTO_PLAN_FOR_DIRECT_MESSAGE`（默认 `true`）

### 2.2 新增策略函数（`bot.py`）
- `_infer_dispatch_mode_from_prompt(prompt)`
  - 从提示词前缀推断 PLAN/YOLO（兼容旧调用链）
- `_resolve_dispatch_mode(intended_mode, prompt)`
  - 先用显式模式，缺省再尝试从提示词推断
- `_should_send_plan_switch_command(intended_mode, prompt)`
  - 仅 `Codex + PLAN` 时返回 true
  - slash 内部命令（如 `/compact`）不注入
- `_maybe_send_plan_switch_command(...)`
  - 先发送 `/plan`，等待短延迟，再继续正文发送

### 2.3 统一注入到派发入口（`bot.py`）
- 扩展 `_dispatch_prompt_to_model(...)` 参数：
  - `intended_mode: Optional[str] = None`
- 在发送正文前统一调用 `_maybe_send_plan_switch_command(...)`

### 2.4 入口行为调整
- 直接文本消息 `_handle_prompt_dispatch(...)`：
  - 默认传 `intended_mode=PLAN`（可由 `ENABLE_AUTO_PLAN_FOR_DIRECT_MESSAGE` 关闭）
- 推送补充“跳过”回调 `on_task_push_model_skip(...)`：
  - 透传 `state.push_mode` 到 `_push_task_to_model(...)`
  - 保证已选择模式不丢失

---

## 3. 测试覆盖

新增/更新测试（`tests/test_task_description.py`）：

1. `test_dispatch_prompt_plan_mode_sends_plan_switch_for_codex`
- 断言：Codex + PLAN 时发送顺序为 `/plan` -> 正文

2. `test_dispatch_prompt_yolo_mode_skips_plan_switch`
- 断言：YOLO 不发 `/plan`

3. `test_dispatch_prompt_plan_mode_skips_switch_for_non_codex`
- 断言：非 Codex 即使 PLAN 也不发 `/plan`

4. `test_handle_prompt_dispatch_defaults_to_plan_mode`
- 断言：直接 Telegram 文本默认以 PLAN 模式派发

5. `test_push_model_skip_keeps_selected_push_mode`
- 断言：点击“跳过补充”后仍保留已选 PLAN/YOLO 模式

---

## 4. 回归结果

执行命令：

```bash
PYTHONPATH=. pytest -q tests/test_task_description.py tests/test_plan_progress.py tests/test_model_quick_reply.py tests/test_codex_jsonl_phase.py tests/test_request_user_input_flow.py tests/test_long_poll_mechanism.py tests/test_auto_compact.py
```

结果：
- `179 passed`

---

## 5. 可验证资料（官方）
- Telegram Bot API `sendMessage`  
  https://core.telegram.org/bots/api#sendmessage
- Telegram Bot API `InlineKeyboardButton`（`callback_data` 限制）  
  https://core.telegram.org/bots/api#inlinekeyboardbutton
- Codex CLI slash commands（`/plan`）  
  https://developers.openai.com/codex/cli/slash-commands

