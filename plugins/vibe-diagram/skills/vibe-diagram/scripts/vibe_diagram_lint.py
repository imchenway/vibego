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


SYSTEM_ARCH_REQUIRED_SEMANTICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("入口", ("入口", "接入", "用户", "渠道", "Web", "APP", "微信", "电话", "邮件")),
    ("应用/Agent", ("应用", "Agent", "智能体", "服务", "会话", "客服")),
    ("能力支撑", ("能力", "支撑", "RAG", "LLM", "大模型", "工具", "插件", "工作流", "检索")),
    ("数据", ("数据", "知识库", "会话数据", "用户数据", "运营数据", "DB", "数据库", "日志")),
    ("基础设施", ("基础设施", "云", "存储", "网络", "安全", "监控", "CI/CD", "运维底座")),
    ("管理/运维", ("管理", "运维", "权限", "审计", "评估", "监控告警")),
    ("主流向", ("主请求流", "请求流", "数据读写流", "反馈流", "→", "->")),
)


def lint_system_architecture(html: str, *, allow_candidates: bool = False) -> list[str]:
    parser = HtmlSignals()
    parser.feed(html)
    text = parser.text
    errors: list[str] = []

    if not allow_candidates and "tablist" in parser.roles:
        errors.append("普通系统架构图不得生成候选 tab；只有显式校准模式可使用 role=tablist。")

    grammars = " ".join(parser.attr_values("data-diagram-grammar"))
    if parser.tag_counts.get("svg", 0) == 0 and "system-architecture-presentation" not in grammars:
        errors.append("系统架构图必须包含 SVG 主画布或 data-diagram-grammar 标记。")

    for label, words in SYSTEM_ARCH_REQUIRED_SEMANTICS:
        if not _contains_any(text + " " + html, words):
            errors.append(f"系统架构图缺少{label}语义。")

    node_like_count = sum(1 for class_name in parser.classes if class_name in {"node", "card", "evidence", "fact-card"})
    evidence_count = len(EVIDENCE_RE.findall(text + " " + html))
    source_path_count = len(SOURCE_PATH_RE.findall(text + " " + html))
    has_presentation_grammar = "system-architecture-presentation" in grammars or parser.tag_counts.get("svg", 0) > 0

    if node_like_count >= 18 and not has_presentation_grammar:
        errors.append("节点/卡片数量过多且缺少 presentation 图形语法，疑似卡片堆叠报告。")
    if evidence_count > 12:
        errors.append("主图证据编号过多；系统架构图应把长证据放入点击详情或附录。")
    if source_path_count > 6:
        errors.append("源码路径或命令证据外露过多；主图节点应只保留组件语义。")

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
        print(f"OK: 未启用 {args.diagram_type} 专用 lint，仅完成文件读取。")
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
