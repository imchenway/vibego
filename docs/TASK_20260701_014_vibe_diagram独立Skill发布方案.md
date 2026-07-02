# TASK_20260701_014 vibe-diagram 独立 Skill 发布方案

## 背景

用户要求回到“vibe-diagram 能否像 superpowers 一样作为独立 skill 发布、从而不依赖 AGENTS 内置索引”的话题。本任务已从方案设计推进到实现落地：默认改为 native skill 发布，AGENTS 索引仅保留 legacy fallback。

## 当前证据

- 当前 AGENTS 索引由 `vibego_cli/agents_sync.py` 生成：`render_builtin_skills()` 把 `# Vibego 内置 Skills`、`vibego-skill-source`、frontmatter `name/description` 写入 managed block；`_render_managed_block()` 会把该索引拼到模板后面。
- 启动脚本兼容链路也有同样逻辑：`scripts/models/common.sh` 的 `sync_agents_block()` 内嵌 `render_builtin_skills()`，用于写目标 AGENTS 文件。
- 当前 override 已经复制完整 skill 目录到 `/Users/david/.config/vibego/agents/current/vibego_cli/data/skills`，但这不是 Codex 原生 skill 目录。
- 测试目前保护索引存在：`tests/test_agents_sync.py` 要求目标 AGENTS 包含 `## Skill: vibe-diagram`；`tests/test_builtin_skills_injection.py` 要求同步块只注入索引、不常驻完整正文。
- 当前 Codex 运行时可发现的个人 skill 根目录包括 `/Users/david/.codex/skills` 和 `/Users/david/.agents/skills`（来自本会话 skill roots）。

## 推荐方案

推荐把 `vibe-diagram` 作为 native skill 安装到 Codex skill 根目录，同时保留 legacy AGENTS 索引开关：

1. `agents-sync` 默认复制 `vibego_cli/data/skills/vibe-diagram` 到 native skill 目录，例如 `/Users/david/.codex/skills/vibe-diagram`，并可选同步 `/Users/david/.agents/skills/vibe-diagram`。
2. 目标 AGENTS 默认不再生成 `# Vibego 内置 Skills` 索引，只保留全局硬边界与 HTML-first 合约。
3. 增加环境变量兜底：`VIBEGO_AGENTS_LEGACY_SKILL_INDEX=1` 时仍写旧索引，用于 Claude/Gemini 或不支持 native skill registry 的入口。
4. 保持 override 目录复制不变，作为 vibego 自己的稳定资源副本和 fallback source。
5. 更新测试：默认断言 AGENTS 无索引、native skill 文件存在；legacy 开关断言旧索引可恢复；shell 和 Python 两条同步链路都覆盖。

## 风险与待确认

- Codex native skill 目录可由当前会话 skill roots 推断，但不同运行环境可能不同；实现时应支持环境变量覆盖，例如 `VIBEGO_CODEX_SKILLS_DIR`。
- Claude/Gemini 是否有同等原生 skill 目录待确认；所以不建议立即删除 legacy fallback。
- 安装到用户级目录属于全局状态变更；实现时必须写测试并在同步输出中明确目标目录。

## 验收口径

- 默认同步后，目标 AGENTS 不含 `# Vibego 内置 Skills`、`vibego-skill-source`、`## Skill: vibe-diagram`。
- 默认同步后，native skill 目录含 `vibe-diagram/SKILL.md` 和 `references/*.md`。
- 设置 `VIBEGO_AGENTS_LEGACY_SKILL_INDEX=1` 后，旧索引仍能生成。
- `tests/test_agents_sync.py`、`tests/test_builtin_skills_injection.py`、`tests/test_agents_template_migration.py` 通过。
- 执行 `agents-sync` 后检查 `/Users/david/.codex/skills/vibe-diagram/SKILL.md` 存在；长期 worker 重启后再现场观察 Available skills 或触发行为。

## 实施记录：已改为 native skill 发布

用户确认“开始处理”后，本轮按上面的推荐方案完成实现：

- `vibego_cli/agents_sync.py`
  - 新增 native skill 发布状态结构与 JSON 输出字段 `native_skill_targets`。
  - 新增默认 native skill 目录解析：`~/.codex/skills` 与 `~/.agents/skills`，支持 `VIBEGO_CODEX_SKILLS_DIR` / `CODEX_SKILLS_DIR` / `VIBEGO_AGENTS_SKILLS_DIR` 覆盖。
  - 新增原子目录替换逻辑，把完整 `vibe-diagram/` 目录发布到 native skill 目录。
  - `_render_managed_block()` 默认不再拼接 `# Vibego 内置 Skills`；只有设置 `VIBEGO_AGENTS_LEGACY_SKILL_INDEX=1` 才写旧索引。
- `scripts/models/common.sh`
  - `sync_agents_block()` 默认发布 native skill，并默认不写 AGENTS skill 索引。
  - `VIBEGO_AGENTS_LEGACY_SKILL_INDEX=1` 时仍可输出旧索引，作为非 native 入口兜底。
- `tests/test_agents_sync.py`
  - 默认同步断言 native skill 目录存在、references 存在、目标 AGENTS 无内置 skill 索引。
  - legacy 开关断言旧索引仍可生成。
- `tests/test_builtin_skills_injection.py`
  - shell 同步链路同样覆盖默认 native install 与 legacy fallback。

## 实施验证

| 命令 | 结果 | 说明 |
|---|---|---|
| `python3.11 -m pytest -q tests/test_agents_sync.py::test_sync_agents_writes_override_and_targets tests/test_agents_sync.py::test_sync_agents_can_render_legacy_skill_index_when_enabled tests/test_builtin_skills_injection.py::test_sync_agents_block_installs_native_vibe_diagram_skill_without_default_index tests/test_builtin_skills_injection.py::test_sync_agents_block_can_emit_legacy_vibe_diagram_skill_index` | RED 后 GREEN，最终通过 | 先看到默认 native 行为失败，再实现到通过 |
| `python3.11 -m pytest -q tests/test_agents_sync.py tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py` | `82 passed in 1.00s` | 模板、skill、同步链路回归通过 |
| `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` | `ok: true`，4 个 AGENTS target updated，2 个 native skill target updated | 本机已同步 |
| `test -f /Users/david/.codex/skills/vibe-diagram/SKILL.md` | `codex_skill_ok` | Codex native skill 已安装 |
| `test -f /Users/david/.codex/skills/vibe-diagram/references/delivery-acceptance.md` | `codex_reference_ok` | references 已随 skill 安装 |
| `test -f /Users/david/.agents/skills/vibe-diagram/SKILL.md` | `agents_skill_ok` | `.agents` native skill 已安装 |
| `cmp -s vibego_cli/data/skills/vibe-diagram/SKILL.md /Users/david/.codex/skills/vibe-diagram/SKILL.md` | `codex_skill_cmp_ok` | native skill 与仓库一致 |
| 目标 AGENTS 反查 | `has_index=False`、`has_source=False`、`has_template=True` | Codex/Claude/Gemini/Vibego 目标都不再含内置 skill 索引 |

## 当前状态与用户动作

- 当前默认生成的 AGENTS 已不再包含 `# Vibego 内置 Skills`、`vibego-skill-source` 或 `## Skill: vibe-diagram`。
- `vibe-diagram` 已作为 native skill 安装到 `/Users/david/.codex/skills/vibe-diagram` 和 `/Users/david/.agents/skills/vibe-diagram`。
- 如果要让长期 worker 立即读取新 native skill registry，请重启对应 worker。
- 若某个非 native 入口仍需要旧索引，可设置 `VIBEGO_AGENTS_LEGACY_SKILL_INDEX=1` 后重新同步。

## 补充说明：可见性与后续更新

- 当前实现不是公开市场发布；只是把 `vibe-diagram` 安装到本机 native skill 目录：`/Users/david/.codex/skills/vibe-diagram` 与 `/Users/david/.agents/skills/vibe-diagram`。
- 其他用户或其他机器需要先更新 vibego 仓库/安装包，再执行 `agents-sync` 或重启 worker 触发同步，才会在自己的 native skill registry 里看到。
- 若要让别人“搜索可安装”，还需要把 `vibe-diagram` 作为 Codex skill/plugin 包发布到对应技能源或插件市场；本轮未做公开发布。
- 后续更新流程：修改 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 或 `references/*.md` → 跑相关测试 → 执行 `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` → 必要时重启长期 worker。

证据锚点：`vibego_cli/agents_sync.py`（`default_native_skill_targets`、`publish_native_skills`、`sync_agents`）、`scripts/models/common.sh`（`native_skill_targets`、`publish_native_skills`、`sync_agents_block`）。
