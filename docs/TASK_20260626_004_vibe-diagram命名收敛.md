# TASK_20260626_004_vibe-diagram 命名收敛

## 任务背景

用户确认内置图形表达 skill 不应继续使用 `html-visual` 这类以交付介质命名的前缀，最终定名为 `vibe-diagram`。

根本动机：skill 名称应表达“图解 / 画图 / 可视化沟通”的用户意图，同时保留 Vibego 专属内置协议辨识度；HTML 只是当前默认交付介质，不应继续出现在 skill 名称里。

## 现状证据

- `vibego_cli/data/skills/html-visual/SKILL.md`：旧 skill 名称为 `html-visual`。
- `AGENTS-template.md`：HTML 图形沟通默认协议引用旧名 `html-visual`。
- `tests/test_builtin_skills_injection.py`：内置 skill 包装与同步测试引用旧目录与旧 frontmatter。
- `tests/test_agents_template_migration.py`：模板迁移测试引用旧名。

## 受影响目录

- `vibego_cli/data/skills/vibe-diagram/`：内置 skill 目录与 frontmatter 名称。
- `AGENTS-template.md`：全局模板中默认触发的 skill 名称。
- `AGENTS.md`：当前仓库事实表与同步后内置 skill 名称证据。
- `tests/test_builtin_skills_injection.py`：内置 skill 资源、注入与协议内容回归。
- `tests/test_agents_template_migration.py`：模板默认触发名称回归。
- `docs/`：记录本次命名决策、TDD 证据与验证结果。

## 不受影响边界

- `bot.py` 的 Telegram 来源上下文注入逻辑不随本次命名变化；它描述的是交付端来源，不依赖 skill 目录名。
- `pyproject.toml` 与 `MANIFEST.in` 使用 `data/skills/*` 通配，不需要为目录改名调整。
- Telegram 自动发送 HTML 附件链路不受影响；它识别最终回复中的 `.html/.htm` 文件引用，不识别 skill 名称。

## 契约变更

| 项目 | 变更前 | 变更后 |
| --- | --- | --- |
| skill 目录 | `vibego_cli/data/skills/html-visual/` | `vibego_cli/data/skills/vibe-diagram/` |
| frontmatter name | `html-visual` | `vibe-diagram` |
| AGENTS 模板触发名 | `html-visual` | `vibe-diagram` |
| 同步后内置 skill 标题 | `## Skill: html-visual` | `## Skill: vibe-diagram` |
| 交付介质 | 单文件 HTML | 保持不变，名称不再绑定 HTML |

## 测试矩阵

| 用例 | 目的 | 结果 |
| --- | --- | --- |
| RED：`test_vibe_diagram_skill_pack_exists_and_is_packaged` | 新测试期望 `vibe-diagram` 目录与 frontmatter，旧实现应失败 | 已失败，FileNotFoundError 指向旧实现缺新目录 |
| RED：`test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks` | 模板仍引用旧名时应失败 | 已失败，断言找不到 `vibe-diagram` |
| GREEN：上述两条聚焦测试 | 验证目录、frontmatter、模板引用已更新 | 通过 |
| `tests/test_builtin_skills_injection.py` 全文件 | 验证内置 skill 注入、协议内容、故障图规则 | 通过 |
| skill validator | 验证 `vibe-diagram` skill 格式有效 | 通过 |
| 残留扫描 | 防止核心实现、模板、测试中遗留旧名 | `old_name_hits=[]` |

## 实施顺序

1. 先更新测试期望为 `vibe-diagram`，运行聚焦测试得到 RED。
2. 将 `vibego_cli/data/skills/html-visual/` 重命名为 `vibego_cli/data/skills/vibe-diagram/`。
3. 更新 `SKILL.md` frontmatter、AGENTS 模板、同步后 AGENTS 证据、测试锚点。
4. 更新 OpenAI skill agent 元信息展示名，避免继续把 skill 名称视觉上绑定 HTML。
5. 运行聚焦测试、skill validator 与旧名残留扫描。

## 风险与回滚

- 风险：已有外部文档或旧会话还引用旧名，短期内可能出现认知差异。
  - 缓解：当前 AGENTS 模板、同步后 AGENTS、测试与内置资源已统一为 `vibe-diagram`。
- 风险：历史 docs 中仍可能保留旧名称作为历史记录。
  - 缓解：残留扫描聚焦模板、实现、测试、脚本与打包入口；历史任务文档不作为运行契约。
- 回滚：如需回滚，只需将目录和 frontmatter 改回旧名，并同步恢复模板与测试期望；打包通配无需改动。

## 验证记录

```bash
BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_builtin_skills_injection.py::test_vibe_diagram_skill_pack_exists_and_is_packaged tests/test_agents_template_migration.py::test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks
# RED：2 failed，符合预期

BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_builtin_skills_injection.py::test_vibe_diagram_skill_pack_exists_and_is_packaged tests/test_agents_template_migration.py::test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks
# GREEN：2 passed

BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py::test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks tests/test_agents_template_migration.py::test_enforced_notice_keeps_user_requirement_header tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt
# 6 passed

python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram
# Skill is valid!

python3 - <<'PY'
from pathlib import Path
paths=[Path('AGENTS-template.md'), Path('AGENTS.md'), Path('vibego_cli/data/skills'), Path('tests'), Path('scripts'), Path('pyproject.toml'), Path('MANIFEST.in'), Path('bot.py')]
old_hits=[]
new_hits=[]
for p in paths:
    files=[p] if p.is_file() else [x for x in p.rglob('*') if x.is_file() and '__pycache__' not in x.parts]
    for f in files:
        try:
            text=f.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        if 'html-visual' in text or 'html-visual-communication' in text:
            old_hits.append(str(f))
        if 'vibe-diagram' in text:
            new_hits.append(str(f))
print('old_name_hits=', old_hits)
print('new_name_hits=', new_hits)
print('new_dir_exists=', Path('vibego_cli/data/skills/vibe-diagram').is_dir())
print('old_html_visual_exists=', Path('vibego_cli/data/skills/html-visual').is_dir())
print('old_html_visual_communication_exists=', Path('vibego_cli/data/skills/html-visual-communication').is_dir())
PY
# old_name_hits=[]; new_dir_exists=True; old dirs=False
```

## 追加验证记录

```bash
BOT_TOKEN=baseline-token MASTER_CHAT_ID=1 MASTER_USER_ID=1 python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py::test_agents_template_prefers_html_visual_communication_for_non_trivial_tasks tests/test_agents_template_migration.py::test_enforced_notice_keeps_user_requirement_header tests/test_agents_template_migration.py::test_enforced_notice_adds_user_requirement_header_before_prompt tests/test_task_description.py::test_prepend_enforced_agents_notice_describes_telegram_html_delivery tests/test_task_description.py::test_prepend_enforced_agents_notice_cases tests/test_plan_progress.py::test_deliver_pending_messages_sends_project_local_html_as_document_after_text tests/test_plan_progress.py::test_collect_model_response_local_documents_accepts_file_uri_link
# 22 passed

python3.11 -m py_compile bot.py
# exit 0

python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram
# Skill is valid!
```
