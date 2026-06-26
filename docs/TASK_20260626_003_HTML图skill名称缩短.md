# TASK_20260626_003 HTML 图 skill 名称缩短

## 1. 需求

用户反馈当前 skill 名称 `vibe-diagram-communication` 太长，希望改短。

## 2. 取证

- `scripts/models/common.sh`（锚点：`render_builtin_skills`）用 `skill_file.parent.name` 作为 AGENTS 注入标题，即目录名会直接显示为 `## Skill: ...`。
- `vibego_cli/data/skills/vibe-diagram-communication/SKILL.md`（锚点：`name: vibe-diagram-communication`）frontmatter 也使用长名。
- `AGENTS-template.md` 与 `tests/test_agents_template_migration.py` 当前仍显式引用 `vibe-diagram-communication`。

## 3. 推荐实现

将内置 skill 名称从 `vibe-diagram-communication` 缩短为 `vibe-diagram`：

- 目录：`vibego_cli/data/skills/vibe-diagram/`
- frontmatter：`name: vibe-diagram`
- AGENTS 模板引用：`vibe-diagram skill`
- 测试引用和注入断言同步更新。

## 4. 边界

- 不改变 skill 内容规则、交付方式、Codex/Telegram 分端契约。
- 不修改历史任务文档里的旧名称引用，避免破坏历史记录；仅更新当前事实表与测试/模板中的现行契约。
- 不新增依赖，不改打包 glob。

## 5. 测试计划

1. 先把测试改成期望 `vibe-diagram`，确认旧实现红灯。
2. 重命名目录和 frontmatter。
3. 更新 AGENTS 模板与 AGENTS 事实表。
4. 运行 `tests/test_builtin_skills_injection.py`、相关 AGENTS 模板测试和 skill validate。

## 6. 风险与回滚

| 风险 | 缓解 | 回滚 |
| --- | --- | --- |
| 旧会话仍显示旧名 | 需要重启/重新同步 AGENTS，旧 JSONL instructions 不会自动更新 | 恢复目录和 frontmatter 名称 |
| 文档历史引用混杂 | 只更新现行契约，历史文档保留当时事实 | 无需回滚 |
| 打包漏文件 | package-data 与 MANIFEST 使用通配符，目录改名仍覆盖；测试验证 | 恢复旧目录 |

## 7. 开发记录与验证

### 7.1 TDD 红灯

| 测试 | 红灯结果 |
| --- | --- |
| `tests/test_builtin_skills_injection.py::test_html_visual_skill_pack_exists_and_is_packaged` | 失败：`vibego_cli/data/skills/vibe-diagram/SKILL.md` 不存在，证明旧目录仍是长名。 |
| `tests/test_agents_template_migration.py::test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks` | 失败：`AGENTS-template.md` 仍期待/包含旧长名引用。 |

### 7.2 实现内容

| 文件/目录 | 修改 |
| --- | --- |
| `vibego_cli/data/skills/vibe-diagram/` | 由 `vibe-diagram-communication` 重命名为短名 `vibe-diagram`。 |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | frontmatter 改为 `name: vibe-diagram`。 |
| `AGENTS-template.md` | 默认触发文案改为 `vibe-diagram skill`。 |
| `AGENTS.md` | 事实表证据路径与 skill 名称改为 `vibe-diagram`。 |
| `tests/test_builtin_skills_injection.py` | 路径、frontmatter、AGENTS 注入标题断言改为短名。 |
| `tests/test_agents_template_migration.py` | 模板断言改为短名。 |

### 7.3 验证结果

| 命令 | 结果 |
| --- | --- |
| `BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py::test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks tests/test_agents_template_migration.py::test_enforced_notice_keeps_user_requirement_header tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt` | 通过：`6 passed in 0.07s`。 |

### 7.4 待补充

- 需要重启/重新同步 worker 后，新 AGENTS 注入标题才会从 `vibe-diagram-communication` 变成 `vibe-diagram`。

### 7.4 最终验证补充

| 命令 | 结果 |
| --- | --- |
| `BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py::test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks tests/test_agents_template_migration.py::test_enforced_notice_keeps_user_requirement_header tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt` | 通过：`6 passed in 0.07s`。 |
| `python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | 通过：`Skill is valid!`。 |
| 旧名残留检查（AGENTS-template、AGENTS、vibego_cli/data/skills、tests、scripts、pyproject、MANIFEST） | `old_name_hits=[]`；新目录存在，旧目录不存在。 |

### 7.5 完成状态

- [x] skill 目录改为 `vibe-diagram`。
- [x] frontmatter 改为 `name: vibe-diagram`。
- [x] AGENTS 模板现行引用改为 `vibe-diagram`。
- [x] 测试与事实表同步短名。
- [x] 聚焦测试与 skill 校验通过。
