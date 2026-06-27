# TASK_20260627_001 vibe-diagram 图形语法质量门禁

## 1. 背景与目标

用户反馈：当前 `vibe-diagram` 生成的故障排查图与需求决策图，截图上看主要是多个圆角卡片、表格和方案块的堆叠；即使有少量箭头，也只是把 Markdown 大纲换成卡片展示，没有达到“一图胜千言”。

本次目标：把 `vibe-diagram` 的通用制图契约从“有节点、有标题、有箭头”升级为“先选视觉语法，再用空间关系表达含义”。所有图型都必须避免退化成等权重卡片列表。

## 2. 现象、影响、根因、修法、验证

### 2.1 现象

- 用户给出的 `TASK_20260627_001` 需求评估图：顶部是 3 个大卡片，下面是 5 个步骤卡片、4 个风险卡片、测试矩阵表和 3 个方案卡片。
- 用户给出的 Zeus 故障排查图：顶部是 7 个卡片串联，下面是证据阶梯卡片、假设裁决卡片和 4 个方案卡片。

### 2.2 影响

- 图的主信息不是关系，而是文本分类；截图阅读时无法快速看出“谁影响谁、什么导致什么、哪里是根因”。
- 故障排查场景中，证据、假设、修法被拆成等权重区域，用户需要重新阅读文字而不是看图理解链路。
- 需求决策场景中，方案与测试矩阵没有被绑定到影响路径，容易变成“卡片版 Markdown”。

### 2.3 根因

当前规则虽已有“只保留一张主图”“避免卡片堆叠”等提示，但缺少可执行的硬门禁：

1. 没要求在生成前先选择具体视觉语法（拓扑、泳道、时序轴、状态机、ER、因果链、决策树等）。
2. 没明确彻底禁止卡片式布局，仍可能把卡片当成节点或布局单元。
3. 没有“去掉箭头/坐标/泳道/分层后是否仍是同一堆文字卡片”的失败判定。
4. 故障排查图虽强调故事线，但没有要求证据、假设、行动锚定到故事线节点，导致它们被画成独立卡片区。

证据锚点：`vibego_cli/data/skills/vibe-diagram/SKILL.md`（锚点：`## 通用视觉规则`、`## 故障排查图规则`）；`tests/test_builtin_skills_injection.py`（锚点：`test_vibe_diagram_fault_diagram_rules_prioritize_storyline`）。

### 2.4 修法

新增 `## 图形语法硬约束`，覆盖所有图型：

- 卡片堆积不是图。
- 生成 HTML 前必须先选定一种视觉语法。
- 全局禁止卡片式布局，不得把卡片作为节点或布局单元。
- 主画布必须占据首屏主要面积。
- 辅助信息不得与主图同等视觉重量。
- 去掉箭头、坐标轴、泳道、分层、包含关系或状态转换线后，若仍是一组同等权重文字卡片，必须重画。

同时强化故障排查图：证据、假设、行动必须锚定到故障故事线节点，禁止各自另起一排等权重卡片。

### 2.5 验证

- RED：新增测试 `test_vibe_diagram_rules_reject_card_pile_across_all_diagram_types` 后，修改前失败，失败点为缺少“卡片堆积不是图”。
- GREEN：更新 `SKILL.md` 后，该聚焦测试通过。

## 3. 受影响目录与边界

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 新增所有图型通用的图形语法硬约束，并强化故障排查图的证据/假设/行动锚定规则。 |
| `tests/test_builtin_skills_injection.py` | 是 | 新增回归断言，防止后续退化为卡片堆；同步 AGENTS 注入也检查新规则。 |
| `docs/TASK_20260627_001_vibe-diagram图形语法质量门禁.md` | 是 | 记录现象、根因、契约、测试矩阵、风险与回滚。 |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 用单文件 HTML 图展示本次故障链路与修法闭环。 |
| `AGENTS.md` | 是 | 更新 Facts Table 中 `HTML 图形沟通默认协议` 事实锚点。 |
| `bot.py` / Telegram 发送链路 | 否 | 本次只改内置 skill 文本，不改变 HTML 文件收集、发送和回传逻辑。 |
| `scripts/models/common.sh` | 否 | 同步机制不变；新规则仍通过现有 `sync_agents_block` 注入。 |
| DB / SQLite / 配置 | 否 | 无表结构、状态文件或配置项变更。 |
| 构建 / CI / 依赖 | 否 | 不新增依赖，不改构建链。 |

## 4. 契约变更

### 4.1 修改前

`vibe-diagram` 对图形质量的约束偏向视觉排版：无遮挡、不溢出、箭头短、标题克制；对“什么才算图”的判定不足。

### 4.2 修改后

所有图型新增硬门禁：

1. 先选视觉语法，再写 HTML。
2. 主画布表达关系结构，辅助信息服务主图。
3. 卡片只能作为图中节点，不允许卡片网格替代图。
4. 若剥离连线/坐标/泳道/分层/状态线后仍能原样阅读，说明不是图，必须重画。
5. 故障排查图中的证据、假设、行动要锚定到故事线，不得并排堆块。

## 5. 测试矩阵

| 阶段 | 命令 | 预期 / 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `3 passed` | 修改前现有内置 skill 测试通过。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k reject_card_pile` | `1 failed` | 新增测试确认当前缺少“卡片堆积不是图”门禁。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k reject_card_pile` | `1 passed, 3 deselected` | 新规则写入后聚焦测试通过。 |
| 聚焦回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `4 passed` | 覆盖打包与 AGENTS 同步注入。 |
| 测试语法检查 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | 通过，无输出 | 确认新增测试文件语法有效。 |
| skill 校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` | 验证 skill 结构有效。 |
| 模板协议聚焦 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` | 验证 AGENTS 模板仍按非琐碎任务触发 HTML 图。 |
| 模板协议无环境变量尝试 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `SystemExit: BOT_TOKEN 未配置` | 该测试模块导入 `bot.py` 需要 BOT_TOKEN；已用 dummy token 重跑通过。 |
| HTML 自检 | `/opt/homebrew/bin/python3.11 - <<'PY' ... HTMLParser ... PY` | `HTML self-check passed` | 确认说明图为单文件 HTML、无外链且可解析。 |

## 6. 实施顺序

1. 读取 `AGENTS.md`、`vibe-diagram/SKILL.md`、内置 skill 测试与历史故障图重设计文档。
2. 视觉检查用户提供的两张截图，确认问题不是单个图型，而是“卡片网格替代图形语法”。
3. 先写失败测试，锁定通用硬门禁与故障排查锚定规则。
4. 最小更新 `vibe-diagram/SKILL.md`，不改发送链路、不新增依赖。
5. 更新 docs 与 AGENTS 证据。
6. 跑聚焦测试、skill 校验，记录未覆盖风险。

## 7. 风险与回滚

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 规则过强导致模型不知道如何落图 | 生成耗时变长或需要重排 | 给出可选视觉语法清单，模型先选一种即可。 |
| 辅助表格被完全禁止 | 复杂测试矩阵无处放 | 允许下方轻量表格，但不得与主图同等视觉重量。 |
| 旧 worker 仍使用旧 skill | Telegram 侧短期仍可能输出卡片堆 | 重新同步 AGENTS 或重启 worker 后生效。 |
| 只改文档无法 100% 约束模型 | 仍可能偶发退化 | 用测试锁定指令文本；后续若仍复发，可继续引入示例模板或自动 lint。 |

回滚方式：恢复 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 中 `## 图形语法硬约束` 及故障排查锚定新增句，移除 `tests/test_builtin_skills_injection.py` 中新增断言，并删除本任务文档与 HTML 说明图。

## 8. 当前完成状态

- [x] 已读取相关规约、skill、测试与历史文档。
- [x] 已确认用户截图中的核心缺陷是“等权重卡片堆替代图形语法”。
- [x] Baseline 已执行。
- [x] RED 已确认。
- [x] `SKILL.md` 已完成最小修正。
- [x] 聚焦回归已通过：`4 passed`。
- [x] skill 结构校验已通过：`Skill is valid!`。
- [x] AGENTS 证据已更新。

## 9. 第 2 轮修复：HTML 纵向画布、根因/修法高亮与前后对照

### 9.1 用户新增反馈

用户指出：HTML 宽度不友好，但长度可以近似无限；这意味着 `vibe-diagram` 不应按“横向海报”把所有信息压进一屏，而应默认采用可纵向滚动的卷轴式主线。

用户进一步确认：对比迭代功能开发设计和故障修复时，根因与计划修复方式应成为高亮节点；当改动较大时，直接用前后两图表达旧逻辑与新逻辑。

### 9.2 契约补充

新增两类通用规则：

1. **HTML 画布方向规则**
   - 宽度服务阅读，长度服务推理。
   - 默认采用正常页面宽度 + 北向南主线 + 局部横向关系。
   - 禁止为了塞满首屏而横向卡片化。
   - 首屏只放结论、图例和主路径起点，后续纵向展开阶段、证据、分支、矩阵、风险和回滚。
   - 桌面可左右对照；移动端或内容复杂时自动改为上下对照。

2. **根因、修法与前后对照规则**
   - 根因和修复方案不是说明文字，必须成为主图中的视觉焦点。
   - 默认使用单图高亮根因与修法。
   - 当修复改变两处以上关键节点，或改变调用链、数据流、状态流转、接口契约、权限边界、用户主路径之一时，必须改用前后对照图。
   - 前后对照图必须标出保留、删除、新增、风险和回滚点。

### 9.3 第 2 轮 TDD 验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `4 passed` | 第二轮修改前现有测试通过。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k vertical_canvas` | `1 failed` | 新测试确认当前缺少“宽度服务阅读，长度服务推理”等规则。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k vertical_canvas` | `1 passed, 4 deselected` | 写入新规则后聚焦测试通过。 |

### 9.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 新增 `HTML 画布方向规则` 与 `根因、修法与前后对照规则`。 |
| `tests/test_builtin_skills_injection.py` | 是 | 新增 `test_vibe_diagram_rules_prefer_vertical_canvas_and_highlight_change_focus`。 |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 从横向链路改为纵向卷轴示意图，根因与修法作为高亮节点。 |
| `AGENTS.md` | 是 | 更新 Facts Table，补充纵向画布与高亮/前后对照契约。 |

### 9.5 状态

- [x] 已补 RED 测试并看到预期失败。
- [x] 已完成 `SKILL.md` 最小修正。
- [x] 已更新 HTML 说明图为纵向卷轴版本。
- [x] 最终聚焦回归与 skill 校验已通过。


### 9.6 第 2 轮最终验证结果

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 内置 skill 聚焦回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `5 passed` |
| AGENTS 模板 HTML 协议聚焦 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` |
| skill 结构校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` |
| 测试语法检查 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | 通过，无输出 |
| HTML 自检 | `/opt/homebrew/bin/python3.11 - <<'PY' ... HTMLParser ... PY` | `HTML self-check passed` |
| in-app browser 可视检查 | Codex in-app browser reload 当前 `file://` 页 | 未覆盖：Browser use URL policy 阻止读取 `file://` 页面；未绕过，已用静态 HTML 自检替代。 |

## 10. 第 3 轮修复：before / after 方向稳定性

### 10.1 用户新增反馈

用户指出：一般来说，左侧都是 before，右侧都是 after。上一版 HTML 虽然底部对照区是 before/after，但主故事线采用左右交替排布，修法节点可能出现在左侧，容易破坏“左 before、右 after”的阅读惯例。

结论：上一版图不够对。纵向卷轴不等于左右交替；如果图中存在前后对照语义，必须稳定保持 before 左/上、after 右/下。

### 10.2 契约补充

新增前后对照方向硬规则：

1. before 固定在左侧或上方。
2. after 固定在右侧或下方。
3. 禁止用左右交替排布表达前后差异。
4. 纵向卷轴中也必须保持左侧为当前/故障逻辑，右侧为修复后/目标逻辑。
5. 如果节点不属于前后对照，只能放在中轴、旁注或详情中。

### 10.3 第 3 轮 TDD 验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `5 passed` | 第三轮修改前现有测试通过。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k before_after_direction` | `1 failed` | 新测试确认当前缺少 before/after 固定方向规则。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k before_after_direction` | `1 passed, 5 deselected` | 写入新规则后聚焦测试通过。 |

### 10.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 在 `根因、修法与前后对照规则` 中补充 before/after 方向稳定性。 |
| `tests/test_builtin_skills_injection.py` | 是 | 新增 `test_vibe_diagram_before_after_direction_must_be_stable`，并同步检查 AGENTS 注入。 |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 重新生成稳定双栏：左 before，右 after；移动端上 before，下 after。 |
| `AGENTS.md` | 是 | 更新 Facts Table 中 HTML 图形沟通默认协议事实。 |

### 10.5 状态

- [x] 已确认上一版主故事线左右交替会误导 before/after 语义。
- [x] 已补 RED 测试并看到预期失败。
- [x] 已完成 `SKILL.md` 最小修正。
- [x] 已重新生成 HTML 为左 before、右 after 的稳定双栏纵向卷轴。
- [x] 最终聚焦回归与 HTML 自检已通过。


### 10.6 第 3 轮最终验证结果

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 内置 skill 聚焦回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `6 passed` |
| AGENTS 模板 HTML 协议聚焦 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` |
| skill 结构校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` |
| 测试语法检查 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | 通过，无输出 |
| HTML 自检 | `/opt/homebrew/bin/python3.11 - <<'PY' ... HTMLParser ... PY` | `HTML self-check passed` |
| diff 空白检查 | `git diff --check` | 通过，无输出 |

## 11. 第 4 轮修复：before/after 只是容器，主画布必须是真流程图

### 11.1 用户新增反馈

用户指出：虽然可以视情况分左右布局，但该有的流程图方式仍然要有。对故障排查、开发迭代这类任务，当前触发生效的仍像卡片堆砌，不是在画流程图。

结论：前后对照方向正确只是基础；如果 before/after 每侧内部仍然只是说明卡片，就没有解决“不是图”的问题。必须补充流程图语法门禁。

### 11.2 契约补充

新增 `流程图语法门禁`：

1. 前后对照只是容器，不是图形语法本身。
2. 功能迭代、开发设计和故障修复必须优先画流程图或流程化对照图。
3. 主画布必须包含开始/结束事件、活动节点、决策菱形、带标签箭头。
4. before/after 每一侧内部也必须是流程图。
5. 禁止把 before/after 列画成普通说明卡片列表。
6. 根因节点和修法节点必须落在流程路径上。
7. 辅助证据只能作为流程节点的证据锚点、旁注或点击详情。

### 11.3 第 4 轮 TDD 验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `6 passed` | 第四轮修改前现有测试通过。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k flowchart_grammar` | `1 failed` | 新测试确认当前缺少流程图语法门禁。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k flowchart_grammar` | `1 passed, 6 deselected` | 写入新规则后聚焦测试通过。 |

### 11.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 新增 `流程图语法门禁`。 |
| `tests/test_builtin_skills_injection.py` | 是 | 新增 `test_vibe_diagram_flowchart_grammar_required_for_fault_and_iteration`。 |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 重生成 HTML：左 before 右 after，但每侧内部是真流程图。 |
| `AGENTS.md` | 是 | 更新 Facts Table，补充流程图语法门禁。 |

### 11.5 状态

- [x] 已确认问题：before/after 布局正确不等于流程图正确。
- [x] 已补 RED 测试并看到预期失败。
- [x] 已完成 `SKILL.md` 最小修正。
- [x] 已重生成 HTML 为流程化前后对照图。
- [x] 最终聚焦回归与 HTML 自检已通过。


### 11.6 第 4 轮最终验证结果

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 内置 skill 聚焦回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `7 passed` |
| AGENTS 模板 HTML 协议聚焦 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` |
| skill 结构校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` |
| 测试语法检查 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | 通过，无输出 |
| HTML 自检 | `/opt/homebrew/bin/python3.11 - <<'PY' ... HTMLParser ... PY` | `HTML self-check passed` |
| diff 空白检查 | `git diff --check` | 通过，无输出 |

## 12. 第 5 轮历史修复：全局禁卡片布局与默认方向收敛（已被第 19 轮“卡片限用”替换）

### 12.1 用户新增反馈

用户指出：应该全局禁止卡片式布局，不允许使用卡片；也不需要通过卡片限制宽度，正常使用页面宽度即可，依靠浏览器自动缩放和响应式能力适配。用户强调图最好北向南，或使用左上角向右下角的时序图；完全从左到右只适合很短的流程图。

结论：上一版虽然补了流程图语法，但仍保留了卡片节点退路，并且还在表达固定宽度倾向。这与用户目标冲突，需要把“禁卡片”和“默认方向”升成硬规则。

### 12.2 契约补充

新增 / 修正规则：

> 历史说明：以下“全局禁卡片”规则是第 5 轮阶段性口径，已在第 19 轮按用户反馈调整为“卡片不是全局禁用，但必须限用”。当前生效口径以第 19 轮和 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 为准。

1. 全局禁止使用卡片式布局。
2. 禁止使用卡片作为默认节点或布局单元；节点必须使用流程图、架构图、时序图、状态图或数据图的标准图形形态。
3. 不要通过卡片限制宽度，也不要为了制造视觉边界而额外包卡片。
4. 正常使用页面宽度，依靠浏览器缩放和响应式重排适配。
5. 默认优先北向南。
6. 调用先后关系可采用左上角向右下角的时序图或斜向流程。
7. 完全从左到右只适合很短流程；超过 5 个主节点不得继续横向铺开。

### 12.3 第 5 轮 TDD 验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `7 passed` | 第五轮修改前现有测试通过。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k "reject_card_pile or direction_defaults"` | `2 failed` | 新测试确认当前缺少全局禁卡片与默认方向规则。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k "reject_card_pile or direction_defaults or vertical_canvas"` | `3 passed, 5 deselected` | 写入新规则后聚焦测试通过。 |

### 12.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 强化 `图形语法硬约束` 与 `HTML 画布方向规则`，移除“卡片作为节点外形”的退路。 |
| `tests/test_builtin_skills_injection.py` | 是 | 更新卡片相关断言，新增 `test_vibe_diagram_direction_defaults_to_north_south_or_diagonal`。 |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 重生成 HTML：使用正常页面宽度的 SVG 北向南流程图，不再用卡片容器限制宽度。 |
| `AGENTS.md` | 是 | 更新 Facts Table，补充全局禁卡片与默认方向契约。 |

### 12.5 状态

- [x] 已确认旧规则仍给“卡片作为节点外形”留退路。
- [x] 已补 RED 测试并看到预期失败。
- [x] 已完成 `SKILL.md` 最小修正。
- [x] 已重生成 HTML 为无卡片容器的 SVG 北向南流程图。
- [x] 最终聚焦回归、HTML 自检与旧模式反向搜索已通过。


### 12.6 第 5 轮最终验证结果

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 内置 skill 聚焦回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `8 passed` |
| AGENTS 模板 HTML 协议聚焦 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` |
| skill 结构校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` |
| 测试语法检查 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | 通过，无输出 |
| HTML 自检 | `/opt/homebrew/bin/python3.11 - <<'PY' ... HTMLParser ... PY` | `HTML self-check passed` |
| 旧模式反向搜索 | 反向搜索旧退路关键词 | 无输出，生产规则、测试、AGENTS、任务文档和 HTML 中不再保留旧退路表述 |
| diff 空白检查 | `git diff --check` | 通过，无输出 |

## 13. 第 6 轮修复：视觉质量门禁与示例图重绘

### 13.1 用户新增反馈

用户指出当前重新生成的 HTML 图“太丑了”。

结论：上一轮已经把卡片堆改成了流程图形状，但视觉质量仍停留在低保真工程草图：硬边框明显、分区粗糙、文字密度高、弹窗重阴影、主路径缺少审美秩序。它虽然更像流程图，但仍不适合作为 `vibe-diagram` 的示例和默认审美锚点。

### 13.2 根因

1. 规则只约束“不能卡片堆”和“必须有流程图语法”，没有约束交付物的基础视觉质量。
2. 当前 HTML 示例为了证明不是卡片，过度使用硬线框和大面积边界，导致像调试草图而不是可交付图。
3. 节点文字和说明文字仍偏多，留白、线宽、字号、图例和层级没有形成统一系统。

### 13.3 契约补充

新增 `视觉质量门禁`：

1. 禁止交付原始工程草图感的 SVG。
2. 视觉质量必须服务流程阅读。
3. 使用统一的线宽、字号、留白、层级和图例。
4. 禁止粗暴边框、重阴影、满屏说明文字和低级默认样式。
5. 流程节点必须像图形符号而不是 UI 容器。
6. 主图应保留足够留白，文字短句化。
7. 颜色只用于状态和路径强调，不用于装饰。

### 13.4 第 6 轮 TDD 验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `8 passed` | 第六轮修改前现有测试通过。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k visual_quality` | `1 failed` | 新测试确认当前缺少“禁止交付原始工程草图感的 SVG”等视觉质量门禁。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k visual_quality` | `1 passed, 8 deselected` | 写入新规则后聚焦测试通过。 |
| 最终回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `9 passed` | 覆盖内置 skill 打包、规则文本与 AGENTS 同步注入。 |

### 13.5 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 新增 `视觉质量门禁`，避免后续生成粗糙工程草图式 SVG。 |
| `tests/test_builtin_skills_injection.py` | 是 | 新增 `test_vibe_diagram_visual_quality_rejects_raw_utilitarian_svg`，并同步 AGENTS 注入断言。 |
| `AGENTS.md` | 是 | 更新 Facts Table，补充视觉质量门禁锚点与测试锚点。 |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 将当前示例图重绘为更克制、统一、可读的工程蓝图式流程图。 |
| 运行链路 / Telegram 发送链路 | 否 | 不改变 HTML 文件收集、发送、附件回传逻辑。 |
| DB / 配置 / 构建依赖 | 否 | 无数据结构、配置项或依赖变更。 |

### 13.6 状态

- [x] 已确认“丑”的根因不是单个 CSS 值，而是缺少视觉质量门禁。
- [x] 已补 RED 测试并看到预期失败。
- [x] 已完成 `SKILL.md` 最小修正。
- [x] 已重绘 HTML 示例图，移除粗糙三角背景、重阴影和英文 card 类名。
- [x] 已执行最终聚焦回归、HTML 自检、浏览器渲染度量与旧模式反向搜索。


### 13.7 第 6 轮最终验证结果

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 内置 skill 聚焦回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `9 passed` |
| AGENTS 模板 HTML 协议聚焦 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` |
| skill 结构校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` |
| 测试语法检查 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | 通过，无输出 |
| HTML 静态自检 | `/opt/homebrew/bin/python3.11 - <<'PY' ... HTMLParser ... PY` | `HTML visual self-check passed` |
| 浏览器桌面渲染度量 | Playwright + 本机 Google Chrome，`1440x1400` | `hasHorizontalOverflow: false`，截图路径 `/tmp/vibe-diagram-visual-qa.png` |
| 浏览器移动端渲染度量 | Playwright + 本机 Google Chrome，`390x844` | `hasHorizontalOverflow: false` |
| 旧模式反向搜索 | 反向搜索旧退路与旧样式关键词 | 无输出 |
| diff 空白检查 | `git diff --check` | 通过，无输出 |

## 14. 第 7 轮修复：浅色背景从米黄纸张改为高级冷调浅色

### 14.1 用户新增反馈

用户指出当前 HTML 背景太黄，希望使用有质感的高级浅色系背景。

结论：示例图背景应服务流程阅读，而不是形成米黄纸张感。默认浅色背景应偏冷白、蓝灰、雾灰，保持低饱和与轻质感。

### 14.2 契约补充

`视觉质量门禁` 新增：浅色背景默认使用冷白、蓝灰、雾灰等低饱和高级浅色，避免米黄纸张感背景；背景只能提供质感和空间层次，不得抢主线。

### 14.3 TDD 与验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 failed` | 当前 skill 缺少高级浅色背景门禁。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 passed, 9 deselected` | 写入背景门禁后通过。 |
| HTML 背景静态检查 | 一次性 Python 检查 | `background check passed` | 确认旧暖黄 token 已移除，新冷调 token 已存在。 |
| HTML 解析检查 | `HTMLParser` | `HTML parse check passed` | 单文件 HTML 可解析且无外链。 |
| 浏览器渲染度量 | Playwright + 本机 Google Chrome，`1440x1400` 与 `390x844` | 两端 `hasHorizontalOverflow: false` | 生成截图 `/tmp/vibe-diagram-bg-polish.png`，确认无横向溢出。 |

### 14.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 背景从暖黄纸张感调整为冷白蓝灰高级浅色系。 |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 新增浅色背景门禁，避免后续生成偏黄背景。 |
| `tests/test_builtin_skills_injection.py` | 是 | 新增 `test_vibe_diagram_background_should_use_premium_light_surfaces`。 |
| `AGENTS.md` | 是 | 更新 Facts Table，补充背景门禁与测试锚点。 |
| 运行链路 / Telegram 发送链路 | 否 | 不改变附件发送与 HTML 收集逻辑。 |

## 15. 第 8 轮修复：背景改为白色主色，移除蓝底倾向

### 15.1 用户新增反馈

用户指出：不需要蓝色，应使用纯浅色，高级质感的白色为主色。

结论：上一轮“冷白蓝灰”仍有蓝色倾向，不符合当前审美方向。背景应改为以白色为主的近白 / 珍珠白 / 浅雾灰中性色，只保留极弱层次，不再使用蓝色底。

### 15.2 契约调整

`视觉质量门禁` 中背景规则调整为：浅色背景默认以白色为主色，使用近白、珍珠白、浅雾灰等低色相中性色；避免蓝色底、米黄纸张感背景；背景只能提供质感和空间层次，不得抢主线。

### 15.3 TDD 与验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `10 passed` | 修改前现有测试通过。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 failed` | 新口径要求“白色为主色”后，旧蓝灰规则失败。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 passed, 9 deselected` | 写入“白色背景不能是扁平纯白”后通过。 |

| 完整回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `10 passed` | 覆盖内置 skill 规则与 AGENTS 同步注入。 |
| 模板 HTML 协议 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` | AGENTS 模板触发协议仍有效。 |
| skill 校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` | skill 结构有效。 |
| HTML 最终检查 | 一次性 Python 检查 | `HTML final premium white check passed` | 确认非扁平纯白、无外链、无灰网格。 |

### 15.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 背景从蓝灰浅色改为中性白色主色；弱化默认蓝色中轴与箭头。 |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 背景门禁从“蓝灰/雾灰”改为“白色主色、避免蓝色底”。 |
| `tests/test_builtin_skills_injection.py` | 是 | 更新背景测试口径。 |
| `AGENTS.md` | 是 | 更新 Facts Table 背景门禁事实。 |

## 16. 第 9 轮修复：去掉灰底与灰色质感层

### 16.1 用户新增反馈

用户指出：不要灰。

结论：上一轮虽然已从蓝灰改为白色主色，但仍保留了中性灰背景层、灰网格、灰中轴与灰色差异箭头。当前口径应进一步收敛为纯白背景，背景不承担灰色质感，只保留流程本身的状态色与必要文字。

### 16.2 契约调整

`视觉质量门禁` 保持并强化为：浅色背景默认以白色为主色，使用近白、珍珠白、浅雾灰等低色相中性色；避免蓝色底、灰色底、米黄纸张感背景；背景只能提供质感和空间层次，不得抢主线。

### 16.3 TDD 与验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 failed` | 新口径要求“避免灰色底”后，旧 skill 文案失败。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 passed, 9 deselected` | 写入“避免蓝色底、灰色底、米黄纸张感背景”后通过。 |
| HTML 白底静态检查 | 一次性 Python 检查 | `pure white background check passed` | 确认 body、paper、panel 为白色，灰网格与可见灰中轴已移除。 |
| 浏览器渲染度量 | Playwright + 本机 Google Chrome，`1440x1400` 与 `390x844` | 两端 `hasHorizontalOverflow: false` | 生成截图 `/tmp/vibe-diagram-pure-white-bg.png`。 |

### 16.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 背景改为纯白；移除灰网格、灰中轴和灰色差异箭头。 |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 背景门禁补充“避免灰色底”。 |
| `tests/test_builtin_skills_injection.py` | 是 | 背景测试补充灰色底断言。 |
| `AGENTS.md` | 是 | Facts Table 同步灰色底禁用口径。 |

## 17. 第 10 轮修复：纯白改为白色系高级明度层次

### 17.1 用户新增反馈

用户指出：当前是纯白色，不够高级。

结论：上一轮去掉蓝、灰、黄后，背景变得干净但扁平。高级浅色不等于整片纯白，而应以白色为主，使用瓷白、珍珠白、雪白、雾白等极轻明度层次。背景仍不能变成蓝底、灰底或米黄纸张底。

### 17.2 契约调整

`视觉质量门禁` 背景规则调整为：浅色背景默认以白色为主色，但白色背景不能是扁平纯白；使用瓷白、珍珠白、雪白、雾白等白色系明度层次；避免蓝色底、灰色底、米黄纸张感背景；背景只能提供质感和空间层次，不得抢主线。

### 17.3 TDD 与验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 passed, 9 deselected` | 修改前背景测试仍只约束避免彩色底。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 failed` | 新增“白色背景不能是扁平纯白”后旧 skill 失败。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 passed, 9 deselected` | 写入“白色背景不能是扁平纯白”后通过。 |

### 17.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 页面和 SVG 主画布从扁平纯白改为白色系微明度层次。 |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 背景门禁补充“不能是扁平纯白”。 |
| `tests/test_builtin_skills_injection.py` | 是 | 背景测试补充白色层次断言。 |
| `AGENTS.md` | 是 | Facts Table 同步白色层次口径。 |

## 18. 第 11 轮修复：白色制图画布质感，不等同纯白

### 18.1 用户新增反馈

用户指出：背景仍不够高级；不是一定要纯白，例如常见制图背景可能使用白底网格，但这只是方向举例，不要求固定白底网格。

结论：上一轮把背景从彩色/灰/黄收敛到白色层次，但仍缺少“制图画布”的语义质感。高级背景应允许极轻白底工程网格、点阵或坐标纸肌理，但网格必须低对比、低存在感，不能变成灰底或花纹背景。

### 18.2 契约调整

`视觉质量门禁` 背景规则补充：允许使用极轻白底工程网格、点阵或坐标纸肌理，但网格必须低对比、低存在感；背景只能提供质感和空间层次，不得抢主线。

### 18.3 TDD 与验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 failed` | 新增“允许使用极轻白底工程网格、点阵或坐标纸肌理”断言后旧 skill 失败。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k premium_light` | `1 passed, 9 deselected` | 写入极轻白底工程网格/点阵/坐标纸肌理规则后通过。 |

| 完整回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `10 passed` | 覆盖内置 skill 规则与 AGENTS 同步注入。 |
| 模板 HTML 协议 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` | AGENTS 模板触发协议仍有效。 |
| skill 校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` | skill 结构有效。 |
| HTML 最终检查 | 一次性 Python 检查 | `HTML final diagram background check passed` | 确认白底制图网格存在且低对比、无外链。 |

### 18.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 在白色系主画布上加入低对比白底工程网格与大网格层次。 |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 背景门禁允许极轻制图肌理，但约束低对比、低存在感。 |
| `tests/test_builtin_skills_injection.py` | 是 | 背景测试补充制图肌理断言。 |
| `AGENTS.md` | 是 | Facts Table 同步制图肌理口径。 |

## 19. 第 12 轮修复：回到柔和卡片风格，但限制卡片滥用

### 19.1 用户新增反馈

用户指出：算了，还是回到最开始的样式；但要限制卡片滥用。

结论：前几轮把“卡片堆不是图”逐步收缩成“全局禁卡片”，这属于过度修正。用户真正反对的是卡片滥用，不是卡片本身。最初那种浅蓝白背景、圆角面板、柔和边框的风格可以保留，但必须让卡片服务流程、泳道、箭头、时序、层级或状态转换。

### 19.2 契约调整

`图形语法硬约束` 从“全局禁卡片”调整为“卡片限用”：

1. 卡片不是全局禁用，但必须限用。
2. 允许使用卡片承载摘要、节点、泳道单元或分组边界。
3. 禁止把卡片作为唯一图形语法。
4. 卡片必须服务箭头、泳道、坐标、层级、时序或状态转换。
5. 不能通过堆卡片来冒充一图胜千言。

### 19.3 TDD 与验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `10 passed` | 修改前现有测试通过。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k reject_card_pile` | `1 failed` | 新测试要求“卡片限用”后，旧“全局禁卡片”规则失败。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k reject_card_pile` | `1 passed, 9 deselected` | skill 从“全局禁卡片”改为“卡片限用”后通过。 |
| 完整回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `10 passed` | 覆盖内置 skill 规则与 AGENTS 同步注入。 |
| 模板 HTML 协议 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` | AGENTS 模板触发协议仍有效。 |
| skill 校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` | skill 结构有效。 |
| Python 编译 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | `passed` | 测试文件语法有效。 |
| HTML 静态检查 | 一次性 Python 检查 | `HTML card-limited diagram check passed` | 确认单文件 HTML 无外链，包含卡片限用、泳道网格、箭头、弹窗与可交互节点。 |
| 浏览器渲染度量 | Playwright + 本机 Google Chrome，`1440x1400` 与 `390x844` | 两端 `hasHorizontalOverflow: false` | 桌面 `nodeCount=12, arrowCount=9`；移动端无横向溢出，截图 `/tmp/vibe-diagram-soft-card-limited.png`。 |
| Diff 空白检查 | `git diff --check` | `passed` | 未发现尾随空格或空白错误。 |

### 19.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 从全局禁卡片改为卡片限用，强调关系语法优先。 |
| `tests/test_builtin_skills_injection.py` | 是 | 回归测试从“禁止卡片”改为“限制卡片滥用”。 |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 回到浅蓝白圆角面板风格，主图用卡片节点 + 泳道 + 箭头表达关系。 |
| `AGENTS.md` | 是 | Facts Table 同步卡片限用口径。 |

## 20. 第 13 轮修复：视觉风格可回退，前序制图要求不可回退

### 20.1 用户新增反馈

用户指出：关于制图的要求仍然要按前面提出的要求执行，前面提过的都要保留。

结论：第 12 轮“回到柔和卡片风格”只允许影响视觉外观，不能被理解为回退制图硬规则。当前必须把用户前序要求集中成不可回退门禁，避免后续 agent 只记住“柔和卡片风格”，忘记北向南、长度承载推理、根因/修法高亮、before/after 方向、流程图语法和背景统一等规则。

### 20.2 契约调整

新增 `累计用户约束不可回退`：

1. 视觉风格可以调整，但不得覆盖制图硬规则。
2. 柔和卡片风格只是一种视觉外观，不是图形语法豁免。
3. 前序用户约束必须同时满足：一图胜千言、卡片限用、HTML 长度承载推理、北向南或左上到右下、根因/修法/验证为焦点、before 左/上 after 右/下、故障排查和开发迭代必须有流程图语法、背景系统必须统一。

### 20.3 TDD 与验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| Baseline | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `10 passed` | 修改前现有测试通过。 |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k accumulated` | `1 failed` | 新增“前序约束不可回退”测试后，旧 skill 缺少集中门禁而失败。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k accumulated` | `1 passed, 10 deselected` | 写入累计用户约束后通过。 |
| 完整回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `11 passed` | 覆盖内置 skill 规则与 AGENTS 同步注入。 |
| 模板 HTML 协议 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` | AGENTS 模板触发协议仍有效。 |
| skill 校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` | skill 结构有效。 |
| Python 编译 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | `passed` | 测试文件语法有效。 |
| HTML 静态检查 | 一次性 Python 检查 | `HTML accumulated-requirements check passed` | 确认单文件 HTML 无外链，且示例中明确写入“前序约束不回退”。 |
| 浏览器渲染度量 | Playwright + 本机 Google Chrome，`1440x1400` 与 `390x844` | 两端 `hasHorizontalOverflow: false` | 桌面 `nodeCount=12, arrowCount=9, ruleCount=4`；截图 `/tmp/vibe-diagram-accumulated-requirements.png`。 |
| Diff 空白检查 | `git diff --check` | `passed` | 未发现尾随空格或空白错误。 |

### 20.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | 新增“累计用户约束不可回退”，明确视觉风格不能覆盖制图硬规则。 |
| `tests/test_builtin_skills_injection.py` | 是 | 新增回归测试锁定前序用户要求必须同时满足，并同步检查 AGENTS 注入内容。 |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 示例图保留柔和卡片风格，同时显式写入“前序约束不回退”。 |
| `AGENTS.md` | 是 | Facts Table 同步“视觉风格可以调整，但不得覆盖制图硬规则”。 |

## 21. 第 14 轮修复：示例 HTML 必须真画流程图，不再保留旧卡片网格

### 21.1 用户新增反馈

用户指出：新画的示例图仍然是老样子。

结论：第 13 轮虽然把“前序制图要求不回退”写入规则和文案，但示例 HTML 主画布仍是四列卡片网格，本质没有从“卡片表”变成“流程图”。这不是样式问题，而是示例图本身没有执行制图语法。

### 21.2 契约调整

示例 HTML 的主画布从旧卡片网格重画为 SVG 流程图：

1. 使用 `diagram-canvas` SVG 主画布，而不是 `flow-grid` 表格化卡片网格。
2. 主路径包含开始事件、活动节点、决策菱形、根因 R1、修法 F1、验证 V1、结束事件。
3. before 固定在左侧，after 固定在右侧。
4. 节点支持键盘聚焦和点击弹窗，弹窗只做补充，主结论仍在图上静态可读。
5. 新增静态测试，禁止示例 HTML 再出现旧卡片网格结构。

### 21.3 TDD 与验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k sample_html` | `1 failed` | 旧 HTML 缺少 `diagram-canvas`、SVG 流程符号，并仍是旧卡片网格。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k sample_html` | `1 passed, 11 deselected` | 重画为 SVG 流程图后通过。 |
| 完整回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `12 passed` | 覆盖内置 skill、示例 HTML 与 AGENTS 同步注入。 |
| 模板 HTML 协议 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` | AGENTS 模板触发协议仍有效。 |
| skill 校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` | skill 结构有效。 |
| Python 编译 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | `passed` | 测试文件语法有效。 |
| HTML 静态检查 | 一次性 Python 检查 | `HTML real-flowchart check passed` | 确认示例 HTML 有 SVG 流程符号，无旧卡片网格标记，无外链。 |
| 浏览器渲染度量 | Playwright + 本机 Google Chrome，`1440x1400` 与 `390x844` | 两端 `hasHorizontalOverflow: false` | 桌面 `svgCount=1, decisionCount=2, roleButtonCount=7, hasOldGrid=false`；截图 `/tmp/vibe-diagram-real-flowchart.png`。 |
| Diff 空白检查 | `git diff --check` | `passed` | 未发现尾随空格或空白错误。 |

### 21.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 从四列卡片网格改为 SVG 主流程图，保留柔和浅色风格。 |
| `tests/test_builtin_skills_injection.py` | 是 | 新增示例 HTML 静态回归，锁定真流程图语法。 |
| `AGENTS.md` | 是 | Facts Table 补充新测试锚点。 |

## 22. 第 15 轮改进：移动端不缩略宽 SVG，改为纵向可读流程

### 22.1 改进动机

当前桌面版 SVG 流程图已经解决“卡片网格不是图”的问题，但在 390px 移动端会把整张宽 SVG 等比缩小，虽然没有横向溢出，但节点文字会变小，仍不符合“HTML 宽度不友好、长度可承载推理”的要求。

结论：桌面保留完整 SVG 主图；移动端隐藏宽 SVG，显示一条纵向流程，利用页面长度承载推理。

### 22.2 契约调整

`HTML 画布方向规则` 补充：移动端不能把整张 SVG 等比缩成缩略图；若桌面主图依赖宽画布、左右旁路或大面积 SVG，移动端必须改为纵向流程、分段 SVG 或可读的阶段轨道，保持字号、节点和箭头可直接阅读。

### 22.3 TDD 与验证

| 阶段 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| RED | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k mobile_readable` | `1 failed` | 旧 skill 和 HTML 缺少移动端纵向流程门禁。 |
| GREEN | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k mobile_readable` | `1 passed, 12 deselected` | skill 补移动端规则，HTML 补 `mobile-flowchart` 后通过。 |
| 完整回归 | `/opt/homebrew/bin/python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | `13 passed` | 覆盖内置 skill、示例 HTML、移动端纵向流程与 AGENTS 同步注入。 |
| 模板 HTML 协议 | `BOT_TOKEN=123:ABC /opt/homebrew/bin/python3.11 -m pytest -q tests/test_agents_template_migration.py -k html_visual` | `1 passed, 7 deselected` | AGENTS 模板触发协议仍有效。 |
| skill 校验 | `/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | `Skill is valid!` | skill 结构有效。 |
| Python 编译 | `/opt/homebrew/bin/python3.11 -m py_compile tests/test_builtin_skills_injection.py` | `passed` | 测试文件语法有效。 |
| HTML 静态检查 | 一次性 Python 检查 | `HTML mobile-readable real-flowchart check passed` | 确认单文件 HTML 无外链、无旧卡片网格标记，包含桌面 SVG 和移动端纵向流程。 |
| 浏览器渲染度量 | Playwright + 本机 Google Chrome，`1440x1400` 与 `390x844` | 两端 `hasHorizontalOverflow: false` | 桌面 `canvasDisplay=block, mobileDisplay=none`；移动端 `canvasDisplay=none, mobileDisplay=block, mobileNodeCount=6`。 |
| Diff 空白检查 | `git diff --check` | `passed` | 未发现尾随空格或空白错误。 |

### 22.4 受影响范围补充

| 路径 | 是否影响 | 说明 |
| --- | --- | --- |
| `vibego_cli/data/skills/vibe-diagram/SKILL.md` | 是 | HTML 画布方向规则新增移动端禁止缩略整张 SVG。 |
| `docs/TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html` | 是 | 桌面保留 SVG 主图，移动端切换为纵向流程节点。 |
| `tests/test_builtin_skills_injection.py` | 是 | 新增移动端可读流程回归，并同步检查 AGENTS 注入。 |
| `AGENTS.md` | 是 | Facts Table 同步移动端可读规则与测试锚点。 |
