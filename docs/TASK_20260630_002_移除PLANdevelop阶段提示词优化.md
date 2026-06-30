# TASK_20260630_002 移除 PLAN/develop 阶段后的提示词优化方案

## 1. 结论

可以移除显式 `PLAN / develop` 阶段名称，但不建议移除“意图门禁”。

推荐方向：把全局提示词从“大而全流程说明”改成“极短路由内核”，只负责：

1. 安全边界。
2. 仓库规约读取。
3. skill 路由。
4. 产物交付边界。
5. 验证与收尾格式。

具体流程、图形、前端设计、TDD、排障、验收，全部下沉到 `superpowers`、`vibe-diagram`、`frontend-skill`、`Impeccable` 等 skill。

## 2. 为什么不能直接删干净

如果只删 `PLAN / develop`，又不补一个轻量路由内核，会出现几个风险：

- 模型可能不主动加载 `superpowers:brainstorming`，导致未评审就实现。
- `vibe-diagram` 不一定在“非明确要求画图”的设计/排障任务中触发。
- 前端任务可能只做功能可用，不会自动进入 `frontend-skill + Impeccable + accessibility`。
- `superpowers` 中个别默认动作可能和你的长期偏好冲突，例如自动 git commit，所以仍需要全局硬边界兜底。
- docs 沉淀、HTML 附件、最终回复字段等项目协作规则不属于单个 skill，仍需要全局保留。

因此，目标不是“全局提示词归零”，而是“全局只管路由和不可让渡的硬边界”。

## 3. 建议保留在全局提示词的内容

### 3.1 不可下沉的硬边界

- 禁止自行执行 git commit/push/merge/revert 等修改历史或远端的动作，除非用户要求。
- 进入仓库任务前读取当前目录及上级/相关项目的 `AGENTS.md`、`PROJECT-STYLE.md`、`CODE-GUIDELINES.md`、`DESIGN.md`。
- 不确定、证据冲突、缺少关键上下文时，必须标注“待确认/推断”，不能装成事实。
- 不能声称完成、修复、验证通过，除非执行过对应验证命令并看到成功结果。
- 非琐碎任务需要写入 `docs/` 任务文档；重要设计/排障/交付优先用单文件 HTML 承载。
- 最终回复保留项目固定收尾字段。

### 3.2 轻量 skill 路由

全局只写“何时加载哪个 skill”，不要重复 skill 里的流程细节。

建议路由：

- 创意、需求、行为变更、方案设计：`superpowers:brainstorming`。
- 复杂实现计划：`superpowers:writing-plans`。
- 代码实现 / Bug 修复：`superpowers:test-driven-development`。
- Bug / 异常 / 测试失败：`superpowers:systematic-debugging`。
- 收尾前：`superpowers:verification-before-completion`。
- 系统/业务/流程/时序/状态/排障/设计沟通图：`vibe-diagram`。
- 前端页面、组件、样式、交互：`frontend-skill` + `impeccable` + `accessibility`。
- 高级视觉/动效：按需加载 `premium-frontend-ui`、`gsap-framer-scroll-animation`。

### 3.3 不再使用阶段名，但保留意图门禁

建议不用 `PLAN / develop` 两个字，而改成：

- 需要设计判断时：先澄清目标、给方案、让用户确认。
- 用户已明确“按推荐做 / 开始修复 / 直接实现”或已有确认文档时：可以进入实现。
- 实现必须有测试或可验证证据。
- 实现后必须回到总结、验证与下一步，不连续无限改。

这样保留了质量控制，但不会和 Codex 自带 Plan Mode 或 Telegram 的手动 PLAN 按钮互相打架。

## 4. 建议删除或下沉的内容

| 现有内容                | 建议                                                                 |
|---------------------|--------------------------------------------------------------------|
| 大段 PLAN 阶段说明        | 删除，交给 `brainstorming` / `writing-plans`                            |
| 大段 develop 阶段说明     | 删除，交给 `test-driven-development` / `verification-before-completion` |
| vibe-diagram 详细制图规则 | 保留在 `vibe-diagram/SKILL.md`，AGENTS 只保留触发条件                         |
| 前端页面设计长规则           | 下沉到 `frontend-skill`、`impeccable`、`accessibility`                  |
| 具体流程图、标题、箭头、样式门禁    | 只放 skill，不放全局                                                      |
| 重复的“少废话/先结论”表达规则    | 保留极简版即可                                                            |

## 5. 推荐全局提示词骨架草案

> 这是建议骨架，不是最终文件。后续如果你确认，我可以按这个骨架重写 AGENTS-template 并补测试。

```md
# Global Agent Kernel

## Hard boundaries
- 不要自行执行 git commit/push/merge/revert 等修改历史或远端动作，除非用户明确要求。
- 仓库任务开始前读取当前目录及相关上级/子项目的 AGENTS.md、PROJECT-STYLE.md、CODE-GUIDELINES.md、DESIGN.md。
- 仓库事实必须有文件路径和锚点；证据不足写“待确认/推断”。
- 不要声称完成、修复或验证通过，除非已执行对应验证并看到成功结果。
- 需要用户决策时，给编号选项并标出推荐项。

## Skill routing
- 需求、方案、行为变更、复杂设计：使用 superpowers:brainstorming。
- 复杂实现计划：使用 superpowers:writing-plans。
- Bug、异常、测试失败：使用 superpowers:systematic-debugging。
- 代码实现或修复：使用 superpowers:test-driven-development。
- 收尾声明前：使用 superpowers:verification-before-completion。
- 系统/业务/流程/时序/状态/故障/技术设计/需求决策等视觉沟通：使用 vibe-diagram。
- 前端页面、组件、布局、样式、交互：使用 frontend-skill + impeccable + accessibility；高级视觉或动效按需加载 premium-frontend-ui / gsap-framer-scroll-animation。

## Work contract
- 先理解目标和现状，再选择技能；不要把需求清单直接变成实现。
- 用户已确认方案或明确说“按推荐做/开始修复/直接实现”时，可以进入实现。
- 非琐碎任务把调研、设计、实现和验证沉淀到 docs/TASK_*.md；需要图形沟通时交付单文件 HTML。
- 代码修改只改源码、测试、docs 和必要规约文件；保持仓库整洁，不留临时产物。

## Reply contract
- 默认先结论，少噪音。
- 生成 HTML 后聊天只给链接/路径、验证摘要和下一步。
- 最后一条回复包含：任务编码、任务名称、本次使用的 skill、本次修改影响点、待重启服务或待执行脚本。
```

## 6. 推荐迁移步骤

1. 先把当前 AGENTS-template 中 `PLAN 阶段`、`develop 阶段` 的长段落删除或迁出到自定义 skill。
2. 保留一个短的 `Global Agent Kernel`。
3. 确认 `vibe-diagram`、`frontend-skill`、`impeccable`、`accessibility` 的触发规则足够完整。
4. 更新测试：确保 AGENTS 注入后仍包含硬边界、skill 路由、HTML 交付、最终字段。
5. 用 3 类真实任务回归：Bug 排查、功能迭代、前端页面设计。

## 7. 推荐决策

推荐选择：**B. 去阶段名，保留路由内核**。

不推荐彻底清空全局提示词；因为全局提示词是“最后兜底”，负责保护 git、证据、docs、验证和交付边界，而 skills 更适合承载具体做法。

## 8. 2026-06-30 实施记录

### 8.1 已按 B 方案落地

- 已将仓库根目录 `AGENTS-template.md` 改写为 `Global Agent Kernel`。
- 已同步更新当前运行配置源 `/Users/david/.config/vibego/agents/current/AGENTS-template.md`，避免 repo 模板和实际同步源漂移。
- 已删除模板中的显式 `## plan 阶段`、`## develop 阶段` 与 `PLAN-> develop` 阶段门禁文案。
- 已保留并压缩以下兜底能力：硬边界、skill routing、工作契约、视觉/前端契约、回复契约。
- 已把 `AGENTS.md` Facts Table 更新为新现实：AGENTS 只做触发和硬边界，vibe-diagram / frontend / superpowers 承担具体流程。

### 8.2 测试变更

- `tests/test_agents_template_migration.py` 新增并更新断言：
    - 模板必须包含 `# Global Agent Kernel` 与 `## Skill routing`。
    - 模板必须包含 superpowers / vibe-diagram / frontend-skill / impeccable / accessibility 的路由。
    - 模板不得再包含 `## plan 阶段`、`## develop 阶段`、`PLAN-> develop`、`vibe -> design -> develop`。
    - HTML 图和 HTML-only 只在 AGENTS 保留触发边界，具体规则下沉到 skill。

### 8.3 验证记录

- RED：`python3.11 -m pytest -q tests/test_agents_template_migration.py`，新增 kernel 断言在旧模板下失败，证明测试能捕捉旧阶段提示词。
- GREEN：`python3.11 -m pytest -q tests/test_agents_template_migration.py`，9 passed。

### 8.4 剩余注意

- 如果已有 vibego worker/master 正在运行，需要重启或重新执行 AGENTS 同步流程后，新的模板才会被运行中的模型会话使用。
- 本次只移除全局模板中的阶段化长提示词；Telegram 的手动 PLAN/MODE 相关产品能力与历史 PlanConfirm 链路未在本轮删除。
- 已执行同步：`~/.config/vibego/AGENTS.md` 与 `~/.codex/AGENTS.md` 的 vibego 受管区块已更新为新 kernel。
