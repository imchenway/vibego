# TASK_20260703_002 vibe-diagram 系统架构图调整方案

## 1. 目标

把 `vibe-diagram` 的系统架构图默认产物，从“证据化 HTML 报告 / 分层节点网格”调整为用户大众认知中的系统架构图：先用图标、边界、层级和箭头让读者一眼看懂系统，再用折叠证据支撑事实。

## 2. 推荐方案

推荐采用 **方案 B：图谱规格 + 系统架构模板 + 产物级 lint**。

### 2.1 调整核心

1. 默认关闭候选全集：普通“画系统架构图”只出一张定稿图；只有用户明确要求多候选/校准/对比时才生成候选 tabs。
2. 新增 `DiagramSpec` 中间层：先生成结构化图谱规格，再渲染 HTML；不允许直接把 Markdown 大纲翻译成 DOM。
3. 固化系统架构图模板：默认使用“入口 rail + 应用层 + 能力支撑层 + 数据层 + 基础设施层 + 管理/运维侧栏 + 主数据流 / 反馈流”。
4. 证据降噪：主图节点只保留架构概念、职责和状态；源码路径、E#、待确认、运行验证进入折叠附录或节点详情。
5. 产物级 lint：对生成的 HTML 检查图标覆盖率、主线连通性、节点文字预算、证据密度、是否存在纯 div 卡片网格。

## 3. 备选方案

- 方案 A：只改 prompt / skill 文案。成本低，但容易继续复发。
- 方案 B：图谱规格 + 模板 + lint。成本中等，能把“像架构图”变成可执行门禁，推荐。
- 方案 C：完整 DSL/renderer。最稳，但成本高，适合作为 B 稳定后的第二阶段。

## 4. 验收口径

1. 同一份输入下，默认系统架构图不再出现候选 tab。
2. 首屏主图必须能静态看出入口、应用、能力、数据、基础设施和管理侧栏。
3. 主图使用图标 / SVG / 线条表达结构；不能只有 CSS grid 节点。
4. 主图节点平均文字量受控，证据不抢主图。
5. 新增 lint 能拦截 `TASK_20260703_002_系统架构图.html#panel-layered` 这类“无 SVG、节点多、证据密”的退化样式。

## 5. 2026-07-03 实施记录

已按方案 B 开发第一阶段能力：

1. `vibego_cli/data/skills/vibe-diagram/SKILL.md`：把“候选全集”收敛为显式校准模式；普通单图请求默认只生成一张首选 presentation 图，不再因 reference 列出备选候选而自动生成 tabs。
2. `vibego_cli/data/skills/vibe-diagram/references/system-architecture.md`：新增 `DiagramSpec` 前置契约、默认 `mode=presentation`、大众系统架构 archetype 模板、证据预算与产物级 lint 门禁。
3. `vibego_cli/data/skills/vibe-diagram/scripts/vibe_diagram_lint.py`：新增无第三方依赖的系统架构 HTML lint，拦截候选 tab、缺少 SVG/图形语法、必备架构语义缺失、证据密度过高与源码路径外露。
4. `plugins/vibe-diagram/skills/vibe-diagram/`：同步内置 skill，保持 Codex plugin 分发与内置 skill 不漂移。
5. `pyproject.toml`：把 `data/skills/*/scripts/*.py` 纳入包数据，确保 skill 附带 lint 脚本可发布。
6. `AGENTS.md`：更新事实表，补充 vibe-diagram scripts 与显式候选边界证据。

## 6. 自动化验证记录

- 红灯验证：新增测试后，目标测试先出现 4 个失败，分别覆盖候选模式、DiagramSpec/archetype 与 lint 脚本缺失。
- 通过验证：
  - `python3.11 -m pytest -q tests/test_builtin_skills_injection.py` → 63 passed。
  - `python3.11 -m pytest -q tests/test_vibe_diagram_plugin_distribution.py` → 6 passed。
  - `python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_vibe_diagram_plugin_distribution.py` → 69 passed。
  - `python3.11 vibego_cli/data/skills/vibe-diagram/scripts/vibe_diagram_lint.py --type system-architecture /Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260703_002_系统架构图.html` → 预期拒绝退化样例。

## 7. 剩余边界

当前第一阶段是 skill 规则 + 产物 lint + 分发同步；还没有引入完整 DSL renderer，也没有把所有历史 HTML 样例重画。后续若继续优化，可补“架构图样例库 + visual QA 截图判分 + 自动修复建议”。
