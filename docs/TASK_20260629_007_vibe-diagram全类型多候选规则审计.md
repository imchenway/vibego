# TASK_20260629_007 vibe-diagram 全类型多候选规则审计

## 1. 背景

用户要求审查今天更新的 `vibe-diagram` skill 内容：判断哪些规则需要对所有生图类型生效，并逐个图型检查、优化 skill 内容。

本轮审计聚焦今日新增/强化过的规则：HTML-only 交付、关键细节外显、文本压缩与 SVG 换行、页面设计稿 3-5 候选、Web/移动候选排版。

## 2. 审计结论

| 今日规则 | 是否全类型生效 | 处理结论 |
| --- | --- | --- |
| HTML-only 交付信封 | 是 | 已有全局规则，无需改动。 |
| 关键细节外显，弹窗不承载唯一信息源 | 是 | 已有全局规则，无需改动。 |
| HTML 图交付后聊天短回复 | 是 | 已有全局规则，无需改动。 |
| SVG 文本换行/HTML 节点优先 | 是 | 已有全局规则，无需改动。 |
| 页面设计稿默认 3-5 候选 | 否 | 只适用于页面设计稿方向评审，不能误套故障排查、系统架构等类型。 |
| Web 端候选纵向、移动稿横向 filmstrip | 部分 | 提升为“多方案 / 多候选表达规则”：凡是出现多个候选都要按对象选择排版，但各图型候选数量和触发条件不同。 |

## 3. 逐类型优化口径

| 生图类型 | 默认是否多候选 | 多候选边界 |
| --- | --- | --- |
| 系统架构图 | 否 | 用户要求架构方案对比时才输出 2-3 个候选架构。 |
| 业务架构图 / 领域地图 | 否 | 仅用于领域边界、能力分层或角色协作方案选择。 |
| 业务流程图 | 否 | 多候选流程用 A/B/C、泳道或阶段对照，不画方案说明卡。 |
| 代码时序图 | 否 | 仅用于调用策略、事务边界、重试/异步策略对比。 |
| 状态 / 数据模型图 | 否 | 仅用于状态机、实体边界、索引或迁移策略对比。 |
| 故障排查图 | 否 | 不生成 3-5 个修法设计稿；多假设进入假设裁决。 |
| 页面设计稿 | 是 | 方向评审默认 3 个，可扩展到 4-5 个。 |
| 技术设计图 | 视场景 | 方案对比保持 2-4 个，覆盖最小/推荐/行业最佳或保守回滚。 |
| 需求 / 决策沟通图 | 视场景 | 方案对比保持 2-4 个，必须展示推荐项、反例、AC 与未决问题。 |

## 4. 实施内容

| 路径 | 修改 |
| --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 新增 `## 多方案 / 多候选表达规则`，逐图型声明多候选触发条件、数量边界、排版规则和低质量候选删除原则。 |
| `tests/test_builtin_skills_injection.py` | 新增全类型多候选规则测试，并扩展同步注入测试，保证 AGENTS 内置 skill 同步后也包含该规则。 |
| `AGENTS.md` | 补充 Facts Table 证据，记录多方案/多候选全类型适用边界。 |
| `docs/TASK_20260629_007_vibe-diagram全类型多候选规则审计.md` | 记录审计、实现、验证和影响范围。 |
| `docs/TASK_20260629_007_vibe-diagram全类型多候选规则审计.html` | 单文件 HTML 交付图，展示全类型规则矩阵和验证闭环。 |

## 5. TDD 记录

| 阶段 | 命令 | 结果 |
| --- | --- | --- |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k multi_candidate` | `1 failed, 21 deselected`，失败点为缺少 `## 多方案 / 多候选表达规则`。 |
| RED 扩展 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k 'multi_candidate or sync_agents_block'` | `2 failed, 20 deselected`，确认 skill 原文与同步注入均缺规则。 |
| GREEN 聚焦 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k 'multi_candidate or sync_agents_block'` | `2 passed, 20 deselected`。 |
| 测试语法 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | 通过，无输出。 |
| skill 结构校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!`。 |
| 完整内置 skill 回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `22 passed`。 |
| HTML 静态自检 | `python3 - <<'PY' ... HTMLParser ... PY` | `HTML self-check passed`。 |

## 6. 风险与边界

- 风险：规则继续增长，skill 变长。缓解：本次新增集中在一个全局章节，减少各图型重复描述。
- 风险：模型误以为所有图型都要多候选。缓解：明确“3-5 只适用于页面设计稿方向评审”，其他图型默认单主图。
- 风险：移动端横向 filmstrip 被误用为唯一阅读路径。缓解：新增“真实移动端不得把横向滚动作为唯一阅读路径”。

## 7. 当前状态

- [x] 已完成只读审计。
- [x] 已写 RED 测试并确认失败原因。
- [x] 已优化 SKILL 与 AGENTS 证据。
- [x] 已完成 GREEN 回归与 HTML 交付图。

## 8. 影响范围

- 影响：后续所有图型一旦进入多方案/多候选表达，都必须遵守候选数量、布局、推荐理由和回滚成本规则。
- 不影响：页面设计稿 3-5 默认候选仍只限方向评审；故障排查图不会因为本规则变成多个修法设计稿。
- 待执行：如需当前运行中的 vibego worker 立即生效，需要同步 AGENTS/Skills 并重启相关 worker。
