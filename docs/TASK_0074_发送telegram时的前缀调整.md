# /TASK_0074 发送 telegram 时的前缀调整

## 1. 背景

- 用户反馈：当前 Telegram 直推到模型时，强制规约前缀后会直接空一行接正文，缺少“以下是用户需求描述：”这一层引导。
- 用户确认：**沿用当前前缀的触发时机**，新文案直接写入现有前缀中，不新增额外分支。

## 2. 仓库证据

- 前缀常量：`bot.py`（锚点：`ENFORCED_AGENTS_NOTICE`）
- 前缀注入逻辑：`bot.py`（锚点：`def _prepend_enforced_agents_notice`）
- 直连回归测试：`tests/test_task_description.py`（锚点：`test_dispatch_prompt_injects_enforced_agents_notice`、`test_prepend_enforced_agents_notice_cases`）
- 文案回归测试：`tests/test_agents_template_migration.py`（锚点：`test_enforced_notice_points_to_agents_md`）

## 3. 设计决策

### 3.1 推荐并采用的方案 ✅

- 仅调整 `ENFORCED_AGENTS_NOTICE` 常量，新增一行：

```text
以下是用户需求描述：
```

- 保持 `_prepend_enforced_agents_notice(...)` 原有的 `\n\n` 拼接方式不变，因此该新增文案后会自然保留一行空行，再接原始 prompt。

### 3.2 未采用方案

- 仅对“普通自由文本”单独加这一行
  - 缺点：会引入新的分支判断，与用户“和现在这段前缀的触发时机是一样的，写在一起就可以了”的确认不一致。

## 4. 实现内容

- `bot.py`
  - 更新 `ENFORCED_AGENTS_NOTICE`
  - 新的尾部结构为：

```text
如未特殊指定模式，则默认进入 PLAN 模式。
以下是用户需求描述：

<原始 prompt>
```

- `tests/test_agents_template_migration.py`
  - 新增 `test_enforced_notice_adds_user_requirement_header_before_prompt`
  - 断言 PLAN 行后紧跟“以下是用户需求描述：”，并与正文之间保留一行空行

## 5. TDD 记录

1. **Baseline Gate**
   - 先运行直连契约测试，全部通过
2. **首次失败验证**
   - 新增测试后，运行：

```bash
python3.11 -m pytest -q tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt
```

   - 结果：失败，证明需求尚未实现
3. **最小实现**
   - 仅修改前缀常量，不改注入逻辑
4. **Self-Test Gate**
   - 针对直连测试范围回归，并连续两次通过

## 6. 测试结果

```bash
python3.11 -m pytest -q \
  tests/test_agents_template_migration.py::test_enforced_notice_points_to_agents_md \
  tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt \
  tests/test_task_description.py::test_dispatch_prompt_injects_enforced_agents_notice \
  tests/test_task_description.py::test_dispatch_prompt_skips_enforced_agents_notice_for_slash_command \
  tests/test_task_description.py::test_dispatch_prompt_skips_enforced_agents_notice_for_plan_implement_prompt \
  tests/test_task_description.py::test_dispatch_prompt_plan_mode_sends_plan_switch_for_codex \
  tests/test_task_description.py::test_dispatch_prompt_yolo_mode_skips_plan_switch \
  tests/test_task_description.py::test_dispatch_prompt_plan_mode_skips_switch_for_non_codex \
  tests/test_task_description.py::test_prepend_enforced_agents_notice_cases
```

- 结果：`23 passed`

## 7. 风险与边界

- 本次不改 slash 命令透传、不改 Plan 收口透传、不改 `/plan` 预命令逻辑。
- 本次仅变更文本契约，不涉及数据库、配置、依赖和构建链。
