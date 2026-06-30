# TASK_20260630_003 vibe-diagram 节点信息承载优化

## 1. 用户反馈

当前 `vibe-diagram` 生成图时，节点里经常只写 1-2 行信息，把证据、风险、测试、回滚等关键信息拆到底部证据卡片或矩阵里。结果读者需要在主路径节点和底部卡片之间反复跳转，反而增加阅读成本。

用户明确偏好：

- 关键信息应直接写在节点内。
- 节点可以限制宽度并自动换行。
- 节点高度不需要强行统一或压短，随内容增长即可。
- 底部证据/矩阵只适合跨节点汇总或原始长材料索引，不能替代节点正文。

## 2. 根因

原 skill 里多处规则虽然要求“关键细节静态可见”，但同时强调“文字短句化”“节点正文超过 3
行时转旁注/证据表”“证据阶梯”等，容易让模型为了视觉整齐把节点压成两行，再把真正要理解的信息拆到底部区域。

这会造成两个问题：

1. 主路径读不完整：节点只剩标题和状态，缺少判断依据。
2. 认知跳转变多：用户需要读节点、找证据编号、下滑到底部，再回到节点继续理解。

## 3. 本次改动

### 3.1 skill 规则收紧

已更新 `vibego_cli/data/skills/vibe-diagram/SKILL.md`：

- 新增 `## 节点信息承载规则`。
- 明确“节点优先承载关键信息”。
- 明确“不要为了保持两行节点而把信息拆到底部证据卡片”。
- 明确“节点可以限制宽度，但高度必须随内容自动增长”。
- 明确“优先增高节点和自动换行，而不是把关键细节挪到图外底部卡片”。
- 明确“底部证据/矩阵只承载跨多个节点的汇总或原始长材料索引”。
- 明确禁止 `line-clamp`、正文 `max-height`、`overflow:hidden` 裁切节点正文。

### 3.2 相关旧规则同步调整

同步调整了以下约束，避免新旧规则互相冲突：

- 关键细节外显规则：从“主图或紧邻区域”收紧为“优先进入对应节点内部”。
- 布局规则：节点正文可超过 3 行，优先自动增高或拆成子节点。
- 视觉规则：节点可以变高，不为短句化牺牲信息完整性。
- 流程图规则：辅助证据优先写入流程节点内部。
- 业务流程图规则：输入输出、风险、证据结论优先写进对应节点。
- 故障排查图规则：证据摘要优先写在对应故障故事线节点内。
- 功能迭代图规则：AC、测试矩阵、灰度、监控、回滚写入或贴近差异节点。
- CSS 规则：节点正文必须 `height:auto` 或仅设置 `min-height`，禁止裁切。
- 输出前自检：增加“节点是否承载足够信息，而不是两行标题 + 底部卡片”的检查。

## 4. 测试记录

新增测试：

- `tests/test_builtin_skills_injection.py::test_vibe_diagram_nodes_should_carry_key_details_without_bottom_card_detours`

验证结果：

- RED：新增测试在旧 skill 下失败，缺少节点承载规则。
- GREEN：补充规则后该测试通过。
- 聚焦回归：`python3.11 -m pytest -q tests/test_builtin_skills_injection.py` → `27 passed`。

## 5. 后续使用口径

后续生成图时，默认读图路径应是：

`沿主路径节点阅读 → 节点内得到关键判断和证据结论 → 需要原始材料时再看底部/弹窗`

而不是：

`读两行节点 → 跳到底部证据卡片 → 再回主图找节点`

## 6. 同步记录

- 已同步 `AGENTS-template.md` 到 `/Users/david/.config/vibego/agents/current/AGENTS-template.md`。
- 已同步 `vibe-diagram/SKILL.md` 到
  `/Users/david/.config/vibego/agents/current/vibego_cli/data/skills/vibe-diagram/SKILL.md`。
- 已通过 `sync_agents_block` 更新 `/Users/david/.config/vibego/AGENTS.md` 与 `/Users/david/.codex/AGENTS.md` 的 vibego
  受管区块。
