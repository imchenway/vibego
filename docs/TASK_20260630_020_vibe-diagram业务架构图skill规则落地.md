# TASK_20260630_020 vibe-diagram 业务架构图 skill 规则落地

## 变更

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`
    - 新增 `## 业务架构图专用骨架`。
    - 更新 `## 业务架构图规则`。
- `tests/test_builtin_skills_injection.py`
    - 新增 `test_vibe_diagram_business_architecture_must_be_domain_map_not_card_report`。
- `AGENTS.md`
    - Facts Table 新增 `业务架构图领域地图门禁`。
- `AGENTS-template.md`
    - 恢复既有测试要求的两处短语，避免换行/删句造成回归。

## 红绿验证

- RED：
  `python3.11 -m pytest -q tests/test_builtin_skills_injection.py::test_vibe_diagram_business_architecture_must_be_domain_map_not_card_report`
    - 结果：`1 failed`，缺少 `## 业务架构图专用骨架`。
- GREEN：同一测试重新执行，结果 `1 passed`。

## 同步

- `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json`
    - 结果：`ok: true`，codex/claude/gemini/vibego targets updated。

## 回归验证

-
`python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py`
    - 结果：`52 passed in 0.87s`。
- 内容检查：skill、AGENTS-template、AGENTS.md、`~/.codex/AGENTS.md`、`~/.config/vibego/AGENTS.md` 关键短语检查通过。

## 注意

已打开的旧会话如果已经加载旧上下文，可能需要新会话或重启对应 worker 才能稳定吃到最新规则。
