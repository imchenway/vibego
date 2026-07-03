# TASK_20260702_003 逐个解释未触发 vibe-diagram 原因排查

## 背景

用户提供截图追问：“这个为什么也没有触发 vibe-diagram？”。截图对应的原始请求是“这个包下有四个文件更新，给我逐个解释其作用。subPackages/customer-service/pages”。

## 现场证据

- 原始 Codex session 已加载当前 vibego AGENTS 规则，且 `base_instructions` 里包含 `Skill routing` 与 `Visual and frontend contract`：`/Users/david/.codex/sessions/2026/07/02/rollout-2026-07-02T16-34-12-019f21f6-d4fb-7e01-a15c-ace54fc6a01a.jsonl:1`。
- 原始用户输入是“逐个解释其作用”，没有显式“画图 / 图形化 / HTML 图 / 完整逻辑 / 调用链 / 状态流转 / 为什么失败”等触发词：`/Users/david/.codex/sessions/2026/07/02/rollout-2026-07-02T16-34-12-019f21f6-d4fb-7e01-a15c-ace54fc6a01a.jsonl:6`。
- 模型最终返回的是文本表格，没有生成 HTML 文件：`/Users/david/.codex/sessions/2026/07/02/rollout-2026-07-02T16-34-12-019f21f6-d4fb-7e01-a15c-ace54fc6a01a.jsonl:42-43`。
- 当前模板要求：明确画图/图形化/HTML 图，或复杂技术/业务逻辑可视化时使用 `vibe-diagram`；完整逻辑、调用链、状态流转、数据口径、前后差异、根因链路、证据链也默认使用 `vibe-diagram`：`/Users/david/.config/vibego/agents/current/AGENTS-template.md:21-25`。
- 当前模板同时保留排除边界：普通概念问答、安装升级说明、轻量决策和非视觉化追问默认简洁文本：`/Users/david/.config/vibego/agents/current/AGENTS-template.md:41`、`/Users/david/.config/vibego/agents/current/AGENTS-template.md:49-51`。
- README 同步说明了同一触发边界：`/Users/david/hypha/tools/vibego/README.md:64-68`。
- 回归测试明确禁止回到“所有实质沟通/所有代码逻辑说明都强制 HTML”的旧口径：`/Users/david/hypha/tools/vibego/tests/test_agents_template_migration.py:170-198`。
- native skill 已同步，但 frontmatter 只覆盖 draw/diagram/visualize/architecture/workflows/complex logic 等视觉信号：`/Users/david/.codex/skills/vibe-diagram/SKILL.md:1-3`。
- 同步实现默认安装 native skill，不把完整 skill 规则常驻写进 AGENTS：`/Users/david/hypha/tools/vibego/vibego_cli/agents_sync.py:430-450`；测试锁定该行为：`/Users/david/hypha/tools/vibego/tests/test_builtin_skills_injection.py:1279-1350`。
- Telegram 来源前缀只告诉模型“如果生成 HTML，Telegram 主交付要发 `.html/.htm` 附件”，不是“所有普通 prompt 强制 HTML”：`/Users/david/hypha/tools/vibego/bot.py:471-473`、`/Users/david/hypha/tools/vibego/bot.py:3303-3306`。

## 结论

本次没有触发 `vibe-diagram` 的直接原因是：原始请求被当前规则更自然地归类为“逐个解释文件作用”的非视觉化轻量说明；它虽然提到了“更新”，但没有明确要求“前后差异”“完整逻辑”“调用链”“图形化”等当前强触发意图。模型文本回答与当前规则边界并不冲突。

如果产品期望是“凡是让模型解释 diff / 文件更新作用，默认给一张结构图”，那当前协议存在一个待决策的策略缺口：需要新增触发边界，例如“解释多个文件更新、页面四件套、模块改造作用时，默认用 `vibe-diagram` 生成技术设计/功能迭代图”。这属于行为策略变更，不能把现有结果直接判为实现 bug。

## 本轮产物

- HTML 排查图：`docs/TASK_20260702_003_逐个解释未触发vibe_diagram原因排查.html`

## 未改动

- 未修改源码、测试、AGENTS 或 skill。
- 未执行回归测试；本轮为只读排查 + docs/HTML 说明。
