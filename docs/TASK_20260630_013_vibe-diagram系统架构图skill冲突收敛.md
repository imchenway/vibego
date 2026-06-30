# TASK_20260630_013 vibe-diagram 系统架构图 skill 冲突收敛

## 背景

用户确认：`TASK_20260630_007_vibe-diagram系统架构图宏观拓扑示例.html` 是目前最好布局；010/011 越改越烂。

## 判断

需要更新系统架构图相关 skill 内容，但不是继续新增复杂图型，而是收敛优先级：

1. 007 宏观拓扑是默认基线。
2. 泳道/分段是例外补救形态，不是默认升级方向。
3. 只有用户明确要求，或 007 基线验证失败时，才考虑泳道或分段故事线。
4. 如果进入例外形态，必须先说明为什么 007 基线不够用。

## 本轮修改

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：新增“系统架构图例外形态不是优化方向”。
- `tests/test_builtin_skills_injection.py`：新增
  `test_vibe_diagram_system_architecture_rule_priority_keeps_swimlanes_as_exception`，锁定 007 基线优先级必须高于泳道例外。
- `AGENTS.md`：更新系统架构图事实行，避免未来把 010/011 当推荐模板。
- 本文件与 HTML 交付说明：沉淀本轮决策和验证。

## 验证

已执行：

- 新增回归测试并确认红灯：`test_vibe_diagram_system_architecture_rule_priority_keeps_swimlanes_as_exception` 首次失败，证明旧
  skill 没有“系统架构图例外形态”优先级规则。
- 更新 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 后，定向测试通过：`4 passed`。
- `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json`：`ok: true`，Codex /
  Claude / Gemini / vibego AGENTS 均 updated。
-
`python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py`：
`51 passed`。
- `python3.11 -m py_compile tests/test_builtin_skills_injection.py`：通过，无输出。
- `python3.11` + `HTMLParser` 检查 `docs/TASK_20260630_013_vibe-diagram系统架构图skill冲突收敛.html`：通过。
- 内容检查：skill、Codex AGENTS、vibego AGENTS、本仓 AGENTS 与 013 HTML 均包含 007 优先、例外形态、010/011 非推荐模板锚点。
