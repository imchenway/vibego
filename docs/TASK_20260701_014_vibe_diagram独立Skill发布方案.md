# TASK_20260701_014 vibe-diagram 独立 Skill 发布与触发规则收敛

## 背景

用户要求回到“vibe-diagram 能否像 superpowers 一样作为独立 skill 发布”的话题。前序工作已完成 native skill 同步、plugin 包和 repo marketplace 雏形；本轮继续按用户反馈收敛提示词，避免把普通问答、安装升级说明和每次追问都强制变成 HTML / vibe-diagram。

## 本轮目标

1. `vibe-diagram` 只在明确视觉化或复杂逻辑可视化时自动使用：画图、架构、流程、时序、状态、故障、页面设计、技术设计、需求决策、交付验收等。
2. 普通概念问答、安装升级说明、轻量决策、非视觉化追问默认简洁文本回答。
3. 顶部 tabs / `role=tablist` 只允许表示同一图型的候选布局 A/B/C；不得把追问、步骤、发布说明或章节导航追加成按钮。
4. 当前 `TASK_20260701_014` HTML 改为单页说明，保留安装与更新命令、当前结论和验证状态，不再堆叠问答按钮。

## 变更范围

- `AGENTS-template.md`
  - 删除“HTML-first 实质沟通 / 为什么 / 怎么做 / 所有实质沟通默认 HTML”的强制口径。
  - 新增边界：明确画图、图形化、HTML 图，或需要把复杂技术/业务逻辑、关系结构、状态流转可视化时才自动使用 `vibe-diagram`。
  - 普通问答、安装升级说明、轻量决策、非视觉化追问默认简洁文本。
- `vibego_cli/data/skills/vibe-diagram/SKILL.md`
  - frontmatter 去除 `HTML-first substantive answer`、`why/how explanations`、`delivery envelope`、`为什么`、`怎么做`、`实质沟通` 等过宽触发词。
  - 候选规则收敛为：tabs 只服务同一图型候选布局；单页结论使用普通标题、目录或章节。
- `vibego_cli/data/skills/vibe-diagram/references/delivery-acceptance.md`
  - 交付验收图仍可在“多个候选布局”场景使用候选按钮。
  - 明确禁止把用户追问、安装升级解释、发布说明或普通章节导航追加成新候选按钮。
- `plugins/vibe-diagram/skills/vibe-diagram/`
  - 与内置 skill 源保持一致，避免 plugin 分发内容漂移。
- `docs/TASK_20260701_014_vibe_diagram独立Skill发布方案.html`
  - 改为单页说明；不含 `role="tablist"`、`role="tab"`、`role="tabpanel"`。
- `README.md`
  - 新增 “Codex Skill / Plugin 与 vibe-diagram” 章节，写明 native skill 同步、repo marketplace plugin 安装/升级、触发边界和 tabs 按钮规则。
- 测试
  - 更新 `tests/test_agents_template_migration.py`、`tests/test_builtin_skills_injection.py`。
  - 扩展 `tests/test_vibe_diagram_plugin_distribution.py`，防止 README 漏写安装升级与触发边界，并继续防止 plugin skill tree 与内置源漂移。

## 当前安装与更新口径

本轮不撤销已有 marketplace/plugin 发布成果；只是修正提示词、skill 规则、测试和当前 HTML 表达方式。

```bash
# 添加 repo marketplace
codex plugin marketplace add /path/to/vibego

# 安装 plugin
codex plugin add vibe-diagram@vibego

# 刷新/升级 marketplace 后重新安装或启用
codex plugin marketplace upgrade vibego
codex plugin add vibe-diagram@vibego

# vibego 包级 native skill 同步
pipx upgrade vibego
vibego agents-sync --json
```

## 验收口径

- `AGENTS-template.md` 不再包含旧强制短语：`默认所有实质沟通都使用单文件 HTML`、`HTML-first 实质沟通`、`用户问“为什么 / 怎么做 / 需要怎么做”也属于实质沟通`、`几乎所有需要解释...都应优先触发 vibe-diagram`。
- `vibe-diagram/SKILL.md` frontmatter 不再包含过宽触发词，并包含 `complex logic` / `逻辑结构` 触发边界。
- `vibe-diagram` 候选规则明确：tabs / `role=tablist` 只能用于同一图型候选布局。
- 当前 TASK_014 HTML 是单页说明，不再有追问按钮导航。
- README 写明安装/升级命令、native skill 与 plugin 区别、触发边界和 tabs 规则。
- plugin skill tree 与内置 skill 源一致。

## 验证记录

| 命令 | 结果 | 说明 |
|---|---|---|
| `python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` | 内置 skill 合法 |
| `python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/vibe-diagram/skills/vibe-diagram` | `Skill is valid!` | plugin 内 skill 合法 |
| `python3.11 /Users/david/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/vibe-diagram` | `Plugin validation passed` | plugin manifest 与目录结构合法 |
| `python3.11 -m pytest -q tests/test_agents_template_migration.py tests/test_builtin_skills_injection.py tests/test_vibe_diagram_plugin_distribution.py` | `80 passed` | AGENTS 触发边界、README 说明、内置 skill 注入、plugin 分发一致性通过 |
| `python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` | `ok: true` | 4 个 AGENTS target updated，2 个 native skill target updated |
| `git diff --check` | 无输出 | 无空白/补丁格式问题 |
