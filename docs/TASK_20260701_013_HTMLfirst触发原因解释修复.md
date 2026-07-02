# TASK_20260701_013 HTML-first 触发原因解释修复

## 背景

用户指出上一轮关于 `vibe-diagram` 是否单独发布的答复仍然用了普通聊天长文，没有生成 HTML。该现象说明 HTML-first 规则虽然存在，但触发信号仍存在缺口：普通“为什么 / 怎么做 / 需要怎么做”类原因解释没有被明确写入 `vibe-diagram` 的 skill 触发描述和 AGENTS skill routing。

## 根因

- `AGENTS-template.md` 已有 HTML-first 总规则，但 `## Skill routing` 中 `vibe-diagram` 的正向路由主要写成“视觉沟通”。这给模型留下了“用户没说画图就可以普通答复”的空间。
- `vibego_cli/data/skills/vibe-diagram/SKILL.md` frontmatter `description` 主要覆盖 draw / diagram / visualize / architecture / workflows 等视觉关键词，缺少 `HTML-first substantive answer`、`why/how explanations`、`为什么`、`怎么做`、`实质沟通` 等触发词。
- 生成的 AGENTS 只注入 skill 索引；索引取自 `description`。因此 description 过窄会直接降低索引触发概率。

## 变更范围

- `AGENTS-template.md`：在 `## Skill routing` 中新增 HTML-first 实质沟通、原因解释、方案建议、修复说明、验收收口路由到 `vibe-diagram`；在 HTML-first 合约中明确“为什么 / 怎么做 / 需要怎么做”类答复也必须生成或更新 HTML。
- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：扩展 frontmatter description，加入 `HTML-first substantive answer`、`why/how explanations`、`delivery envelope` 和中文触发词；正文首段同步说明可承接 HTML-first 实质回复。
- `tests/test_agents_template_migration.py`：新增模板路由回归测试。
- `tests/test_builtin_skills_injection.py`：新增 skill description 覆盖 HTML-first 实质回复的回归测试，并反查同步索引保留这些触发词。
- active 全局目标：通过 `agents-sync` 同步到 `/Users/david/.codex/AGENTS.md`、`/Users/david/.claude/CLAUDE.md`、`/Users/david/.gemini/GEMINI.md`、`/Users/david/.config/vibego/AGENTS.md`。

## TDD 记录

1. RED：先新增两个回归测试，要求 AGENTS 明确把“为什么 / 怎么做”类实质答复路由到 `vibe-diagram`，并要求 `vibe-diagram` description 覆盖 HTML-first 实质回复。
2. 失败命令：`python3.11 -m pytest -q tests/test_agents_template_migration.py::test_agents_template_routes_substantive_why_how_answers_to_vibe_diagram tests/test_builtin_skills_injection.py::test_vibe_diagram_description_covers_html_first_substantive_replies`。
3. 失败结果：`2 failed`，证实旧规则缺少对应触发词。
4. GREEN：更新模板路由和 skill description。
5. 通过命令：同一聚焦命令加压缩保护测试 → `3 passed in 0.04s`。

## 验证

| 命令 | 结果 | 说明 |
|---|---|---|
| `python3.11 -m pytest -q tests/test_agents_template_migration.py::test_agents_template_routes_substantive_why_how_answers_to_vibe_diagram tests/test_builtin_skills_injection.py::test_vibe_diagram_description_covers_html_first_substantive_replies tests/test_builtin_skills_injection.py::test_vibe_diagram_prompt_compaction_preserves_recent_feedback_rules` | `3 passed in 0.04s` | 聚焦验证触发修复与提示词长度保护 |
| `python3.11 -m pytest -q tests/test_agents_template_migration.py tests/test_builtin_skills_injection.py tests/test_agents_sync.py` | `80 passed in 0.90s` | 模板、skill、同步链路回归通过 |
| `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` | `ok: true`，4 个 target 为 `updated` | active skill 与全局目标已同步 |
| `cmp -s AGENTS-template.md /Users/david/.config/vibego/agents/current/AGENTS-template.md` | `template_cmp_ok` | active 模板一致 |
| `cmp -s vibego_cli/data/skills/vibe-diagram/SKILL.md /Users/david/.config/vibego/agents/current/vibego_cli/data/skills/vibe-diagram/SKILL.md` | `skill_cmp_ok` | active skill 一致 |
| `grep -n 'HTML-first 实质沟通\|HTML-first substantive answer\|why/how explanations' /Users/david/.codex/AGENTS.md ...` | 命中新路由和新 description | 生成的 AGENTS 索引已带触发词 |
| `git diff --check` | 无输出，退出码 0 | 空白检查通过 |

## 影响与回滚

- 影响入口：后续“为什么 / 怎么做 / 需要怎么做”类非阻塞性实质答复，会被明确纳入 HTML-first / `vibe-diagram` 触发范围。
- 用户动作：无需再执行脚本；本轮已执行 `agents-sync`。如果要让长期 worker 立即读取新规约，需要重启对应 worker。
- 未覆盖：未做长期 worker 重启后的现场观察；未跑全量 pytest。
- 回滚：恢复 `AGENTS-template.md` 与 `vibe-diagram/SKILL.md` 中新增触发词和对应测试，再执行 `agents-sync`。
