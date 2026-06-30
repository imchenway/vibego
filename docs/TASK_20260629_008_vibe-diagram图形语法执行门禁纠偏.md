# TASK_20260629_008_vibe-diagram 图形语法执行门禁纠偏

## 1. 需求背景

用户指出：当前 `vibe-diagram` 的图虽然已经避免了部分卡片堆叠，但仍然“不够直观”，根因不是缺少摘要，而是**没有严格执行每类图自己的图形语法
**。流程图和时序图之所以好懂，是因为它们天然有清晰的层级、时间、箭头和递进关系；HTML 图必须把这些关系先画出来，而不是把文字平铺到多个容器中。

本轮用户进一步要求逐个定义：

- 各种图应该长成什么样。
- 如何充分利用页面宽度和高度。
- 如何确定节点和箭头位置。
- 如何避免节点互相覆盖。
- 如何避免节点内文字溢出。

## 2. 证据与输入

### 2.1 当前仓库入口

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：内置 `vibe-diagram` skill 主协议。
- `tests/test_builtin_skills_injection.py`：内置 skill 包发布、规则内容、AGENTS 注入同步测试。
- `scripts/models/common.sh`：`sync_agents_block` 会把 `vibego_cli/data/skills/*/SKILL.md` 注入 AGENTS 受管块。
- `AGENTS.md`：Strict Evidence Mode 要求仓库事实附路径和锚点。

### 2.2 用户提到的 Codex 会话证据

用户给出的会话 ID：`019f135c-036a-7ab0-baae-629954482c61`。

本轮未暴露 `read_thread` 工具；已通过本地 session 文件定位到：

`/Users/david/.codex/sessions/2026/06/29/rollout-2026-06-29T20-30-25-019f135c-036a-7ab0-baae-629954482c61.jsonl`

关键证据：

- JSONL line 131：用户质疑“你管这叫代码时序图？”说明此前图命名为时序图，但形态不是时序图。
- JSONL line 136：修正口径变为“参与者列 + 时间自上而下 + 每一步调用/返回/分支/源码锚点”。
- JSONL line 177：最终用户要求后，图调整为“宽版 SVG 时序画布、箭头连到参与者生命线中心、注释节点独立占位、页面宽度上限扩到
  1600px、关键节点矩形无重叠”。

该证据证明：**时序图不能只说明调用关系，必须长成真正的时序图；箭头锚点、生命线、宽度利用、注释占位和防重叠都属于生图类型契约。
**

## 3. 根因判断

旧规则已经有“卡片不是图”“默认北向南”等约束，但仍不足以稳定产出直观图，根因有三点：

1. **按类型定义不够具象**：只说“系统架构图/时序图/流程图”覆盖什么内容，没有说它必须长成什么形态。
2. **布局算法门禁缺失**：没有明确“先分配主轴和泳道、再放节点、最后连线”，导致边写边排，容易文字平铺。
3. **几何约束缺失**：箭头锚点、防重叠、文本换行、防裁切没有形成交付前硬门禁。

## 4. 本轮落地方案

### 4.1 新增“各图型形态与布局契约”

在 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 中新增逐图型契约：

| 图型           | 必须长成                     |
|--------------|--------------------------|
| 系统架构图        | 北向南分层拓扑                  |
| 业务架构图 / 领域地图 | 能力层 + 对象关系 + 规则约束        |
| 业务流程图        | BPMN-light 流程            |
| 代码时序图        | 参与者列 + 时间自上而下            |
| 状态 / 数据模型图   | 状态机、ER-lite 或生命周期        |
| 故障排查图        | 因果链、流程化对照或排障时序图          |
| 页面设计稿        | 页面线框 / artboard          |
| 技术设计图        | 模块 / 契约 / 数据 / 发布回滚落地设计图 |
| 需求 / 决策沟通图   | 决策树或方案矩阵与主路径绑定           |

核心约束：如果无法按对应形态画出主谓宾关系，必须换图型，不能保留错误图型再靠标题和长说明补救。

### 4.2 新增“布局、箭头与防重叠算法门禁”

新增规则：

1. 画布先分配主轴和泳道，再放节点，最后连线。
2. 宽度用于承载泳道、参与者列、before/after 或局部对照；高度用于承载时间、阶段、因果递进和证据展开。
3. 节点先排版后连线；同层节点等高或按内容自适应后统一留白。
4. 箭头只能连接节点边缘锚点；代码时序图消息箭头连接参与者生命线中心或消息端点。
5. 连线层低于节点层，箭头不得穿过正文、标题、图例或交互区。
6. 节点正文使用 HTML/CSS 可换行容器，不得固定高度裁切文字。
7. 桌面宽度和 390px 宽度都必须检查；任一节点重叠、线穿字、文字溢出都必须重排。

## 5. TDD 与验证

新增测试：

- `test_vibe_diagram_diagram_type_shape_contracts_are_explicit`
- `test_vibe_diagram_layout_arrow_and_collision_rules_are_explicit`
- 扩展 `test_sync_agents_block_embeds_builtin_vibe_diagram_skill`

RED 结果：新增 3 个测试先失败，失败点均为规则缺失。

GREEN 后需验证：

```bash
python3.11 -m pytest -q tests/test_builtin_skills_injection.py -k 'diagram_type_shape_contracts or layout_arrow_and_collision or sync_agents_block'
python3.11 -m pytest -q tests/test_builtin_skills_injection.py
python3.11 -m py_compile tests/test_builtin_skills_injection.py
/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram
```

## 6. 影响范围

- `vibego_cli/data/skills/vibe-diagram/SKILL.md`：新增全图型形态契约与布局/箭头/防重叠算法门禁。
- `tests/test_builtin_skills_injection.py`：新增与扩展规则测试。
- `AGENTS.md`：补充事实表，记录全图型形态、布局与防重叠门禁。
- `docs/TASK_20260629_008_vibe-diagram图形语法执行门禁纠偏.md` 与对应 HTML：沉淀本轮决策、证据和验收。

## 7. 回滚方式

如发现规则过严导致某些简图表达成本过高，可回滚本轮新增的两个 skill 章节与对应测试；但不建议回滚到“只禁止卡片堆叠”的旧口径，因为用户已明确指出问题是缺少层级递进和图形逻辑。

## 8. 本轮验证结果（2026-06-30）

已执行：

```bash
python3.11 -m pytest -q tests/test_builtin_skills_injection.py
# 24 passed in 0.06s

python3.11 -m py_compile tests/test_builtin_skills_injection.py
# exit 0

/opt/homebrew/bin/python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram
# Skill is valid!

python3.11 - <<'PY'
# HTMLParser 校验 docs/TASK_20260629_008_vibe-diagram图形语法执行门禁纠偏.html
PY
# html ok ... 11617
```

未执行：未启动 vibego 服务；本轮为内置 skill 文档、测试与任务文档更新，不需要重启服务即可检查文件内容。若要让已安装的 pipx
版本立即使用新 skill，需要重新安装/升级包或运行项目对应同步流程。
