# TASK_20260701_006 Product Design 融入全局提示词调研

## 结论

已按条件路由方式融入，但没有把 OpenAI Product Design 设为所有前端/视觉任务的默认入口；当前是 `AGENTS-template.md` 的**若当前环境已提供才按需使用 + 明确门禁**。缺失 skill 不会因模板自动安装，缺失时不得假装可用，应回退到现有前端/视觉链路。

推荐口径：

```md
- 产品体验、UX 研究、用户流程审计、视觉方向探索、原型/重设计/URL 克隆、截图/Figma/ImageGen 到可交互原型、原型视觉 QA：若当前环境已提供 OpenAI Product Design，则按需使用；不会因本模板自动安装，缺失时不得假装可用，应回退到现有前端/视觉链路；使用时必须先确认 brief，缺少视觉目标时先生成 3 个方向并等待用户选择，不得从文字 brief 直接实现；落地代码仍遵守 superpowers:test-driven-development、frontend-skill、impeccable、accessibility 与本仓库验证门禁。
```

同时建议在视觉/前端契约中补一条边界：

```md
- Product Design 负责产品设计前置、视觉探索、原型化和视觉 QA；正式仓库实现仍以本仓源码、测试、docs 和必要规约文件为交付边界。全局 HTML-first 交付优先级高于 Product Design 的普通聊天输出格式。
```


## 实施记录（2026-07-01）

- 已修改 `/Users/david/hypha/tools/vibego/AGENTS-template.md` 与 `/Users/david/.config/vibego/agents/current/AGENTS-template.md`。
- 已新增 Product Design 条件按需路由：若当前环境已提供才使用；不会因模板自动安装；缺失时回退到现有前端/视觉链路。
- 已移除 `AGENTS-template.md` 里的 `Reply contract` 字段块：`任务编码：-`、`任务名称：-`、`本次使用的skill：-`、`本次修改的影响功能点：-`、`待用户重启服务或待执行脚本：-`。
- 已把平台入口侧要求从全局模板移走，改为“平台或入口侧要求由发送侧提示词前缀或运行时适配层注入”。
- 已补充 `tests/test_agents_template_migration.py` 回归断言。
- 聚焦验证：`python3.11 -m pytest -q tests/test_agents_template_migration.py` → `12 passed`。

## 关键证据

| 结论点 | 证据 |
|---|---|
| 当前全局提示词已有 skill routing，但没有 Product Design 路由 | `/Users/david/.config/vibego/agents/current/AGENTS-template.md` 行 13-23 |
| 当前 HTML-first 与视觉/前端契约已规定实质沟通写入 HTML、vibe-diagram 负责图形表达、frontend-skill/impeccable/accessibility 负责前端细节 | `/Users/david/.config/vibego/agents/current/AGENTS-template.md` 行 34-54 |
| Product Design 的定位是把早期产品想法、URL、静态截图变成可评审/可交互原型 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/README.md` 行 1-8 |
| Product Design 能力覆盖原型、URL 复刻、选中设计实现、视觉方向、重设计、流程审计、用户摩擦研究、原型分享 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/README.md` 行 19-31 |
| Product Design 可用 Browser/Chrome/Playwright、Figma/Canva、Image generation、Sites/Vercel 等工具 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/README.md` 行 37-48 |
| 插件元数据确认其由 OpenAI 提供，能力是确认 brief、探索方向、审计用户流、从 live URL 做原型、把静态截图变可交互 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/.codex-plugin/plugin.json` 行 2-11、26-44 |
| Product Design router 只负责路由，真正执行要进入 user-context/get-context/research/audit/ideate/prototype/image-to-code/url-to-code/share/design-qa 等技能 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/skills/index/SKILL.md` 行 30-38、86-128 |
| “无视觉目标不构建”是硬约束：缺 URL/截图/Figma/mock/source image 时必须 get-context → ideate → 3 个方向 → 等用户选择，不能先 scaffold/改文件/起服务 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/skills/index/SKILL.md` 行 40-49 |
| brief gate 是硬约束：设计/构建/原型/克隆/重设计/扩展/UI 方向都要先确认 brief；确认 brief 也不等于视觉目标 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/references/critical-overrides.md` 行 16-22 |
| 在已有项目中必须先找相似流程、组件、设计系统、tokens，不得重造轮子 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/references/critical-overrides.md` 行 5-14 |
| 审计不是泛泛意见，必须截图取证，发现与步骤/截图绑定，且不能从截图声称完整可访问性合规 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/skills/audit/SKILL.md` 行 8-17、76-81 |
| Ideate 必须在 get-context 确认后生成 3 个独立视觉方向，并在用户选择前停止构建 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/skills/ideate/SKILL.md` 行 25-99 |
| Prototype 的金科玉律是有视觉目标才进入 image-to-code/url-to-code；新产品路径是 brief → 3 个方向 → 用户选择 → image-to-code | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/skills/prototype/SKILL.md` 行 12-19、61-69 |
| Image-to-code 必须有选中的图/截图/mock/ImageGen，文字 brief 不够；构建后 design-qa 是阻塞门禁 | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/skills/image-to-code/SKILL.md` 行 23-29、66-97 |
| design-qa 需要源视觉目标 + 渲染实现；缺任一则写 blocked；必须检查字体、间距、颜色、图片资产、文案等 fidelity surfaces | `/Users/david/.codex/plugins/cache/openai-curated-remote/product-design/0.1.47/skills/design-qa/SKILL.md` 行 12-19、57-81 |

## 融入判断

### 可以融入的场景

1. 用户明确要产品体验、UX 研究、用户摩擦、用户流审计。
2. 用户要做产品 UI 视觉方向、三版方案、重设计、原型、URL 克隆、截图/Figma/ImageGen 转交互原型。
3. 用户要对已实现原型做视觉 QA 或 fidelity 检查。

### 不建议直接交给 Product Design 的场景

1. 纯技术图、排障图、架构图、时序图、交付验收图：继续用 `vibe-diagram`。
2. 现有仓库内明确的小型前端 bugfix / 样式修正 / 组件实现：继续 `superpowers:test-driven-development + frontend-skill + impeccable + accessibility`。
3. 用户只是要单文件 HTML 视觉沟通：继续遵守 Vibego HTML-first 与 vibe-diagram 规则。

## 风险与回滚

| 风险 | 影响 | 控制方式 |
|---|---|---|
| Product Design 的 brief/visual-target 门禁会拖慢小修小补 | 小样式修复被迫进入三方案流程 | 只在产品体验/原型/视觉探索类触发，不替换现有前端路由 |
| Product Design 原型构建可能引入本地 app/依赖/服务，与 Vibego 单 HTML 交付冲突 | 用户要的是 HTML 图，结果变成 app 原型 | 全局提示词声明 HTML-first 优先，Product Design 只负责产品原型路径 |
| Product Design 沟通协议偏“设计伙伴口吻”，与 Vibego 信封式 HTML 交付冲突 | 聊天输出变长、重复 HTML 内容 | 明确 Vibego 的 HTML-first 和最终信封口径优先 |
| 直接把所有 UI 任务交给 Product Design | TDD/验证资产/源码修改边界被绕过 | 声明正式仓库实现仍遵守 superpowers TDD、frontend-skill 和验证门禁 |

## 建议下一步

若用户确认，我再实施：

1. 修改 `/Users/david/.config/vibego/agents/current/AGENTS-template.md` 的 `Skill routing` 与 `Visual and frontend contract`。
2. 补 `tests/test_agents_template_migration.py` 或相关注入测试，确保 Product Design 路由、brief gate、visual target gate、HTML-first 优先级被固化。
3. 运行聚焦测试与 diff 检查。
4. 重新同步/重启 Vibego worker，让活跃 AGENTS 注入新规则。


## 平台入口文案去耦

`AGENTS-template.md` 不再内置具体运行入口名称或入口附件口径；这些要求由发送侧提示词前缀或运行时适配层注入。
