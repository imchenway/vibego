# TASK_20260701_002：vibe-diagram 候选全集与背景网格硬约束

## 1. 目标

按用户要求，将 `vibe-diagram` 临时切换为“候选全集校准模式”：每次命中某个生图类型时，HTML 内必须生成该类型的首选候选与全部备选候选，便于逐类比较默认首选。同时将背景系统收紧为固定白底工程纸网格，禁止点阵、柔光、彩色大面积背景和主画布背景割裂。

## 2. 实施范围

| 类型 | 文件 | 锚点 | 变更 |
|---|---|---|---|
| skill 内核 | `vibego_cli/data/skills/vibe-diagram/SKILL.md` | `## 候选全集校准模式` | 新增候选全集校准模式、11 类候选清单、HTML 结构与信息不足处理规则。 |
| skill 内核 | `vibego_cli/data/skills/vibe-diagram/SKILL.md` | `## 视觉、CSS 与可访问性规则` | 固定 `--paper` / `--grid-line` / `--grid-line-strong`，要求 24px 小网格 + 96px 大网格，禁止 `radial-gradient` 和大面积彩色背景。 |
| skill 内核 | `vibego_cli/data/skills/vibe-diagram/SKILL.md` | `## 输出前自检` | 增加候选全生成、固定 CSS token、禁用背景反查自检。 |
| 图型 reference | `vibego_cli/data/skills/vibe-diagram/references/*.md` | `## 候选全集清单` | 11 个 reference 均写入对应生图类型的首选候选与必生成备选候选。 |
| 测试 | `tests/test_builtin_skills_injection.py` | `CANDIDATE_ATLAS_EXPECTATIONS`、`test_vibe_diagram_candidate_atlas_mode_is_required_for_calibration`、`test_vibe_diagram_background_grid_contract_is_fixed_and_testable`、`test_task_20260701_fault_html_uses_candidate_atlas_and_fixed_grid` | 新增候选全集、reference 覆盖、背景硬约束、TASK_20260701_001 样例回归。 |
| 样例 HTML | `docs/TASK_20260701_001_Vibego启动失败Telegram连通性排查.html` | `candidate-atlas`、`candidate-sequence`、`candidate-causal-chain`、`candidate-bpmn`、`candidate-before-after`、`candidate-state-breakpoint` | 重画为故障排查图候选全集样例。 |
| 历史验收样例 | `docs/TASK_20260630_024_vibe-diagram交付验收图直观化.html` | CSS `:root` / `body` | 统一改为固定白底工程网格，避免旧测试继续固化柔光背景。 |

## 3. 候选全集口径

| 生图类型 | 首选候选 | 必生成备选候选 |
|---|---|---|
| 系统架构图 | 北向南分层拓扑 | 主请求中轴 + 控制/数据/兜底泳道；运行时依赖拓扑 |
| 业务架构 / 领域地图 | 能力层 + 领域对象关系图 | 参与方边界图；规则约束热区图；价值链地图 |
| 业务流程图 | BPMN-light 流程图 | 泳道流程图；阶段轨道图；异常分支流程图 |
| 代码时序图 | 参与者列 + 时间向下时序图 | 异步回调时序图；事务边界时序图；重试/异常返回时序图 |
| 状态 / 数据模型图 | 状态机图 | ER-lite；生命周期轨道；数据流图；状态-事件矩阵热区 |
| 故障排查图 | 排障时序图 | 因果链图；BPMN-light 排查流程；before/after 流程化对照；状态/数据断点图 |
| 功能迭代 / 开发设计图 | 当前流程 vs 目标流程的流程化对照 | current/target 技术时序；差异热区；发布回滚轨道 |
| 页面设计稿 | 页面线框 / artboard | 多候选 artboard filmstrip；响应式状态板；主路径页面流 |
| 技术设计图 | 模块 / 契约 / 数据 / 发布回滚拓扑 | API 契约泳道；数据流 + 一致性边界；发布切换轨道 |
| 需求 / 决策沟通图 | 决策树 | 方案矩阵 + 主路径绑定；取舍象限；推荐路径图 |
| 交付验收图 | 需求到证据的验收轨道 | R# 泳道验收看板；证据矩阵热区；地铁站点式验收线路 |

## 4. 背景硬约束

固定 CSS token：

```css
--paper: #fbfaf7;
--grid-line: rgba(24, 32, 28, 0.035);
--grid-line-strong: rgba(24, 32, 28, 0.055);
background-size: 24px 24px, 24px 24px, 96px 96px, 96px 96px;
```

禁止项：

- `radial-gradient` 点阵或柔光背景。
- 绿色、蓝色、灰色、米黄色大面积底。
- 大面积彩色 `linear-gradient`。
- body 一个背景、SVG/主画布另一个背景。
- 背景线穿过标题、节点正文、箭头标签。

## 5. 验证记录

| 命令 | 状态 | 结果 |
|---|---|---|
| `python3.11 -m pytest -q tests/test_builtin_skills_injection.py` | 通过 | `52 passed in 0.07s`。 |
| `python3.11 /Users/david/.codex/skills/.system/skill-creator/scripts/quick_validate.py vibego_cli/data/skills/vibe-diagram` | 通过 | `Skill is valid!`。 |
| `git diff --check` | 通过 | 无输出。 |

## 6. 风险与回滚

- 风险：校准期会让每个 HTML 变长；这是用户要求的临时对比模式。
- 风险：历史 docs 中仍可能保留旧视觉样例；本轮只重画用户指定的 `TASK_20260701_001`，并修正被测试固化的交付验收样例背景。
- 回滚：如用户确定每类默认首选，可将 `SKILL.md` 的 `## 候选全集校准模式` 改回“单首选 + 按需备选”，保留 reference 中的候选清单作为按需素材。

## 7. 待用户动作

- 需要重新同步 / 重启 Vibego worker，才能让活跃 AGENTS 注入新 `vibe-diagram` 规则。
