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

## 8. 2026-07-03 二次修正：marker-only 退化样例

用户复核最新产物 `/Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260703_002_系统架构图.html` 后指出仍然“差远了”。现场复核结果：该 HTML 已包含 `data-diagram-grammar="system-architecture-presentation"`，没有候选 tab，但 `<svg>` 数量为 0，主体仍是 `.node` 节点网格与证据按钮。因此第一阶段 lint 存在漏洞：允许用 presentation 标记替代真实图形层。

本次修正：

1. `tests/test_builtin_skills_injection.py` 新增 marker-only 红灯测试：带 presentation 标记但没有真实 SVG/图标/连线层的系统架构 HTML 必须失败。
2. `vibego_cli/data/skills/vibe-diagram/scripts/vibe_diagram_lint.py` 改为要求系统架构图必须包含真实 SVG 主画布；`presentation` 标记不能替代图形层。
3. `vibego_cli/data/skills/vibe-diagram/references/system-architecture.md` 同步修改 lint 门禁：`data-diagram-grammar` 只是机读标记，不等于架构图。
4. 已同步 `plugins/vibe-diagram/skills/vibe-diagram/`，并执行 `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` 更新本机 `~/.codex/skills`、`~/.agents/skills` 和 vibego override。

二次验证：

- `python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k "system_architecture_lint_rejects"` → 2 passed。
- `python3.11 vibego_cli/data/skills/vibe-diagram/scripts/vibe_diagram_lint.py --type system-architecture /Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260703_002_系统架构图.html` → 预期失败，错误包括“系统架构图必须包含真实 SVG 主画布；presentation 标记不能替代图形层”。

## 9. 2026-07-03 三次修正：复刻版细节沉淀为稳定样式契约

用户确认重画复刻版整体“对味”后，继续指出横向滚动条、空白过多、局部图标丑、emoji 被误删、图标与文案未居中、孤立箭头标签含义不清等细节问题。本次把这些反馈收敛为系统架构图的 presentation 微规则，而不是只修单个 HTML。

本次修正：

1. `tests/test_builtin_skills_injection.py` 新增两类回归测试：一类锁定系统架构图提示词必须包含版式锁定、节点自居中、emoji 局部替换、箭头反歧义和局部反馈编辑规则；另一类用退化 HTML 证明横向滚动、固定坐标 SVG 文案和“主请求入口”孤立标签会被 lint 拦截。
2. `vibego_cli/data/skills/vibe-diagram/references/system-architecture.md` 新增“系统架构图 presentation 版式锁定”，固定默认布局为左侧入口 rail、中央应用层、南向能力/数据/基础设施和右侧管理/运维/兜底侧栏，并要求节点内部内容整体水平/垂直居中。
3. `vibego_cli/data/skills/vibe-diagram/scripts/vibe_diagram_lint.py` 新增三项产物级门禁：主画布不得依赖横向滚动或超大 `min-width`；节点内容必须使用 `foreignObject` 或自居中 HTML 容器；含糊箭头标签“主请求入口”必须失败。
4. `plugins/vibe-diagram/skills/vibe-diagram/` 已同步内置 skill，保持插件分发不漂移；并执行 `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` 更新本机 native skills。

三次验证：

- 红灯：`python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k "locks_polished_presentation_micro_rules or rejects_scroll_canvas_raw_svg_text"` 先失败 2 个，证明原规则还没有覆盖本轮反馈。
- 绿灯：`python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_vibe_diagram_plugin_distribution.py` → 72 passed。
- 正向样例：`python3.11 vibego_cli/data/skills/vibe-diagram/scripts/vibe_diagram_lint.py --type system-architecture /Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260703_002_系统架构图_重画复刻版.html` → OK。
- 反向样例：`python3.11 vibego_cli/data/skills/vibe-diagram/scripts/vibe_diagram_lint.py --type system-architecture /Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260703_002_系统架构图.html` → 预期失败，错误包括“真实 SVG 主画布”“marker-only 节点网格”。

## 10. 2026-07-03 四次修正：排查为什么最新 skill 仍诱导退化文件

用户指出：应该排查 skill 为什么导致 `/Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260703_002_系统架构图.html` 的问题，而不是直接修改生成文件。本轮没有修改该生成文件，改为追溯 skill 规则与门禁。

证据：

1. 退化文件虽然带有 `data-diagram-grammar="system-architecture-presentation"`，也包含 `<svg>` 与 `foreignObject`，因此旧 lint 会通过。
2. 退化文件的主画布标题是 `主请求流：入口 → SDK → /api/cs/v1/* → Facade → Agent → 数据/兜底`，已经从系统架构图退化为接口/代码流水线。
3. 退化文件 lane 标题包含 `1. 小程序接入 / SDK 表现层`、`2. HTTP 接入 / Gateway`、`3. 应用层 / 会话编排` 等编号实现分层，说明模型被旧规则里的 007 宏观拓扑/运行时链路口径拉偏。
4. 本地已安装的 `~/.codex/skills/vibe-diagram/references/system-architecture.md` 与 `~/.agents/skills/...` 在同步前仍含“系统架构图默认优先采用 007 宏观拓扑基线”，与源码中新加的 presentation 版式锁定发生优先级冲突。

根因：

- **规则冲突**：同一 reference 同时写了“presentation 大众架构图模板”和“007 宏观拓扑默认优先”。模型选择了后者，于是生成编号 lane + SDK/API/Facade 的实现流水线。
- **门禁漏洞**：lint 只拦截无 SVG、无 foreignObject、候选 tab、证据密度等粗粒度问题，没有识别“有 SVG 但语义仍是代码调用链”的伪架构图。
- **同步窗口**：源码修正后，如果没有重新 `agents-sync`，活跃 native skill 仍可能使用旧 reference。

本次只修 skill/门禁，不修生成文件：

1. `system-architecture.md` 明确：`presentation 版式锁定优先级高于 007 宏观拓扑基线`；007 降级为“内部运行时证据架构”的例外形态。
2. 普通系统架构图禁止 `1.` / `2.` / `3.` 编号 lane 作为主层标题；SDK 表现层、HTTP 接入、Controller、Facade、Handler、DTO 等实现名只能进详情。
3. `vibe_diagram_lint.py` 新增三类拦截：画布逻辑宽度过窄、编号实现分层、主画布标题为接口调用链/代码流水线。
4. 重新同步插件副本与 native skills。

验证：

- `python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_vibe_diagram_plugin_distribution.py` → 74 passed。
- 退化文件 lint 现在预期失败，错误包括：`presentation 画布过窄`、`不得退化为编号实现分层`、`不得把主画布标题写成接口调用链或代码流水线`。
- `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` 已同步到 `~/.codex/skills` 与 `~/.agents/skills`。
