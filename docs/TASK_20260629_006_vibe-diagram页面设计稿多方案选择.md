# TASK_20260629_006 vibe-diagram 页面设计稿多方案选择

## 1. 任务背景

用户提出：`vibe-diagram` 中“页面设计稿”这个生图类型，不应只给单个设计稿，而应该做 3-5 个设计稿给用户选择。

根本动机：页面设计稿本质是“方向选择”而不是“既定实现说明”。如果只输出一个设计稿，用户很难判断这是唯一合理方案，还是模型默认路径；给出 3-5 个候选可以把视觉方向、信息架构、交互层级的取舍外显，降低后续返工。

## 2. 仓库现状证据

| 现状 | 证据 |
| --- | --- |
| `vibe-diagram` 是内置 skill，随包发布，并通过 AGENTS 同步注入全局规约。 | `vibego_cli/data/skills/vibe-diagram/SKILL.md`（锚点：`name: vibe-diagram`、`HTML 图形表达协议`）；`scripts/models/common.sh`（锚点：`render_builtin_skills`、`sync_agents_block`）；`tests/test_builtin_skills_injection.py`（锚点：`test_sync_agents_block_embeds_builtin_vibe_diagram_skill`） |
| 页面/设计稿请求会自动路由到“页面设计稿”。 | `vibego_cli/data/skills/vibe-diagram/SKILL.md`（锚点：`用户提到页面、设计稿、交互、布局、移动端、空/错/加载态`、`页面设计稿`） |
| 当前页面设计稿规则只要求先做 5 个判断，再输出服务单主路径的页面设计 HTML；没有要求输出多个候选方案。 | `vibego_cli/data/skills/vibe-diagram/SKILL.md`（锚点：`## 页面设计稿规则`） |
| 既有图形协议强调“只选一种主图型”“只保留一张主图”，因此多稿规则需要写成“同一个页面设计稿 HTML 内的候选 artboard”，而不是多个不相关图型或多个附件。 | `vibego_cli/data/skills/vibe-diagram/SKILL.md`（锚点：`先选一种主图型`、`只保留一张主图`、`页面只能服务一个主路径`） |

## 3. 关键设计判断

### 3.1 不建议无条件所有场景都强制 5 个

页面设计稿第一次用于方向评审时，应默认给多个候选；但以下场景不应机械扩展为 3-5 个：

1. 用户明确说“只要一个最终稿 / 按某个方向细化”。
2. 已经有上轮候选并已选定方向，本轮只是深化或修正。
3. 缺陷修复、最终交付验收、布局小修等目标不是方向选择。
4. 设计目标极窄，强行凑 5 个会制造低质量差异。

因此推荐规则是：**页面设计稿首次方向评审默认 3 个候选；需求开放、视觉方向不明确或用户明确要多方案时给 4-5 个；用户明确单稿或已定方向时给 1 个最终稿，并说明不再发散。**

### 3.2 与现有“一张主图”规则的兼容方式

- 不把 3-5 个候选理解为 3-5 个独立 HTML 文件。
- 仍输出一个单文件 HTML，顶部标题仍为 `页面设计稿：主题结论`。
- 主图型仍然只有“页面设计稿”；3-5 个候选是同一主图中的 A/B/C/D/E artboard。
- 每个候选必须围绕同一个用户目标和主路径，不允许把不同产品功能平铺成多个页面。
- 候选之间差异必须来自信息架构、视觉方向、首屏锚点、交互递进或响应式结构，而不是换颜色、换圆角这类表层皮肤。

## 4. 方案对比

| 方案 | 描述 | 优点 | 缺点 | 适用边界 |
| --- | --- | --- | --- | --- |
| A. 无条件输出 3-5 个设计稿 | 任何页面设计稿都生成多个候选。 | 最符合用户字面要求；选择感最强。 | 已定稿/小修场景会浪费；可能逼模型凑低质量方案；与最终交付场景冲突。 | 只适合早期探索，不适合所有页面任务。 |
| B. 首次方向评审默认 3，开放问题扩到 5，已定方向可单稿 | 将“多稿”绑定到方向选择场景。 | 兼顾选择空间与效率；不破坏现有单主路径、一张 HTML 规则；可测试。 | 规则文字稍长，需要明确例外。 | 推荐作为默认契约。 |
| C. 每次先问用户要几个 | 所有页面设计稿先澄清数量。 | 最大程度尊重用户选择。 | 频繁打断；违背“Default 模式优先合理推进”；Telegram 体验变慢。 | 仅在需求特别模糊或成本高时使用。 |

## 5. 推荐方案

推荐采用 **方案 B**。

拟修改 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 的 `## 页面设计稿规则`：

1. 页面设计稿用于方向评审时，默认在一个单文件 HTML 内提供 **3 个候选设计稿**，标记为 A/B/C；当用户明确要求多方案、视觉方向开放、或需要覆盖明显不同的信息架构时，可扩展到 **4-5 个**。
2. 每个候选必须包含：
   - `visual thesis`：视觉情绪、材质和能量。
   - `content plan`：首屏、支撑、细节、行动或任务闭环。
   - `interaction thesis`：2-3 个关键交互或动效方向，必须可降级。
   - 页面线框：首层展示、收纳区、主 CTA 或主操作。
   - 状态覆盖：默认、加载、空、错误态至少以缩略状态或旁注外显。
   - 响应式与可访问性：移动端阅读、键盘焦点、对比度、点击目标、非颜色状态表达。
3. 候选必须真正不同：至少在信息架构、首屏视觉锚点、交互递进、布局节奏中有一项本质差异；禁止只换颜色/阴影/圆角凑数量。
4. HTML 内必须提供候选对比矩阵和推荐项，说明每个候选的优点、缺点、适用边界和回滚/调整成本。
5. 用户明确指定“只要一个最终稿”、已有方向已确认、或本轮是缺陷修复/最终交付时，可输出单稿，但必须说明“本轮不发散候选”的理由。

## 6. 开发设计

### 6.1 受影响文件

| 路径 | 变更 |
| --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 强化 `## 页面设计稿规则`，新增 3-5 候选设计稿规则与例外。 |
| `tests/test_builtin_skills_injection.py` | 新增回归测试，锁定页面设计稿多候选、候选质量、单文件 HTML 和同步注入。 |
| `AGENTS.md` | 如实现阶段更新仓库 Facts Table，补充页面设计稿多候选契约证据。 |
| `docs/TASK_20260629_006_vibe-diagram页面设计稿多方案选择.md` | 记录本次 PLAN、方案对比、验收口径。 |
| `docs/TASK_20260629_006_vibe-diagram页面设计稿多方案选择.html` | 单文件 HTML 决策图。 |

### 6.2 测试建议

新增测试建议：

- `test_vibe_diagram_page_mockup_rules_offer_multiple_candidates_for_selection`
  - 断言 `页面设计稿规则` 中包含 `3-5 个候选设计稿`、`默认 3 个`、`A/B/C`、`4-5 个`。
  - 断言包含 `visual thesis`、`content plan`、`interaction thesis`。
  - 断言包含 `候选对比矩阵`、`推荐项`、`优点、缺点、适用边界`。
  - 断言包含 `禁止只换颜色/阴影/圆角凑数量`。
  - 断言包含 `用户明确只要一个最终稿` / `已确认方向` / `缺陷修复或最终交付` 的单稿例外。
- 扩展 `test_sync_agents_block_embeds_builtin_vibe_diagram_skill`
  - 断言同步后的 AGENTS 内置 skill 区块也含上述关键规则。

### 6.3 验证命令

```bash
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k page_mockup
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py
/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram
/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py
```

## 7. 验收标准（AC）

- AC1：页面设计稿规则明确“首次方向评审默认 3 个候选，开放场景可到 4-5 个”。
- AC2：每个候选必须有真实差异，不允许只换视觉皮肤凑数量。
- AC3：仍然只输出一个单文件 HTML，标题以 `页面设计稿：` 开头，候选以 A/B/C/D/E artboard 形式在同一页面内呈现。
- AC4：候选必须围绕同一个用户目标和主路径，不得把需求清单平铺成多个页面。
- AC5：HTML 必须有候选对比矩阵与推荐项，用户能直接选择方向。
- AC6：用户明确单稿、已定方向、缺陷修复或最终交付场景允许单稿，并写明不发散原因。
- AC7：回归测试覆盖 skill 原文与同步注入结果。

## 8. 风险与回滚

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 强制多稿导致低质量凑数 | 设计方向变浅，浪费用户时间 | 默认 3，只有开放场景扩到 5；要求本质差异。 |
| 与“一张主图”规则冲突 | 模型可能输出多个文件或多个不相关图 | 明确为单 HTML 内 A/B/C artboard，主图型仍是页面设计稿。 |
| 过度发散拖慢开发 | 已确认方向仍反复出方案 | 增加单稿例外。 |
| 只改 prompt 不能完全约束模型 | 仍可能偶发单稿 | 用测试锁定规则文本；后续可补样例模板。 |

回滚方式：恢复 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 中页面设计稿规则段落，移除新增测试断言与 AGENTS Facts 更新。

## 9. 当前状态

- [x] PLAN 阶段只读调研已完成。
- [x] 已形成推荐方案 B。
- [x] 已沉淀任务文档。
- [x] 用户确认按推荐方案进入 develop。
- [x] 已按 TDD 更新 `vibe-diagram` 页面设计稿规则与回归测试。

## 10. develop 实施记录

### 10.1 用户确认后的补充判断

用户确认按推荐方案推进，并补充关注多个设计稿在不同被设计对象下的排版：

- Web 端设计稿：纵向排列更友好，避免多个网页稿横向并排后被压成不可读缩略图。
- 移动端设计稿：可以考虑横版排列展示多个手机稿，便于比较首屏、导航与操作差异。

最终落地口径：

1. Web 端候选稿优先纵向排列，用页面长度保留真实首屏比例、滚动节奏和关键状态。
2. 移动端设计稿可以在桌面宽度下使用横向手机稿 filmstrip 对比。
3. 真实移动端查看 HTML 时不得依赖横向溢出理解候选；必须提供可访问的上一稿/下一稿、scroll-snap 或纵向降级，保证 390px 宽度下不点击、不横向拖动也能读到候选摘要、推荐项和关键差异。

### 10.2 修改内容

| 路径 | 修改 |
| --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 在 `## 页面设计稿规则` 中新增 3-5 候选设计稿、候选质量、Web/移动候选排版、对比矩阵、推荐项与单稿例外规则；同时修正既有 SVG 文本换行规则被断行导致测试断言失败的问题。 |
| `tests/test_builtin_skills_injection.py` | 新增 `test_vibe_diagram_page_mockup_rules_offer_selectable_candidates`，锁定页面设计稿多候选与桌面/移动排版规则。 |
| `AGENTS.md` | Facts Table 新增页面设计稿多候选方向评审契约与证据锚点。 |
| `docs/TASK_20260629_006_vibe-diagram页面设计稿多方案选择.md` | 记录实现、验证与剩余动作。 |

### 10.3 TDD 与验证记录

| 阶段 | 命令 | 结果 |
| --- | --- | --- |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k page_mockup` | `1 failed, 20 deselected`；失败点为缺少 `页面设计稿用于方向评审时，默认在一个单文件 HTML 内提供 3 个候选设计稿`。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k page_mockup` | `1 passed, 20 deselected`。 |
| 测试语法 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | 通过，无输出。 |
| 完整内置 skill 回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | 首次失败于既有 SVG 规则换行断言；修正后 `21 passed`。 |
| skill 结构校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!`。 |
| 契约锚点检查 | `python3 - <<'PY' ... contract anchors present ... PY` | `contract anchors present`。 |

### 10.4 影响与边界

- 影响：后续触发“页面设计稿”生图类型时，默认按选择型交付生成 3 个候选，开放场景可到 4-5 个。
- 不影响：系统架构、故障排查、技术设计、业务流程等其他生图类型规则；Telegram HTML 附件发送链路；构建依赖与数据库。
- 待执行：如需让已安装/运行中的 vibego worker 立即使用新规则，需要同步 AGENTS/Skills 到本机并重启相关 worker；源码测试已通过，但运行环境未在本轮自动同步。
