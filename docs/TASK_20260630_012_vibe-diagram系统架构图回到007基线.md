# TASK_20260630_012 vibe-diagram 系统架构图回到 007 基线

## 背景

用户评审多版系统架构图后明确反馈：越改越烂，目前看下来最好的还是 `TASK_20260630_007_vibe-diagram系统架构图宏观拓扑示例.html`
的布局。

## 决策

系统架构图默认回到 007 宏观拓扑基线：

`北向南层级 + 层间流向分隔条 + 节点内摘要 + 点击详情证据`

## 原因

- 007 能一眼看到系统边界、层级和主流向。
- 010 的多泳道方案虽然信息更全，但读者需要横向逐格扫描，可读性下降。
- 011 的分段故事线虽然降低了横向复杂度，但系统架构感变弱，更像讲解稿而不是架构拓扑。
- 后续不应因为“还有优化空间”就自动升级为多泳道、五列表格或分段故事线。

## 规则更新

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：新增 007 宏观拓扑基线规则。
- `tests/test_builtin_skills_injection.py`：新增回归测试
  `test_vibe_diagram_system_architecture_prefers_007_macro_topology_baseline`。
- `AGENTS.md`：新增事实行，标记 007 为系统架构图默认基线。

## 验证

已执行：

- 先新增回归测试并确认红灯：`test_vibe_diagram_system_architecture_prefers_007_macro_topology_baseline` 首次失败，证明旧
  skill 没有把 007 作为默认基线。
- 更新 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 后，定向测试通过：`1 passed`。
- `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json`：`ok: true`，Codex /
  Claude / Gemini / vibego AGENTS 均 updated。
-
`python3.11 -m pytest -q tests/test_builtin_skills_injection.py::test_vibe_diagram_system_architecture_prefers_007_macro_topology_baseline tests/test_builtin_skills_injection.py::test_vibe_diagram_system_architecture_swimlanes_must_preserve_readability`：
`2 passed`。
-
`python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py`：
`50 passed`。
- `python3.11 -m py_compile tests/test_builtin_skills_injection.py`：通过，无输出。
- `python3.11` + `HTMLParser` 检查 `docs/TASK_20260630_012_vibe-diagram系统架构图回到007基线.html`：通过。
- 内容检查：skill、Codex AGENTS、vibego AGENTS、本仓 AGENTS 与 012 HTML 均包含 007 基线或反例锚点。
