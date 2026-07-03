# 实施计划：vibe-diagram 系统架构图从候选卡片页收敛为 presentation 架构图

## 目标

把系统架构图默认输出从“候选全集 + 证据卡片报告”调整为“单张可读的 presentation 架构图”：入口、应用层、能力支撑层、数据层、基础设施层、管理/运维侧栏、主请求流与数据流必须第一眼可见；多候选只在用户明确要求校准、对比或视觉探索时启用。

## 证据锚点

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：当前存在 `## 候选全集校准模式`，但文案仍容易被解释为普通请求默认生成候选全集。
- `vibego_cli/data/skills/vibe-diagram/references/system-architecture.md`：当前系统架构 reference 仍要求命中后生成首选候选与备选候选，并缺少明确的 DiagramSpec 与架构 archetype 出图协议。
- `tests/test_vibe_diagram_plugin_distribution.py`：插件 skill tree 必须与内置 source 完全一致，因此 skill 改动必须同步到 `plugins/vibe-diagram/skills/vibe-diagram`。

## 实施步骤

1. 先补红灯测试：覆盖普通系统架构图默认 single presentation、显式校准才启用候选、系统架构 DiagramSpec/archetype/证据预算，以及 HTML lint 对证据卡片页的拦截。
2. 调整 `vibe-diagram/SKILL.md`：把候选全集改成“显式校准模式”，禁止普通单图请求自动 tabs。
3. 调整 `references/system-architecture.md`：新增 DiagramSpec、默认大众架构图 archetype、图标/语义线、证据预算、产物级 lint 门禁。
4. 增加 skill 内置脚本 `scripts/vibe_diagram_lint.py`：不依赖第三方包，针对系统架构图检查 tabs、SVG/图形语法、证据密度、必备架构层与主流向。
5. 更新 `pyproject.toml` package-data，确保 skill scripts 随包发布；同步插件目录。
6. 更新任务文档 `docs/TASK_20260703_002_vibe-diagram系统架构图调整方案.md`，记录实现与验证。
7. 跑目标 pytest、插件同步测试、脚本自检、diff check；失败则继续修正。
