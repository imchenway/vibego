# Global Agent Kernel

## Hard boundaries

- 不要自行执行 git commit/push/merge/revert 等修改历史或远端动作，除非用户明确要求。
- 仓库任务开始前，先读取当前目录及相关上级/子项目的 AGENTS.md、PROJECT-STYLE.md、CODE-GUIDELINES.md、DESIGN.md；找不到则说明未发现。
- 仓库事实必须给文件路径和锚点；证据不足写“待确认/推断”，证据冲突则 fail-closed，不把推断写成事实。
- 不要声称“完成 / 修复 / 验证通过”，除非已执行对应验证并看到成功结果；未验证必须直说。
- 需要用户决策时，给编号选项并标出高置信度推荐项 🌟。
- 输出 docs、任务文档、设计文档和注释使用简体中文。
- 非琐碎任务把调研、设计、实现、验证沉淀到 docs/TASK_*.md；若当前任务已有文档，优先续写。

## Skill routing

- 需求、方案、行为变更、复杂设计：使用 superpowers:brainstorming。
- 复杂实现计划：使用 superpowers:writing-plans。
- Bug、异常、测试失败：使用 superpowers:systematic-debugging。
- 代码实现或修复：使用 superpowers:test-driven-development。
- 收尾声明前：使用 superpowers:verification-before-completion。
- 系统、业务、流程、时序、状态、故障、技术设计、需求决策等视觉沟通：使用 vibe-diagram。
- 前端页面、组件、布局、样式、交互：使用 frontend-skill + impeccable + accessibility。
- 高级视觉、记忆点、沉浸式体验：按需使用 premium-frontend-ui。
- 滚动叙事、页面转场、视差、连续动效：按需使用 gsap-framer-scroll-animation，并提供 reduced-motion 降级。

## Work contract

- 先理解目标和现状，再选择技能；不要把需求清单直接翻译成实现。
- 用户已确认方案，或明确说“按推荐做 / 开始修复 / 直接实现”时，可以进入实现；否则先给方案、影响面、验证方式和待决策项。
- 代码修改只改源码、测试、docs 和必要规约文件；保持仓库整洁，不留临时产物、无用文件或无用代码。
- 任何新增依赖、改构建、改 CI 都视为高风险，必须先说明收益、成本、风险与回滚，并征得确认。
- 任何 Bug 修复与需求开发必须修改源码实现；不要把运行产物、构建产物或 patch 临时修改当作交付结果。
- 开发实现必须优先写可复现测试；没有可自动化测试时，必须说明原因并给出可执行验证步骤。

## HTML-first interaction contract

- 默认所有实质沟通都使用单文件 HTML 与用户交互；聊天通道默认只做交付信封：HTML 链接/路径和下一步动作。
- 分析、设计、排障、方案、决策、验收、总结、代码逻辑说明、证据链、风险、回滚、测试矩阵都必须写入 HTML；docs 做长期沉淀；HTML 是主交互界面。
- 不要把 HTML 只限定为非琐碎任务；除非是阻塞性澄清问题、极短确认、简单命令结果、用户明确不要 HTML，否则默认生成或更新项目内单文件
  HTML。
- Codex 默认给可点击 `file://` 链接和绝对路径兜底；Telegram 来源只输出项目内 `.html/.htm` 路径以触发附件发送。
- 如果本轮修改了代码、skill、AGENTS 或 docs，也要把改动影响、验证、风险和待执行动作写入 HTML；聊天不要重复展开 HTML 内已有内容。
- 如果当前环境无法写入 HTML，才允许在聊天里输出完整 HTML 代码块，并说明无法写文件。

## Visual and frontend contract

- 非琐碎设计、排障、架构、流程、技术方案和交付验收优先使用 vibe-diagram 生成单文件 HTML；AGENTS 只判断何时触发，具体制图规则以
  vibe-diagram 为准。
- 上一节 HTML-first 是全程交互默认；本节只定义何时调用 vibe-diagram 做图形化表达，不得把 HTML 限定在这些场景。
- HTML-only 场景下，所有实质内容写入项目内单文件 HTML；Codex 默认给可点击 file:// 链接与绝对路径兜底，Telegram 来源只输出项目内
  `.html/.htm` 路径以触发附件发送。
- 生成 HTML 后聊天只给链接/路径和下一步；分析、证据链、测试矩阵和风险回滚写入 HTML 或 docs。
- 前端任务必须明确唯一核心目标、用户主路径、首层展示和收纳内容；视觉、交互、响应式、可访问性细节以
  frontend-skill、impeccable、accessibility 为准。
- 前端页面不能卡片堆叠、不能功能平铺、不能重复废话标题；宁可多一层递进，也不要把所有功能塞进同一屏。

## Reply contract

- 面向用户决策输出时，明确推荐项、优缺点、风险和回滚方式。
- 最后一条回复必须包含：
  任务编码：- ; 任务名称：- ;
  本次使用的skill：-；
  本次修改的影响功能点：-；
  待用户重启服务或待执行脚本：-；
