#!/usr/bin/env python3
"""轻量检查 vibe-diagram 生成的单文件 HTML 图。

本脚本是 skill 附带的产物级自检工具，不依赖第三方包；当前重点约束
system-architecture，避免普通架构图退化为候选 tab + 证据卡片页。
"""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


class HtmlSignals(HTMLParser):
    """Collect coarse, dependency-free signals from a generated HTML file."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.tag_counts: dict[str, int] = {}
        self.roles: list[str] = []
        self.classes: list[str] = []
        self.attrs: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag_counts[tag.lower()] = self.tag_counts.get(tag.lower(), 0) + 1
        for name, value in attrs:
            if value is None:
                continue
            name_lower = name.lower()
            self.attrs.setdefault(name_lower, []).append(value)
            if name_lower == "role":
                self.roles.append(value)
            if name_lower == "class":
                self.classes.extend(value.split())

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text_parts.append(data)

    @property
    def text(self) -> str:
        return " ".join(part.strip() for part in self.text_parts if part.strip())

    def attr_values(self, name: str) -> list[str]:
        return self.attrs.get(name.lower(), [])


def _contains_any(text: str, words: Iterable[str]) -> bool:
    return any(word in text for word in words)


SOURCE_PATH_RE = re.compile(
    r"(?:/Users/|/src/|/tmp/|[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|java|md|sql):\d+)"
)
EVIDENCE_RE = re.compile(r"\bE\d{1,3}\b")
VIEWBOX_RE = re.compile(r"<svg\b[^>]*\bviewBox=[\"']\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)[\"']", re.IGNORECASE)
NUMBERED_LANE_RE = re.compile(r">\s*[1-9]\d*\.\s*(?:小程序接入|HTTP\s*接入|应用层|领域智能体|数据层|基础设施|SDK|Gateway)", re.IGNORECASE)
PIPELINE_TITLE_RE = re.compile(r"(?:主请求流|请求流)[^<]{0,40}(?:SDK|/api/|Controller|Facade|Handler|DTO)[^<]{0,80}(?:Facade|Agent|数据|兜底)", re.IGNORECASE)
HORIZONTAL_CANVAS_SCROLL_RE = re.compile(
    r"(?:canvas|svg|architecture|canvas-wrap|arch)[^{]{0,120}\{[^}]*overflow(?:-x)?\s*:\s*(?:auto|scroll)",
    re.IGNORECASE | re.DOTALL,
)
OVERSIZED_MIN_WIDTH_RE = re.compile(r"[{;]\s*min-width\s*:\s*(?:1[3-9]\d{2}|[2-9]\d{3})px", re.IGNORECASE)
TITLE_DESCRIPTION_NODE_RE = re.compile(
    r"<(?P<tag>[a-z0-9:-]+)\b[^>]*\bclass=[\"'](?P<class>[^\"']+)[\"'][^>]*>\s*<b\b[^>]*>.*?</b>\s*<span\b",
    re.IGNORECASE | re.DOTALL,
)
CSS_CLASS_RULE_RE = re.compile(
    r"\.(?P<class>[A-Za-z0-9_-]+)(?:\b|[.#:{\s>+~,])[^{}]*\{(?P<body>[^{}]*)\}",
    re.IGNORECASE | re.DOTALL,
)
AUTO_CENTERED_NODE_MARKERS = (
    "<foreignobject",
    "data-node-layout=\"auto-centered\"",
    "data-node-content=\"auto-centered\"",
    "class=\"node-content\"",
    "class=\"node-content ",
    "class='node-content'",
    "class='node-content ",
)



SYSTEM_ARCH_ALLOWED_VIEWS = {
    "router",
    "context",
    "container",
    "component",
    "deployment",
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
}

SYSTEM_ARCH_TEMPLATE_IDS = {
    "router-v6",
    "system-context",
    "workload-overview",
    "component-breakdown",
    "deployment-topology",
    "logical-layering",
    "data-architecture",
    "data-flow",
    "api-integration",
    "event-driven",
    "network-topology",
    "security-view",
    "identity-access",
    "resilience-view",
    "observability-view",
    "delivery-pipeline",
}

TEMPLATE_LAYOUTS_BY_TYPE: dict[str, dict[str, str]] = {
    "business-architecture": {
        "capability-domain-map": "capability-domain-map",
        "participant-boundary": "participant-boundary",
        "rule-constraint-heatmap": "rule-constraint-heatmap",
        "value-chain-map": "value-chain-map",
    },
    "business-flow": {
        "bpmn-light-flow": "bpmn-light-flow",
        "swimlane-flow": "swimlane-flow",
        "stage-track": "stage-track",
        "exception-branch-flow": "exception-branch-flow",
    },
    "code-sequence": {
        "participant-timeline": "participant-timeline",
        "async-callback-sequence": "async-callback-sequence",
        "transaction-boundary-sequence": "transaction-boundary-sequence",
        "retry-exception-sequence": "retry-exception-sequence",
    },
    "state-data-model": {
        "state-machine": "state-machine",
        "er-lite": "er-lite",
        "lifecycle-track": "lifecycle-track",
        "data-flow-model": "data-flow-model",
        "state-event-matrix": "state-event-matrix",
    },
    "fault-debugging": {
        "debugging-sequence": "debugging-sequence",
        "causal-chain": "causal-chain",
        "bpmn-debug-flow": "bpmn-debug-flow",
        "before-after-flow": "before-after-flow",
        "state-data-breakpoint": "state-data-breakpoint",
    },
    "feature-iteration": {
        "current-target-flow": "current-target-flow",
        "current-target-sequence": "current-target-sequence",
        "diff-heatmap": "diff-heatmap",
        "release-rollback-track": "release-rollback-track",
    },
    "page-mockup": {
        "artboard-wireframe": "artboard-wireframe",
        "artboard-filmstrip": "artboard-filmstrip",
        "responsive-state-board": "responsive-state-board",
        "primary-path-page-flow": "primary-path-page-flow",
    },
    "technical-design": {
        "module-contract-data-topology": "module-contract-data-topology",
        "api-contract-swimlane": "api-contract-swimlane",
        "data-consistency-boundary": "data-consistency-boundary",
        "release-switch-track": "release-switch-track",
    },
    "decision-communication": {
        "decision-tree": "decision-tree",
        "option-matrix-path": "option-matrix-path",
        "tradeoff-quadrant": "tradeoff-quadrant",
        "recommended-path": "recommended-path",
    },
    "delivery-acceptance": {
        "acceptance-ledger": "acceptance-ledger",
        "evidence-swimlane": "evidence-swimlane",
        "risk-action-board": "risk-action-board",
        "delivery-timeline": "delivery-timeline",
    },
}

SYSTEM_ARCH_C4_TERMS = ("Context", "Container", "Component", "Deployment")
SYSTEM_ARCH_SPECIALTY_TERMS = (
    "Logical",
    "Data Architecture",
    "Data Flow",
    "API Integration",
    "Event-driven",
    "Network",
    "Security",
    "Identity",
    "Resilience",
    "Observability",
    "CI/CD",
)
SYSTEM_ARCH_HANDOFF_TERMS = ("Business Architecture", "Domain Model", "ER", "State Machine", "Sequence")
SYSTEM_ARCH_FIXED_DEFAULT_TERMS = ("客服", "智能体", "Agent", "RAG", "LLM", "SDK", "Gateway")
CONTENT_SOURCE_ATTRS = ("data-content-source", "data-evidence-source", "data-source", "data-material-source")
CONTENT_SOURCE_ALLOW_WORDS = ("user", "用户", "material", "材料", "evidence", "证据", "repo", "仓库")


SYSTEM_ARCH_REQUIRED_SEMANTICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("入口", ("入口", "接入", "用户", "渠道", "Web", "APP", "微信", "电话", "邮件")),
    ("应用/服务", ("应用", "Agent", "智能体", "服务", "会话", "客服")),
    ("能力支撑", ("能力", "支撑", "RAG", "LLM", "大模型", "工具", "插件", "工作流", "检索")),
    ("数据", ("数据", "知识库", "会话数据", "用户数据", "运营数据", "DB", "数据库", "日志")),
    ("基础设施", ("基础设施", "云", "存储", "网络", "安全", "监控", "CI/CD", "运维底座")),
    ("管理/运维", ("管理", "运维", "权限", "审计", "评估", "监控告警")),
    ("主流向", ("主请求流", "请求流", "数据读写流", "反馈流", "→", "->")),
)


def _first_attr(parser: HtmlSignals, name: str) -> str | None:
    values = [value.strip() for value in parser.attr_values(name) if value.strip()]
    return values[0] if values else None


def _has_user_material_source(parser: HtmlSignals) -> bool:
    for attr in CONTENT_SOURCE_ATTRS:
        for value in parser.attr_values(attr):
            value_lower = value.lower()
            if any(word in value_lower for word in CONTENT_SOURCE_ALLOW_WORDS):
                return True
    return False


def _count_terms(text: str, terms: Iterable[str]) -> int:
    return sum(1 for term in terms if term in text)


def lint_title_description_stacking(html: str) -> list[str]:
    """Check that node title/body pairs are not laid out as horizontal flex rows."""

    title_description_classes: set[str] = set()
    for match in TITLE_DESCRIPTION_NODE_RE.finditer(html):
        title_description_classes.update(
            class_name for class_name in match.group("class").split() if class_name.strip()
        )
    if not title_description_classes:
        return []

    css_rules: dict[str, list[str]] = {}
    for match in CSS_CLASS_RULE_RE.finditer(html):
        css_rules.setdefault(match.group("class"), []).append(match.group("body"))

    horizontal_flex_classes = sorted(
        class_name
        for class_name in title_description_classes
        for rule_body in css_rules.get(class_name, [])
        if re.search(r"(?:^|;)\s*display\s*:\s*flex\s*(?:;|$)", rule_body, re.IGNORECASE)
        and not re.search(r"(?:^|;)\s*flex-direction\s*:\s*column\s*(?:;|$)", rule_body, re.IGNORECASE)
    )
    if not horizontal_flex_classes:
        return []

    return [
        "节点标题和描述必须上下排布；"
        f"{', '.join(f'.{class_name}' for class_name in horizontal_flex_classes)} "
        "使用 display:flex 时必须同时声明 flex-direction:column。"
    ]


def lint_system_architecture(html: str, *, allow_candidates: bool = False) -> list[str]:
    parser = HtmlSignals()
    parser.feed(html)
    text = parser.text
    errors: list[str] = lint_title_description_stacking(html)

    if not allow_candidates and "tablist" in parser.roles:
        errors.append("普通系统架构图不得生成候选 tab；只有显式校准模式可使用 role=tablist。")

    grammars = " ".join(parser.attr_values("data-diagram-grammar"))
    svg_count = parser.tag_counts.get("svg", 0)
    if svg_count == 0:
        errors.append("系统架构图必须包含真实 SVG 主画布；presentation 标记不能替代图形层。")

    selected_view = _first_attr(parser, "data-system-arch-view")
    if not selected_view:
        errors.append("系统架构图必须声明 data-system-arch-view，记录 v6 路由器选出的主视图。")
    elif selected_view not in SYSTEM_ARCH_ALLOWED_VIEWS:
        errors.append("系统架构图 data-system-arch-view 必须来自 v6 路由器允许的视图集合。")

    routing_confidence = _first_attr(parser, "data-routing-confidence")
    if not routing_confidence:
        errors.append("系统架构图必须声明 data-routing-confidence，记录模板路由置信度。")
    else:
        try:
            confidence = float(routing_confidence)
        except ValueError:
            errors.append("系统架构图 data-routing-confidence 必须是 0 到 1 的数字。")
        else:
            if confidence < 0 or confidence > 1:
                errors.append("系统架构图 data-routing-confidence 必须是 0 到 1 的数字。")

    template_family = _first_attr(parser, "data-template-family")
    if template_family != "system-architecture":
        errors.append('系统架构图必须声明 data-template-family="system-architecture"，证明来自系统架构 HTML 模板资产。')
    template_id = _first_attr(parser, "data-template-id")
    if not template_id:
        errors.append("系统架构图必须声明 data-template-id，记录所复制的 HTML 模板资产。")
    elif template_id not in SYSTEM_ARCH_TEMPLATE_IDS:
        errors.append("系统架构图 data-template-id 必须来自 templates/system-architecture/ 的已知模板文件。")

    html_lower = html.lower()
    if HORIZONTAL_CANVAS_SCROLL_RE.search(html) or OVERSIZED_MIN_WIDTH_RE.search(html):
        errors.append("系统架构图主画布不得依赖横向滚动或超大 min-width；应压缩空白并在正常页面宽度内可读。")
    for width_text, height_text in VIEWBOX_RE.findall(html):
        try:
            width = float(width_text)
            height = float(height_text)
        except ValueError:
            continue
        if width < 1400 and height >= 700:
            errors.append("系统架构图 presentation 画布过窄；默认应使用 1500-1700 左右逻辑宽度再用 CSS 等比缩放。")
            break
    if svg_count > 0 and not any(marker in html_lower for marker in AUTO_CENTERED_NODE_MARKERS):
        errors.append("系统架构图节点内容必须使用 foreignObject 或自居中 HTML 容器，避免图标/文案固定坐标错位。")
    if "主请求入口" in text and not _contains_any(text, ("入口汇聚", "入口汇入", "入口进入", "入口接入")):
        errors.append("检测到含糊箭头标签“主请求入口”；每条箭头必须有明确源节点、目标节点和关系标签。")
    if NUMBERED_LANE_RE.search(html):
        errors.append("普通系统架构图不得退化为编号实现分层；主层标题应使用入口、应用、能力、数据、基础设施、管理侧栏等大众语义。")
    if PIPELINE_TITLE_RE.search(text):
        errors.append("不得把主画布标题写成接口调用链或代码流水线；标题应概括系统架构关系。")

    c4_count = _count_terms(html, SYSTEM_ARCH_C4_TERMS)
    specialty_count = _count_terms(html, SYSTEM_ARCH_SPECIALTY_TERMS)
    handoff_count = _count_terms(html, SYSTEM_ARCH_HANDOFF_TERMS)
    if c4_count >= 4 and specialty_count >= 6 and handoff_count >= 3:
        errors.append("不要把 C4 主线、专项扩展和跨图型转交全部混入同一张最终架构图；应先选一个主视图，其它作为 companion 或转交。")

    fixed_default_hits = [term for term in SYSTEM_ARCH_FIXED_DEFAULT_TERMS if term in text]
    if len(fixed_default_hits) >= 3 and not _has_user_material_source(parser):
        errors.append("系统架构图疑似使用固定业务默认内容；客服 / Agent / RAG / LLM / SDK / Gateway 等词必须来自用户材料或节点详情证据。")

    for label, words in SYSTEM_ARCH_REQUIRED_SEMANTICS:
        if not _contains_any(text + " " + html, words):
            errors.append(f"系统架构图缺少{label}语义。")

    node_like_count = sum(
        1
        for class_name in parser.classes
        if class_name in {"node", "card", "evidence", "evidence-button", "fact-card"}
    )
    evidence_count = len(EVIDENCE_RE.findall(text + " " + html))
    source_path_count = len(SOURCE_PATH_RE.findall(text + " " + html))
    has_presentation_grammar = "system-architecture-presentation" in grammars and svg_count > 0

    if node_like_count >= 18 and not has_presentation_grammar:
        errors.append("节点/卡片数量过多且缺少 presentation 图形语法，疑似卡片堆叠报告。")
    if svg_count == 0 and node_like_count >= 6:
        errors.append("检测到 marker-only 节点网格；必须重画为含图标、边界和连线层的架构画布。")
    if evidence_count > 12:
        errors.append("主图证据编号过多；系统架构图应把长证据放入点击详情或附录。")
    if source_path_count > 6:
        errors.append("源码路径或命令证据外露过多；主图节点应只保留组件语义。")

    return errors


def lint_template_identity(html: str, diagram_type: str) -> list[str]:
    """Validate generic HTML template identity for non-system diagram types."""

    parser = HtmlSignals()
    parser.feed(html)
    errors: list[str] = lint_title_description_stacking(html)
    known_templates = TEMPLATE_LAYOUTS_BY_TYPE.get(diagram_type)
    if known_templates is None:
        return errors

    declared_type = _first_attr(parser, "data-diagram-type")
    if declared_type != diagram_type:
        errors.append(f'{diagram_type} 图必须声明 data-diagram-type="{diagram_type}"。')

    template_family = _first_attr(parser, "data-template-family")
    if template_family != diagram_type:
        errors.append(f'{diagram_type} 图必须声明 data-template-family="{diagram_type}"。')

    template_id = _first_attr(parser, "data-template-id")
    if not template_id:
        errors.append(f"{diagram_type} 图必须声明 data-template-id。")
    elif template_id not in known_templates:
        errors.append(f"{diagram_type} data-template-id 必须来自 templates/{diagram_type}/ 的已知模板文件。")

    template_layout = _first_attr(parser, "data-template-layout")
    if not template_layout:
        errors.append(f"{diagram_type} 图必须声明 data-template-layout。")
    elif template_id in known_templates and template_layout != known_templates[template_id]:
        errors.append(f"{diagram_type} data-template-layout 必须匹配所选 HTML 模板资产。")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint a vibe-diagram single-file HTML artifact.")
    parser.add_argument("html_file", type=Path, help="待检查的单文件 HTML")
    parser.add_argument("--type", dest="diagram_type", default="system-architecture")
    parser.add_argument(
        "--allow-candidates",
        action="store_true",
        help="仅当用户显式要求候选全集/校准/对比时放行候选 tab",
    )
    args = parser.parse_args(argv)

    html = args.html_file.read_text(encoding="utf-8")
    if args.diagram_type != "system-architecture":
        errors = lint_template_identity(html, args.diagram_type)
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        print(f"OK: {args.diagram_type} template lint passed.")
        return 0

    errors = lint_system_architecture(html, allow_candidates=args.allow_candidates)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("OK: system-architecture presentation lint passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
