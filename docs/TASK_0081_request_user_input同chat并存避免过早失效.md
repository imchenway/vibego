# /TASK_0081 request_user_input 同 chat 并存，避免过早失效

## 1. 背景

- 用户现象：
  - Telegram 决策卡片仍显示“有效期剩余约 14 分钟”
  - 但点击按钮立即提示“该交互已失效，请重新触发”
- 截图证据：
  - `/Users/david/.config/vibego/data/telegram/vibegobot/2026-03-11/20260311_154600444-f33941cdbd85.jpg`
- 根因证据：
  - `bot.py:2267-2269`
    - 新一轮入模时会直接 drop 当前 chat 的 request_input token
  - `bot.py:10466-10469`
    - 新 request_input 创建时会替换同 chat 的旧 token
  - `bot.py:13899-13901`
    - 按钮点击要求 token 必须等于 chat 当前 active token

## 2. Class Impact Plan

### 2.1 受影响子项目与目录

- worker request_input 交互链路：`bot.py`
- 相关测试：
  - `tests/test_request_user_input_flow.py`
  - `tests/test_task_description.py`

### 2.2 受影响单元

| 单元 | 实现文件 | 测试文件 |
|---|---|---|
| `_dispatch_prompt_to_model` | `bot.py` | `tests/test_request_user_input_flow.py`, `tests/test_task_description.py` |
| `_start_request_input_interaction` | `bot.py` | `tests/test_request_user_input_flow.py` |
| `_drop_request_input_session` | `bot.py` | `tests/test_request_user_input_flow.py` |
| `_get_request_input_session_for_chat` | `bot.py` | `tests/test_request_user_input_flow.py` |
| `_handle_request_input_custom_text_message` | `bot.py` | `tests/test_request_user_input_flow.py` |
| `on_request_user_input_callback` | `bot.py` | `tests/test_request_user_input_flow.py` |
| `_set_request_input_text_focus`（新增） | `bot.py` | `tests/test_request_user_input_flow.py` |
| `_clear_request_input_text_focus`（新增） | `bot.py` | `tests/test_request_user_input_flow.py` |

### 2.3 直连依赖测试

- `tests/test_request_user_input_flow.py`
  - 直接覆盖 request_input 的按钮、提交、自定义输入、过期与并行上下文
- `tests/test_task_description.py`
  - 直接覆盖 `_dispatch_prompt_to_model(...)` 的通用派发契约

### 2.4 测试范围升级判断

- 结论：❌ 未升级
- 原因：
  - 本次仅调整 request_input 的会话并存与输入焦点规则
  - 未修改外部接口、数据库 schema、构建链与公共脚本

## 3. Baseline Gate

执行：

```bash
python3.11 -m pytest -q tests/test_request_user_input_flow.py
```

结果：

- ✅ `21 passed`

## 4. TDD 红灯

新增测试：

- `test_request_input_custom_option_claims_text_focus_token`
- `test_request_input_old_callback_still_works_after_newer_session_created`
- `test_dispatch_prompt_to_model_does_not_drop_existing_request_input_session_for_same_chat`

首次执行：

```bash
python3.11 -m pytest -q \
  tests/test_request_user_input_flow.py::test_request_input_custom_option_claims_text_focus_token \
  tests/test_request_user_input_flow.py::test_request_input_old_callback_still_works_after_newer_session_created \
  tests/test_request_user_input_flow.py::test_dispatch_prompt_to_model_does_not_drop_existing_request_input_session_for_same_chat
```

结果：

- ❌ 自定义输入模式未抢占 text focus token
- ❌ 旧 request_input 按钮会被“最新 token”门禁挡掉
- ❌ 同 chat 新一轮入模会直接删掉旧 request_input 会话

## 5. 最小实现

### 5.1 request_input 改为“同 chat 按会话并存”

- `bot.py::_dispatch_prompt_to_model(...)`
  - 去掉“新一轮入模直接 drop 当前 request_input token”的逻辑
- `bot.py::_start_request_input_interaction(...)`
  - 去掉“新 request_input 替换旧 token”的逻辑

### 5.2 chat 级 token 改为“文本输入焦点”

- `CHAT_ACTIVE_REQUEST_INPUT_TOKENS`
  - 不再表示“同 chat 唯一有效会话”
  - 仅表示“当前哪一个 request_input 会话正在等待自由文本输入”
- 新增：
  - `_set_request_input_text_focus(chat_id, token)`
  - `_clear_request_input_text_focus(chat_id, token=None)`

### 5.3 按钮按自身 token 校验，不再被最新会话替换

- `bot.py::on_request_user_input_callback(...)`
  - 移除 `active_token != token` 的 fail-closed 门禁
  - 只校验：
    - session 是否存在
    - chat/user 是否匹配
    - 是否过期 / 已提交 / 已取消

### 5.4 自定义输入仍保持单焦点

- `bot.py::_handle_request_input_custom_text_message(...)`
  - 继续只消费当前 chat 的 text focus token
  - 当：
    - 取消输入
    - 输入完成
    - 题目失效
    - 题目已锁定
  - 会清理 text focus
- `bot.py::on_request_user_input_callback(...)`
  - 点击 `D. 输入自定义决策` 时，切换 text focus 到当前 token

## 6. Self-Test Gate

### 6.1 定向双跑

```bash
python3.11 -m pytest -q \
  tests/test_request_user_input_flow.py \
  tests/test_task_description.py::test_dispatch_prompt_rebinds_when_pointer_updates \
  tests/test_task_description.py::test_dispatch_prompt_injects_enforced_agents_notice \
  tests/test_task_description.py::test_dispatch_prompt_plan_mode_sends_plan_switch_for_codex
```

结果：

- ✅ 第一轮：`27 passed`
- ✅ 第二轮：`27 passed`

### 6.2 额外验证

```bash
python3.11 -m pytest -q \
  tests/test_request_user_input_flow.py::test_request_input_custom_option_claims_text_focus_token \
  tests/test_request_user_input_flow.py::test_request_input_old_callback_still_works_after_newer_session_created \
  tests/test_request_user_input_flow.py::test_dispatch_prompt_to_model_does_not_drop_existing_request_input_session_for_same_chat
```

结果：

- ✅ `3 passed`

## 7. 用户可见结果

1. 同一个 Telegram chat 里，旧 request_input 按钮不会因为新问题/新一轮入模而提前失效。
2. 真实过期、已提交、已取消，仍会正常失效。
3. 自定义文本输入仍然一次只绑定一个会话；若切到别的题，重新点击 `D. 输入自定义决策` 即可恢复焦点。
