from __future__ import annotations

import os
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
    assert "辅助证据只能作为流程节点的证据锚点" in skill_text


def test_vibe_diagram_fault_diagram_rejects_vertical_card_timeline_escape_hatch() -> None:
    """故障排查图不能用竖向故事线加同形圆角卡片逃逸流程图门禁。"""

    skill_file = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")

    assert "故障排查图主路径不得由一列同形圆角卡片承担" in skill_text
    assert "左侧竖线、步骤图标、箭头标签只能作为辅助连接" in skill_text
    assert "隐藏节点正文后只剩一列卡片和弱连接线" in skill_text
    assert "竖向故事线 + 圆角卡片列表" in skill_text
    assert "必须重画为流程图、因果链、泳道、时序轴或状态转换图" in skill_text


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
    assert "禁止交付原始工程草图感的 SVG" in synced_text
    assert "浅色背景默认以白色为主色" in synced_text
    assert "白色背景不能是扁平纯白" in synced_text
    assert "允许使用极轻白底工程网格、点阵或坐标纸肌理" in synced_text
    assert "背景纹理必须全局统一" in synced_text
    assert "视觉风格可以调整，但不得覆盖制图硬规则" in synced_text
    assert "前序用户约束必须同时满足" in synced_text
    assert "避免蓝色底、灰色底、米黄纸张感背景" in synced_text
    assert "任何图都禁止文字被节点、连线、标签或背景层遮挡" in synced_text
    assert "箭头必须短、直、少交叉" in synced_text
    assert "<!-- vibego-agents:end -->" in synced_text
