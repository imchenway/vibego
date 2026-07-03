# Global Agent Kernel

## Hard boundaries

- 不要自行执行 git commit/push/merge/revert 等修改历史或远端动作，除非用户明确要求。
- DO NOT send optional commentary.
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
- 明确要求画图、图形化、HTML 图，或需要把复杂技术/业务逻辑、关系结构或状态流转可视化：使用 vibe-diagram。
- 系统、业务、流程、时序、状态、故障、页面设计、技术设计、需求决策、交付验收等视觉沟通：使用 vibe-diagram。
- “为什么 / 怎么做”先按意图分流：概念为什么、安装升级、轻量用法说明默认简洁文本；行为/故障为什么默认使用 vibe-diagram 生成单文件 HTML 图。
- 用户追问具体功能、按钮、接口、配置、权限、构建、任务、数据或运行现场“为什么没反应 / 为什么失败 / 为什么没生效 / 为什么走错 / 为什么变慢 / 为什么不一致”时，视为故障成因或逻辑链路理解，默认使用 vibe-diagram。
- 用户要求理解完整逻辑、调用链、状态流转、数据口径、前后差异、根因链路、证据链时，默认使用 vibe-diagram。
- 用户让解释具体对象、代码、文件更新、diff、模块、页面、接口、配置、数据、功能入口或运行结果时，默认使用 vibe-diagram 生成单文件 HTML 图；只在纯概念定义、翻译改写、一句话答案、简单命令或用户明确不要图时，才默认简洁文本。
- 前端页面、组件、布局、样式、交互：使用 frontend-skill + impeccable + accessibility。
- 产品体验、UX 研究、用户流程审计、视觉方向探索、原型/重设计/URL 克隆、截图/Figma/ImageGen 到可交互原型、原型视觉 QA：若当前环境已提供 OpenAI Product Design，则按需使用；不会因本模板自动安装，缺失时不得假装可用，应回退到现有前端/视觉链路；使用时必须先确认 brief，缺少视觉目标时先生成 3 个方向并等待用户选择，不得从文字 brief 直接实现；落地代码仍遵守 superpowers:test-driven-development、frontend-skill、impeccable、accessibility 与本仓库验证门禁。
- 高级视觉、记忆点、沉浸式体验：按需使用 premium-frontend-ui。
- 滚动叙事、页面转场、视差、连续动效：按需使用 gsap-framer-scroll-animation，并提供 reduced-motion 降级。

## Work contract

- 先理解目标和现状，再选择技能；不要把需求清单直接翻译成实现。
- 代码修改只改源码、测试、docs 和必要规约文件；保持仓库整洁，不留临时产物、无用文件或无用代码。
- 任何新增依赖、改构建、改 CI 都视为高风险，必须先说明收益、成本、风险与回滚，并征得确认。
- 任何 Bug 修复与需求开发必须修改源码实现；不要把运行产物、构建产物或 patch 临时修改当作交付结果。
- 开发实现必须优先写可复现测试；没有可自动化测试时，必须说明原因并给出可执行验证步骤。

## HTML / visual delivery contract

- 用户明确要求 HTML/图形化，或确实需要用图表达复杂关系，或解释具体对象、代码、文件更新、diff、模块、页面、接口、配置、数据、功能入口或运行结果时，生成或更新项目内单文件 HTML；纯概念定义、翻译改写、一句话答案、简单命令和用户明确不要图时默认使用简洁文本。
- 生成 HTML 时，分析、设计、排障、方案、决策、验收、总结、代码逻辑说明、证据链、风险、回滚、测试矩阵写入 HTML；docs 做长期沉淀；HTML 是主交互界面。
- Codex 默认给可点击 `file://` 链接和绝对路径兜底；链接文字必须使用 HTML 内部 `<h1>` 主标题，不要写成固定的“打开 HTML”；平台或入口侧要求由发送侧提示词前缀或运行时适配层注入，不写入本全局模板。
- 如果本轮修改了代码、skill、AGENTS 或 docs，且本轮需要 HTML 交付，则把改动影响、验证、风险和待执行动作写入 HTML；聊天不要重复展开 HTML 内已有内容。
- 如果当前环境无法写入 HTML，才允许在聊天里输出完整 HTML 代码块，并说明无法写文件。

## Visual and frontend contract

- 明确要求画图、图形化、HTML 图，需要把复杂技术/业务逻辑、关系结构或状态流转可视化，或解释具体对象、代码、文件更新、diff、模块、页面、接口、配置、数据、功能入口或运行结果时，优先触发 vibe-diagram 生成单文件 HTML；核心理念是一图胜千言；AGENTS 只判断何时触发，具体制图规则以 vibe-diagram 为准。
- 概念为什么默认文本；行为/故障为什么默认 HTML 图。不要因为用户没有显式说“画图”，就把具体运行现象、故障成因、代码逻辑链路或证据链追问退回聊天长文。
- 纯概念定义、翻译改写、一句话答案、简单命令、安装升级说明、轻量决策和用户明确不要图时默认使用简洁文本；不要为了回答每个抽象概念、简单步骤补充或轻量追问而强制生成 HTML。
- 生成 HTML 后聊天只给链接/路径和下一步；Markdown 链接文字必须使用 HTML 内部 `<h1>` 主标题，不要写成固定的“打开 HTML”；分析、证据链、测试矩阵和风险回滚写入 HTML 或 docs。
- 前端任务必须明确唯一核心目标、用户主路径、首层展示和收纳内容；视觉、交互、响应式、可访问性细节以
  frontend-skill、impeccable、accessibility 为准。
- Product Design 仅负责产品设计前置、视觉探索、原型化和视觉 QA；正式仓库实现仍以本仓源码、测试、docs 和必要规约文件为交付边界。若产出需要视觉化交付，仍遵守本仓 HTML 图交付规则。
- 前端页面不能卡片堆叠、不能功能平铺、不能重复废话标题；宁可多一层递进，也不要把所有功能塞进同一屏。
