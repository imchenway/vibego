# TASK_0137 移除普通消息自动 /plan 预命令（DEVELOP）

## 1. 背景
- 用户反馈：Telegram 发送普通消息时，vibego 会自动先注入一次 `/plan`。
- 期望：普通消息不再强制切到 PLAN，由用户通过按钮开关自行控制。

## 2. 决策与范围
1. 仅调整“普通消息”入口，不影响任务“推送到模型”里的 PLAN/YOLO 显式选择逻辑。
2. 普通消息链路固定不传 `intended_mode=PLAN`，从源头禁用自动 `/plan`。
3. 保留现有 `/plan` 注入能力给显式 PLAN 场景（如任务推送时选择 PLAN）。

## 3. 代码变更

### 3.1 `bot.py`
- 位置：`_handle_prompt_dispatch(...)`
- 变更：
  - 删除普通消息默认 `intended_mode = PUSH_MODE_PLAN if ENABLE_AUTO_PLAN_FOR_DIRECT_MESSAGE else None`。
  - 调整为固定 `intended_mode=None` 调用 `_dispatch_prompt_to_model(...)`。
  - 增加中文注释说明“普通消息不再自动注入 /plan，模式由手动选择控制”。

### 3.2 `tests/test_task_description.py`
- 旧用例：`test_handle_prompt_dispatch_defaults_to_plan_mode`
- 新用例：`test_handle_prompt_dispatch_uses_manual_mode_control`
- 断言从 `captured == [PUSH_MODE_PLAN]` 调整为 `captured == [None]`，覆盖“即使开关为 true，也不自动 PLAN”。

## 4. 测试记录

### 4.1 修改前（相关基线）
```bash
PYTHONPATH=. pytest -q tests/test_task_description.py -k "handle_prompt_dispatch_defaults_to_plan_mode or push_model"
# 12 passed, 130 deselected
```

### 4.2 修改后（相关回归）
```bash
PYTHONPATH=. pytest -q tests/test_task_description.py -k "handle_prompt_dispatch_uses_manual_mode_control or push_model"
# 12 passed, 130 deselected
```

### 4.3 修改后（全量回归）
```bash
PYTHONPATH=. pytest -q
# 605 passed, 6 warnings
```

## 5. 验收结果
- ✅ 普通 Telegram 文本消息不再自动触发 `/plan`。
- ✅ “推送到模型”显式 PLAN/YOLO 选择链路保持不变。
- ✅ 全量测试通过。

## 6. 可验证资料（官方）
- Telegram ReplyKeyboardMarkup：
  https://core.telegram.org/bots/api#replykeyboardmarkup
- Telegram CallbackQuery：
  https://core.telegram.org/bots/api#callbackquery
- tmux `send-keys`（预命令注入相关）：
  https://man7.org/linux/man-pages/man1/tmux.1.html
