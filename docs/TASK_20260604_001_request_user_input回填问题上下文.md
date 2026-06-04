# TASK_20260604_001 request_user_input 回填问题上下文

## 1. 背景与目标

### 现象
`request_user_input` / `ask_user` 通过 Telegram 按钮提交后，回推 tmux 的 prompt 只有 `call_id` 与 `answers` JSON。模型能看到答案值，但看不到该答案对应的原题、分组、选项说明，容易在继续执行时丢失决策语义。

### 目标
在不改变现有答案 schema 的前提下，在 `answers` JSON 前新增一行 `question_context=...`，补充每道题的紧凑上下文，帮助模型把答案和原问题对齐。

### 本次不做
- 不改 Telegram 按钮交互。
- 不改 `answers` / `ask_user` 现有 payload schema。
- 不新增依赖。
- 不改数据库、迁移、配置和 CI。
- 不处理普通消息里的 `以下是用户需求描述：` 全局规约前缀；该前缀来自 `_prepend_enforced_agents_notice`，不属于本次问题。

## 2. 当前实现证据

| 事实 | 证据 |
| --- | --- |
| Telegram 侧工具交互会话使用 `RequestInputSession` 保存 `questions`、选项与自定义答案 | `bot.py`（锚点：`class RequestInputSession`） |
| `request_user_input` 输出结构由 `_build_request_input_output_payload` 生成，普通工具维持 `{"answers":...}` | `bot.py`（锚点：`_build_request_input_output_payload`） |
| `ask_user` 输出结构同样复用按钮链路，但 payload 是 schema 对象，例如 `{"scope":"all_pages"}` | `bot.py`（锚点：`_build_request_input_output_payload`、`tool_name == "ask_user"`） |
| 回推模型 prompt 由 `_build_request_input_submission_prompt` 统一构造 | `bot.py`（锚点：`_build_request_input_submission_prompt`） |
| 提交流程在 `_submit_request_input_session` 中调用 `_dispatch_prompt_to_model` | `bot.py`（锚点：`_submit_request_input_session`） |
| 既有回归测试覆盖 request_input 提交、选项自动提交、自定义文本提交与 ask_user schema payload | `tests/test_request_user_input_flow.py`（锚点：`test_request_input_submit_dispatches_structured_payload`、`test_request_input_option_auto_submits_when_all_answered`、`test_request_input_custom_text_auto_submits`、`test_ask_user_submit_dispatches_schema_payload`） |

## 3. 方案

### 推荐方案（已实施）
保留现有 `answers` JSON 不变，在其前新增一行：

```text
question_context={"questions":[...]}
```

每个问题包含：

- `id`
- `header`
- `question`
- `selected_kind`：`option` / `custom` / `unanswered`
- `selected_option_label`
- `selected_option_description`

自定义答案正文不写入 `question_context`，仍只放在现有 `answers` JSON 里，避免长文本重复放大。

### 输出示例

```text
request_user_input 工具结果（来自 Telegram 按钮交互）：
call_id=call_submit_1
question_context={"questions":[{"id":"scope","header":"定位范围","question":"请选择范围","selected_kind":"option","selected_option_label":"两页","selected_option_description":"库存页与订单页统一改"}]}
{"answers":{"scope":{"answers":["两页"]}}}
请基于上述工具结果继续执行后续步骤。
```

`ask_user` 示例保持原 schema payload：

```text
ask_user 工具结果（来自 Telegram 按钮交互）：
call_id=tool_ask_submit_1
question_context={"questions":[{"id":"scope","header":"请选择范围","question":"请选择范围","selected_kind":"option","selected_option_label":"两页都改","selected_option_description":""}]}
{"scope":"all_pages"}
请基于上述工具结果继续执行后续步骤。
```

## 4. 影响范围

| 目录/文件 | 影响 |
| --- | --- |
| `bot.py` | 新增纯函数 `_build_request_input_question_context`；扩展 `_build_request_input_submission_prompt` 可选 `question_context` 参数；提交流程传入上下文。 |
| `tests/test_request_user_input_flow.py` | 增强 4 条回归测试，覆盖普通提交、按钮自动提交、自定义文本提交和 `ask_user` schema payload 兼容。 |
| `docs/TASK_20260604_001_request_user_input回填问题上下文.md` | 记录设计、契约、测试矩阵与风险。 |
| `AGENTS.md` | 增加仓库事实锚点，说明 request_input 回推上下文契约。 |

## 5. 契约变更

### 对外/模型侧 prompt 契约
- 新增：`question_context=...` 行。
- 保持不变：`call_id=...` 行。
- 保持不变：`answers` JSON 或 `ask_user` schema JSON 的结构与位置仍在最终“请基于...”前。
- 保持不变：Telegram 按钮和回显文案。

### 数据库
- 不涉及数据库表、字段、索引、约束或迁移。
- 回滚不需要 DB 操作。

### 接口/API
- 无 HTTP API 或外部接口变更。
- 仅变更回推 tmux 的文本 prompt 契约。

## 6. 测试矩阵

| 用例 | 覆盖点 | 预期 |
| --- | --- | --- |
| `test_request_input_submit_dispatches_structured_payload` | 多题提交、选项描述、原 answers JSON | prompt 包含 `question_context=`；原 `{"answers":...}` 不变。 |
| `test_request_input_option_auto_submits_when_all_answered` | 按钮选择后自动提交 | 自动提交 prompt 同样包含题目上下文。 |
| `test_request_input_custom_text_auto_submits` | 自定义文本提交 | `question_context` 标记 `selected_kind=custom`，不重复自定义正文；正文仅保留在 answers JSON。 |
| `test_ask_user_submit_dispatches_schema_payload` | Copilot `ask_user` 兼容 | prompt 包含上下文；`{"scope":"all_pages"}` schema payload 不变。 |
| `tests/test_request_user_input_flow.py` 全文件 | request_input 既有按钮、并行、自定义附件、失败重试 | 25 条全部通过。 |
| 全量 pytest | 仓库级回归 | 本次改动未新增失败；但 baseline 已存在 `tests/test_agents_template_migration.py` 3 个无关失败。 |

## 7. 实施顺序

1. 读取 AGENTS 与 request_input 历史 docs。
2. 运行受影响 baseline：`PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py`。
3. 运行全量 baseline，记录既有无关 blocker。
4. 先补 4 条回归断言并确认失败。
5. 在 `bot.py` 增加 question context 生成与 prompt 注入。
6. 运行聚焦测试与全文件测试。
7. 运行全量测试并按既有 blocker 隔离说明。
8. 更新 docs 与 AGENTS 证据锚点。

## 8. 风险与回滚

### 风险
- prompt 行数增加，极长问题集会略微增加 tmux 输入长度；当前 questions 数量受既有上限保护。
- 下游如果用极严格的文本行解析，只读取 `call_id` 后下一行作为 payload，可能需要忽略新增 `question_context` 行。但本仓库内当前消费方是模型自然语言，不是机器解析器。

### 回滚
- 移除 `_build_request_input_question_context` 调用。
- 将 `_build_request_input_submission_prompt` 恢复为只输出 `tool_name/call_id/payload_json/继续执行` 四段。
- 保持测试回滚到不检查 `question_context` 的版本。
- 不需要数据库或配置回滚。

## 9. 验证记录

### Baseline

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py
# 25 passed in 2.27s
```

```bash
PYTHONPATH=. python3.11 -m pytest -q
# 3 failed, 967 passed, 6 warnings in 40.51s
# 失败均在 tests/test_agents_template_migration.py，属于既有 AGENTS/Comet 模板口径 blocker，本次不改该链路。
```

### TDD 红灯

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py -k "submit_dispatches_structured_payload or option_auto_submits_when_all_answered or custom_text_auto_submits or ask_user_submit_dispatches_schema_payload"
# 4 failed, 1 passed, 20 deselected
# 失败原因：prompt 尚无 question_context= 行。
```

### 当前绿灯

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py -k "submit_dispatches_structured_payload or option_auto_submits_when_all_answered or custom_text_auto_submits or ask_user_submit_dispatches_schema_payload"
# 5 passed, 20 deselected
```

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py
# 25 passed in 2.36s
```

> 最终双轮验证结果见本任务最终回复与后续补充记录。

### 最终验证补充（实现与文档更新后）

```bash
PYTHONPATH=. python3.11 -m pytest -q tests/test_request_user_input_flow.py
# 第 1 轮：25 passed in 2.29s
# 第 2 轮：25 passed in 2.29s
# 文档/格式微调后追加确认：25 passed, 2 warnings in 2.36s
```

```bash
PYTHONPATH=. python3.11 -m pytest -q
# 第 1 轮：3 failed, 967 passed, 6 warnings in 39.66s
# 第 2 轮：3 failed, 967 passed, 6 warnings in 39.19s
# 文档/格式微调后追加确认：3 failed, 967 passed, 6 warnings in 39.03s
# 三次失败均为既有 tests/test_agents_template_migration.py：
# - test_enforced_notice_points_to_agents_md
# - test_enforced_notice_adds_user_requirement_header_before_prompt
# - test_agents_template_requires_comet_for_complex_workflows
```

```bash
PYTHONPATH=. python3.11 -m vibego_cli doctor
# exit 0；python_ok=true；dependencies=[]；config_root=/Users/david/.config/vibego
```
