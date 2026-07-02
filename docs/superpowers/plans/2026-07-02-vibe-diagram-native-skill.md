# vibe-diagram Native Skill 发布 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `vibe-diagram` 从默认 AGENTS 索引注入迁移为 Codex native skill 安装，AGENTS 索引仅作为 legacy fallback。

**Architecture:** `agents-sync` 继续发布 override 资源副本，同时新增 native skill 目录发布；managed AGENTS block 默认只包含模板，不再拼接 `# Vibego 内置 Skills`。`scripts/models/common.sh` 保持 worker 启动兜底：默认安装 native skill，只有 `VIBEGO_AGENTS_LEGACY_SKILL_INDEX=1` 时写旧索引。

**Tech Stack:** Python 3.11、pytest、shell 内嵌 Python、文件原子替换。

---

### Task 1: Python agents-sync native skill 发布

**Files:**
- Modify: `tests/test_agents_sync.py`
- Modify: `vibego_cli/agents_sync.py`

- [x] Step 1: 写失败测试：默认同步后 target AGENTS 不含 skill 索引，`$HOME/.codex/skills/vibe-diagram/SKILL.md` 和 `$HOME/.agents/skills/vibe-diagram/SKILL.md` 存在。
- [x] Step 2: 写失败测试：设置 `VIBEGO_AGENTS_LEGACY_SKILL_INDEX=1` 后 target AGENTS 仍可生成旧索引。
- [x] Step 3: 运行聚焦测试确认失败。
- [x] Step 4: 新增 native skill target 解析、原子复制和 JSON 输出字段。
- [x] Step 5: `_render_managed_block()` 默认不拼索引；legacy 开关时拼接。
- [x] Step 6: 运行聚焦测试确认通过。

### Task 2: shell sync_agents_block native skill 发布

**Files:**
- Modify: `tests/test_builtin_skills_injection.py`
- Modify: `scripts/models/common.sh`

- [x] Step 1: 写失败测试：shell `sync_agents_block` 默认不写 `# Vibego 内置 Skills`，但会复制 native skill。
- [x] Step 2: 写失败测试：shell 设置 legacy 开关时仍写索引。
- [x] Step 3: 运行聚焦测试确认失败。
- [x] Step 4: 在 shell 内嵌 Python 中增加 native skill 复制和 legacy 条件拼接。
- [x] Step 5: 运行聚焦测试确认通过。

### Task 3: 文档、同步与验收

**Files:**
- Modify: `docs/TASK_20260701_014_vibe_diagram独立Skill发布方案.md`
- Modify/Create: `docs/TASK_20260701_014_vibe_diagram独立Skill发布方案.html`

- [x] Step 1: 更新任务文档，记录实现变更、测试、同步输出和未覆盖点。
- [x] Step 2: 执行 `python3.11 -m pytest -q tests/test_agents_sync.py tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py`。
- [x] Step 3: 执行 `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json`。
- [x] Step 4: 反查 `/Users/david/.codex/AGENTS.md` 默认无 `# Vibego 内置 Skills`，`/Users/david/.codex/skills/vibe-diagram/SKILL.md` 存在。
- [x] Step 5: 更新 HTML 交付页并静态检查。
