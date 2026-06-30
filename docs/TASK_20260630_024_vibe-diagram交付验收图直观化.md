# TASK_20260630_024：vibe-diagram 交付验收图直观化

## 背景

用户指出 Zeus 示例交付验收 HTML 不直观：`/Users/david/hypha/zeus/docs/TASK_20260630_001_Zeus新建对话空态样式优化.html`。
只读审查后可见旧图主要由“主路径 / 代码影响点 / 验证闭环 / 保留与回滚”多个区块组成，读者需要在需求、改动、测试和风险之间来回跳读，才能判断每条原始要求是否验收通过。

## 目标

将 `vibe-diagram` 的交付验收图从“报告卡片区块”收敛为“需求到证据的验收轨道”：每条用户需求或 AC 都有独立 R#
泳道，并串起原始要求、交付变更、验证证据、验收判定、待执行 / 回滚。

## 方案

- 在 `vibego_cli/data/skills/vibe-diagram/SKILL.md` 中新增交付验收图路由：命中“交付验收、验收、收尾、验证闭环、完成交付、测试通过、发布/打包”时读取
  `references/delivery-acceptance.md`。
- 新增 `vibego_cli/data/skills/vibe-diagram/references/delivery-acceptance.md`，定义交付验收图专用骨架、反直觉版式红线、状态与移动端规则。
- 更新 `tests/test_builtin_skills_injection.py`，确保交付验收 reference 随包发布，并测试“需求验收轨道”关键规则。
- 更新 `AGENTS.md` Facts Table，记录交付验收图需求证据轨道约束。
- 新增本 HTML 交付物，用同一个 Zeus 例子演示“旧图为何不直观”和“新默认形态如何读”。

## 本轮追加：交付验收必答信息

用户反馈交付验收还应明确告知：改了什么、用户需要执行什么脚本、重启什么服务、影响哪些功能入口、如何验证。

本轮已将这些内容补成 `delivery-acceptance.md` 的硬规则：交付验收图必须在首屏或紧邻主轨道的固定信息区直接外显 `改了什么`、
`影响功能入口`、`验证方式`、`待用户执行脚本 / 重启服务`、`剩余未覆盖点`；如果无需脚本或无需重启，也必须明确写“无需执行脚本”或“无需重启服务”。

## TDD 与验证记录

- RED：
  `python3.11 -m pytest -q tests/test_builtin_skills_injection.py::test_vibe_diagram_reference_files_exist_and_are_packaged tests/test_builtin_skills_injection.py::test_vibe_diagram_diagram_type_shape_contracts_are_explicit tests/test_builtin_skills_injection.py::test_vibe_diagram_delivery_acceptance_uses_requirement_evidence_track`
  初始失败，缺少 `delivery-acceptance.md` 与交付验收图轨道规则。
- GREEN：同一组测试在新增规则后通过，结果 `3 passed`；追加“交付验收必答信息”测试先红后绿，结果 `1 passed`
  ；追加“拒绝卡片堆样例”测试先红后绿，结果 `1 passed`；第三轮把测试收紧为要求 `acceptance-subway` / `subway-station` 并禁止旧
  `acceptance-board`、`slot`、`map-row` 等矩形线路结构，结果 `1 passed`；第四轮新增“总控轴默认纵向”测试先红后绿，结果
  `2 passed`；第五轮新增“清爽产品式验收账本”测试先红后绿，聚焦测试结果 `3 passed`；第六轮新增“统一白底背景不可被视觉返工改掉”测试先红后绿，聚焦测试结果
  `2 passed`；第七轮收紧“统一白底格子纹理不可丢失、几乎所有实质会话优先触发 HTML 图、删除多余回复提示和验证摘要”测试，聚焦测试结果
  `5 passed`；第八轮按用户给出的旧系统/业务架构 HTML 恢复双色柔光 + 28px 工程格子 + 白底渐变背景，聚焦测试结果 `1 passed`。
- 最终回归：
  `python3.11 -m pytest -q tests/test_builtin_skills_injection.py tests/test_agents_template_migration.py tests/test_agents_sync.py`
  通过，结果 `61 passed`。
- 规则同步：`python3.11 -m vibego_cli agents-sync --source-root /Users/david/hypha/tools/vibego --json` 返回 `ok: true`
  ，Codex/Claude/Gemini/vibego 目标均为 `updated`。
- HTML 响应式检查：使用 Chrome headless 打开本 HTML，桌面 1220px 与移动 390px 的
  `documentElement.scrollWidth == clientWidth`，移动端无横向溢出。

## 本轮再追加：去卡片堆重画

用户继续反馈当前图“太乱，又是纯卡片和文字的堆砌”。本轮把样例 HTML 从“交付说明卡片 + 旧图问题卡片 + R# 卡片行”重画为单张连通验收看板：左侧
`交付总控轨` 串起改动、入口、验证、脚本/重启和未覆盖点；右侧 `R# 证据线路图` 让每条需求沿同一条线读完要求、变更、证据、判定和动作。

同时在 `delivery-acceptance.md` 增加硬规则：交付说明栏不能做成 5
张等权重卡片，必须收敛为横向或纵向连通的信息带；旧图问题只能作为旁注或反例标记，不得占据首屏主视觉；主图第一视觉必须是验收线路、验收看板或证据矩阵热区。

## 本轮第三次追加：从线路卡片改为站点图

用户继续指出“这还不是一大堆卡片吗？”。本轮把主画布彻底移出矩形节点：桌面端改为 `交付总控线 + R# 验收线路`
的地铁站点图，移动端改为纵向站点线路；圆点表示要求、变更、证据、判定和动作，文字只做站点短标注。底部仅保留轻量索引表，不再作为主阅读路径。

规则同步补强：`delivery-acceptance.md`
明确“交付验收主画布不得再用矩形卡片节点承载每一步；即使矩形之间有箭头，也仍然是卡片线路图。优先使用地铁图 / 站点图 / 状态热区”。

## 本轮第四次追加：总控轴改为纵向并优化内容

用户指出 `改了什么、影响入口、如何验证、脚本 / 重启、未覆盖点` 应该纵向，且内容需要再优化。本轮将桌面端顶部横向交付总控线改为左侧纵向总控轴，文案改为更短的验收导览：
`改了什么：新增验收图规则 + 站点样例`、`影响入口：交付验收 / 收尾 / 验证闭环`、`如何验证：59 项回归通过 + sync ok`、
`脚本 / 重启：无需用户手动再跑脚本；重启旧 worker / 新开长会话`、`未覆盖点：未重跑 Zeus，只验证 vibego`。

规则同步补强：`delivery-acceptance.md` 明确这五项默认纵向排列；除非用户明确要求横向总控线，否则不要横向铺满首屏。

## 本轮第五次追加：视觉从重装饰线路图改为清爽验收账本

用户继续反馈“还是很丑”。本轮不再微调 SVG 线路图，而是把 HTML 主体重做为克制的产品式验收账本：白瓷纸面、细线、圆点站位、一枚验收章；左侧仍保留纵向交付五问，右侧用
R# 行级路线串起原始要求、交付变更、验证证据、验收判定和动作 / 回滚。

规则同步补强：`delivery-acceptance.md` 明确交付验收样例优先使用产品式验收账本或路线牌，避免重装饰 SVG
图纸感、背景网格、发光渐变和密集彩色线路。新增测试 `test_vibe_diagram_delivery_acceptance_sample_uses_quiet_product_layout`
，约束 HTML 包含 `acceptance-ledger`、`status-stamp`、`visual-thesis`，且不得出现内嵌 SVG 或发光渐变。

## 本轮第六次追加：恢复统一白底背景系统

用户指出背景配色不应被改掉，统一背景是 `vibe-diagram` 的全局约束。本轮将 HTML 背景从偏暖纸色 `oklch(... 88/92)` 恢复为统一白底系统：
`--paper: #fbfdff`、`--sheet: rgba(255,255,255,.84)`、`--panel: rgba(255,255,255,.72)`。结构仍保持清爽验收账本，不回退到重装饰
SVG 线路图。

新增测试 `test_vibe_diagram_delivery_acceptance_sample_keeps_unified_white_background`，防止后续视觉返工再次改掉统一背景配色。

## 本轮第七次追加：恢复格子纹理、扩大 HTML 图触发、删除多余提示

用户指出四个问题：统一白底里的格子纹理也不应被改掉；部分会话没有触发 `vibe-diagram`，期望几乎所有实质沟通都用 HTML
图表达，核心理念是“一图胜千言”；不需要 `默认先结论，少噪音` 和缺陷故障固定收敛句；最终聊天不需要 `验证摘要` 小节。

本轮处理：

1. HTML 背景恢复为用户给出的旧架构图背景系统：保留 `--paper: #fbfdff`、`--sheet: rgba(255,255,255,.84)`、
   `--panel: rgba(255,255,255,.72)`，并按 `/Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260630_001_系统架构图.html` 的
   body 背景恢复双色柔光、28px 工程网格和白底渐变。
2. `AGENTS-template.md` 将触发口径从“非琐碎设计/排障/架构...”放大到“几乎所有需要解释、判断、设计、排障、复盘、代码逻辑说明或交付验收的会话都应优先触发
   vibe-diagram”，并写入“一图胜千言”。
3. 删除 Reply contract 中用户明确不要的两条提示：`默认先结论，少噪音` 与 `现象 -> 影响 -> 根因 -> 修法 -> 验证`。
4. `AGENTS-template.md` 与 `vibe-diagram/SKILL.md` 的 HTML 交付信封均删除“验证摘要”要求，改为只保留 HTML
   路径/链接和下一步 / 待执行动作。

## 本轮第八次追加：按旧架构图恢复真实背景样式

用户给出两个旧 HTML：

- `/Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260630_002_业务架构图.html`
- `/Users/david/cckg/tcapp/Back-End/css/docs/TASK_20260630_001_系统架构图.html`

审查后确认旧背景不是简单 24px 网格，而是 `radial-gradient` 双色柔光 + `rgba(93, 133, 173, .045)` 的 28px 工程网格 +
`#ffffff -> #f7fbff -> #fbfdff` 白底渐变。本轮将当前交付 HTML 按旧系统架构图背景恢复，并把测试改为锁定这些 CSS
片段，避免后续再凭审美改背景。

## 本轮第九次追加：修复验收章小字溢出

用户在浏览器里标注 `60 TESTS · SYNC OK` 溢出验收章。根因是状态章把测试数量和同步状态塞进同一行，并使用较大的字距。

本轮将状态小字改成两行 `status-meta`：第一行测试数量，第二行同步状态；同时限制最大宽度、降低字距并保留可读行高。新增回归测试
`test_vibe_diagram_delivery_acceptance_status_stamp_text_does_not_overflow`，锁定不得再出现单行 `tests · sync ok`
。由于新增了该用例，回归计数同步更新为 `61 passed`。

## 风险与回滚

- 风险：模型仍可能把交付验收画成普通总结页。规避：新增独立 reference 与测试锁定“不得拆成验证闭环/代码影响点/保留与回滚卡片区”。
- 风险：交付验收图和功能迭代图边界混淆。规避：reference 明确“未实现前切需求决策/技术设计，根因解释切故障排查”。
- 回滚：移除 `delivery-acceptance.md`、恢复 `SKILL.md` 路由与测试 expected set，并删除 AGENTS 新事实行。
