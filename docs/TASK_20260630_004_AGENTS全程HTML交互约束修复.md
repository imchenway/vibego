# TASK_20260630_004 AGENTS 全程 HTML 交互约束修复

## 用户反馈

用户指出：AGENTS.md 里移除了很多 `vibe-diagram` 的使用约束；期望不是“非琐碎任务才使用 HTML”，而是**全程默认使用 HTML 与 AI
交互**。

## 根因

`AGENTS-template.md` 在收敛 PLAN/develop 长阶段提示词时，只保留了“非琐碎设计、排障、架构、流程、技术方案和交付验收优先使用
vibe-diagram”的触发边界。这个口径会让模型把 HTML 理解为“画图或复杂任务时才用”，而不是“默认交互界面”。

## 修复

1. 在 `AGENTS-template.md` 新增 `## HTML-first interaction contract`。
2. 明确：默认所有实质沟通都使用单文件 HTML 与用户交互。
3. 明确：聊天通道默认只做交付信封，包含 HTML 链接/路径、1-3 条验证摘要和下一步动作。
4. 明确：分析、设计、排障、方案、决策、验收、总结、代码逻辑说明、证据链、风险、回滚、测试矩阵都必须写入 HTML。
5. 明确：docs 做长期沉淀；HTML 是主交互界面。
6. 明确：只有阻塞性澄清问题、极短确认、简单命令结果、用户明确不要 HTML 时，才可使用普通文本。
7. 明确：`Visual and frontend contract` 只定义何时调用 `vibe-diagram` 做图形化表达，不得把 HTML 限定在这些场景。
8. 通过 `vibego_cli agents-sync` 同步到：
    - `/Users/david/.config/vibego/agents/current/AGENTS-template.md`
    - `/Users/david/.config/vibego/AGENTS.md`
    - `/Users/david/.codex/AGENTS.md`
    - `/Users/david/.claude/CLAUDE.md`
    - `/Users/david/.gemini/GEMINI.md`
9. 更新根目录 `AGENTS.md` Facts Table，补充 HTML-first 全程交互契约证据。

## 验证

| 验证项     | 命令 / 口径                                                                                                                            | 结果                                              |
|---------|------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------|
| TDD 红灯  | `python3.11 -m pytest -q tests/test_agents_template_migration.py::test_agents_template_requires_html_first_interaction_contract`   | 修复前失败，确认缺少 HTML-first 契约                        |
| TDD 绿灯  | 同上                                                                                                                                 | 修复后 `1 passed`                                  |
| 相关回归    | `python3.11 -m pytest -q tests/test_agents_template_migration.py tests/test_agents_sync.py tests/test_builtin_skills_injection.py` | `44 passed`                                     |
| 语法检查    | `python3.11 -m py_compile tests/test_agents_template_migration.py`                                                                 | 通过                                              |
| 同步验证    | `rg -n "HTML-first interaction contract\|默认所有实质沟通都使用单文件 HTML\|聊天通道默认只做交付信封" ...`                                                   | 仓库模板、override 模板、vibego AGENTS、Codex AGENTS 均命中 |
| HTML 解析 | `HTMLParser().feed(...)`                                                                                                           | 通过                                              |

## 影响面

- 之后默认不再把 HTML 当作“非琐碎画图任务”的附加动作，而是把 HTML 当作主交互界面。
- `vibe-diagram` 仍负责具体制图规则、质量门禁、交付信封和附件交付；AGENTS 负责声明 HTML-first 的交互边界。
- 已运行中的旧模型会话可能仍持有旧上下文，建议重启或新开会话让新规约生效。

## 变更文件

- `AGENTS-template.md`
- `AGENTS.md`
- `tests/test_agents_template_migration.py`
- `docs/TASK_20260630_004_AGENTS全程HTML交互约束修复.md`
- `docs/TASK_20260630_004_AGENTS全程HTML交互约束修复.html`
- 同步生成/更新：`/Users/david/.config/vibego/agents/current/AGENTS-template.md`、`/Users/david/.config/vibego/AGENTS.md`、
  `/Users/david/.codex/AGENTS.md`、`/Users/david/.claude/CLAUDE.md`、`/Users/david/.gemini/GEMINI.md`
