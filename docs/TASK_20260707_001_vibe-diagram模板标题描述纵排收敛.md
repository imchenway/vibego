# TASK_20260707_001_vibe-diagram 模板标题描述纵排收敛

## 背景

用户在 `/Users/david/Downloads/TASK_20260706_001_DWT项目内容分析.html` 中发现架构图节点标题容易被描述内容挤压换行，并要求“检查所有的模板，全部都要处理”。

## 调研结论

- 目标生成物的问题点是 `.arch-box{display:flex;...}` 没有 `flex-direction:column`，导致 `<b>标题</b><span>描述</span>` 在 flex 默认横向主轴上互相挤压。
- 仓库内置模板源位于 `vibego_cli/data/skills/vibe-diagram/templates/`，插件镜像位于 `plugins/vibe-diagram/skills/vibe-diagram/templates/`。
- 当前共有 58 个源模板和 58 个插件镜像模板需要保持一致。

## 设计

- 对所有模板的 `.slot` 节点统一改为显式上下堆叠：`grid-template-rows:auto auto`、`align-content:center`、`justify-items:center`、`gap:4px`。
- 标题 `<b>` 与描述 `<span>` 都设置 `max-width:100%`，标题使用独立行高，描述取消依赖 `margin-top` 的伪分隔。
- 在 `vibe_diagram_lint.py` 增加产物级门禁：包含直接 `<b>` + `<span>` 的节点类如果使用 `display:flex`，必须同时声明 `flex-direction:column`。
- 在 `SKILL.md` 规则层补充自由绘制节点也必须“标题单独一行、描述在下方”。

## 实现范围

- 更新 `vibego_cli/data/skills/vibe-diagram/templates/**/*.html` 全部 58 个源模板。
- 同步 `plugins/vibe-diagram/skills/vibe-diagram/`，保持插件分发树与内置 skill 源一致。
- 更新 `vibego_cli/data/skills/vibe-diagram/scripts/vibe_diagram_lint.py` 与对应测试。

## 验证记录

- RED：新增测试后，`test_vibe_diagram_all_html_templates_stack_slot_titles_above_descriptions` 与 `test_vibe_diagram_lint_rejects_horizontal_flex_title_description_nodes` 均失败，证明旧模板/旧 lint 未覆盖该问题。
- GREEN：修改模板、规则与 lint 后，聚焦测试通过。
- 额外检查：脚本扫描源模板与插件镜像模板共 116 个文件，均命中标题/描述纵排 CSS。
- 现有 DWT HTML 产物已可被新 lint 拦截：报错 `.arch-box 使用 display:flex 时必须同时声明 flex-direction:column`。

## 待确认

- 本轮没有直接修改 `/Users/david/Downloads/TASK_20260706_001_DWT项目内容分析.html`，只收敛仓库模板与未来产物门禁。
- 活跃技能同步：已执行 `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json`，输出 `ok: true`，并更新 `/Users/david/.codex/skills` 与 `/Users/david/.agents/skills`。
- 活跃技能检查：`/Users/david/.codex/skills/vibe-diagram/templates/**/*.html` 中 58 个模板均命中纵排 CSS；`/Users/david/.codex/skills/vibe-diagram/SKILL.md` 已包含标题/描述纵排规则。
