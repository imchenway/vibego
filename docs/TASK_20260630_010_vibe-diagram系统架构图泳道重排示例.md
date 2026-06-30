# TASK_20260630_010 vibe-diagram 系统架构图泳道重排示例

## 背景

用户在 `TASK_20260630_009_vibe-diagram系统架构图二次优化建议.html` 中选择 **B：中度重排**。

## 本轮目标

将上一版北向南宏观拓扑进一步重排为：

`控制面泳道 | 主请求中轴 | 数据/知识面泳道 | 兜底面 rail`

目标不是增加实现细节，而是让控制、数据、兜底边界更容易一眼区分。

## 本轮产物

- `docs/TASK_20260630_010_vibe-diagram系统架构图泳道重排示例.html`
- `docs/TASK_20260630_010_vibe-diagram系统架构图泳道重排示例.md`
- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：补充中等复杂系统架构的泳道拓扑规则。
- `tests/test_builtin_skills_injection.py`：补充泳道拓扑规则回归测试。

## 设计取舍

- 保留 007 的北向南层级与证据进节点详情原则。
- 采用主请求中轴作为第一阅读路径。
- 控制面不再与 Gateway / Facade 等权重平铺，而是作为约束/开关/策略泳道。
- 数据/知识面作为南向或侧向依赖泳道，RAG / Adapter 缺口用状态角标表达。
- 七鱼兜底从数据面移出，变成右侧虚线 rail，避免误读为数据节点。
- 运行语义条集中表达同步 HTTP、无独立 MQ、trace、待接入状态。

## 验证

已执行：

- 先新增回归测试并确认红灯：`test_vibe_diagram_system_architecture_supports_plane_swimlanes_for_medium_complexity`
  首次失败，证明旧 skill 没有泳道拓扑规则。
- 更新 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 后，定向测试通过：`1 passed`。
- `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json`：`ok: true`，Codex /
  Claude / Gemini / vibego AGENTS 均 updated。
-
`python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py`：
`48 passed`。
- `python3.11` + `HTMLParser` 检查 `docs/TASK_20260630_010_vibe-diagram系统架构图泳道重排示例.html`：通过。
- `python3.11 -m py_compile tests/test_builtin_skills_injection.py`：通过，无输出。
