from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_vibe_diagram_skill_pack_exists_and_is_packaged() -> None:
    """vibe-diagram 图形表达 skill 必须作为 vibego 内置资源随包发布。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest_text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    frontmatter = skill_text.split("---", 2)[1]

    assert "name: vibe-diagram" in skill_text
    assert "description:" in skill_text
    assert "description: Use when" in frontmatter
    assert "单文件 HTML" in skill_text
    assert "直接发送 `.html` 文件" in skill_text
    assert "必须作为文件附件发送" in skill_text
    assert "禁止只发送 Markdown 链接" in skill_text
    assert "Telegram 中必须看到文件卡片" in skill_text
    assert "Codex 默认" in skill_text
    assert "`file://`" in skill_text
    assert "Telegram 来源" in skill_text
    assert "不需要 PNG" in skill_text
    assert "不要把 `file://` 作为 Telegram 主入口" in skill_text
    assert "系统架构图" in skill_text
    assert "BPMN-light" in skill_text
    assert "禁止把业务流程图画成密集表格" in skill_text
    assert "事件圆点" in skill_text
    assert "决策菱形" in skill_text
    assert "任何图都禁止文字被节点、连线、标签或背景层遮挡" in skill_text
    assert "顶部标题必须克制" in skill_text
    assert "顶部描述最多一行" in skill_text
    assert "箭头必须短、直、少交叉" in skill_text
    assert "连线不得穿过节点正文" in skill_text
    assert "发现遮挡或箭头混乱必须重排，不得交付" in skill_text
    assert "## 自动路由规则" in skill_text
    assert "路由冲突优先级" in skill_text
    assert "业务架构图 / 领域地图" in skill_text
    assert "状态 / 数据模型图" in skill_text
    assert "技术设计图" in skill_text
    assert "需求 / 决策沟通图" in skill_text
    assert "用户提到业务能力、领域、对象、规则、价值链" in skill_text
    assert "用户提到状态、状态流转、生命周期、实体关系、表结构" in skill_text
    assert "用户提到 API、数据库、模块、契约、部署、回滚" in skill_text
    assert "系统架构图规则" in skill_text
    assert "业务架构图规则" in skill_text
    assert "业务流程图规则" in skill_text
    assert "代码时序图规则" in skill_text
    assert "状态 / 数据模型图规则" in skill_text
    assert "故障排查图规则" in skill_text
    assert "页面设计稿规则" in skill_text
    assert "技术设计与需求决策图规则" in skill_text
    assert "## AGENTS 配合协议" in skill_text
    assert "当 AGENTS 要求默认通过 HTML 图沟通时" in skill_text
    assert "功能迭代 / 开发设计" in skill_text
    assert "前后差异对比图" in skill_text
    assert "缺陷排查 / 故障分析" in skill_text
    assert "高亮根因节点" in skill_text
    assert "设计定稿 / 方案确认" in skill_text
    assert "每个 HTML 图都必须能脱离聊天记录独立阅读" in skill_text
    assert "data/skills/*/SKILL.md" in pyproject_text
    assert "data/skills/*/agents/*.yaml" in pyproject_text
    assert "recursive-include vibego_cli/data/skills" in manifest_text


def test_vibe_diagram_html_only_delivery_envelope_contract() -> None:
    """vibe-diagram 应支持 HTML-only 信封模式，避免文本通道承载实质内容。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "## HTML-only 交付信封模式" in skill_text
    assert "文本通道只做交付信封" in skill_text
    assert "所有实质内容必须写入项目内单文件自包含 HTML" in skill_text
    assert "禁止在普通文本回复中展开分析、方案、证据链、测试矩阵、风险回滚或验收总结" in skill_text
    assert "阻塞性澄清问题" in skill_text
    assert "HTML 交付信封" in skill_text
    assert "[打开 HTML](file:///绝对路径/xxx.html)" in skill_text
    assert "只输出项目内 `.html/.htm` 文件路径" in skill_text
    assert "若无法写入 HTML 文件，才允许输出完整 HTML 代码块" in skill_text


def test_vibe_diagram_delivery_reply_must_stay_concise() -> None:
    """普通 HTML 图交付也应压缩聊天文本，避免 HTML 与聊天双重长文。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "## HTML 图交付后的文本压缩规则" in skill_text
    assert "生成或修改 HTML 图后，最终回复只保留 HTML 路径/链接、验证摘要、待执行动作" in skill_text
    assert "不得在聊天里重复展开 HTML 已承载的分析、证据链、测试矩阵、风险回滚" in skill_text
    assert "HTML-only 是更严格的信封模式；普通 HTML 图交付也必须默认短回复" in skill_text


def test_vibe_diagram_svg_text_wrapping_rules() -> None:
    """vibe-diagram 必须禁止把长句直接放进单个 SVG text，避免文字溢出节点。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "SVG 节点文字规则" in skill_text
    assert "优先使用 HTML/CSS 节点承载可换行正文" in skill_text
    assert "若使用 SVG `<text>`，必须使用 `<tspan>` 分行、`foreignObject` 或缩短为编号标签" in skill_text
    assert "禁止把长句直接放进单个 SVG `<text>` 节点" in skill_text
    assert "交付前必须用 390px 与桌面宽度检查节点正文不溢出" in skill_text


def test_vibe_diagram_fault_diagram_rules_prioritize_storyline() -> None:
    """故障排查图规则应把主图收敛为来龙去脉，而不是根因长文堆叠。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "故障故事线" in skill_text
    assert "证据阶梯" in skill_text
    assert "假设裁决" in skill_text
    assert "行动闭环" in skill_text
    assert "主图不写长段根因分析" in skill_text
    assert "未验证只能标为最高可疑点" in skill_text


def test_vibe_diagram_rules_reject_card_pile_across_all_diagram_types() -> None:
    """所有图型都必须有图形语法，卡片可限用但不能退化成等权重卡片堆。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "卡片堆积不是图" in skill_text
    assert "卡片不是全局禁用，但必须限用" in skill_text
    assert "允许使用卡片承载摘要、节点、泳道单元或分组边界" in skill_text
    assert "禁止把卡片作为唯一图形语法" in skill_text
    assert "卡片必须服务箭头、泳道、坐标、层级、时序或状态转换" in skill_text
    assert "不能通过堆卡片来冒充一图胜千言" in skill_text
    assert "正常使用页面宽度" in skill_text
    assert "必须先选定一种视觉语法" in skill_text
    assert "主画布必须占据首屏主要面积" in skill_text
    assert "辅助信息不得与主图同等视觉重量" in skill_text
    assert "所有图型都必须显式表达关系" in skill_text
    assert "如果去掉箭头、坐标轴、泳道、分层、包含关系或状态转换线后，剩下的仍然是一组同等权重文字卡片，必须重画" in skill_text
    assert "证据、假设、行动必须锚定到故障故事线节点" in skill_text


def test_vibe_diagram_direction_defaults_to_north_south_or_diagonal() -> None:
    """复杂 HTML 图默认北向南或左上到右下，纯左到右只适合短流程。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "默认优先北向南" in skill_text
    assert "或采用左上角向右下角的时序图" in skill_text
    assert "完全从左到右只适合很短的流程图" in skill_text
    assert "超过 5 个主节点不得继续横向铺开" in skill_text
    assert "依靠浏览器缩放和响应式重排适配" in skill_text


def test_vibe_diagram_rules_prefer_vertical_canvas_and_highlight_change_focus() -> None:
    """HTML 图应利用纵向卷轴，并把根因/修法或前后对照做成视觉焦点。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "宽度服务阅读，长度服务推理" in skill_text
    assert "默认采用正常页面宽度 + 北向南主线 + 局部横向关系" in skill_text
    assert "禁止为了塞满首屏而横向卡片化" in skill_text
    assert "首屏只放结论、图例和主路径起点" in skill_text
    assert "根因和修复方案不是说明文字，必须成为主图中的视觉焦点" in skill_text
    assert "默认使用单图高亮根因与修法" in skill_text
    assert "当修复改变两处以上关键节点" in skill_text
    assert "必须改用前后对照图" in skill_text
    assert "桌面可左右对照；移动端或内容复杂时自动改为上下对照" in skill_text


def test_vibe_diagram_before_after_direction_must_be_stable() -> None:
    """前后对照必须稳定遵循左 before 右 after，不能左右交替造成误读。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "before 固定在左侧或上方" in skill_text
    assert "after 固定在右侧或下方" in skill_text
    assert "禁止用左右交替排布表达前后差异" in skill_text
    assert "纵向卷轴中也必须保持左侧为当前/故障逻辑，右侧为修复后/目标逻辑" in skill_text
    assert "如果节点不属于前后对照，只能放在中轴、旁注或详情中" in skill_text


def test_vibe_diagram_flowchart_grammar_required_for_fault_and_iteration() -> None:
    """故障修复和开发迭代不能只画前后卡片列，主画布必须有流程图语法。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "前后对照只是容器，不是图形语法本身" in skill_text
    assert "功能迭代、开发设计和故障修复必须优先画流程图或流程化对照图" in skill_text
    assert "主画布必须包含开始/结束事件、活动节点、决策菱形、带标签箭头" in skill_text
    assert "before/after 每一侧内部也必须是流程图" in skill_text
    assert "禁止把 before/after 列画成普通说明卡片列表" in skill_text
    assert "根因节点和修法节点必须落在流程路径上" in skill_text
    assert "辅助证据优先写入流程节点内部" in skill_text


def test_vibe_diagram_fault_diagram_rejects_vertical_card_timeline_escape_hatch() -> None:
    """故障排查图不能用竖向故事线加同形圆角卡片逃逸流程图门禁。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "故障排查图主路径不得由一列同形圆角卡片承担" in skill_text
    assert "左侧竖线、步骤图标、箭头标签只能作为辅助连接" in skill_text
    assert "隐藏节点正文后只剩一列卡片和弱连接线" in skill_text
    assert "竖向故事线 + 圆角卡片列表" in skill_text
    assert "必须重画为流程图、因果链、泳道、时序轴或状态转换图" in skill_text


def test_vibe_diagram_must_not_hide_essential_details_behind_click_details() -> None:
    """HTML 图的关键细节必须静态可读，点击弹窗只能做补充，不能成为唯一信息源。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "节点优先承载关键信息" in skill_text
    assert "关键细节必须直接呈现在对应节点内部" in skill_text
    assert "点击详情只能用于补充、放大或复制主图已可见的信息" in skill_text
    assert "不得把验收标准、规则口径、接口/DB 契约、测试矩阵、风险回滚、根因证据或方案优缺点仅放入弹窗" in skill_text
    assert "弹窗不得承载唯一信息源" in skill_text
    assert "如果关闭 JavaScript 或不点击任何节点仍读不懂主结论，必须重画" in skill_text
    assert "点击详情里的换行必须使用真实换行、`&#10;` 或渲染前归一化" in skill_text
    assert "禁止让用户看到字面量 `\\n`" in skill_text


def test_vibe_diagram_visual_quality_rejects_raw_utilitarian_svg() -> None:
    """HTML 图不能只是粗糙可用的 SVG 草图，视觉质量也要服务读图。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "禁止交付原始工程草图感的 SVG" in skill_text
    assert "视觉质量必须服务流程阅读" in skill_text
    assert "使用统一的线宽、字号、留白、层级和图例" in skill_text
    assert "禁止粗暴边框、重阴影、满屏说明文字和低级默认样式" in skill_text
    assert "流程节点必须像图形符号而不是 UI 容器" in skill_text
    assert "主图应保留足够留白，文字短句化" in skill_text
    assert "颜色只用于状态和路径强调，不用于装饰" in skill_text


def test_vibe_diagram_background_should_use_premium_light_surfaces() -> None:
    """浅色背景应以有层次的高级白色为主，而不是扁平纯白或彩色底。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "浅色背景默认以白色为主色" in skill_text
    assert "白色背景不能是扁平纯白" in skill_text
    assert "使用瓷白、珍珠白、雪白、雾白等白色系明度层次" in skill_text
    assert "允许使用极轻白底工程网格、点阵或坐标纸肌理" in skill_text
    assert "网格必须低对比、低存在感" in skill_text
    assert "背景纹理必须全局统一" in skill_text
    assert "不要只在主画布局部铺网格或局部底纹" in skill_text
    assert "HTML body、SVG 主画布和弹窗遮罩以外的页面区域应共享同一背景系统" in skill_text
    assert "避免蓝色底、灰色底、米黄纸张感背景" in skill_text
    assert "背景只能提供质感和空间层次，不得抢主线" in skill_text


def test_vibe_diagram_style_must_not_override_accumulated_drawing_requirements() -> None:
    """视觉风格可调整，但不能回退用户已明确过的制图硬要求。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "视觉风格可以调整，但不得覆盖制图硬规则" in skill_text
    assert "柔和卡片风格只是一种视觉外观，不是图形语法豁免" in skill_text
    assert "前序用户约束必须同时满足" in skill_text
    assert "一图胜千言，而不是卡片胜千言" in skill_text
    assert "HTML 宽度不友好、长度可承载推理" in skill_text
    assert "根因、修法、验证闭环必须是主路径焦点" in skill_text
    assert "大改动使用 before / after 对照，小改动使用单图高亮" in skill_text
    assert "before 一律在左或上，after 一律在右或下" in skill_text
    assert "故障排查、开发迭代必须具备流程图语法" in skill_text
    assert "背景系统必须统一" in skill_text
    assert "卡片只能作为节点、摘要、泳道单元或分组边界" in skill_text


def test_vibe_diagram_sample_html_uses_flowchart_not_card_grid() -> None:
    """示例 HTML 必须真画流程图，不能仍是旧卡片网格。"""

    html_file = ROOT / "docs" / "TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html"
    html_text = html_file.read_text(encoding="utf-8")

    assert "diagram-canvas" in html_text
    assert "<svg" in html_text
    assert "marker-end" in html_text
    assert "start-event" in html_text
    assert "end-event" in html_text
    assert "decision-diamond" in html_text
    assert "root-cause" in html_text
    assert "fix-action" in html_text
    assert "verify-loop" in html_text
    assert "before-zone" in html_text
    assert "after-zone" in html_text
    assert "role=\"button\"" in html_text
    assert "flow-grid" not in html_text
    assert "role=\"table\"" not in html_text


def test_vibe_diagram_sample_html_has_mobile_readable_vertical_flowchart() -> None:
    """移动端不能把整张宽 SVG 缩成缩略图，应提供纵向可读流程。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")
    html_file = ROOT / "docs" / "TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html"
    html_text = html_file.read_text(encoding="utf-8")

    assert "移动端不能把整张 SVG 等比缩成缩略图" in skill_text
    assert "移动端必须改为纵向流程" in skill_text
    assert "mobile-flowchart" in html_text
    assert "mobile-start-event" in html_text
    assert "mobile-decision-diamond" in html_text
    assert "mobile-root-cause" in html_text
    assert "mobile-fix-action" in html_text
    assert "mobile-verify-loop" in html_text
    assert "mobile-end-event" in html_text
    assert "不把整张 SVG 缩成缩略图" in html_text
    assert "@media (max-width: 720px)" in html_text


def test_vibe_diagram_macro_topology_sample_modal_details_do_not_show_literal_escaped_newlines() -> None:
    """宏观拓扑样例的点击详情不应把转义换行 \\n 当成正文显示。"""

    html_file = ROOT / "docs" / "TASK_20260630_007_vibe-diagram系统架构图宏观拓扑示例.html"
    html_text = html_file.read_text(encoding="utf-8")
    detail_values = re.findall(r'data-detail="([^"]*)"', html_text)

    assert detail_values
    assert "&#10;" in html_text
    assert all("\\n" not in value for value in detail_values)
    assert "normalizeDetailText" in html_text


def test_vibe_diagram_page_mockup_rules_offer_selectable_candidates() -> None:
    """页面设计稿应默认提供可选择的多候选方向，并明确桌面/移动候选排版。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "页面设计稿用于方向评审时，默认在一个单文件 HTML 内提供 3 个候选设计稿" in skill_text
    assert "当用户明确要求多方案、视觉方向开放、或需要覆盖明显不同的信息架构时，可扩展到 4-5 个" in skill_text
    assert "候选以 A/B/C/D/E artboard" in skill_text
    assert "Web 端候选稿优先纵向排列" in skill_text
    assert "移动端设计稿可以在桌面宽度下用横向手机稿 filmstrip 对比" in skill_text
    assert "真实移动端查看时不得依赖横向溢出理解候选" in skill_text
    assert "必须提供可访问的上一稿/下一稿、scroll-snap 或纵向降级" in skill_text
    assert "visual thesis" in skill_text
    assert "content plan" in skill_text
    assert "interaction thesis" in skill_text
    assert "候选对比矩阵" in skill_text
    assert "推荐项" in skill_text
    assert "禁止只换颜色、阴影、圆角凑数量" in skill_text
    assert "用户明确指定只要一个最终稿" in skill_text
    assert "已有方向已确认" in skill_text
    assert "缺陷修复或最终交付" in skill_text


def test_vibe_diagram_multi_candidate_rules_cover_all_diagram_types() -> None:
    """多方案/多候选表达应逐图型声明适用边界，避免把页面设计稿规则误套到所有图。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "## 多方案 / 多候选表达规则" in skill_text
    assert "多候选不是页面设计稿的专属排版问题，但 3-5 个默认候选只适用于页面设计稿方向评审" in skill_text
    assert "系统架构图：默认单架构；只有用户要求架构方案对比时才输出 2-3 个候选架构" in skill_text
    assert "业务架构图 / 领域地图：默认单领域地图；多候选只用于领域边界、能力分层或角色协作方案选择" in skill_text
    assert "业务流程图：默认单主流程；多候选流程必须用流程方案 A/B/C、泳道或阶段对照表达" in skill_text
    assert "代码时序图：默认单调用链；多候选只用于替代调用策略、事务边界、重试/异步策略对比" in skill_text
    assert "状态 / 数据模型图：默认单模型；多候选只用于状态机、实体边界、索引或迁移策略对比" in skill_text
    assert "故障排查图：不生成 3-5 个修法设计稿；多假设必须进入假设裁决" in skill_text
    assert "页面设计稿：方向评审默认 3 个候选，可扩展到 4-5 个" in skill_text
    assert "技术设计图：方案对比保持 2-4 个" in skill_text
    assert "需求 / 决策沟通图：方案对比保持 2-4 个" in skill_text
    assert "Web 端多候选优先纵向展开" in skill_text
    assert "窄移动稿可在桌面视口横向 filmstrip 对比" in skill_text
    assert "真实移动端不得把横向滚动作为唯一阅读路径" in skill_text
    assert "每个候选必须有明确差异维度、适用边界、推荐理由和回滚或调整成本" in skill_text


def test_vibe_diagram_diagram_type_shape_contracts_are_explicit() -> None:
    """每一种图型都必须声明它应该长成什么样，避免继续退化成文字平铺。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "## 各图型形态与布局契约" in skill_text
    assert "系统架构图必须长成北向南分层拓扑" in skill_text
    assert "业务架构图 / 领域地图必须长成能力层 + 对象关系 + 规则约束" in skill_text
    assert "业务流程图必须长成 BPMN-light 流程" in skill_text
    assert "代码时序图必须长成参与者列 + 时间自上而下" in skill_text
    assert "每一步调用、返回、抛出、异步回调都占独立消息行" in skill_text
    assert "状态 / 数据模型图必须长成状态机、ER-lite 或生命周期" in skill_text
    assert "故障排查图必须长成因果链、流程化对照或排障时序图" in skill_text
    assert "页面设计稿必须长成页面线框 / artboard" in skill_text
    assert "技术设计图必须长成模块 / 契约 / 数据 / 发布回滚的落地设计图" in skill_text
    assert "需求 / 决策沟通图必须长成决策树或方案矩阵与主路径绑定" in skill_text
    assert "如果某类图无法按上述形态画出主谓宾关系，必须换图型" in skill_text


def test_vibe_diagram_layout_arrow_and_collision_rules_are_explicit() -> None:
    """skill 必须定义画布利用、箭头锚点、防重叠和文字防溢出的硬门禁。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "## 布局、箭头与防重叠算法门禁" in skill_text
    assert "画布先分配主轴和泳道，再放节点，最后连线" in skill_text
    assert "宽度用于承载泳道、参与者列、before/after" in skill_text
    assert "或局部对照" in skill_text
    assert "高度用于承载时间、阶段、因果递进和证据展开" in skill_text
    assert "节点先排版后连线" in skill_text
    assert "箭头只能连接节点边缘锚点" in skill_text
    assert "北向南主线使用下边缘到上边缘锚点" in skill_text
    assert "代码时序图消息箭头必须连接参与者生命线中心或消息端点" in skill_text
    assert "禁止箭头穿过节点正文" in skill_text
    assert "节点间距不得小于" in skill_text
    assert "16px，主路径阶段间距不得小于" in skill_text
    assert "28px" in skill_text
    assert "同层节点必须等高或按内容自适应后统一留白" in skill_text
    assert "连线层必须低于节点层" in skill_text
    assert "节点正文必须使用 HTML/CSS 可换行容器" in skill_text
    assert "必须通过 max-width、min-height、height:auto、line-height、overflow-wrap:anywhere" in skill_text
    assert "不得用固定高度裁切文字" in skill_text
    assert "如果任一节点重叠、线穿字、文字溢出，必须重排" in skill_text
    assert "必须用桌面宽度和 390px 宽度分别检查" in skill_text


def test_vibe_diagram_nodes_should_carry_key_details_without_bottom_card_detours() -> None:
    """关键证据和口径应优先写进对应节点，不能为了节点两行化拆到底部卡片增加阅读成本。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "节点优先承载关键信息" in skill_text
    assert "不要为了保持两行节点而把信息拆到底部证据卡片" in skill_text
    assert "节点可以限制宽度，但高度必须随内容自动增长" in skill_text
    assert "优先增高节点和自动换行，而不是把关键细节挪到图外底部卡片" in skill_text
    assert "证据、风险、测试、回滚默认写入对应主路径节点" in skill_text
    assert "底部证据/矩阵只承载跨多个节点的汇总或原始长材料索引" in skill_text
    assert "禁止用 line-clamp、max-height 或 overflow:hidden 裁掉节点正文" in skill_text


def test_vibe_diagram_raw_evidence_should_live_in_node_details_not_bottom_piles() -> None:
    """原始证据不应默认堆到底部，节点摘要可见，长证据进入节点点击详情。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "原始证据默认进入对应节点的点击详情" in skill_text
    assert "节点内静态展示证据编号、结论、可信度或状态即可" in skill_text
    assert "不要默认在底部铺完整证据卡片" in skill_text
    assert "底部证据区只用于跨节点冲突裁决、全局证据索引或测试矩阵" in skill_text
    assert "点击详情可以承载文件路径、行号、日志片段、SQL、JSON、命令输出和截图说明" in skill_text


def test_vibe_diagram_system_architecture_must_read_as_global_topology_not_layered_cards() -> None:
    """系统架构图必须第一眼读出全局拓扑，而不是分层卡片清单加证据文字。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "系统架构图不是组件清单、证据清单或分层卡片目录" in skill_text
    assert "首屏必须出现一张北向南全局拓扑总览" in skill_text
    assert "外部入口、接入/网关、业务服务/Agent、工具与中间件、状态/数据/观测按层成带状排列" in skill_text
    assert "层间必须画出主请求流、控制流、数据读写流或兜底流" in skill_text
    assert "同层节点只保留组件名、职责、协议/接口、运行状态和关键约束" in skill_text
    assert "源码证据、文件路径和长说明进入该节点点击详情" in skill_text
    assert "如果第一眼只能看到多列卡片和证据文字，看不出入口到数据面的流向，必须重画" in skill_text


def test_vibe_diagram_system_architecture_supports_plane_swimlanes_for_medium_complexity() -> None:
    """中等复杂系统架构可用主请求中轴 + 控制/数据/兜底泳道，而不是继续堆卡片。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "中等复杂系统架构可以使用“主请求中轴 + 控制面 / 数据面 / 兜底面泳道”" in skill_text
    assert "控制面不要与主请求节点等权重平铺" in skill_text
    assert "数据/知识面应作为南向或侧向依赖泳道" in skill_text
    assert "兜底/人工流适合画成侧边 rail" in skill_text
    assert "运行语义条" in skill_text
    assert "状态角标" in skill_text


def test_vibe_diagram_system_architecture_swimlanes_must_preserve_readability() -> None:
    """泳道式系统架构不能退化成多列表格，必须以单主线分段展开降低认知负担。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "泳道不是表格，不得把层级、控制面、主请求、数据面、兜底面全部做成等权重多列网格" in skill_text
    assert "先保留一条粗主线，再把控制、数据、兜底折成贴近当前阶段的侧向胶囊或短注" in skill_text
    assert "单个视口内主路径节点建议 3-5 个" in skill_text
    assert "如果需要 5 列以上才能表达，必须改为分段卷轴、阶段轨道或多张局部小图" in skill_text
    assert "侧向泳道只能服务当前主线阶段，不得形成第二张需要逐格阅读的矩阵" in skill_text


def test_vibe_diagram_system_architecture_prefers_007_macro_topology_baseline() -> None:
    """用户评审确认 007 宏观拓扑布局是当前最佳基线，应作为系统架构图默认形态。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "系统架构图默认优先采用 007 宏观拓扑基线" in skill_text
    assert "北向南层级 + 层间流向分隔条 + 节点内摘要 + 点击详情证据" in skill_text
    assert "不要因为“还有优化空间”就自动升级为多泳道、五列表格或分段故事线" in skill_text
    assert "只有当 007 基线无法表达清楚两个以上独立平面时，才考虑侧向泳道" in skill_text
    assert "泳道和分段图是例外补救形态，不是系统架构图的默认升级方向" in skill_text


def test_vibe_diagram_system_architecture_rule_priority_keeps_swimlanes_as_exception() -> None:
    """系统架构图规则必须先讲 007 默认基线，再讲泳道例外，避免未来误读成优先升级。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    baseline_index = skill_text.index("系统架构图默认优先采用 007 宏观拓扑基线")
    exception_index = skill_text.index("系统架构图例外形态")

    assert baseline_index < exception_index
    assert "系统架构图例外形态不是优化方向" in skill_text
    assert "除非用户明确要求或 007 基线验证失败，否则不要使用泳道或分段故事线" in skill_text
    assert "007 基线验证失败" in skill_text
    assert "不要把 010/011 当作推荐模板" in skill_text


def test_vibe_diagram_fault_debugging_dedicated_skeleton_prioritizes_current_chain() -> None:
    """故障排查图应先画当前现状链路，再把证据、根因、修法和回滚贴到链路上。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "## 故障排查图专用骨架" in skill_text
    assert "故障排查图必须先画当前现状链路，再画根因和修法" in skill_text
    assert "现象 / 影响 → 当前现状链路 → 证据裁决 → 根因或最高可疑点 → 修法 → 验证 / 回滚" in skill_text
    assert "现状链路必须展示当前代码、接口、状态、配置或运行路径如何一步步走到问题点" in skill_text
    assert "证据必须贴在现状链路节点上" in skill_text
    assert "假设裁决只能作为证据到根因的旁路" in skill_text
    assert "根因节点必须落在现状链路上" in skill_text
    assert "修法节点必须明确切断、替换、补偿或兜底哪一段故障链路" in skill_text
    assert "验证 / 回滚闭环必须贴近修法节点" in skill_text
    assert "小故障用单图因果链" in skill_text
    assert "链路故障用代码时序排障图" in skill_text
    assert "影响两处以上用 before/after 流程化对照" in skill_text


def test_vibe_diagram_feature_iteration_dedicated_skeleton_prioritizes_current_and_diff() -> None:
    """功能迭代图应先画当前功能和实现，再把目标、差异、验证和发布闭环映射上去。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "## 功能迭代 / 开发设计图专用骨架" in skill_text
    assert "功能迭代图必须先画当前功能和当前实现，再画目标和差异" in skill_text
    assert "当前功能主路径 → 当前开发实现链路 → 目标主路径 → 差异映射 → 验证与发布闭环" in skill_text
    assert "当前功能主路径必须说明用户现在入口、动作、状态和结果" in skill_text
    assert "当前开发实现链路必须说明前端入口、API、服务、DB/状态、中间件或异步任务" in skill_text
    assert "差异映射必须把新增 / 修改 / 删除 / 不变 / 风险 / 回滚贴到对应节点" in skill_text
    assert "不得只画目标方案" in skill_text
    assert "体验变更用 before/after 用户主路径对照" in skill_text
    assert "技术链路变更用 current/target 技术时序或数据流对照" in skill_text
    assert "大迭代用用户体验层 → 系统实现层 → 验证发布层" in skill_text
    assert "AC、测试矩阵、灰度发布、监控和回滚动作必须写入或贴近差异节点" in skill_text


def test_worker_start_failure_diagram_uses_wrapping_html_nodes() -> None:
    """当前 worker 启动失败图应使用可换行 HTML 节点，避免 SVG text 溢出。"""

    html_file = ROOT / "docs" / "TASK_20260629_003_worker启动失败日志截断.html"
    html_text = html_file.read_text(encoding="utf-8")

    assert "<!doctype html>" in html_text
    assert "flow-node" in html_text
    assert "decision-node" in html_text
    assert "overflow-wrap: anywhere" in html_text
    assert "<text" not in html_text
    assert "white-space: nowrap" not in html_text


def test_vibe_diagram_title_must_start_with_generated_diagram_type() -> None:
    """所有 HTML 图顶部标题必须先显示本次触发的生图类型，而不是 skill 名。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")
    html_file = ROOT / "docs" / "TASK_20260627_001_vibe-diagram卡片堆叠故障排查.html"
    html_text = html_file.read_text(encoding="utf-8")

    assert "顶部标题必须以触发的生图类型开头" in skill_text
    assert "标题格式：`生图类型：主题结论`" in skill_text
    assert "不要把 skill 名称写成页面主标题" in skill_text
    assert "故障排查：" in skill_text
    assert "系统架构：" in skill_text
    assert "技术设计：" in skill_text
    assert '<h1 id="title">故障排查：不是换皮卡片：主图必须先像图</h1>' in html_text
    assert "vibe-diagram 不是换皮卡片" not in html_text


def test_sync_agents_block_embeds_builtin_vibe_diagram_skill(tmp_path: Path) -> None:
    """同步全局 AGENTS 时，应把 vibego 内置 skill 注入到同一个受管块。"""

    target = tmp_path / "AGENTS.md"
    env = os.environ.copy()
    env.update(
        {
            "PYTHON_EXEC": sys.executable,
            "TARGET_AGENTS_FILE": str(target),
        }
    )

    subprocess.run(
        [
            "bash",
            "-lc",
            (
                "set -euo pipefail; "
                "source scripts/models/common.sh; "
                'sync_agents_block "$TARGET_AGENTS_FILE" AGENTS-template.md >/dev/null'
            ),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )

    synced_text = target.read_text(encoding="utf-8")

    assert "<!-- vibego-agents:start -->" in synced_text
    assert "# Vibego 内置 Skills" in synced_text
    assert "## Skill: vibe-diagram" in synced_text
    assert "当用户要求画系统架构图、业务流程图、代码时序图、故障排查图、页面设计稿" in synced_text
    assert "最终必须直接发送 `.html` 文件" in synced_text
    assert "必须作为文件附件发送" in synced_text
    assert "## 自动路由规则" in synced_text
    assert "业务架构图 / 领域地图" in synced_text
    assert "状态 / 数据模型图" in synced_text
    assert "技术设计图" in synced_text
    assert "## AGENTS 配合协议" in synced_text
    assert "## HTML-only 交付信封模式" in synced_text
    assert "文本通道只做交付信封" in synced_text
    assert "所有实质内容必须写入项目内单文件自包含 HTML" in synced_text
    assert "高亮根因节点" in synced_text
    assert "卡片堆积不是图" in synced_text
    assert "卡片不是全局禁用，但必须限用" in synced_text
    assert "宽度服务阅读，长度服务推理" in synced_text
    assert "默认优先北向南" in synced_text
    assert "移动端不能把整张 SVG 等比缩成缩略图" in synced_text
    assert "移动端必须改为纵向流程" in synced_text
    assert "顶部标题必须以触发的生图类型开头" in synced_text
    assert "不要把 skill 名称写成页面主标题" in synced_text
    assert "默认使用单图高亮根因与修法" in synced_text
    assert "before 固定在左侧或上方" in synced_text
    assert "前后对照只是容器，不是图形语法本身" in synced_text
    assert "故障排查图主路径不得由一列同形圆角卡片承担" in synced_text
    assert "隐藏节点正文后只剩一列卡片和弱连接线" in synced_text
    assert "节点优先承载关键信息" in synced_text
    assert "关键细节必须直接呈现在对应节点内部" in synced_text
    assert "弹窗不得承载唯一信息源" in synced_text
    assert "HTML 图交付后的文本压缩规则" in synced_text
    assert "SVG 节点文字规则" in synced_text
    assert "禁止交付原始工程草图感的 SVG" in synced_text
    assert "浅色背景默认以白色为主色" in synced_text
    assert "白色背景不能是扁平纯白" in synced_text
    assert "允许使用极轻白底工程网格、点阵或坐标纸肌理" in synced_text
    assert "背景纹理必须全局统一" in synced_text
    assert "## 多方案 / 多候选表达规则" in synced_text
    assert "3-5 个默认候选只适用于页面设计稿方向评审" in synced_text
    assert "真实移动端不得把横向滚动作为唯一阅读路径" in synced_text
    assert "## 各图型形态与布局契约" in synced_text
    assert "代码时序图必须长成参与者列 + 时间自上而下" in synced_text
    assert "## 布局、箭头与防重叠算法门禁" in synced_text
    assert "箭头只能连接节点边缘锚点" in synced_text
    assert "如果任一节点重叠、线穿字、文字溢出，必须重排" in synced_text
    assert "## 故障排查图专用骨架" in synced_text
    assert "故障排查图必须先画当前现状链路，再画根因和修法" in synced_text
    assert "## 功能迭代 / 开发设计图专用骨架" in synced_text
    assert "功能迭代图必须先画当前功能和当前实现，再画目标和差异" in synced_text
    assert "视觉风格可以调整，但不得覆盖制图硬规则" in synced_text
    assert "前序用户约束必须同时满足" in synced_text
    assert "避免蓝色底、灰色底、米黄纸张感背景" in synced_text
    assert "任何图都禁止文字被节点、连线、标签或背景层遮挡" in synced_text
    assert "箭头必须短、直、少交叉" in synced_text
    assert "<!-- vibego-agents:end -->" in synced_text
