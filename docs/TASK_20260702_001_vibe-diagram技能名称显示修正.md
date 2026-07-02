# TASK_20260702_001_vibe-diagram 技能名称显示修正

## 问题

用户在 Codex 技能候选列表中输入 `$vibe` 时，候选名称显示为中文“Vibe 图形表达”，预期显示可调用名称 `vibe-diagram`。

## 调研证据

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`（锚点：frontmatter `name: vibe-diagram`）已声明真实 skill name 为 `vibe-diagram`。
- `vibego_cli/data/skills/vibe-diagram/agents/openai.yaml`（锚点：`interface.display_name`）原先把 OpenAI/Codex 原生技能界面名称写为 `Vibe 图形表达`。
- `plugins/vibe-diagram/.codex-plugin/plugin.json`（锚点：`interface.displayName`）插件市场展示名为 `Vibe Diagram`，与截图中的中文候选不一致；因此截图更可能来自 native skill 的 `agents/openai.yaml`，而不是插件 manifest。

## 处理

- 增加回归测试，锁定 native skill UI 的 `display_name` 必须与可调用名称一致。
- 将内置源与插件随包副本中的 `agents/openai.yaml` 的 `display_name` 改为 `vibe-diagram`。

## 验证记录

- RED：`PYTHONPATH=. pytest tests/test_vibe_diagram_plugin_distribution.py::test_vibe_diagram_native_skill_display_name_matches_invocation_name -q`
  - 结果：失败，断言 `display_name: "vibe-diagram"` 不存在，实际为 `display_name: "Vibe 图形表达"`。
- GREEN：`PYTHONPATH=. pytest tests/test_vibe_diagram_plugin_distribution.py tests/test_agents_sync.py::test_sync_agents_writes_override_and_targets tests/test_builtin_skills_injection.py::test_sync_agents_block_installs_native_vibe_diagram_skill_without_default_index -q`
  - 结果：`8 passed in 0.07s`。
- 本机 native skill 同步：通过 `vibego_cli.agents_sync.publish_native_skills(...)` 只更新 `/Users/david/.codex/skills` 与 `/Users/david/.agents/skills`。
  - 结果：`/Users/david/.codex/skills/vibe-diagram/agents/openai.yaml` 与 `/Users/david/.agents/skills/vibe-diagram/agents/openai.yaml` 均为 `display_name: "vibe-diagram"`。

## 未覆盖 / 待执行

- Codex 已运行实例是否立即刷新候选列表取决于客户端缓存；若仍显示旧名称，需重启/刷新 Codex 会话后再看候选。
