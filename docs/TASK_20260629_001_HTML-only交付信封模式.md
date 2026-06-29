# TASK_20260629_001 HTML-only 交付信封模式

## 1. 背景与目标

用户提出：希望在提示词里禁止文本对话，全部内容都使用 HTML 文件沟通，并确认“写入”。

目标不是让模型完全不输出文本，而是把文本通道降级为“交付信封”：聊天文本只负责给 HTML 文件路径或阻塞性澄清问题；所有实质内容必须写入项目内单文件 HTML。

## 2. 当前现状

### 2.1 已有 HTML 图形协议

- `AGENTS-template.md` 已定义 `## HTML 图形沟通默认协议`，规定非琐碎任务默认优先使用 `vibe-diagram`。
- `vibego_cli/data/skills/vibe-diagram/SKILL.md` 已定义单文件 HTML、Codex `file://`、Telegram 文件附件等交付规则。
- 但旧协议仍允许最终回复用简短文本说明“HTML 已生成/已发送”，没有严格禁止文本承载分析、方案、证据链或验收总结。

证据：`AGENTS-template.md`（锚点：`## HTML 图形沟通默认协议`）；`vibego_cli/data/skills/vibe-diagram/SKILL.md`（锚点：`## 交付铁律`）。

## 3. 需求口径

当用户明确要求以下任一表达时，启用 HTML-only 交付信封模式：

- “禁止文本对话”
- “全部内容都用 HTML 文件沟通”
- “只发 HTML 附件/文件”
- 同义表达

生效后：

1. 文本回复只允许作为交付信封。
2. 所有实质内容进入项目内单文件 HTML。
3. Codex 场景只输出 `file://` 链接与绝对路径兜底。
4. Telegram 来源只输出项目内 `.html/.htm` 路径以触发附件发送。
5. 只有阻塞性澄清问题可以临时用文本，且最多 1 个问题。
6. 若无法写入 HTML 文件，才允许输出完整 HTML 代码块作为 fallback。

## 4. 方案对比

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| A. 只写入 `vibe-diagram` | 交付格式集中 | AGENTS 模板无法提前约束“文本信封”触发条件 | 不采用 |
| B. 只写入 `AGENTS-template.md` | 触发条件清晰 | skill 具体交付格式仍不够硬 | 不采用 |
| C. 同时写入模板与 skill | 触发层和执行层分工明确；同步注入可覆盖 worker | 需要补两组测试 | 采用 |

## 5. 契约变更

### 5.1 `AGENTS-template.md`

新增 `## HTML-only 交付信封模式`：

- 定义何时启用 HTML-only。
- 规定文本只作为交付信封。
- 规定 Codex 与 Telegram 的信封格式边界。
- 规定阻塞性澄清和 fallback 边界。

### 5.2 `vibe-diagram/SKILL.md`

新增 `## HTML-only 交付信封模式`：

- 禁止普通文本展开分析、方案、证据链、测试矩阵、风险回滚或验收总结。
- 定义允许的两类文本：阻塞性澄清问题、HTML 交付信封。
- 明确 Codex 和 Telegram 的输出格式。
- 增加输出前自检项。

## 6. 受影响目录

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `AGENTS-template.md` | 是 | 新增 HTML-only 交付信封触发规则。 |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 新增 HTML-only 交付信封执行规则。 |
| `tests/test_agents_template_migration.py` | 是 | 增加模板规则断言。 |
| `tests/test_builtin_skills_injection.py` | 是 | 增加 skill 规则与同步注入断言。 |
| `AGENTS.md` | 是 | Facts Table 新增 HTML-only 交付信封事实。 |
| `docs/TASK_20260629_001_HTML-only交付信封模式.md` | 是 | 记录本轮需求、契约、测试矩阵、风险与回滚。 |
| `bot.py` / Telegram 发送链路 | 否 | 本轮不改变运行时代码；仍复用现有项目内 HTML 路径触发附件发送。 |
| DB / 配置 / 构建依赖 | 否 | 无数据库、配置项、依赖或 CI 变更。 |

## 7. 测试矩阵

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` | 模板 HTML 协议基线通过。 |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `15 passed` | 内置 skill 基线通过。 |
| RED | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 failed` | 新增断言后，旧模板缺少 HTML-only 信封模式。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k html_only_delivery` | `1 failed` | 新增断言后，旧 skill 缺少 HTML-only 信封模式。 |
| GREEN | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` | 模板写入规则后聚焦通过。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k html_only_delivery` | `1 passed, 15 deselected` | skill 写入规则后聚焦通过。 |
| 回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `16 passed` | 覆盖模板、内置 skill 与同步注入。 |
| skill 校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` | 确认 skill 结构有效。 |
| Python 编译 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_agents_template_migration.py tests/test_builtin_skills_injection.py` | 通过，无输出 | 测试文件语法有效。 |
| Diff 空白检查 | `git diff --check` | 通过，无输出 | 确认无尾随空格或空白错误。 |

> 备注：首次 baseline 未带 `BOT_TOKEN` 时，`bot.py` 导入按既有逻辑退出；已按测试既有口径补 `BOT_TOKEN=123:ABC` 重新执行。

## 8. 实施顺序

1. 读取当前 AGENTS 证据、HTML 协议任务记忆和相关测试。
2. 跑受影响测试 baseline。
3. 先补 RED 测试，确认模板和 skill 均缺少 HTML-only 信封契约。
4. 写入 `AGENTS-template.md` 与 `vibe-diagram/SKILL.md`。
5. 更新 AGENTS Facts Table 与本任务文档。
6. 执行聚焦回归、完整回归、skill 校验、语法检查和空白检查。

## 9. 风险与回滚

| 风险 | 影响 | 回滚方式 |
| --- | --- | --- |
| 文本完全禁用导致无法提示阻塞问题 | 中 | 保留“阻塞性澄清问题最多 1 个”的例外。 |
| Telegram 场景只输出 `file://` 导致附件不发送 | 中 | 规则明确 Telegram 只输出项目内 `.html/.htm` 路径。 |
| 模型把 HTML 内容又复制到文本里 | 中 | 新增测试锁定“文本只做交付信封”规则，后续发现实际逃逸继续收紧。 |
| 旧 worker 未同步新 AGENTS | 中 | 重启 worker 或重新同步 AGENTS 后生效。 |

## 10. Checklist

- [x] 已明确需求目标：文本信封化，不是模型绝对沉默。
- [x] 已补模板 RED 测试并看到预期失败。
- [x] 已补 skill RED 测试并看到预期失败。
- [x] 已写入 `AGENTS-template.md`。
- [x] 已写入 `vibe-diagram/SKILL.md`。
- [x] 已更新 AGENTS Facts Table。
- [x] 已执行最终回归、skill 校验、语法检查和空白检查。
