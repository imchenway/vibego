# TASK_20260630_023：vibe-diagram skill 拆分实施验收

## 结论

已按确认的 B 方案实施：`vibe-diagram/SKILL.md` 从长规则文件收敛为 138 行薄内核，新增 10 个按图型读取的 reference
文件，并通过测试保证 AGENTS 只注入薄内核与 reference 索引，不把所有 reference 正文常驻注入。

## 改动影响

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：改为薄内核，保留交付、路由、共性红线、reference 索引、自检。
- `vibego_cli/data/skills/vibe-diagram/references/*.md`：新增 10 个图型专属 reference。
- `tests/test_builtin_skills_injection.py`：测试改为内核 + reference 结构，新增薄内核、reference、同步索引断言。
- `pyproject.toml`：发布包包含 `data/skills/*/references/*.md`。
- `AGENTS.md`：同步 managed block，并更新 project-doc 事实锚点。
- `docs/superpowers/plans/2026-06-30-vibe-diagram-skill-split.md`：实施计划。

## 验证

- RED：
  `python3.11 -m pytest -q tests/test_builtin_skills_injection.py::test_vibe_diagram_core_is_thin_and_routes_to_references tests/test_builtin_skills_injection.py::test_vibe_diagram_reference_files_exist_and_are_packaged`
  初始失败，确认测试能抓住旧状态。
- GREEN：同一组测试变为 `2 passed`。
- 回归：
  `python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py`
  结果 `54 passed in 0.87s`。
- 同步：`python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` 返回 `ok: true`，四个目标均
  updated。
- 内容检查：薄内核 138 行；10 个 reference 文件存在；`pyproject.toml` 包含 references package-data；全局 AGENTS 有 reference
  索引且不含系统架构 reference 正文。

## 风险与回滚

- 风险：模型可能不主动读取 reference。规避：薄内核写入“选择图型后必须读取对应 reference；读取失败必须 fail-closed”。
- 风险：reference 发布包缺失。规避：`pyproject.toml` 与测试覆盖。
- 回滚：恢复旧单文件 `SKILL.md`，移除 references package-data 与新测试。

## 待执行

无需重启服务。若要让已有长会话立刻使用新 AGENTS，建议新开会话或重新加载上下文。
