# TASK_20260701_005 vibe-diagram 规则沉淀逐项审计

## 目标

逐步核对用户连续提出的 `vibe-diagram` 视觉与交付规则，确认是否已经沉淀进入 `vibego_cli/data/skills/vibe-diagram/SKILL.md`、对应 reference、自动化测试和样例 HTML。

## 审计结论

- 已沉淀：候选全集模式、候选切换按钮、11 类候选清单、参考业务架构图配色、右上交付卡片、任务编码与 skill 标题顶部同一行、标题左列紧凑、右上交付区上移填空、候选按钮紧跟描述、标题字号、输出前自检反查。
- 本轮发现并补齐：`输出前自检` 原先未逐项反查标题左列紧凑、右上交付卡片上移对齐、候选按钮紧跟描述、防左侧大块空白、`skill-strip` 单行省略和完整 title。已补入 `SKILL.md` 并新增测试断言。
- 未覆盖：仍需用户重启或重新同步 Vibego worker，让活跃 AGENTS 注入最新 skill 文本。

## 影响范围

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`
- `tests/test_builtin_skills_injection.py`
- `docs/TASK_20260701_005_vibe-diagram规则沉淀逐项审计.html`

## 验证命令

- `python3.11 -m pytest -q tests/test_builtin_skills_injection.py::test_vibe_diagram_html_must_pin_task_handoff_meta_top_right`
- `python3.11 -m pytest -q tests/test_builtin_skills_injection.py`：53 passed。
- `python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram`：Skill is valid!
- 静态 HTML 契约检查：001/002/003/004/005 顶部紧凑双列结构通过。
- Chrome CDP 视觉复核：005 页 `scrollY=0`、顶部卡片高度 283px、标题与右侧交付卡起点同为 66px、描述到候选按钮间距 14px。
- `git diff --check`：无输出。
