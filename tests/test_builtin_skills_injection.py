from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIBE_DIAGRAM_DIR = ROOT / "vibego_cli" / "data" / "skills" / "vibe-diagram"
VIBE_DIAGRAM_SKILL = VIBE_DIAGRAM_DIR / "SKILL.md"
VIBE_DIAGRAM_REFERENCES = VIBE_DIAGRAM_DIR / "references"
VIBE_DIAGRAM_SCRIPTS = VIBE_DIAGRAM_DIR / "scripts"


def _read_vibe_diagram_core() -> str:
    """读取 vibe-diagram 常驻薄内核。"""

    return VIBE_DIAGRAM_SKILL.read_text(encoding="utf-8")


def _read_vibe_diagram_reference(name: str) -> str:
    """读取某个图型的按需 reference。"""

    return (VIBE_DIAGRAM_REFERENCES / name).read_text(encoding="utf-8")


def _read_vibe_diagram_all_rules() -> str:
    """读取 vibe-diagram 内核与所有 reference，供规则覆盖类断言使用。"""

    texts = [_read_vibe_diagram_core()]
    if VIBE_DIAGRAM_REFERENCES.exists():
        texts.extend(path.read_text(encoding="utf-8") for path in sorted(VIBE_DIAGRAM_REFERENCES.glob("*.md")))
    return "\n".join(texts)


CANDIDATE_ATLAS_EXPECTATIONS = {
    "system-architecture.md": (
        "系统架构图",
        "北向南分层拓扑",
        ["主请求中轴 + 控制/数据/兜底泳道", "运行时依赖拓扑"],
    ),
    "business-architecture.md": (
        "业务架构 / 领域地图",
        "能力层 + 领域对象关系图",
        ["参与方边界图", "规则约束热区图", "价值链地图"],
    ),
    "business-flow.md": (
        "业务流程图",
        "BPMN-light 流程图",
        ["泳道流程图", "阶段轨道图", "异常分支流程图"],
    ),
    "code-sequence.md": (
        "代码时序图",
        "参与者列 + 时间向下时序图",
        ["异步回调时序图", "事务边界时序图", "重试/异常返回时序图"],
    ),
    "state-data-model.md": (
        "状态 / 数据模型图",
        "状态机图",
        ["ER-lite", "生命周期轨道", "数据流图", "状态-事件矩阵热区"],
    ),
    "fault-debugging.md": (
        "故障排查图",
        "排障时序图",
        ["因果链图", "BPMN-light 排查流程", "before/after 流程化对照", "状态/数据断点图"],
    ),
    "feature-iteration.md": (
        "功能迭代 / 开发设计图",
        "当前流程 vs 目标流程的流程化对照",
        ["current/target 技术时序", "差异热区", "发布回滚轨道"],
    ),
    "page-mockup.md": (
        "页面设计稿",
        "页面线框 / artboard",
        ["多候选 artboard filmstrip", "响应式状态板", "主路径页面流"],
    ),
    "technical-design.md": (
        "技术设计图",
        "模块 / 契约 / 数据 / 发布回滚拓扑",
        ["API 契约泳道", "数据流 + 一致性边界", "发布切换轨道"],
    ),
    "decision-communication.md": (
        "需求 / 决策沟通图",
        "决策树",
        ["方案矩阵 + 主路径绑定", "取舍象限", "推荐路径图"],
    ),
    "delivery-acceptance.md": (
        "交付验收图",
        "验收账本 / 需求到证据签收表",
        ["证据泳道图", "风险动作板", "交付时间线"],
    ),
}

REFERENCE_BUSINESS_ARCHITECTURE_COLOR_LINES = [
    "--ink: #102033;",
    "--muted: #5a6c80;",
    "--paper: #fbfdff;",
    "--panel: rgba(255,255,255,.9);",
    "--line: #8db3d8;",
    "--blue: #1f6fb2;",
    "--blue-soft: #e8f3ff;",
    "--green: #17785a;",
    "--green-soft: #e9f8f2;",
    "--amber: #986506;",
    "--amber-soft: #fff6de;",
    "--red: #a63a3a;",
    "--red-soft: #fff0f0;",
    "--violet: #6254ad;",
    "--violet-soft: #f1efff;",
    "--shadow: 0 16px 42px rgba(20, 57, 92, .085);",
    "--radius: 20px;",
]

REFERENCE_BUSINESS_ARCHITECTURE_BACKGROUND_LINES = [
    "background:",
    "radial-gradient(circle at 18% 3%, rgba(214,233,255,.78), transparent 30rem),",
    "radial-gradient(circle at 78% 6%, rgba(228,246,239,.8), transparent 28rem),",
    "linear-gradient(rgba(93,133,173,.045) 1px, transparent 1px),",
    "linear-gradient(90deg, rgba(93,133,173,.045) 1px, transparent 1px),",
    "linear-gradient(180deg, #fff 0%, #f7fbff 54%, #fbfdff 100%);",
    "background-size: auto, auto, 28px 28px, 28px 28px, auto;",
]


def test_vibe_diagram_core_is_thin_and_routes_to_references() -> None:
    """vibe-diagram 常驻内核应变薄，并把重图型规则按需路由到 references。"""

    core = _read_vibe_diagram_core()

    assert core.count("\n") + 1 <= 300
    assert "## 图型规则索引" in core
    assert "选择图型后必须读取对应 reference" in core
    assert "读取失败必须 fail-closed" in core
    assert "references/system-architecture.md" in core
    assert "references/business-architecture.md" in core
    assert "references/fault-debugging.md" in core
    assert "references/feature-iteration.md" in core


def test_vibe_diagram_reference_files_exist_and_are_packaged() -> None:
    """每个生图类型应有独立 reference，并随 Python 包发布。"""

    expected = {
        "business-architecture.md",
        "business-flow.md",
        "code-sequence.md",
        "delivery-acceptance.md",
        "decision-communication.md",
        "fault-debugging.md",
        "feature-iteration.md",
        "page-mockup.md",
        "state-data-model.md",
        "system-architecture.md",
        "technical-design.md",
    }
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert VIBE_DIAGRAM_REFERENCES.is_dir()
    assert {path.name for path in VIBE_DIAGRAM_REFERENCES.glob("*.md")} == expected
    assert "data/skills/*/references/*.md" in pyproject_text
    assert "data/skills/*/scripts/*.py" in pyproject_text




def test_vibe_diagram_prompt_compaction_preserves_recent_feedback_rules() -> None:
    """vibe-diagram 提示词应沉淀近期反馈，同时避免继续膨胀。"""

    core_text = _read_vibe_diagram_core()
    delivery_text = _read_vibe_diagram_reference("delivery-acceptance.md")

    assert len(core_text) <= 11000
    assert len(delivery_text) <= 3100
    assert "Markdown 链接文字必须使用 HTML 内部 `<h1>` 主标题" in core_text
    assert "不要写成固定的“打开 HTML”" in core_text
    assert "标题顶部只保留任务编码" in core_text
    assert "不要生成 skill 使用清单" in core_text
    assert "不再生成右上角交付信息卡片" in core_text
    assert "候选切换按钮和候选面板可见标题只显示图名本身" in core_text
    assert "tabs / role=tablist 只能用于同一图型的候选布局" in core_text
    assert "不得把步骤、补充问答、发布说明或普通章节导航做成 tab" in core_text
    assert "交付验收图保留候选切换入口" not in core_text
    assert "不要用移除按钮或删除面板来解决可读性问题" in delivery_text
    assert "证据泳道图" in delivery_text
    assert "风险动作板" in delivery_text
    assert "交付时间线" in delivery_text


def test_vibe_diagram_description_is_scoped_to_visual_and_logic_diagrams() -> None:
    """skill 索引描述只覆盖显式视觉化和复杂逻辑结构说明。"""

    core_text = _read_vibe_diagram_core()
    frontmatter = core_text.split("---", 2)[1]

    assert "description: Use when" in frontmatter
    assert "complex logic" in frontmatter
    assert "visual explanation" in frontmatter
    assert "triggers include" in frontmatter
    assert "画图" in frontmatter
    assert "逻辑结构" in frontmatter
    assert "HTML-first" not in frontmatter
    assert "substantive answer" not in frontmatter
    assert "why/how explanations" not in frontmatter
    assert "delivery envelope" not in frontmatter
    assert "为什么" not in frontmatter
    assert "怎么做" not in frontmatter
    assert "实质沟通" not in frontmatter
    assert "交付信封" not in frontmatter


def test_vibe_diagram_candidate_atlas_mode_is_explicit_calibration_only() -> None:
    """候选全集只能服务显式校准/对比，不应让普通架构图默认退化成 tab 页。"""

    core_text = _read_vibe_diagram_core()
    all_rule_text = _read_vibe_diagram_all_rules()

    assert "## 候选全集校准模式" in core_text
    assert "候选全集只在用户明确要求多候选、校准、对比、视觉探索或任务文档明确要求候选全集时启用" in core_text
    assert "普通单图请求默认只生成一张首选 presentation 图" in core_text
    assert "不得因为 reference 列出备选候选就自动生成 tabs" in core_text
    assert "校准模式启用后，每次只对当前命中的生图类型生成候选全集" in core_text
    assert "首选候选图型 + 全部备选候选图型" in core_text
    assert "候选 A/B/C/D" in core_text
    assert "每个候选都必须是真图" in core_text
    assert "信息不足时也必须生成该备选候选" in core_text
    assert "待确认节点" in core_text
    assert "候选对比矩阵只比较“可读性、事实承载、适用边界、风险”" in core_text
    assert "tabs / role=tablist 只能用于同一图型的候选布局" in core_text
    assert "单一结论页用普通标题、目录或章节" in core_text
    assert "交付验收图保留候选切换入口" not in core_text

    for _reference_name, (_diagram_type, preferred, alternatives) in CANDIDATE_ATLAS_EXPECTATIONS.items():
        assert preferred in all_rule_text
        for alternative in alternatives:
            assert alternative in all_rule_text


def test_vibe_diagram_candidate_labels_must_use_plain_diagram_names_for_all_types() -> None:
    """候选切换和面板可见标题应只显示图名，不带候选/方案/视图前缀。"""

    core_text = _read_vibe_diagram_core()

    assert "候选切换按钮和候选面板可见标题只显示图名本身" in core_text
    assert "不得显示 `候选 A：`、`候选 B：`、`方案 A：`、`方案 B：`、`验收视图：`、`验收图层：`" in core_text
    assert "内部 DOM id、hash、aria-controls 可继续使用稳定编号" in core_text
    assert "推荐、首选、适用边界等说明只能放在按钮旁的辅助文案、面板右侧说明或说明栏" in core_text
    assert "该规则适用于所有生图类型" in core_text


def test_vibe_diagram_references_list_candidate_atlas_for_every_diagram_type() -> None:
    """每个图型 reference 都应声明首选候选与全量备选，供生成 HTML 前读取。"""

    for reference_name, (diagram_type, preferred, alternatives) in CANDIDATE_ATLAS_EXPECTATIONS.items():
        reference_text = _read_vibe_diagram_reference(reference_name)

        assert "## 候选全集清单" in reference_text
        assert f"生图类型：{diagram_type}" in reference_text
        assert f"首选候选：{preferred}" in reference_text
        assert "必生成备选候选" in reference_text
        for alternative in alternatives:
            assert alternative in reference_text


def test_vibe_diagram_background_grid_contract_is_fixed_and_testable() -> None:
    """背景必须固化为参考业务架构图的白蓝工程纸配色系统。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "参考业务架构图配色系统" in skill_text
    assert "必须与参考文件逐项对齐" in skill_text
    for line in REFERENCE_BUSINESS_ARCHITECTURE_COLOR_LINES:
        assert line in skill_text
    for line in REFERENCE_BUSINESS_ARCHITECTURE_BACKGROUND_LINES:
        assert line in skill_text
    assert "主画布可叠加局部低对比网格" in skill_text
    assert "禁止 body 一个背景、SVG/主画布另一个背景" in skill_text
    assert "节点正文区域必须使用不透明白底或等效遮罩" in skill_text
    assert "状态色只能用于描边、角标、少量状态章" in skill_text
    assert "不得回退到旧版 `--paper: #fbfaf7`" in skill_text
    assert "标题字体默认使用 `clamp(24px, 2.8vw, 32px)`" in skill_text


def test_task_20260701_fault_html_uses_candidate_atlas_and_reference_color_system() -> None:
    """TASK_20260701_001 故障排查样例必须是真候选全集，而不是卡片/表格主图。"""

    html_text = (
        ROOT / "docs" / "TASK_20260701_001_Vibego启动失败Telegram连通性排查.html"
    ).read_text(encoding="utf-8")

    assert "candidate-atlas" in html_text
    assert html_text.count("candidate-panel") >= 5
    assert "candidate-sequence" in html_text
    assert "candidate-causal-chain" in html_text
    assert "candidate-bpmn" in html_text
    assert "candidate-before-after" in html_text
    assert "candidate-state-breakpoint" in html_text
    assert "--paper:#fbfdff" in html_text or "--paper: #fbfdff" in html_text
    assert "--panel:rgba(255,255,255,.9)" in html_text or "--panel: rgba(255,255,255,.9)" in html_text
    assert "--blue:#1f6fb2" in html_text or "--blue: #1f6fb2" in html_text
    assert "radial-gradient(circle at 18% 3%, rgba(214,233,255,.78), transparent 30rem)" in html_text
    assert "radial-gradient(circle at 78% 6%, rgba(228,246,239,.8), transparent 28rem)" in html_text
    assert "background-size:auto,auto,28px28px,28px28px,auto" in html_text.replace(" ", "")
    assert "font-size:clamp(24px,2.8vw,32px)" in html_text.replace(" ", "")
    assert "<svg" in html_text
    assert "marker-end" in html_text
    assert 'class="flow"' not in html_text
    assert 'button class="node"' not in html_text
    assert 'class="matrix"' not in html_text


def test_vibe_diagram_candidate_atlas_uses_accessible_tab_switching() -> None:
    """候选全集校准期应默认用按钮切换候选，避免把全部候选纵向压到同一阅读流。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "候选切换按钮" in skill_text
    assert "默认只显示当前按钮对应候选" in skill_text
    assert "所有候选仍必须完整生成在 HTML DOM 中" in skill_text
    assert "关闭 JavaScript 或脚本失败时必须降级为全部纵向展开" in skill_text
    assert "支持 URL hash 深链" in skill_text
    assert "左右方向键切换候选" in skill_text
    assert '`role="tablist"`' in skill_text
    assert '`role="tab"`' in skill_text
    assert '`aria-selected`' in skill_text
    assert '`role="tabpanel"`' in skill_text


def test_vibe_diagram_html_should_not_generate_skill_strip_or_top_right_handoff_cards() -> None:
    """后续 HTML 不再生成顶部 skill 条和右上角交付卡片。"""

    skill_text = _read_vibe_diagram_all_rules()
    self_check = skill_text.split("## 输出前自检", 1)[1]

    assert "标题顶部只保留任务编码" in skill_text
    assert "不要生成 skill 使用清单" in skill_text
    assert "不要生成标题顶部第二枚长胶囊" in skill_text
    assert "不再生成右上角交付信息卡片" in skill_text
    assert "交付动作、验证、风险和未覆盖点必须并入主图或正文区域" in skill_text
    assert "任务编码只显示编码值" in skill_text
    assert "不得写成 `任务编码：TASK_xxx`" in skill_text
    assert "任务编码和本次使用的skill必须放在标题顶部" not in skill_text
    assert "任务编码和本次使用的skill必须在同一行" not in skill_text
    assert "`skill-strip` 必须" not in skill_text
    assert "右上角固定任务交付信息卡片" not in skill_text
    assert "每个字段都必须是独立小卡片" not in skill_text
    assert "本次修改的影响功能点" not in skill_text
    assert "待用户执行事项" not in skill_text
    assert "是否没有生成 skill 使用清单" in self_check
    assert "是否没有生成右上角交付信息卡片" in self_check
    assert "skill-strip" not in self_check
    assert "右上角交付卡片" not in self_check

def test_task_20260701_fault_html_uses_tabs_and_top_right_handoff_meta() -> None:
    """故障排查候选全集样例应可通过按钮切换，并在右上角外显交付信息。"""

    html_text = (
        ROOT / "docs" / "TASK_20260701_001_Vibego启动失败Telegram连通性排查.html"
    ).read_text(encoding="utf-8")

    assert "candidate-tabs" in html_text
    assert 'role="tablist"' in html_text
    assert html_text.count('role="tab"') >= 5
    assert html_text.count('role="tabpanel"') >= 5
    assert 'aria-selected="true"' in html_text
    assert 'aria-controls="candidate-a"' in html_text
    assert 'data-candidate-target="candidate-b"' in html_text
    assert "selectCandidate" in html_text
    assert "hashchange" in html_text
    assert "ArrowRight" in html_text
    assert "ArrowLeft" in html_text
    assert "no-js" in html_text
    assert "title-meta" in html_text
    assert "task-code-pill" in html_text
    assert "skill-strip" in html_text
    assert ".title-meta{display:grid;grid-template-columns:autominmax(0,1fr)" in html_text.replace(" ", "")
    assert ".skill-strip{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" in html_text.replace(" ", "")
    assert '<span class="skill-strip" title="本次使用的skill：' in html_text
    assert ".top-title-column" in html_text
    assert html_text.index('<div class="title-meta"') < html_text.index('<div class="top-grid"')
    assert html_text.index('<div class="top-grid"') < html_text.index("<h1")
    assert html_text.index("<h1") < html_text.index('<div class="candidate-tabs"')
    assert html_text.index('<div class="candidate-tabs"') < html_text.index('<aside class="task-handoff"')
    assert html_text.index("task-code-pill") < html_text.index("<h1")
    assert html_text.index("skill-strip") < html_text.index("<h1")
    assert '<span class="task-code-pill">TASK_20260701_001</span>' in html_text
    assert "task-code-pill\">任务编码" not in html_text
    assert "task-handoff" in html_text
    handoff = html_text.split('<aside class="task-handoff"', 1)[1].split("</aside>", 1)[0]
    assert handoff.count("task-handoff-card") == 2
    assert "grid-template-columns: 1fr" in html_text
    assert "repeat(2,minmax(0,1fr))" not in html_text.replace(" ", "")
    assert "任务编码" not in handoff
    assert "本次使用的skill" not in handoff
    assert "本次修改的影响功能点" in html_text
    assert "待用户执行事项" in html_text


def test_task_20260701_delivery_html_uses_tabs_and_top_right_handoff_meta() -> None:
    """交付验收候选全集样例同样应支持按钮切换与右上角交付信息。"""

    html_text = (
        ROOT / "docs" / "TASK_20260701_002_vibe-diagram候选全集与背景网格硬约束.html"
    ).read_text(encoding="utf-8")

    assert "candidate-tabs" in html_text
    assert 'role="tablist"' in html_text
    assert html_text.count('role="tab"') >= 4
    assert html_text.count('role="tabpanel"') >= 4
    assert "selectCandidate" in html_text
    assert "hashchange" in html_text
    assert "title-meta" in html_text
    assert ".title-meta{display:grid;grid-template-columns:autominmax(0,1fr)" in html_text.replace(" ", "")
    assert ".skill-strip{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" in html_text.replace(" ", "")
    assert '<span class="skill-strip" title="本次使用的skill：' in html_text
    assert ".top-title-column" in html_text
    assert html_text.index('<div class="title-meta"') < html_text.index('<div class="top-grid"')
    assert html_text.index('<div class="top-grid"') < html_text.index("<h1")
    assert html_text.index("<h1") < html_text.index('<div class="candidate-tabs"')
    assert html_text.index('<div class="candidate-tabs"') < html_text.index('<aside class="task-handoff"')
    assert html_text.index("task-code-pill") < html_text.index("<h1")
    assert html_text.index("skill-strip") < html_text.index("<h1")
    assert '<span class="task-code-pill">TASK_20260701_002</span>' in html_text
    assert "task-handoff" in html_text
    handoff = html_text.split('<aside class="task-handoff"', 1)[1].split("</aside>", 1)[0]
    assert handoff.count("task-handoff-card") == 2
    assert "grid-template-columns: 1fr" in html_text
    assert "repeat(2,minmax(0,1fr))" not in html_text.replace(" ", "")
    assert "任务编码：TASK_20260701_002" not in html_text
    assert "任务编码" not in handoff
    assert "本次使用的skill" not in handoff
    assert "本次修改的影响功能点" in html_text
    assert "待用户执行事项" in html_text


def test_task_20260701_html_samples_use_reference_palette_handoff_cards_and_smaller_title() -> None:
    """本轮相关 HTML 样例必须统一参考配色、独立交付卡片和较小标题字号。"""

    samples = [
        ROOT / "docs" / "TASK_20260701_001_Vibego启动失败Telegram连通性排查.html",
        ROOT / "docs" / "TASK_20260701_002_vibe-diagram候选全集与背景网格硬约束.html",
        ROOT / "docs" / "TASK_20260701_003_候选全集按钮切换与右上交付信息.html",
        ROOT / "docs" / "TASK_20260701_004_参考配色固化与右上交付卡片.html",
        ROOT / "docs" / "TASK_20260701_005_vibe-diagram规则沉淀逐项审计.html",
    ]

    for sample in samples:
        html_text = sample.read_text(encoding="utf-8")
        compact = html_text.replace(" ", "")

        for line in REFERENCE_BUSINESS_ARCHITECTURE_COLOR_LINES:
            assert line in html_text
        for line in REFERENCE_BUSINESS_ARCHITECTURE_BACKGROUND_LINES:
            assert line in html_text
        assert 'h1 { margin: 0 0 14px; font-size: clamp(24px, 2.8vw, 32px); line-height: 1.18; letter-spacing: -.025em; }' in html_text
        assert "title-meta" in html_text
        assert "task-code-pill" in html_text
        assert "skill-strip" in html_text
        assert ".title-meta{display:grid;grid-template-columns:autominmax(0,1fr)" in compact
        assert ".skill-strip{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" in compact
        assert ".top-title-column" in html_text
        assert '<span class="skill-strip" title="本次使用的skill：' in html_text
        assert html_text.index('<div class="title-meta"') < html_text.index('<div class="top-grid"')
        assert html_text.index('<div class="top-grid"') < html_text.index("<h1")
        assert html_text.index("<h1") < html_text.index('<div class="candidate-tabs"')
        assert html_text.index('<div class="candidate-tabs"') < html_text.index('<aside class="task-handoff"')
        assert html_text.index("task-code-pill") < html_text.index("<h1")
        assert html_text.index("skill-strip") < html_text.index("<h1")
        handoff = html_text.split('<aside class="task-handoff"', 1)[1].split("</aside>", 1)[0]
        assert handoff.count("task-handoff-card") == 2
        assert "grid-template-columns: 1fr" in html_text
        assert "repeat(2,minmax(0,1fr))" not in compact
        assert "任务编码" not in handoff
        assert "本次使用的skill" not in handoff
        assert "本次修改的影响功能点" in html_text
        assert "待用户执行事项" in html_text


def test_vibe_diagram_skill_pack_exists_and_is_packaged() -> None:
    """vibe-diagram 图形表达 skill 必须作为 vibego 内置资源随包发布。"""

    skill_text = _read_vibe_diagram_core()
    all_rule_text = _read_vibe_diagram_all_rules()
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest_text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    frontmatter = skill_text.split("---", 2)[1]

    assert "name: vibe-diagram" in skill_text
    assert "description:" in skill_text
    assert "description: Use when" in frontmatter
    assert "单文件 HTML" in all_rule_text
    assert "直接发送 `.html` 文件" in all_rule_text
    assert "必须作为文件附件发送" in all_rule_text
    assert "禁止只发送 Markdown 链接" in all_rule_text
    assert "Telegram 中必须看到文件卡片" in all_rule_text
    assert "Codex 默认" in all_rule_text
    assert "`file://`" in all_rule_text
    assert "Telegram 来源" in all_rule_text
    assert "不需要 PNG" in all_rule_text
    assert "不要把 `file://` 作为 Telegram 主入口" in all_rule_text
    assert "系统架构图" in all_rule_text
    assert "BPMN-light" in all_rule_text
    assert "禁止把业务流程图画成密集表格" in all_rule_text
    assert "事件圆点" in all_rule_text
    assert "决策菱形" in all_rule_text
    assert "任何图都禁止文字被节点、连线、标签或背景层遮挡" in all_rule_text
    assert "顶部标题必须克制" in all_rule_text
    assert "顶部描述最多一行" in all_rule_text
    assert "箭头必须短、直、少交叉" in all_rule_text
    assert "连线不得穿过节点正文" in all_rule_text
    assert "发现遮挡或箭头混乱必须重排，不得交付" in all_rule_text
    assert "## 自动路由规则" in all_rule_text
    assert "路由冲突优先级" in all_rule_text
    assert "业务架构图 / 领域地图" in all_rule_text
    assert "状态 / 数据模型图" in all_rule_text
    assert "技术设计图" in all_rule_text
    assert "需求 / 决策沟通图" in all_rule_text
    assert "用户提到业务能力、领域、对象、规则、价值链" in all_rule_text
    assert "用户提到状态、状态流转、生命周期、实体关系、表结构" in all_rule_text
    assert "用户提到 API、数据库、模块、契约、部署、回滚" in all_rule_text
    assert "系统架构图规则" in all_rule_text
    assert "业务架构图规则" in all_rule_text
    assert "业务流程图规则" in all_rule_text
    assert "代码时序图规则" in all_rule_text
    assert "状态 / 数据模型图规则" in all_rule_text
    assert "故障排查图规则" in all_rule_text
    assert "页面设计稿规则" in all_rule_text
    assert "技术设计与需求决策图规则" in all_rule_text
    assert "## AGENTS 配合协议" in all_rule_text
    assert "当 AGENTS 或用户要求通过 HTML 图沟通时" in all_rule_text
    assert "功能迭代 / 开发设计" in all_rule_text
    assert "前后差异对比图" in all_rule_text
    assert "缺陷排查 / 故障分析" in all_rule_text
    assert "高亮根因节点" in all_rule_text
    assert "设计定稿 / 方案确认" in all_rule_text
    assert "每个 HTML 图都必须能脱离聊天记录独立阅读" in all_rule_text
    assert "data/skills/*/SKILL.md" in pyproject_text
    assert "data/skills/*/agents/*.yaml" in pyproject_text
    assert "recursive-include vibego_cli/data/skills" in manifest_text

def test_vibe_diagram_html_delivery_envelope_is_scoped_to_generated_html() -> None:
    """vibe-diagram 只在已经生成 HTML 图后压缩文本，不把普通问答强制成信封。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "## HTML 图交付后的文本压缩规则" in skill_text
    assert "仅当本轮已经生成或修改 HTML 图时" in skill_text
    assert "普通问答不要套用 HTML-only 或交付信封口径" in skill_text
    assert "不得在聊天里重复展开 HTML 已承载的分析、证据链、测试矩阵、风险回滚" in skill_text
    assert "[交付验收：移除阶段确认回退门禁](file:///绝对路径/xxx.html)" in skill_text
    assert "链接文字必须使用 HTML 内部 `<h1>` 主标题" in skill_text
    assert "不要写成固定的“打开 HTML”" in skill_text
    assert "[打开 HTML](file:///绝对路径/xxx.html)" not in skill_text
    assert "## HTML-only 交付信封模式" not in skill_text
    assert "所有实质内容必须写入项目内单文件自包含 HTML" not in skill_text


def test_vibe_diagram_delivery_reply_must_stay_concise() -> None:
    """普通 HTML 图交付也应压缩聊天文本，避免 HTML 与聊天双重长文。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "## HTML 图交付后的文本压缩规则" in skill_text
    assert "仅当本轮已经生成或修改 HTML 图时，最终回复只保留 HTML 路径/链接和待执行动作" in skill_text
    assert "链接文字必须使用 HTML 内部 `<h1>` 主标题" in skill_text
    assert "验证摘要" not in skill_text
    assert "不得在聊天里重复展开 HTML 已承载的分析、证据链、测试矩阵、风险回滚" in skill_text
    assert "普通问答不要套用 HTML-only 或交付信封口径" in skill_text


def test_vibe_diagram_svg_text_wrapping_rules() -> None:
    """vibe-diagram 必须禁止把长句直接放进单个 SVG text，避免文字溢出节点。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "SVG 节点文字规则" in skill_text
    assert "优先使用 HTML/CSS 节点承载可换行正文" in skill_text
    assert "若使用 SVG `<text>`，必须使用 `<tspan>` 分行、`foreignObject` 或缩短为编号标签" in skill_text
    assert "禁止把长句直接放进单个 SVG `<text>` 节点" in skill_text
    assert "交付前必须用 390px 与桌面宽度检查节点正文不溢出" in skill_text


def test_vibe_diagram_fault_diagram_rules_prioritize_storyline() -> None:
    """故障排查图规则应把主图收敛为来龙去脉，而不是根因长文堆叠。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "故障故事线" in skill_text
    assert "证据阶梯" in skill_text
    assert "假设裁决" in skill_text
    assert "行动闭环" in skill_text
    assert "主图不写长段根因分析" in skill_text
    assert "未验证只能标为最高可疑点" in skill_text


def test_vibe_diagram_rules_reject_card_pile_across_all_diagram_types() -> None:
    """所有图型都必须有图形语法，卡片可限用但不能退化成等权重卡片堆。"""

    skill_text = _read_vibe_diagram_all_rules()

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

    skill_text = _read_vibe_diagram_all_rules()

    assert "默认优先北向南" in skill_text
    assert "或采用左上角向右下角的时序图" in skill_text
    assert "完全从左到右只适合很短的流程图" in skill_text
    assert "超过 5 个主节点不得继续横向铺开" in skill_text
    assert "依靠浏览器缩放和响应式重排适配" in skill_text


def test_vibe_diagram_rules_prefer_vertical_canvas_and_highlight_change_focus() -> None:
    """HTML 图应利用纵向卷轴，并把根因/修法或前后对照做成视觉焦点。"""

    skill_text = _read_vibe_diagram_all_rules()

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

    skill_text = _read_vibe_diagram_all_rules()

    assert "before 固定在左侧或上方" in skill_text
    assert "after 固定在右侧或下方" in skill_text
    assert "禁止用左右交替排布表达前后差异" in skill_text
    assert "纵向卷轴中也必须保持左侧为当前/故障逻辑，右侧为修复后/目标逻辑" in skill_text
    assert "如果节点不属于前后对照，只能放在中轴、旁注或详情中" in skill_text


def test_vibe_diagram_flowchart_grammar_required_for_fault_and_iteration() -> None:
    """故障修复和开发迭代不能只画前后卡片列，主画布必须有流程图语法。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "前后对照只是容器，不是图形语法本身" in skill_text
    assert "功能迭代、开发设计和故障修复必须优先画流程图或流程化对照图" in skill_text
    assert "主画布必须包含开始/结束事件、活动节点、决策菱形、带标签箭头" in skill_text
    assert "before/after 每一侧内部也必须是流程图" in skill_text
    assert "禁止把 before/after 列画成普通说明卡片列表" in skill_text
    assert "根因节点和修法节点必须落在流程路径上" in skill_text
    assert "辅助证据优先写入流程节点内部" in skill_text


def test_vibe_diagram_fault_diagram_rejects_vertical_card_timeline_escape_hatch() -> None:
    """故障排查图不能用竖向故事线加同形圆角卡片逃逸流程图门禁。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "故障排查图主路径不得由一列同形圆角卡片承担" in skill_text
    assert "左侧竖线、步骤图标、箭头标签只能作为辅助连接" in skill_text
    assert "隐藏节点正文后只剩一列卡片和弱连接线" in skill_text
    assert "竖向故事线 + 圆角卡片列表" in skill_text
    assert "必须重画为流程图、因果链、泳道、时序轴或状态转换图" in skill_text


def test_vibe_diagram_must_not_hide_essential_details_behind_click_details() -> None:
    """HTML 图的关键细节必须静态可读，点击弹窗只能做补充，不能成为唯一信息源。"""

    skill_text = _read_vibe_diagram_all_rules()

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

    skill_text = _read_vibe_diagram_all_rules()

    assert "禁止交付原始工程草图感的 SVG" in skill_text
    assert "视觉质量必须服务流程阅读" in skill_text
    assert "使用统一的线宽、字号、留白、层级和图例" in skill_text
    assert "禁止粗暴边框、重阴影、满屏说明文字和低级默认样式" in skill_text
    assert "流程节点必须像图形符号而不是 UI 容器" in skill_text
    assert "主图应保留足够留白，文字短句化" in skill_text
    assert "颜色只用于状态和路径强调，不用于装饰" in skill_text


def test_vibe_diagram_background_should_use_premium_light_surfaces() -> None:
    """浅色背景应以有层次的高级白色为主，而不是扁平纯白或彩色底。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "浅色背景默认以白色为主色" in skill_text
    assert "白色背景不能是扁平纯白" in skill_text
    assert "参考业务架构图配色系统" in skill_text
    assert "--paper: #fbfdff" in skill_text
    assert "--panel: rgba(255,255,255,.9)" in skill_text
    assert "--shadow: 0 16px 42px rgba(20, 57, 92, .085)" in skill_text
    assert "28px 低对比工程网格" in skill_text
    assert "背景纹理必须全局统一" in skill_text
    assert "不要只在主画布局部铺网格或局部底纹" in skill_text
    assert "HTML body、SVG 主画布和弹窗遮罩以外的页面区域应共享同一背景系统" in skill_text
    assert "状态色只能用于描边、角标、少量状态章" in skill_text
    assert "背景只能提供质感和空间层次，不得抢主线" in skill_text


def test_vibe_diagram_style_must_not_override_accumulated_drawing_requirements() -> None:
    """视觉风格可调整，但不能回退用户已明确过的制图硬要求。"""

    skill_text = _read_vibe_diagram_all_rules()

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

    skill_text = _read_vibe_diagram_all_rules()
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

    skill_text = _read_vibe_diagram_all_rules()

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

    skill_text = _read_vibe_diagram_all_rules()

    assert "## 多方案 / 多候选表达规则" in skill_text
    assert "校准期多候选不再是页面设计稿专属" in skill_text
    assert "每次只对当前命中的生图类型生成候选全集" in skill_text
    assert "系统架构图：首选北向南分层拓扑，备选主请求中轴 + 控制/数据/兜底泳道、运行时依赖拓扑" in skill_text
    assert "业务架构图 / 领域地图：首选能力层 + 领域对象关系图" in skill_text
    assert "业务流程图：首选 BPMN-light 流程图" in skill_text
    assert "代码时序图：首选参与者列 + 时间向下时序图" in skill_text
    assert "状态 / 数据模型图：首选状态机图" in skill_text
    assert "故障排查图：首选排障时序图" in skill_text
    assert "页面设计稿：首选页面线框 / artboard" in skill_text
    assert "技术设计图：首选模块 / 契约 / 数据 / 发布回滚拓扑" in skill_text
    assert "需求 / 决策沟通图：首选决策树" in skill_text
    assert "交付验收图：首选验收账本 / 需求到证据签收表" in skill_text
    assert "Web 端多候选优先纵向展开" in skill_text
    assert "窄移动稿可在桌面视口横向 filmstrip 对比" in skill_text
    assert "真实移动端不得把横向滚动作为唯一阅读路径" in skill_text
    assert "每个候选必须有明确差异维度、适用边界、推荐理由和回滚或调整成本" in skill_text


def test_vibe_diagram_diagram_type_shape_contracts_are_explicit() -> None:
    """每一种图型都必须声明它应该长成什么样，避免继续退化成文字平铺。"""

    skill_text = _read_vibe_diagram_all_rules()

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
    assert "交付验收图必须长成需求到证据的验收轨道" in skill_text
    assert "如果某类图无法按上述形态画出主谓宾关系，必须换图型" in skill_text


def test_vibe_diagram_delivery_acceptance_uses_requirement_evidence_track() -> None:
    """交付验收图应把原始需求逐条映射到交付、证据和剩余动作，而不是报告卡片堆。"""

    core_text = _read_vibe_diagram_core()
    delivery_text = _read_vibe_diagram_reference("delivery-acceptance.md")
    skill_text = _read_vibe_diagram_all_rules()

    assert "交付验收、验收、收尾、验证闭环、完成交付" in core_text
    assert "references/delivery-acceptance.md" in core_text
    assert "## 交付验收图专用骨架" in delivery_text
    assert "交付验收图必须默认回答“原始要求是否逐条满足、证据是否足够、还剩什么动作”" in delivery_text
    assert "验收账本：原始要求 → 交付变更 → 验证证据 → 验收判定 → 待执行 / 回滚" in delivery_text
    assert "每一条用户需求或 AC 都必须拥有独立 R# 泳道" in delivery_text
    assert "验证命令、测试结果、截图核对和打包门禁必须贴在对应 R# 泳道内" in delivery_text
    assert "不得把“验证闭环”“代码影响点”“保留与回滚”拆成彼此等权重的底部卡片区" in delivery_text
    assert "如果读者需要在需求卡、代码卡、验证卡之间来回跳读才能判断某条需求是否通过，必须重画" in delivery_text
    assert "pass / warn / fail / blocked 必须同时使用文字标签和形状或图标" in delivery_text
    assert "交付验收图规则" in skill_text


def test_vibe_diagram_delivery_acceptance_tabs_are_only_for_candidate_layouts() -> None:
    """交付验收图只有在展示多个候选布局时才使用 tabs，不能把问答步骤做成 tabs。"""

    core_text = _read_vibe_diagram_core()
    delivery_text = _read_vibe_diagram_reference("delivery-acceptance.md")

    assert "tabs / role=tablist 只能用于同一图型的候选布局" in core_text
    assert "不得把步骤、补充问答、发布说明或普通章节导航做成 tab" in core_text
    assert "不要用移除按钮或删除面板来解决可读性问题" in core_text
    assert "若只交付一个结论或单页说明，不得生成候选按钮" in core_text
    assert "首选候选：验收账本 / 需求到证据签收表" in delivery_text
    assert "证据泳道图" in delivery_text
    assert "风险动作板" in delivery_text
    assert "交付时间线" in delivery_text
    assert "多个候选布局可使用候选按钮" in delivery_text
    assert "不得把用户追问、安装升级解释、发布说明或普通章节导航追加成新候选按钮" in delivery_text
    assert "交付验收图保留候选切换入口" not in delivery_text


def test_task_20260701_012_delivery_html_preserves_tabs_with_readable_panel_layouts() -> None:
    """当前交付 HTML 应保留按钮，并把四个面板改成不同的清晰布局。"""

    html_text = (ROOT / "docs" / "TASK_20260701_012_HTML交付链接使用主标题.html").read_text(
        encoding="utf-8"
    )

    assert 'role="tablist"' in html_text
    assert html_text.count('role="tab"') >= 4
    assert html_text.count('role="tabpanel"') >= 4
    assert "candidate-panel" in html_text
    assert "selectPanel" in html_text
    assert "acceptance-ledger" in html_text
    assert "evidence-lanes" in html_text
    assert "risk-action-board" in html_text
    assert "delivery-timeline" in html_text
    assert html_text.count("panel-summary") >= 4
    assert html_text.count("reading-order") >= 4
    assert "签收账本" in html_text
    assert "证据泳道" in html_text
    assert "风险动作板" in html_text
    assert "交付时间线" in html_text
    assert "移除按钮" not in html_text
    assert "证据矩阵热区" not in html_text
    assert "地铁验收线路" not in html_text


def test_task_20260701_014_delivery_html_is_single_page_not_followup_tabs() -> None:
    """TASK_014 是单页说明，不应把追问答复堆成 tab 导航。"""

    html_text = (
        ROOT / "docs" / "TASK_20260701_014_vibe_diagram独立Skill发布方案.html"
    ).read_text(encoding="utf-8")

    assert 'role="tablist"' not in html_text
    assert 'role="tab"' not in html_text
    assert 'role="tabpanel"' not in html_text
    assert "selectPanel" not in html_text
    assert "button class=\"tab" not in html_text
    button_text = "\n".join(re.findall(r"<button[^>]*>(.*?)</button>", html_text, flags=re.S))
    assert "发布到市场" not in button_text
    assert "自动升级" not in button_text
    assert "插件与 Skill 区别" not in button_text


def test_vibe_diagram_delivery_acceptance_must_expose_change_user_action_and_entry_impact() -> None:
    """交付验收图必须明确改了什么、影响入口、用户动作、重启脚本和验证方式。"""

    delivery_text = _read_vibe_diagram_reference("delivery-acceptance.md")

    assert "## 交付验收必答信息" in delivery_text
    assert "改了什么" in delivery_text
    assert "影响哪些功能入口" in delivery_text
    assert "用户需要执行什么脚本" in delivery_text
    assert "需要重启什么服务" in delivery_text
    assert "如何验证" in delivery_text
    assert "必须在首屏或紧邻主轨道的固定信息区直接外显" in delivery_text
    assert "待用户执行脚本 / 重启服务" in delivery_text
    assert "影响功能入口" in delivery_text
    assert "验证方式" in delivery_text
    assert "如果无需脚本或无需重启，也必须明确写“无需执行脚本”或“无需重启服务”" in delivery_text
    assert "不得只在最终聊天收尾字段里说明" in delivery_text


def test_vibe_diagram_delivery_acceptance_rejects_card_pile_sample() -> None:
    """交付验收图应在每个面板内部使用清晰结构，而不是低信息密度卡片线路图。"""

    delivery_text = _read_vibe_diagram_reference("delivery-acceptance.md")
    html_text = (ROOT / "docs" / "TASK_20260701_012_HTML交付链接使用主标题.html").read_text(
        encoding="utf-8"
    )

    assert "交付说明栏不能做成 5 张等权重卡片" in delivery_text
    assert "必须收敛成一条横向或纵向连通的信息带" in delivery_text
    assert "旧图问题只能作为旁注或反例标记" in delivery_text
    assert "主图第一视觉必须是当前面板的关系结构" in delivery_text
    assert "交付验收主画布不得再用矩形卡片节点承载每一步" in delivery_text
    assert "不要用移除按钮或删除面板来解决可读性问题" in delivery_text
    assert 'role="tablist"' in html_text
    assert "candidate-panel" in html_text
    assert "acceptance-ledger" in html_text
    assert "evidence-lanes" in html_text
    assert "risk-action-board" in html_text
    assert "delivery-timeline" in html_text
    assert "acceptance-subway" not in html_text
    assert "subway-station" not in html_text
    assert "acceptance-board" not in html_text
    assert "handoff-rail" not in html_text
    assert "evidence-map" not in html_text
    assert "handoff-grid" not in html_text
    assert "pain-axis" not in html_text
    assert "mini-grid" not in html_text
    assert "class=\"handoff\"" not in html_text
    assert "class=\"slot" not in html_text
    assert "class=\"map-row" not in html_text
    assert "class=\"rail-item" not in html_text


def test_vibe_diagram_delivery_acceptance_handoff_axis_is_vertical() -> None:
    """交付总控信息应默认纵向排列，避免横向拥挤且更符合用户验收顺序。"""

    delivery_text = _read_vibe_diagram_reference("delivery-acceptance.md")
    html_text = (ROOT / "docs" / "TASK_20260630_024_vibe-diagram交付验收图直观化.html").read_text(
        encoding="utf-8"
    )

    assert "改了什么、影响入口、如何验证、脚本 / 重启、未覆盖点默认纵向排列" in delivery_text
    assert "除非用户明确要求横向总控线，否则不要把这五项横向铺满首屏" in delivery_text
    assert "handoff-column" in html_text
    assert "vertical-handoff" in html_text
    assert "d=\"M120 70 H1060\"" not in html_text
    assert "61 项回归通过" in html_text
    assert "无需用户手动再跑脚本" in html_text
    assert "重启旧 worker / 新开长会话" in html_text


def test_vibe_diagram_delivery_acceptance_sample_uses_quiet_product_layout() -> None:
    """交付验收样例应避免重装饰工程图感，采用克制的产品式验收账本。"""

    delivery_text = _read_vibe_diagram_reference("delivery-acceptance.md")
    html_text = (ROOT / "docs" / "TASK_20260630_024_vibe-diagram交付验收图直观化.html").read_text(
        encoding="utf-8"
    )

    assert "交付验收样例优先使用克制的产品式验收账本或路线牌" in delivery_text
    assert "避免重装饰 SVG 图纸感" in delivery_text
    assert "acceptance-ledger" in html_text
    assert "status-stamp" in html_text
    assert "visual-thesis" in html_text
    assert "<svg" not in html_text
    assert "radial-gradient(circle at 8%" not in html_text


def test_vibe_diagram_delivery_acceptance_sample_keeps_unified_white_background() -> None:
    """交付验收样例不得因视觉返工改掉 vibe-diagram 统一白底背景系统。"""

    html_text = (ROOT / "docs" / "TASK_20260630_024_vibe-diagram交付验收图直观化.html").read_text(
        encoding="utf-8"
    )

    assert "--paper: #fbfdff" in html_text
    assert "--panel: rgba(255,255,255,.9)" in html_text
    assert "--blue: #1f6fb2" in html_text
    assert "--green: #17785a" in html_text
    assert "radial-gradient(circle at 18% 3%, rgba(214,233,255,.78), transparent 30rem)" in html_text
    assert "radial-gradient(circle at 78% 6%, rgba(228,246,239,.8), transparent 28rem)" in html_text
    assert "background-size: auto, auto, 28px 28px, 28px 28px, auto" in html_text
    assert "参考业务架构图配色系统" in html_text
    assert "linear-gradient(180deg, #ffffff 0%, #f7fbff 52%, #fbfdff 100%)" not in html_text
    assert "oklch(98.2% 0.008 88)" not in html_text
    assert "oklch(99.4% 0.006 92)" not in html_text


def test_vibe_diagram_delivery_acceptance_status_stamp_text_does_not_overflow() -> None:
    """状态章内的小字必须可换行且不能把测试与同步状态塞成一行。"""

    html_text = (ROOT / "docs" / "TASK_20260630_024_vibe-diagram交付验收图直观化.html").read_text(
        encoding="utf-8"
    )

    assert "class=\"status-meta\"" in html_text
    assert "<span>61 tests · sync ok</span>" not in html_text
    assert "<span>61 tests</span><span>sync ok</span>" in html_text
    assert ".status-meta { display: grid;" in html_text
    assert "max-width: 82px" in html_text
    assert "overflow-wrap: normal" in html_text


def test_vibe_diagram_business_architecture_must_be_domain_map_not_card_report() -> None:
    """业务架构图应默认是业务化领域地图，不能退化成章节化卡片报告。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "## 业务架构图专用骨架" in skill_text
    assert "业务架构图 / 领域地图必须默认回答“业务能力如何被角色、对象和规则约束”" in skill_text
    assert "参与方边界 → 业务能力层 → 业务对象关系 → 规则约束热区 → 服务结果 / 决策轴" in skill_text
    assert "主图必须是一张首屏可读的领域地图" in skill_text
    assert "禁止把摘要、事实口径、能力、对象、规则、证据拆成章节化卡片报告" in skill_text
    assert "业务对象必须业务化命名" in skill_text
    assert "客服触点、咨询工单、业务场景、服务依据、人工接续、服务质量资产" in skill_text
    assert "技术对象名只能作为点击详情或证据补充" in skill_text
    assert "对象关系必须用动词显式连接：拥有、提出、承接、产生、约束、查询、交接、沉淀、反哺" in skill_text
    assert "规则约束热区必须贴近被约束的对象或能力" in skill_text
    assert "如果去掉标题后只剩“摘要卡片 + 事实卡片 + 能力卡片 + 对象卡片 + 规则卡片”，必须重画" in skill_text
    assert "如果技术对象名比业务对象名更醒目，必须重写节点命名" in skill_text


def test_vibe_diagram_layout_arrow_and_collision_rules_are_explicit() -> None:
    """skill 必须定义画布利用、箭头锚点、防重叠和文字防溢出的硬门禁。"""

    skill_text = _read_vibe_diagram_all_rules()

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

    skill_text = _read_vibe_diagram_all_rules()

    assert "节点优先承载关键信息" in skill_text
    assert "不要为了保持两行节点而把信息拆到底部证据卡片" in skill_text
    assert "节点可以限制宽度，但高度必须随内容自动增长" in skill_text
    assert "优先增高节点和自动换行，而不是把关键细节挪到图外底部卡片" in skill_text
    assert "证据、风险、测试、回滚默认写入对应主路径节点" in skill_text
    assert "底部证据/矩阵只承载跨多个节点的汇总或原始长材料索引" in skill_text
    assert "禁止用 line-clamp、max-height 或 overflow:hidden 裁掉节点正文" in skill_text


def test_vibe_diagram_raw_evidence_should_live_in_node_details_not_bottom_piles() -> None:
    """原始证据不应默认堆到底部，节点摘要可见，长证据进入节点点击详情。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "原始证据默认进入对应节点的点击详情" in skill_text
    assert "节点内静态展示证据编号、结论、可信度或状态即可" in skill_text
    assert "不要默认在底部铺完整证据卡片" in skill_text
    assert "底部证据区只用于跨节点冲突裁决、全局证据索引或测试矩阵" in skill_text
    assert "点击详情可以承载文件路径、行号、日志片段、SQL、JSON、命令输出和截图说明" in skill_text


def test_vibe_diagram_system_architecture_must_read_as_global_topology_not_layered_cards() -> None:
    """系统架构图必须第一眼读出全局拓扑，而不是分层卡片清单加证据文字。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "系统架构图不是组件清单、证据清单、编号实现分层或接口调用链" in skill_text
    assert "首屏必须出现一张宽画布架构拓扑总览" in skill_text
    assert "入口、应用、支撑能力、数据面、基础设施和管理侧栏如何协作" in skill_text
    assert "层间必须画出主请求流、控制流、数据读写流或兜底流" in skill_text
    assert "同层节点只保留组件名、职责、协议/接口、运行状态和关键约束" in skill_text
    assert "源码证据、文件路径和长说明进入该节点点击详情" in skill_text
    assert "如果第一眼只能看到多列卡片和证据文字，看不出入口到数据面的流向，必须重画" in skill_text


def test_vibe_diagram_system_architecture_defaults_to_single_presentation_diagramspec() -> None:
    """普通系统架构图默认应输出一张 presentation 定稿图，并先形成 DiagramSpec。"""

    core_text = _read_vibe_diagram_core()
    system_text = _read_vibe_diagram_reference("system-architecture.md")

    assert "普通单图请求默认只生成一张首选 presentation 图" in core_text
    assert "系统架构普通请求默认只生成一张 presentation 定稿图" in system_text
    assert "mode=presentation" in system_text
    assert "DiagramSpec" in system_text
    assert "diagram_type=system-architecture" in system_text
    assert "audience=大众认知架构图读者" in system_text
    assert "不得生成候选 tab、候选 panel 或候选对比矩阵" in system_text
    assert "入口 rail + 智能体应用层 + 能力支撑层 + 数据层 + 基础设施层 + 管理/运维侧栏" in system_text


def test_vibe_diagram_system_architecture_has_architecture_archetype_and_evidence_budget() -> None:
    """系统架构 reference 应内置大众架构图模板、图标语义和主图证据预算。"""

    system_text = _read_vibe_diagram_reference("system-architecture.md")

    assert "## 大众系统架构 archetype 模板" in system_text
    assert "外部入口 rail" in system_text
    assert "用户/渠道入口必须画成左侧竖向 rail" in system_text
    assert "核心应用/服务层必须是主视觉中心" in system_text
    assert "能力支撑层必须用图标 + 短标签" in system_text
    assert "数据层必须区分知识库、会话数据、用户数据、运营数据" in system_text
    assert "基础设施层必须横向铺成底座" in system_text
    assert "管理/运维侧栏必须靠右收纳" in system_text
    assert "主请求流用实线箭头" in system_text
    assert "数据读写流用双向或下沉箭头" in system_text
    assert "证据预算" in system_text
    assert "主图节点禁止展示源码路径、E# 证据列表或长运行验证命令" in system_text
    assert "每个节点主文案最多 3 行" in system_text
    assert "文件路径、行号、命令输出和长证据只能放入点击详情" in system_text


def test_vibe_diagram_system_architecture_locks_polished_presentation_micro_rules() -> None:
    """系统架构图应把本次复刻版反馈沉淀成稳定的提示词契约。"""

    system_text = _read_vibe_diagram_reference("system-architecture.md")

    assert "## 系统架构图 presentation 版式锁定" in system_text
    assert "左侧入口 rail + 中央应用层 + 南向能力/数据/基础设施 + 右侧管理/运维/兜底侧栏" in system_text
    assert "节点内部内容必须作为一个整体自动水平居中和垂直居中" in system_text
    assert "高节点默认图标在上、文案在下" in system_text
    assert "紧凑节点默认图标在左、文案在右" in system_text
    assert "优先使用 `foreignObject` + HTML/CSS `grid`/`flex` 承载节点内容" in system_text
    assert "不得因为局部图例丑就全局去掉 emoji" in system_text
    assert "每条箭头必须有明确源节点、目标节点、边缘锚点和关系标签" in system_text
    assert "如果读者会问“这个箭头指向哪里”，必须改名或重连" in system_text
    assert "浏览器标注反馈默认只修改用户选中区域及直接相邻关系" in system_text
    assert "横向滚动条、节点内滚动条、文字溢出、图标/文案未居中、箭头穿字、孤立箭头标签均为失败" in system_text


def test_vibe_diagram_system_architecture_v6_router_contract_is_documented() -> None:
    """系统架构 reference 必须固化 v6 路由器，而不是继续只描述某个固定业务 archetype。"""

    core_text = _read_vibe_diagram_core()
    system_text = _read_vibe_diagram_reference("system-architecture.md")

    assert "系统架构图命中后必须进入 `references/system-architecture.md` 的 v6 路由器" in core_text
    assert "## 系统架构图 v6 路由器" in system_text
    assert "C4 主线" in system_text
    for selected_view in ("context", "container", "component", "deployment"):
        assert selected_view in system_text
    for specialty in (
        "logical",
        "data-architecture",
        "data-flow",
        "api-integration",
        "event-driven",
        "network",
        "security",
        "identity",
        "resilience",
        "observability",
        "ci-cd",
    ):
        assert specialty in system_text
    for handoff in ("business-architecture", "domain-model", "er", "state-machine", "sequence"):
        assert handoff in system_text
    assert "selected_view=" in system_text
    assert "data-system-arch-view" in system_text
    assert "data-routing-confidence" in system_text
    assert "低于 0.45" in system_text
    assert "槽位可变" in system_text
    assert "不要把所有模板混入一张图" in system_text
    assert "不要把客服 / Agent / RAG / LLM / SDK / Gateway 等词当作默认业务内容" in system_text


def test_vibe_diagram_system_architecture_lint_requires_v6_diagramspec_attrs(
    tmp_path: Path,
) -> None:
    """v6 落地后，普通系统架构图必须带路由后的机读 DiagramSpec 属性。"""

    lint_script = VIBE_DIAGRAM_SCRIPTS / "vibe_diagram_lint.py"
    no_spec_html = tmp_path / "no_v6_spec.html"
    no_spec_html.write_text(
        """<!doctype html><html><body>
        <main data-diagram-type="system-architecture"
              data-diagram-grammar="system-architecture-presentation">
          <svg viewBox="0 0 1500 860" aria-label="系统架构图">
            <foreignObject x="0" y="0" width="260" height="90">
              <div xmlns="http://www.w3.org/1999/xhtml" class="node-content">入口 接入 用户 渠道</div>
            </foreignObject>
            <foreignObject x="320" y="0" width="260" height="90">
              <div xmlns="http://www.w3.org/1999/xhtml" class="node-content">应用 服务 能力支撑 数据 基础设施 管理 运维</div>
            </foreignObject>
            <path data-flow="主请求流"></path><path data-flow="数据读写流"></path>
          </svg>
          <section>入口 接入 用户 渠道 应用 服务 能力 支撑 数据 数据库 基础设施 云 存储 网络 安全 监控 管理 运维 权限 审计 评估 主请求流</section>
        </main></body></html>""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(lint_script), "--type", "system-architecture", str(no_spec_html)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "系统架构图必须声明 data-system-arch-view" in result.stdout
    assert "系统架构图必须声明 data-routing-confidence" in result.stdout


def test_vibe_diagram_system_architecture_lint_rejects_mixed_all_template_router_canvas(
    tmp_path: Path,
) -> None:
    """系统架构产物不能把 C4、专项扩展和跨图型转交全部混进同一张最终架构图。"""

    lint_script = VIBE_DIAGRAM_SCRIPTS / "vibe_diagram_lint.py"
    mixed_html = tmp_path / "mixed_all_templates.html"
    mixed_html.write_text(
        """<!doctype html><html><body>
        <main data-diagram-type="system-architecture"
              data-diagram-grammar="system-architecture-presentation"
              data-system-arch-view="container"
              data-routing-confidence="0.86">
          <svg viewBox="0 0 1500 860" aria-label="系统架构图">
            <foreignObject x="0" y="0" width="320" height="100">
              <div xmlns="http://www.w3.org/1999/xhtml" class="node-content">Context Container Component Deployment Logical Data Architecture Data Flow API Integration Event-driven Network Security Identity Resilience Observability CI/CD Business Architecture Domain Model ER State Machine Sequence</div>
            </foreignObject>
            <path data-flow="主请求流"></path><path data-flow="数据读写流"></path>
          </svg>
          <section>入口 接入 用户 渠道 应用 服务 能力 支撑 数据 数据库 基础设施 云 存储 网络 安全 监控 管理 运维 权限 审计 评估 主请求流</section>
        </main></body></html>""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(lint_script), "--type", "system-architecture", str(mixed_html)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "不要把 C4 主线、专项扩展和跨图型转交全部混入同一张最终架构图" in result.stdout


def test_vibe_diagram_system_architecture_lint_rejects_unattributed_fixed_business_defaults(
    tmp_path: Path,
) -> None:
    """模板槽位化后，客服/Agent/RAG/LLM 等词不能作为无来源默认内容出现在普通架构图。"""

    lint_script = VIBE_DIAGRAM_SCRIPTS / "vibe_diagram_lint.py"
    fixed_html = tmp_path / "fixed_business_defaults.html"
    fixed_html.write_text(
        """<!doctype html><html><body>
        <main data-diagram-type="system-architecture"
              data-diagram-grammar="system-architecture-presentation"
              data-system-arch-view="container"
              data-routing-confidence="0.91">
          <svg viewBox="0 0 1500 860" aria-label="系统架构图">
            <foreignObject x="0" y="0" width="280" height="100">
              <div xmlns="http://www.w3.org/1999/xhtml" class="node-content">客服智能体 Agent RAG LLM SDK Gateway</div>
            </foreignObject>
            <path data-flow="主请求流"></path><path data-flow="数据读写流"></path>
          </svg>
          <section>入口 接入 用户 渠道 应用 服务 能力 支撑 数据 数据库 基础设施 云 存储 网络 安全 监控 管理 运维 权限 审计 评估 主请求流</section>
        </main></body></html>""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(lint_script), "--type", "system-architecture", str(fixed_html)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "系统架构图疑似使用固定业务默认内容" in result.stdout


def test_vibe_diagram_system_architecture_presentation_overrides_007_runtime_pipeline() -> None:
    """最新复核表明 007 编号实现分层会把系统架构图拉回代码链路图，应降级为例外。"""

    system_text = _read_vibe_diagram_reference("system-architecture.md")

    assert "presentation 版式锁定优先级高于 007 宏观拓扑基线" in system_text
    assert "007 仅作为内部运行时证据架构的例外形态" in system_text
    assert "普通系统架构图不得使用 `1.`、`2.`、`3.` 编号 lane 作为主层标题" in system_text
    assert "SDK 表现层、HTTP 接入、Controller、Facade、Handler、DTO 等实现名只能进入节点详情" in system_text
    assert "不得把主画布标题写成接口调用链或代码流水线" in system_text
    assert "系统架构图默认优先采用 007 宏观拓扑基线" not in system_text
    assert "不要因为“还有优化空间”就自动升级为多泳道、五列表格或分段故事线" not in system_text


def test_vibe_diagram_system_architecture_lint_rejects_card_report_and_accepts_presentation(
    tmp_path: Path,
) -> None:
    """产物级 lint 应拦截候选 tab + 证据卡片页，并放行一张 presentation 架构图。"""

    lint_script = VIBE_DIAGRAM_SCRIPTS / "vibe_diagram_lint.py"
    assert lint_script.is_file()

    bad_html = tmp_path / "bad.html"
    bad_html.write_text(
        """<!doctype html><html><body>
        <main data-diagram-type="system-architecture">
          <div role="tablist"><button role="tab">分层图</button></div>
          <section id="panel-layered">
            <article class="node">入口 E1 /tmp/src/app.py:10</article>
            <article class="node">应用 E2 /tmp/src/app.py:20</article>
            <article class="node">数据 E3 /tmp/src/db.py:30</article>
            <article class="evidence">E4 文件路径与命令输出</article>
            <article class="evidence">E5 文件路径与命令输出</article>
            <article class="evidence">E6 文件路径与命令输出</article>
          </section>
        </main></body></html>""",
        encoding="utf-8",
    )
    bad = subprocess.run(
        [sys.executable, str(lint_script), "--type", "system-architecture", str(bad_html)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert bad.returncode == 1
    assert "普通系统架构图不得生成候选 tab" in bad.stdout
    assert "系统架构图必须包含真实 SVG 主画布" in bad.stdout

    good_html = tmp_path / "good.html"
    good_html.write_text(
        """<!doctype html><html><body>
        <main data-diagram-type="system-architecture"
              data-diagram-grammar="system-architecture-presentation"
              data-system-arch-view="container"
              data-routing-confidence="0.88"
              data-arch-archetype="entry-rail-application-capability-data-infra-ops">
          <svg viewBox="0 0 1500 860" aria-label="系统架构图">
            <foreignObject x="0" y="0" width="220" height="80">
              <div class="node-content">应用服务核心</div>
            </foreignObject>
            <text>用户接入 rail → 应用/服务核心 → 能力支撑层 → 数据层 → 基础设施层 → 管理/运维侧栏</text>
            <path data-flow="主请求流"></path>
            <path data-flow="数据读写流"></path>
          </svg>
          <section>Web APP 入口 接入 用户 渠道 应用 服务 会话 能力 支撑 工具 工作流 检索 数据层 知识库 会话数据 用户数据 运营数据 数据库 基础设施 云计算 存储 网络 安全 监控告警 管理 运维 权限 审计 评估 主请求流</section>
        </main></body></html>""",
        encoding="utf-8",
    )
    good = subprocess.run(
        [sys.executable, str(lint_script), "--type", "system-architecture", str(good_html)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert good.returncode == 0
    assert "OK" in good.stdout


def test_vibe_diagram_system_architecture_lint_rejects_marker_only_card_canvas(
    tmp_path: Path,
) -> None:
    """不能只贴 presentation 标记；没有真实 SVG/图标/连线层的节点网格仍应失败。"""

    lint_script = VIBE_DIAGRAM_SCRIPTS / "vibe_diagram_lint.py"
    marker_only_html = tmp_path / "marker_only.html"
    marker_only_html.write_text(
        """<!doctype html><html><body>
        <section class="architecture-canvas"
                 data-diagram-type="system-architecture"
                 data-diagram-grammar="system-architecture-presentation">
          <div class="rail">入口 Web APP 微信</div>
          <section class="flow-stack">
            <button class="node">应用 Agent 会话服务</button>
            <button class="node">能力支撑 RAG LLM 工具 插件</button>
            <button class="node">数据 知识库 会话数据 用户数据</button>
            <button class="node">基础设施 云 存储 网络 安全 监控</button>
            <button class="node">管理 运维 权限 审计 评估</button>
          </section>
          <button class="evidence-button">证据索引 文件路径</button>
          <p>主请求流 → 数据读写流 → 反馈流</p>
        </section></body></html>""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(lint_script), "--type", "system-architecture", str(marker_only_html)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "系统架构图必须包含真实 SVG 主画布" in result.stdout
    assert "presentation 标记不能替代图形层" in result.stdout


def test_vibe_diagram_system_architecture_lint_rejects_scroll_canvas_raw_svg_text_and_ambiguous_arrow(
    tmp_path: Path,
) -> None:
    """复刻版暴露的横向滚动、未自居中节点和孤立箭头标签应进入产物级门禁。"""

    lint_script = VIBE_DIAGRAM_SCRIPTS / "vibe_diagram_lint.py"
    bad_html = tmp_path / "scroll_and_raw_text.html"
    bad_html.write_text(
        """<!doctype html><html><head><style>
        .canvas-wrap { overflow: auto; min-width: 1530px; }
        </style></head><body>
        <main data-diagram-type="system-architecture"
              data-diagram-grammar="system-architecture-presentation">
          <section class="canvas-wrap">
            <svg aria-label="系统架构图">
              <g class="node">
                <rect x="10" y="10" width="120" height="48"></rect>
                <text x="18" y="32">📱</text>
                <text x="44" y="32">入口 Web APP 微信</text>
              </g>
              <text>主请求入口</text>
              <path data-flow="主请求流"></path>
              <path data-flow="数据读写流"></path>
            </svg>
          </section>
          <section>入口 接入 用户 渠道 应用 Agent 智能体 会话 能力支撑 RAG LLM 大模型 工具 插件 数据 知识库 会话数据 用户数据 运营数据 基础设施 云 存储 网络 安全 监控 管理 运维 权限 审计 评估</section>
        </main></body></html>""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(lint_script), "--type", "system-architecture", str(bad_html)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "系统架构图主画布不得依赖横向滚动或超大 min-width" in result.stdout
    assert "系统架构图节点内容必须使用 foreignObject 或自居中 HTML 容器" in result.stdout
    assert "检测到含糊箭头标签“主请求入口”" in result.stdout


def test_vibe_diagram_system_architecture_lint_rejects_numbered_runtime_pipeline_canvas(
    tmp_path: Path,
) -> None:
    """最新失败样式有 SVG/foreignObject，但仍是窄画布 + 编号实现流水线，lint 必须拦截。"""

    lint_script = VIBE_DIAGRAM_SCRIPTS / "vibe_diagram_lint.py"
    bad_html = tmp_path / "numbered_pipeline.html"
    bad_html.write_text(
        """<!doctype html><html><body>
        <main data-diagram-type="system-architecture"
              data-diagram-grammar="system-architecture-presentation">
          <svg viewBox="0 0 1180 860" aria-label="系统架构图">
            <text class="canvas-title" x="28" y="42">主请求流：入口 → SDK → /api/cs/v1/* → Facade → Agent → 数据/兜底</text>
            <text class="lane-label" x="228" y="120">1. 小程序接入 / SDK 表现层</text>
            <text class="lane-label" x="228" y="254">2. HTTP 接入 / Gateway</text>
            <text class="lane-label" x="228" y="388">3. 应用层 / 会话编排</text>
            <foreignObject x="40" y="140" width="140" height="70">
              <button xmlns="http://www.w3.org/1999/xhtml" class="node-content"><span>📱</span><strong>入口 rail</strong></button>
            </foreignObject>
            <foreignObject x="220" y="140" width="170" height="70">
              <button xmlns="http://www.w3.org/1999/xhtml" class="node-content"><span>🤖</span><strong>应用 Agent 智能体 会话</strong></button>
            </foreignObject>
            <path data-flow="主请求流"></path><path data-flow="数据读写流"></path>
          </svg>
          <section>入口 接入 用户 渠道 应用 Agent 智能体 会话 能力支撑 RAG LLM 大模型 工具 插件 数据 知识库 会话数据 用户数据 运营数据 基础设施 云 存储 网络 安全 监控 管理 运维 权限 审计 评估</section>
        </main></body></html>""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(lint_script), "--type", "system-architecture", str(bad_html)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "普通系统架构图不得退化为编号实现分层" in result.stdout
    assert "系统架构图 presentation 画布过窄" in result.stdout
    assert "不得把主画布标题写成接口调用链或代码流水线" in result.stdout


def test_vibe_diagram_system_architecture_supports_plane_swimlanes_for_medium_complexity() -> None:
    """中等复杂系统架构可用主请求中轴 + 控制/数据/兜底泳道，而不是继续堆卡片。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "中等复杂系统架构可以使用“主请求中轴 + 控制面 / 数据面 / 兜底面泳道”" in skill_text
    assert "控制面不要与主请求节点等权重平铺" in skill_text
    assert "数据/知识面应作为南向或侧向依赖泳道" in skill_text
    assert "兜底/人工流适合画成侧边 rail" in skill_text
    assert "运行语义条" in skill_text
    assert "状态角标" in skill_text


def test_vibe_diagram_system_architecture_swimlanes_must_preserve_readability() -> None:
    """泳道式系统架构不能退化成多列表格，必须以单主线分段展开降低认知负担。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "泳道不是表格，不得把层级、控制面、主请求、数据面、兜底面全部做成等权重多列网格" in skill_text
    assert "先保留一条粗主线，再把控制、数据、兜底折成贴近当前阶段的侧向胶囊或短注" in skill_text
    assert "单个视口内主路径节点建议 3-5 个" in skill_text
    assert "如果需要 5 列以上才能表达，必须改为分段卷轴、阶段轨道或多张局部小图" in skill_text
    assert "侧向泳道只能服务当前主线阶段，不得形成第二张需要逐格阅读的矩阵" in skill_text


def test_vibe_diagram_system_architecture_demotes_007_to_internal_runtime_exception() -> None:
    """用户复核确认 007 会诱导编号实现分层，普通系统架构图应优先 presentation 模板。"""

    skill_text = _read_vibe_diagram_all_rules()

    assert "presentation 版式锁定优先级高于 007 宏观拓扑基线" in skill_text
    assert "007 仅作为内部运行时证据架构的例外形态" in skill_text
    assert "普通系统架构图不得使用 `1.`、`2.`、`3.` 编号 lane 作为主层标题" in skill_text
    assert "北向南层级 + 层间流向分隔条 + 节点内摘要 + 点击详情证据" in skill_text
    assert "系统架构图默认优先采用 007 宏观拓扑基线" not in skill_text


def test_vibe_diagram_system_architecture_rule_priority_keeps_swimlanes_as_exception() -> None:
    """系统架构图规则必须先讲 presentation 默认，再讲泳道/007 例外。"""

    skill_text = _read_vibe_diagram_all_rules()

    baseline_index = skill_text.index("presentation 版式锁定优先级高于 007 宏观拓扑基线")
    exception_index = skill_text.index("系统架构图例外形态")

    assert baseline_index < exception_index
    assert "系统架构图例外形态不是优化方向" in skill_text
    assert "除非用户明确要求或 presentation 模板无法区分两个以上独立平面" in skill_text
    assert "为什么大众架构图模板不够用" in skill_text
    assert "不要把 010/011 当作推荐模板" in skill_text


def test_vibe_diagram_fault_debugging_dedicated_skeleton_prioritizes_current_chain() -> None:
    """故障排查图应先画当前现状链路，再把证据、根因、修法和回滚贴到链路上。"""

    skill_text = _read_vibe_diagram_all_rules()

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

    skill_text = _read_vibe_diagram_all_rules()

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

    skill_text = _read_vibe_diagram_all_rules()
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


def test_sync_agents_block_installs_native_vibe_diagram_skill_without_default_index(tmp_path: Path) -> None:
    """shell 同步默认安装 native skill，不再把 vibe-diagram 索引写入 AGENTS。"""

    target = tmp_path / "AGENTS.md"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
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
    assert "# Vibego 内置 Skills" not in synced_text
    assert "## Skill: vibe-diagram" not in synced_text
    assert "name: vibe-diagram" not in synced_text
    assert "description: Use when the user asks to draw" not in synced_text
    assert "vibego-skill-source" not in synced_text
    assert (tmp_path / "home" / ".codex" / "skills" / "vibe-diagram" / "SKILL.md").exists()
    assert (
        tmp_path
        / "home"
        / ".codex"
        / "skills"
        / "vibe-diagram"
        / "references"
        / "delivery-acceptance.md"
    ).exists()
    assert (tmp_path / "home" / ".agents" / "skills" / "vibe-diagram" / "SKILL.md").exists()
    assert "当用户要求画系统架构图、业务流程图、代码时序图、故障排查图、页面设计稿" not in synced_text
    assert "最终必须直接发送 `.html` 文件" not in synced_text
    assert "## 自动路由规则" not in synced_text
    assert "## AGENTS 配合协议" not in synced_text
    assert "## HTML-only 交付信封模式" not in synced_text
    assert "卡片堆积不是图" not in synced_text
    assert "宽度服务阅读，长度服务推理" not in synced_text
    assert "移动端不能把整张 SVG 等比缩成缩略图" not in synced_text
    assert "顶部标题必须以触发的生图类型开头" not in synced_text
    assert "参考业务架构图配色系统" not in synced_text
    assert "--paper: #fbfdff" not in synced_text
    assert "## 图型规则索引" not in synced_text
    assert "references/system-architecture.md" not in synced_text
    assert "references/business-architecture.md" not in synced_text
    assert "references/fault-debugging.md" not in synced_text
    assert "references/page-mockup.md" not in synced_text
    assert "箭头只能连接节点边缘锚点" not in synced_text
    assert "如果任一节点重叠、线穿字、文字溢出，必须重排" not in synced_text
    assert "状态色只能用于描边、角标、少量状态章" not in synced_text
    assert "系统架构图默认优先采用 007 宏观拓扑基线" not in synced_text
    assert "故障排查图必须先画当前现状链路，再画根因和修法" not in synced_text
    assert "功能迭代图必须先画当前功能和当前实现，再画目标和差异" not in synced_text
    assert "<!-- vibego-agents:end -->" in synced_text


def test_sync_agents_block_can_emit_legacy_vibe_diagram_skill_index(tmp_path: Path) -> None:
    """显式 legacy 开关打开时，shell 同步仍可写旧索引。"""

    target = tmp_path / "AGENTS.md"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PYTHON_EXEC": sys.executable,
            "TARGET_AGENTS_FILE": str(target),
            "VIBEGO_AGENTS_LEGACY_SKILL_INDEX": "1",
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

    skill_index = synced_text.split("## Skill: vibe-diagram", 1)[1]

    assert "# Vibego 内置 Skills" in synced_text
    assert "## Skill: vibe-diagram" in synced_text
    assert "complex logic" in skill_index
    assert "visual explanation" in skill_index
    assert "逻辑结构" in skill_index
    assert "HTML-first substantive answer" not in skill_index
    assert "why/how explanations" not in skill_index
    assert "delivery envelope" not in skill_index
    assert "为什么" not in skill_index
    assert "怎么做" not in skill_index
    assert "实质沟通" not in skill_index
    assert "命中该 skill 时，先读取上方 vibego-skill-source 指向的 SKILL.md 全文" in skill_index
    assert "## HTML-only 交付信封模式" not in synced_text
