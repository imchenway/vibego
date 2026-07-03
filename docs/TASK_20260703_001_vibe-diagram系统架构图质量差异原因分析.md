# TASK_20260703_001 vibe-diagram 系统架构图质量差异原因分析

## 1. 问题

用户对比了两类系统架构图：

1. GPT5.5 pro 直接生成的附件图：符合大众认知里的系统架构图，分层、边界、图标、箭头和数据流清晰。
2. gpt5.5 xhigh 通过 `vibe-diagram` 生成的 HTML：`/Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260703_002_系统架构图.html#panel-layered`，可读性明显更差。

本轮目标：解释差异成因，不修改业务代码。

## 2. 证据

- 附件图视觉观察：`/Users/david/Library/Containers/com.wiheads.paste/Data/tmp/images/ChatGPT 2026-07-03 09.33.42.png`，呈现的是“总体架构 + RAG 架构 + 多 Agent 架构 + 端到端数据流”的常见架构图组合。
- 目标 HTML 静态解析：`/Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260703_002_系统架构图.html`。本轮读取确认：无 `<svg>`，存在 3 个候选 tab、23 个 `.node`、75 个 evidence 标签、25 个 evidence details。
- `vibe-diagram` 当前规则：`/Users/david/.agents/skills/vibe-diagram/SKILL.md` 要求候选全集、事实来源、证据、待确认、单文件 HTML 与输出自检。
- 系统架构 reference：`/Users/david/.agents/skills/vibe-diagram/references/system-architecture.md` 要求北向南全局拓扑，但也要求大量组件、入口、控制面、工作面、中间件、数据证据和运行语义。

## 3. 结论

根因不是单纯“模型不会画架构图”，而是 `vibe-diagram` 当前把模型从“架构图设计师”推成了“证据审计员 + HTML 页面作者”：它优先追求事实覆盖、证据锚点、候选齐全和不幻觉，弱化了大众架构图最关键的视觉抽象、图标语义、层级取舍和第一眼阅读路径。

## 4. 主要成因

1. 目标函数错位：GPT 直接生成时优化“像架构图”；`vibe-diagram` 优化“可追溯、可验收、可自包含”。
2. 候选全集模式增加噪音：系统架构 reference 要求同一 HTML 同时生成首选与备选候选，用户首屏先看到 tab 与说明，而不是一个定稿架构图。
3. 缺少架构图模板库：当前规则只说“北向南分层拓扑”，没有强制采用用户附件中那种“入口 rail + 应用层 + 能力层 + 数据层 + 基础设施 + 管理侧栏 + 数据流”的经典版式。
4. 事实证据外泄：源码路径、运行缺口、E# 证据和待确认信息进入大量节点，导致节点从架构概念变成审计段落。
5. 产物级门禁不够：已有测试多锁规则文本，缺少对任意生成 HTML 的视觉语法 lint，例如图标覆盖率、主线可追踪性、证据密度、节点文字量、是否使用真实拓扑画布。

## 5. 建议方向

推荐把 `vibe-diagram` 的系统架构图拆成两层：

- 默认用户交付图：只输出一张大众架构图，控制信息密度，使用固定架构模板与图标语法。
- 证据审计层：源码路径、E#、缺口和运行验证放进折叠详情或附录，不抢主图。

若后续要实施，应新增“系统架构 archetype 模板 + 产物级 lint + 候选全集显式调试开关”。
