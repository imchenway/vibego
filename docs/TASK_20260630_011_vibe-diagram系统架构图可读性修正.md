# TASK_20260630_011 vibe-diagram 系统架构图可读性修正

## 背景

用户反馈 `TASK_20260630_010_vibe-diagram系统架构图泳道重排示例.html`：可读性并不高。

## 复盘结论

010 的问题不是信息不完整，而是表达方式退化成了多列表格：层级、控制面、主请求、数据面、兜底面同时横向展开，读者必须逐格扫描，主线反而不突出。

## 本轮修正

1. 更新 `vibe-diagram` 系统架构图规则：泳道不是表格，不能把所有平面做成等权重多列网格。
2. 新增回归测试：锁定“主请求粗主线 + 侧向胶囊 / 短注 + 分段卷轴”的可读性门禁。
3. 新画 `TASK_20260630_011_vibe-diagram系统架构图可读性修正版.html`：
    - 主路径只保留 5 个大节点。
    - 控制、数据、兜底收成贴近当前阶段的胶囊，不形成第二张矩阵。
    - 证据使用原生 `<details>` 作为渐进展开，核心信息静态可读。
    - 移动端按纵向阶段自然重排，不需要横向拖动。

## 验证

已执行：

- 先新增回归测试并确认红灯：`test_vibe_diagram_system_architecture_swimlanes_must_preserve_readability` 首次失败，证明旧
  skill 没有“泳道不是表格”的可读性门禁。
- 更新 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 后，定向测试通过：`1 passed`。
- `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json`：`ok: true`，Codex /
  Claude / Gemini / vibego AGENTS 均 updated。
-
`python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py`：
`49 passed`。
- `python3.11 -m py_compile tests/test_builtin_skills_injection.py`：通过，无输出。
- `python3.11` + `HTMLParser` 检查 `docs/TASK_20260630_011_vibe-diagram系统架构图可读性修正版.html`：通过。
- 内容检查：skill、Codex AGENTS、vibego AGENTS 与 011 HTML 均包含本轮可读性门禁或主线分段锚点。
