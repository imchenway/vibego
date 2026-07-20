#!/usr/bin/env python3
"""Validate a self-contained HTML diagram without third-party dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "templates"
FAMILY_POLICY_PATH = SKILL_ROOT / "contracts" / "family-policies.json"
EXPECTED_TEMPLATE_COUNT = 58
RESOURCE_ATTRIBUTES = {"src", "srcset", "poster", "action", "formaction"}
LINK_ATTRIBUTES = {"href", "xlink:href"}
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
TITLE_DESCRIPTION_NODE_RE = re.compile(
    r"<(?P<tag>[a-z0-9:-]+)\b[^>]*\bclass=[\"'](?P<class>[^\"']+)[\"'][^>]*>"
    r"\s*<b\b[^>]*>.*?</b>\s*<span\b",
    re.IGNORECASE | re.DOTALL,
)
CSS_CLASS_RULE_RE = re.compile(
    r"\.(?P<class>[A-Za-z0-9_-]+)(?:\b|[.#:{\s>+~,])[^{}]*\{(?P<body>[^{}]*)\}",
    re.IGNORECASE | re.DOTALL,
)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
CSS_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})\s?|([^\r\n\f]))")
JAVASCRIPT_ESCAPE_RE = re.compile(
    r"\\u\{([0-9a-fA-F]{1,6})\}|\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})"
)
NETWORK_SCRIPT_PATTERNS = (
    re.compile(r"\bfetch\b"),
    re.compile(r"\bXMLHttpRequest\b"),
    re.compile(r"\bWebSocket\b"),
    re.compile(r"\bEventSource\b"),
    re.compile(r"\bsendBeacon\b"),
    re.compile(r"\bimportScripts\b"),
    re.compile(r"\bimport\s*\("),
    re.compile(r"\bWorker\b"),
    re.compile(r"\b(?:eval|Function)\b"),
    re.compile(r"\bnew\s+Image\s*\(", re.IGNORECASE),
    re.compile(
        r"(?:\.\s*(?:src|srcset|href|poster|action|formaction)"
        r"|\[\s*['\"](?:src|srcset|href|poster|action|formaction)['\"]\s*\])\s*=",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:window|document|self|globalThis)\s*"
        r"(?:\.\s*(?:location|open)|\[\s*['\"](?:location|open)['\"]\s*\])",
        re.IGNORECASE,
    ),
    re.compile(r"\blocation\s*\.\s*(?:assign|replace)\s*\(", re.IGNORECASE),
    re.compile(r"https?:|(?<!:)//", re.IGNORECASE),
)
HORIZONTAL_CANVAS_SCROLL_RE = re.compile(
    r"(?:canvas|svg|architecture|canvas-wrap|arch)[^{]{0,120}\{[^}]*"
    r"overflow(?:-x)?\s*:\s*(?:auto|scroll)",
    re.IGNORECASE | re.DOTALL,
)
OVERSIZED_MIN_WIDTH_RE = re.compile(
    r"[{;]\s*min-width\s*:\s*(?:1[3-9]\d{2}|[2-9]\d{3})px",
    re.IGNORECASE,
)
EVIDENCE_RE = re.compile(r"\bE\d{1,3}\b")
SOURCE_PATH_RE = re.compile(
    r"(?:/Users/|/ho" r"me/|/tmp/|[A-Za-z0-9_./-]+\.[A-Za-z0-9]+:\d+)"
)
SEQUENCE_CONTRACT_VERSION = "1"
SEQUENCE_MESSAGE_KINDS = frozenset({"sync", "return", "async", "self", "error"})
SEQUENCE_PARTICIPANT_LIMIT = 12
SEQUENCE_MESSAGE_LIMIT = 40
SEQUENCE_PHASE_LIMIT = 4
SEQUENCE_ROLES = frozenset({"standalone", "overview", "detail"})
SEQUENCE_WIDTH_MODES = frozenset({"auto", "contained", "wide"})
SEQUENCE_HEIGHT_MODES = frozenset({"auto", "flow", "scroll"})
SEQUENCE_OWNER_TEMPLATES = frozenset(
    {
        ("fault-debugging", "debugging-sequence"),
        ("feature-iteration", "current-target-sequence"),
    }
)
GENERIC_CONTRACT_VERSION = "1"
GENERIC_PROFILES = frozenset({"graph", "matrix", "timeline", "artboard", "ledger"})
GENERIC_WIDTH_MODES = frozenset({"contained", "auto", "wide"})
GENERIC_HEIGHT_MODES = frozenset({"flow", "auto", "scroll"})
GENERIC_MOBILE_MODES = frozenset({"stack", "scroll", "summary"})
GENERIC_LIMIT_KEYS = frozenset({"nodes", "relations", "groups", "details"})
FAMILY_POLICY_KEYS = frozenset(
    {
        "schema_version",
        "contract_version",
        "sequence_exclusions",
        "migration_batches",
        "families",
    }
)
FAMILY_POLICY_FAMILY_KEYS = frozenset({"limits", "templates"})
FAMILY_POLICY_TEMPLATE_KEYS = frozenset({"profile", "limits"})
EXPECTED_SEQUENCE_EXCLUSIONS = (
    "code-sequence/async-callback-sequence.html",
    "code-sequence/participant-timeline.html",
    "code-sequence/retry-exception-sequence.html",
    "code-sequence/transaction-boundary-sequence.html",
    "fault-debugging/debugging-sequence.html",
    "feature-iteration/current-target-sequence.html",
)


@dataclass(frozen=True)
class SequenceCanvas:
    canvas_id: str
    role: str
    detail_for: str
    participant_ids: Tuple[str, ...]
    messages: Tuple[Tuple[str, str, str, str], ...]
    phase_ids: Tuple[str, ...]


@dataclass
class _SequenceRecord:
    attrs: Dict[str, str]
    participant_ids: List[str]
    messages: List[Tuple[str, str, str, str]]
    phase_ids: List[str]


def _duplicates(attrs: Sequence[Tuple[str, Optional[str]]]) -> List[str]:
    names = [name.lower() for name, _ in attrs]
    return sorted({name for name in names if names.count(name) > 1})


def _read_json_unique(path: Path) -> Dict[str, Any]:
    def reject_duplicates(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number in {path}: {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _validated_limits(value: Any, label: str, *, partial: bool) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    keys = set(value)
    if (not partial and keys != GENERIC_LIMIT_KEYS) or (partial and not keys <= GENERIC_LIMIT_KEYS):
        raise ValueError(f"{label} has an invalid key set")
    result: Dict[str, int] = {}
    for key, limit in value.items():
        if type(limit) is not int or limit < 1:
            raise ValueError(f"{label}.{key} must be a positive integer")
        result[key] = limit
    return result


def _validated_migration_batches(
    value: Any, generic_templates: set[str]
) -> Dict[str, List[str]]:
    if not isinstance(value, dict) or list(value) != sorted(value):
        raise ValueError("family policy migration batches must be an ordered object")
    seen = set()
    result: Dict[str, List[str]] = {}
    for batch, paths in value.items():
        if re.fullmatch(r"B(?:0[1-9]|1[0-3])", batch) is None:
            raise ValueError(f"family policy migration batch id is invalid: {batch}")
        if (
            not isinstance(paths, list)
            or not paths
            or paths != sorted(paths)
            or len(paths) != len(set(paths))
            or not set(paths) <= generic_templates
            or seen & set(paths)
        ):
            raise ValueError(f"family policy migration batch paths are invalid: {batch}")
        seen.update(paths)
        result[batch] = paths
    return result


def load_family_policies(path: Path = FAMILY_POLICY_PATH) -> Dict[str, Any]:
    policy = _read_json_unique(path)
    if set(policy) != FAMILY_POLICY_KEYS:
        raise ValueError("family policy has an invalid root schema")
    if type(policy["schema_version"]) is not int or policy["schema_version"] != 1:
        raise ValueError("family policy schema_version must be integer 1")
    if policy["contract_version"] != GENERIC_CONTRACT_VERSION:
        raise ValueError("family policy contract_version is invalid")
    if policy["sequence_exclusions"] != list(EXPECTED_SEQUENCE_EXCLUSIONS):
        raise ValueError("family policy sequence exclusions are invalid")
    families = policy["families"]
    if not isinstance(families, dict) or len(families) != 10:
        raise ValueError("family policy must define exactly ten generic families")
    catalog = load_template_layouts()
    covered = set()
    for family, definition in families.items():
        if not isinstance(definition, dict) or set(definition) != FAMILY_POLICY_FAMILY_KEYS:
            raise ValueError(f"family policy definition is invalid: {family}")
        family_limits = _validated_limits(
            definition["limits"], f"families.{family}.limits", partial=False
        )
        templates = definition["templates"]
        if not isinstance(templates, dict) or not templates:
            raise ValueError(f"family policy templates must be a non-empty object: {family}")
        for template_id, template in templates.items():
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", template_id):
                raise ValueError(f"family policy template id is invalid: {family}/{template_id}")
            if not isinstance(template, dict) or set(template) != FAMILY_POLICY_TEMPLATE_KEYS:
                raise ValueError(
                    f"family policy template definition is invalid: {family}/{template_id}"
                )
            if template["profile"] not in GENERIC_PROFILES:
                raise ValueError(f"family policy profile is invalid: {family}/{template_id}")
            overrides = _validated_limits(
                template["limits"],
                f"families.{family}.templates.{template_id}.limits",
                partial=True,
            )
            if any(limit > family_limits[key] for key, limit in overrides.items()):
                raise ValueError(
                    f"family policy template limit widens its family: {family}/{template_id}"
                )
            covered.add(f"{family}/{template_id}.html")
    all_templates = {
        f"{family}/{template_id}.html"
        for family, entries in catalog.items()
        for template_id in entries
    }
    expected = all_templates - set(EXPECTED_SEQUENCE_EXCLUSIONS)
    if covered != expected:
        raise ValueError("family policy must cover the exact 52 non-sequence templates")
    _validated_migration_batches(policy["migration_batches"], expected)
    return policy


@dataclass
class _GenericCanvasRecord:
    attrs: Dict[str, str]
    node_ids: List[str]
    group_ids: List[str]
    relations: List[Tuple[str, str, str, str, str]]
    row_ids: List[str]
    col_ids: List[str]
    cells: List[Tuple[str, str]]
    detail_ids: List[str]


class _GenericContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canvases: List[_GenericCanvasRecord] = []
        self.fallback_ids: List[str] = []
        self.errors: List[str] = []
        self._canvas: Optional[_GenericCanvasRecord] = None
        self._stack: List[Tuple[str, bool]] = []

    def handle_starttag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        normalized = [(name.lower(), value or "") for name, value in attrs]
        values = dict(normalized)
        starts_canvas = "data-diagram-canvas" in values
        if starts_canvas:
            if self._canvas is not None:
                self.errors.append("Diagram canvases must not be nested.")
            else:
                self._canvas = _GenericCanvasRecord(values, [], [], [], [], [], [], [])
                self.canvases.append(self._canvas)
        if "data-fallback-for" in values:
            self.fallback_ids.append(values["data-fallback-for"].strip())
        if self._canvas is not None:
            semantic = any(
                key in values
                for key in (
                    "data-diagram-node-id",
                    "data-diagram-group-id",
                    "data-diagram-relation-id",
                    "data-matrix-row-id",
                    "data-matrix-col-id",
                    "data-diagram-detail-id",
                )
            )
            duplicates = _duplicates(attrs) if semantic or starts_canvas else []
            if duplicates:
                self.errors.append(
                    "Duplicate generic contract attributes: " + ", ".join(duplicates) + "."
                )
            if "data-diagram-node-id" in values:
                self._canvas.node_ids.append(values["data-diagram-node-id"].strip())
                if not values.get("data-semantic-role", "").strip():
                    self.errors.append("Every diagram node must declare data-semantic-role.")
            if "data-diagram-group-id" in values:
                self._canvas.group_ids.append(values["data-diagram-group-id"].strip())
                if not values.get("data-semantic-role", "").strip():
                    self.errors.append("Every diagram group must declare data-semantic-role.")
            if "data-diagram-relation-id" in values:
                self._canvas.relations.append(
                    (
                        values["data-diagram-relation-id"].strip(),
                        values.get("data-from", "").strip(),
                        values.get("data-to", "").strip(),
                        values.get("data-relation-kind", "").strip(),
                        values.get("data-semantic", "").strip(),
                    )
                )
            if "data-matrix-row-id" in values:
                self._canvas.row_ids.append(values["data-matrix-row-id"].strip())
            if "data-matrix-col-id" in values:
                self._canvas.col_ids.append(values["data-matrix-col-id"].strip())
            if "data-matrix-row" in values or "data-matrix-col" in values:
                self._canvas.cells.append(
                    (
                        values.get("data-matrix-row", "").strip(),
                        values.get("data-matrix-col", "").strip(),
                    )
                )
            if "data-diagram-detail-id" in values:
                self._canvas.detail_ids.append(values["data-diagram-detail-id"].strip())
        if tag not in VOID_ELEMENTS:
            self._stack.append((tag, starts_canvas))

    def handle_startendtag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        _open_tag, closes_canvas = self._stack.pop()
        if closes_canvas:
            self._canvas = None


def generic_contract_errors(
    html: str,
    family: str,
    template_id: str,
    policy: Mapping[str, Any],
) -> List[str]:
    relative = f"{family}/{template_id}.html"
    if relative in set(policy["sequence_exclusions"]):
        return []
    definition = policy["families"].get(family, {}).get("templates", {}).get(template_id)
    if definition is None:
        return [f"No generic contract policy exists for {family}/{template_id}."]
    parser = _GenericContractParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        return [f"Could not parse generic diagram contract: {exc}."]
    errors = list(parser.errors)
    if not parser.canvases:
        errors.append("Generic diagram contract requires at least one canvas.")
        return errors
    family_limits = policy["families"][family]["limits"]
    limits = {**family_limits, **definition["limits"]}
    canvas_ids = [canvas.attrs.get("data-diagram-id", "").strip() for canvas in parser.canvases]
    if "" in canvas_ids:
        errors.append("Every diagram canvas requires a non-empty data-diagram-id.")
    if len(canvas_ids) != len(set(canvas_ids)):
        errors.append("Diagram canvas ids must be unique.")
    for canvas in parser.canvases:
        attrs = canvas.attrs
        canvas_id = attrs.get("data-diagram-id", "").strip()
        if attrs.get("data-diagram-contract") != GENERIC_CONTRACT_VERSION:
            errors.append("Diagram canvas contract must be version 1.")
        if attrs.get("data-diagram-profile") != definition["profile"]:
            errors.append("Diagram canvas profile must match its trusted family policy.")
        if attrs.get("data-diagram-width") not in GENERIC_WIDTH_MODES:
            errors.append("Diagram canvas width mode is invalid.")
        if attrs.get("data-diagram-height") not in GENERIC_HEIGHT_MODES:
            errors.append("Diagram canvas height mode is invalid.")
        if attrs.get("data-diagram-mobile") not in GENERIC_MOBILE_MODES:
            errors.append("Diagram canvas mobile fallback mode is invalid.")
        semantic_ids = canvas.node_ids + canvas.group_ids
        if any(not value for value in semantic_ids):
            errors.append("Diagram node and group ids must be non-empty.")
        if len(semantic_ids) != len(set(semantic_ids)):
            errors.append("Diagram node and group ids must be unique within a canvas.")
        endpoints = set(semantic_ids)
        relation_ids = []
        for relation_id, source, target, kind, semantic in canvas.relations:
            relation_ids.append(relation_id)
            if not all((relation_id, source, target, kind, semantic)):
                errors.append("Every diagram relation requires id, endpoints, kind, and semantic.")
            elif source not in endpoints or target not in endpoints:
                errors.append("Diagram relation endpoints must reference authored nodes or groups.")
        if len(relation_ids) != len(set(relation_ids)):
            errors.append("Diagram relation ids must be unique within a canvas.")
        if definition["profile"] == "matrix":
            rows, columns = set(canvas.row_ids), set(canvas.col_ids)
            if not rows or not columns or not canvas.cells:
                errors.append("Matrix profile requires authored row axes, column axes, and cells.")
            for row, column in canvas.cells:
                if row not in rows or column not in columns:
                    errors.append("Matrix cells must reference authored row and column axes.")
        counts = {
            "nodes": len(canvas.node_ids),
            "relations": len(canvas.relations),
            "groups": len(canvas.group_ids),
            "details": len(canvas.detail_ids),
        }
        for key, count in counts.items():
            if count > limits[key]:
                errors.append(f"Diagram canvas exceeds the {key} complexity budget.")
        if canvas_id and canvas_id not in parser.fallback_ids:
            errors.append("Every diagram canvas requires a matching data-fallback-for baseline.")
    return list(dict.fromkeys(errors))


def lint_generic_contract(
    html: str,
    family: str,
    template_id: str,
    policy: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    trusted = load_family_policies() if policy is None else policy
    return generic_contract_errors(html, family, template_id, trusted)


def lint_adaptive_kernel(html: str) -> List[str]:
    errors = []
    paths = {
        "style": SKILL_ROOT / "assets" / "contracts" / "adaptive-viewport" / "v1.css",
        "script": SKILL_ROOT / "assets" / "contracts" / "adaptive-viewport" / "v1.js",
    }
    for tag, path in paths.items():
        expected = path.read_text(encoding="utf-8").rstrip("\n")
        matches = re.findall(
            rf'<{tag} data-adaptive-viewport-kernel="1">\n(.*?)\n</{tag}>',
            html,
            flags=re.DOTALL,
        )
        if len(matches) != 1:
            errors.append(f"Migrated generic template requires exactly one adaptive {tag} kernel.")
        elif matches[0] != expected:
            errors.append(f"Migrated generic template adaptive {tag} kernel has drifted.")
    return errors


class HtmlSignals(HTMLParser):
    """Collect identity, layout, style, script, and resource signals."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: List[str] = []
        self.tag_counts: Dict[str, int] = {}
        self.roles: List[str] = []
        self.classes: List[str] = []
        self.attrs: Dict[str, List[str]] = {}
        self.main_attrs: List[Dict[str, str]] = []
        self.attribute_events: List[Tuple[str, str, str]] = []
        self.styles: List[str] = []
        self.scripts: List[str] = []
        self.errors: List[str] = []
        self._style_depth = 0
        self._script_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        tag = tag.lower()
        self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1
        duplicates = _duplicates(attrs)
        if duplicates:
            self.errors.append(f"Duplicate attributes on {tag}: {', '.join(duplicates)}")
        attrs_map = {name.lower(): value or "" for name, value in attrs}
        if tag == "main":
            self.main_attrs.append(attrs_map)
        if tag == "meta" and attrs_map.get("http-equiv", "").strip().casefold() == "refresh":
            self.errors.append("Meta refresh navigation is forbidden")
        if tag in {"iframe", "object", "embed"}:
            self.errors.append(f"Embedded container is forbidden: {tag}")
        for name, value in attrs:
            name = name.lower()
            value = value or ""
            self.attrs.setdefault(name, []).append(value)
            self.attribute_events.append((tag, name, value))
            if name == "ping" and value.strip():
                self.errors.append("Ping navigation is forbidden")
            elif name == "role":
                self.roles.append(value)
            elif name == "class":
                self.classes.extend(value.split())
            elif name == "style":
                self.styles.append(value)
        if tag == "style":
            self._style_depth += 1
        if tag == "script":
            if attrs_map.get("type", "").strip().lower() == "module":
                self.errors.append("JavaScript module loading is forbidden")
            self._script_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "style":
            self._style_depth = max(0, self._style_depth - 1)
        elif tag == "script":
            self._script_depth = max(0, self._script_depth - 1)

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text_parts.append(data)
        if self._style_depth:
            self.styles.append(data)
        if self._script_depth:
            self.scripts.append(data)

    @property
    def text(self) -> str:
        return " ".join(part.strip() for part in self.text_parts if part.strip())

    def attr_values(self, name: str) -> List[str]:
        return self.attrs.get(name.lower(), [])


def _parse(html: str) -> HtmlSignals:
    parser = HtmlSignals()
    parser.feed(html)
    parser.close()
    return parser


class _SequenceParser(HTMLParser):
    """Parse sequence semantics only from structured data attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: List[_SequenceRecord] = []
        self.errors: List[str] = []
        self._active: List[_SequenceRecord] = []
        self._stack: List[Tuple[str, bool]] = []

    def _start(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
        push: bool,
    ) -> None:
        tag = tag.lower()
        duplicates = _duplicates(attrs)
        attrs_map = {name.lower(): value or "" for name, value in attrs}
        is_canvas = "data-sequence-canvas" in attrs_map
        sequence_endpoint_attributes = {
            "data-from",
            "data-to",
            "data-message-kind",
            "data-semantic",
            "data-participant-id",
        }
        if duplicates and any(
            name.startswith("data-sequence") or name in sequence_endpoint_attributes
            for name in duplicates
        ):
            self.errors.append(
                f"Duplicate sequence attributes on {tag}: {', '.join(duplicates)}."
            )
        if is_canvas:
            if self._active:
                self.errors.append("Sequence canvases must not be nested.")
            record = _SequenceRecord(attrs_map, [], [], [])
            self.records.append(record)
            self._active.append(record)
        if self._active:
            record = self._active[-1]
            if "data-participant-id" in attrs_map:
                record.participant_ids.append(attrs_map["data-participant-id"].strip())
            if "data-sequence-message" in attrs_map:
                record.messages.append(
                    (
                        attrs_map.get("data-from", "").strip(),
                        attrs_map.get("data-to", "").strip(),
                        attrs_map.get("data-message-kind", "").strip(),
                        attrs_map.get("data-semantic", "").strip(),
                    )
                )
            if "data-sequence-phase-id" in attrs_map:
                record.phase_ids.append(attrs_map["data-sequence-phase-id"].strip())
        if push and tag not in VOID_ELEMENTS:
            self._stack.append((tag, is_canvas))
        elif is_canvas:
            self.errors.append("A sequence canvas must not be a void or self-closing element.")
            self._active.pop()

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        self._start(tag, attrs, push=True)

    def handle_startendtag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        self._start(tag, attrs, push=False)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._stack:
            return
        open_tag, closes_canvas = self._stack.pop()
        if open_tag != tag:
            self.errors.append(f"Malformed sequence markup: expected </{open_tag}>, got </{tag}>.")
        if closes_canvas and self._active:
            self._active.pop()

    def finish(self) -> None:
        if self._active:
            self.errors.append("A sequence canvas is not closed.")


def _parse_sequence_records(html: str) -> _SequenceParser:
    parser = _SequenceParser()
    parser.feed(html)
    parser.close()
    parser.finish()
    return parser


def parse_sequence_canvases(html: str) -> Tuple[SequenceCanvas, ...]:
    """Return structured sequence canvases without reading visible route text."""

    parser = _parse_sequence_records(html)
    if parser.errors:
        raise ValueError("; ".join(parser.errors))
    return tuple(
        SequenceCanvas(
            canvas_id=record.attrs.get("data-sequence-id", "").strip(),
            role=record.attrs.get("data-sequence-role", "").strip(),
            detail_for=record.attrs.get("data-sequence-detail-for", "").strip(),
            participant_ids=tuple(record.participant_ids),
            messages=tuple(record.messages),
            phase_ids=tuple(record.phase_ids),
        )
        for record in parser.records
    )


def _decode_css_escapes(value: str) -> str:
    def replace(match: re.Match) -> str:
        if match.group(1):
            codepoint = int(match.group(1), 16)
            return chr(codepoint) if codepoint and codepoint <= 0x10FFFF else "\ufffd"
        return match.group(2) or "\ufffd"

    return CSS_ESCAPE_RE.sub(replace, value)


def _decode_javascript_escapes(value: str) -> str:
    def replace(match: re.Match) -> str:
        codepoint = int(next(group for group in match.groups() if group is not None), 16)
        return chr(codepoint) if codepoint <= 0x10FFFF else "\ufffd"

    return JAVASCRIPT_ESCAPE_RE.sub(replace, value)


def _allowed_embedded_reference(value: str) -> bool:
    value = value.strip()
    return not value or value.startswith("#") or value.startswith("data:")


def load_template_layouts(
    template_root: Path = TEMPLATE_ROOT,
) -> Dict[str, Dict[str, str]]:
    """Read family, id, and layout from packaged assets and fail closed."""

    paths = sorted(template_root.rglob("*.html"), key=lambda path: path.relative_to(template_root).as_posix())
    if len(paths) != EXPECTED_TEMPLATE_COUNT:
        raise ValueError(f"Expected {EXPECTED_TEMPLATE_COUNT} template assets, found {len(paths)}")
    catalog: Dict[str, Dict[str, str]] = {}
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"Template assets must not be symlinks: {path}")
        parser = _parse(path.read_text(encoding="utf-8"))
        if parser.errors:
            raise ValueError(f"Invalid template {path}: {'; '.join(parser.errors)}")
        if len(parser.main_attrs) != 1:
            raise ValueError(f"Template must contain exactly one main element: {path}")
        attrs = parser.main_attrs[0]
        family = attrs.get("data-template-family", "").strip()
        template_id = attrs.get("data-template-id", "").strip()
        layout = attrs.get("data-template-layout", "").strip()
        if not family or not template_id or not layout:
            raise ValueError(f"Template identity is incomplete: {path}")
        if family != path.parent.name or template_id != path.stem:
            raise ValueError(f"Template identity does not match path: {path}")
        family_entries = catalog.setdefault(family, {})
        if template_id in family_entries:
            raise ValueError(f"Duplicate template identity: {family}/{template_id}")
        family_entries[template_id] = layout
    return catalog


def lint_title_description_stacking(html: str) -> List[str]:
    """Require title/body node pairs to use vertical rather than row flex."""

    title_description_classes = set()
    for match in TITLE_DESCRIPTION_NODE_RE.finditer(html):
        title_description_classes.update(match.group("class").split())
    if not title_description_classes:
        return []
    css_rules: Dict[str, List[str]] = {}
    for match in CSS_CLASS_RULE_RE.finditer(html):
        css_rules.setdefault(match.group("class"), []).append(match.group("body"))
    horizontal = sorted(
        class_name
        for class_name in title_description_classes
        for body in css_rules.get(class_name, [])
        if re.search(r"(?:^|;)\s*display\s*:\s*flex\s*(?:;|$)", body, re.IGNORECASE)
        and not re.search(
            r"(?:^|;)\s*flex-direction\s*:\s*column\s*(?:;|$)",
            body,
            re.IGNORECASE,
        )
    )
    if not horizontal:
        return []
    return [
        "Node titles and descriptions must be stacked vertically; "
        + ", ".join(f".{name}" for name in horizontal)
        + " uses row flex without flex-direction: column."
    ]


def lint_system_architecture(
    html: str,
    allow_candidates: bool = False,
) -> List[str]:
    """Apply presentation-specific density and candidate-view gates."""

    parser = _parse(html)
    errors = lint_title_description_stacking(html)
    if parser.tag_counts.get("svg", 0) == 0:
        errors.append("The primary system architecture canvas must contain an SVG diagram.")
    if not allow_candidates and "tablist" in parser.roles:
        errors.append("Candidate tabs require explicit calibration mode approval.")
    if HORIZONTAL_CANVAS_SCROLL_RE.search(html) or OVERSIZED_MIN_WIDTH_RE.search(html):
        errors.append("The architecture canvas must not depend on horizontal scrolling or oversized min-width.")
    node_count = sum(
        1
        for class_name in parser.classes
        if class_name in {"node", "card", "evidence", "evidence-button", "fact-card"}
    )
    grammars = " ".join(parser.attr_values("data-diagram-grammar"))
    if node_count >= 18 and "system-architecture-presentation" not in grammars:
        errors.append("Excessive node density requires an explicit presentation grammar or a split view.")
    evidence_count = len(EVIDENCE_RE.findall(parser.text))
    source_count = len(SOURCE_PATH_RE.findall(parser.text))
    if evidence_count > 6 or source_count > 6:
        errors.append("Move dense evidence and source paths out of the primary architecture canvas.")
    return errors


def lint_template_identity(html: str, diagram_type: str) -> List[str]:
    """Require an artifact to identify one known packaged template and layout."""

    parser = _parse(html)
    errors = list(parser.errors)
    if len(parser.main_attrs) != 1:
        errors.append("The artifact must contain exactly one main element with template identity.")
        return errors
    attrs = parser.main_attrs[0]
    family = attrs.get("data-template-family", "").strip()
    declared_type = attrs.get("data-diagram-type", "").strip()
    template_id = attrs.get("data-template-id", "").strip()
    layout = attrs.get("data-template-layout", "").strip()
    if family != diagram_type:
        errors.append(f'Template family must equal the requested diagram type "{diagram_type}".')
    if declared_type != diagram_type:
        errors.append(f'Diagram type must equal the requested diagram type "{diagram_type}".')
    catalog = load_template_layouts()
    expected_layout = catalog.get(diagram_type, {}).get(template_id)
    if expected_layout is None:
        errors.append(f'Template id "{template_id or "<missing>"}" must name a known template for {diagram_type}.')
        return errors
    if layout != expected_layout:
        errors.append(
            f'Template layout for "{template_id}" must be "{expected_layout}", not "{layout or "<missing>"}".'
        )
    return errors


def lint_sequence_contract(html: str) -> List[str]:
    """Validate structured sequence identities, endpoints, limits, and split linkage."""

    parser = _parse_sequence_records(html)
    errors = list(parser.errors)
    canvases = [
        SequenceCanvas(
            canvas_id=record.attrs.get("data-sequence-id", "").strip(),
            role=record.attrs.get("data-sequence-role", "").strip(),
            detail_for=record.attrs.get("data-sequence-detail-for", "").strip(),
            participant_ids=tuple(record.participant_ids),
            messages=tuple(record.messages),
            phase_ids=tuple(record.phase_ids),
        )
        for record in parser.records
    ]
    if not canvases:
        errors.append("A sequence artifact must contain at least one data-sequence-canvas.")
    canvas_ids = [canvas.canvas_id for canvas in canvases]
    for index, (record, canvas) in enumerate(zip(parser.records, canvases), start=1):
        label = canvas.canvas_id or f"canvas-{index}"
        contract = record.attrs.get("data-sequence-contract", "").strip()
        width = record.attrs.get("data-sequence-width", "").strip()
        height = record.attrs.get("data-sequence-height", "").strip()
        if not canvas.canvas_id:
            errors.append(f"Sequence {label} must declare a non-empty data-sequence-id.")
        elif canvas_ids.count(canvas.canvas_id) > 1:
            errors.append(f'Sequence canvas id "{canvas.canvas_id}" is duplicated.')
        if contract != SEQUENCE_CONTRACT_VERSION:
            errors.append(
                f'Sequence {label} contract must be "{SEQUENCE_CONTRACT_VERSION}".'
            )
        if canvas.role not in SEQUENCE_ROLES:
            errors.append(f"Sequence {label} role must be standalone, overview, or detail.")
        if width not in SEQUENCE_WIDTH_MODES:
            errors.append(f"Sequence {label} width mode must be auto, contained, or wide.")
        if height not in SEQUENCE_HEIGHT_MODES:
            errors.append(f"Sequence {label} height mode must be auto, flow, or scroll.")
        if canvas.role == "detail" and not canvas.detail_for:
            errors.append(f"Detail sequence {label} must declare data-sequence-detail-for.")
        if canvas.role in {"standalone", "overview"} and canvas.detail_for:
            errors.append(f"Sequence {label} with role {canvas.role} must not declare detail-for.")
        if any(not participant for participant in canvas.participant_ids):
            errors.append(f"Sequence {label} participant ids must be non-empty.")
        if len(canvas.participant_ids) < 2:
            errors.append(f"Sequence {label} must declare at least two participants.")
        if not canvas.messages:
            errors.append(f"Sequence {label} must declare at least one primary message.")
        duplicate_participants = sorted(
            {
                participant
                for participant in canvas.participant_ids
                if participant and canvas.participant_ids.count(participant) > 1
            }
        )
        if duplicate_participants:
            errors.append(
                f"Sequence {label} has duplicate participant ids: "
                + ", ".join(duplicate_participants)
                + "."
            )
        if any(not phase for phase in canvas.phase_ids):
            errors.append(f"Sequence {label} phase ids must be non-empty.")
        duplicate_phases = sorted(
            {phase for phase in canvas.phase_ids if phase and canvas.phase_ids.count(phase) > 1}
        )
        if duplicate_phases:
            errors.append(
                f"Sequence {label} has duplicate phase ids: " + ", ".join(duplicate_phases) + "."
            )
        participants = set(canvas.participant_ids)
        for message_index, (source, target, kind, semantic) in enumerate(canvas.messages, start=1):
            if not source or not target or source not in participants or target not in participants:
                errors.append(
                    f"Sequence {label} message {message_index} endpoint must reference a declared participant."
                )
            if kind not in SEQUENCE_MESSAGE_KINDS:
                errors.append(f"Sequence {label} message {message_index} has an unknown message kind.")
            if not semantic:
                errors.append(f"Sequence {label} message {message_index} must declare data-semantic.")
            if kind == "self" and source != target:
                errors.append(
                    f"Sequence {label} self message {message_index} must use the same endpoint."
                )
            if kind in SEQUENCE_MESSAGE_KINDS - {"self"} and source and source == target:
                errors.append(
                    f"Sequence {label} non-self message {message_index} must use different endpoints."
                )
        participant_over = len(canvas.participant_ids) > SEQUENCE_PARTICIPANT_LIMIT
        message_over = len(canvas.messages) > SEQUENCE_MESSAGE_LIMIT
        phase_over = len(canvas.phase_ids) > SEQUENCE_PHASE_LIMIT
        if canvas.role in {"standalone", "detail"} and (
            participant_over or message_over or phase_over
        ):
            errors.append(
                f"Sequence {label} exceeds the complexity budget; "
                "split into one overview and linked detail sequences."
            )
        if canvas.role == "overview" and (participant_over or message_over):
            errors.append(
                f"Overview sequence {label} exceeds its participant or message complexity budget."
            )

    standalones = [canvas for canvas in canvases if canvas.role == "standalone"]
    overviews = [canvas for canvas in canvases if canvas.role == "overview"]
    details = [canvas for canvas in canvases if canvas.role == "detail"]
    if standalones and (overviews or details):
        errors.append("Standalone sequences must not be mixed with overview or detail sequences.")
    if details and len(overviews) != 1:
        errors.append("Documents with detail sequences must contain exactly one overview sequence.")
    if len(overviews) > 1:
        errors.append("A sequence document must not contain more than one overview sequence.")
    if overviews:
        overview = overviews[0]
        detail_phases = [detail.detail_for for detail in details if detail.detail_for]
        for detail in details:
            if detail.detail_for and detail.detail_for not in set(overview.phase_ids):
                errors.append(
                    f'Detail sequence {detail.canvas_id or "<missing>"} references unknown overview phase '
                    f'"{detail.detail_for}".'
                )
        for phase in overview.phase_ids:
            if phase and phase not in detail_phases:
                errors.append(f'Overview phase "{phase}" must have at least one linked detail sequence.')
        if not details:
            errors.append("An overview sequence must have linked detail sequences.")
        else:
            detail_participants = {
                participant for detail in details for participant in detail.participant_ids if participant
            }
            detail_message_count = sum(len(detail.messages) for detail in details)
            split_is_needed = (
                len(detail_participants) > SEQUENCE_PARTICIPANT_LIMIT
                or detail_message_count > SEQUENCE_MESSAGE_LIMIT
                or len(overview.phase_ids) > SEQUENCE_PHASE_LIMIT
            )
            if not split_is_needed:
                errors.append(
                    "The overview and detail split is unnecessary within the sequence complexity budget."
                )
    return _deduplicate(errors)


def _sequence_kernel_block(html: str, tag: str) -> str:
    pattern = re.compile(
        rf"<{tag}\b(?P<attrs>[^>]*)>(?P<body>.*?)</{tag}\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    blocks = []
    for match in pattern.finditer(html):
        attrs = match.group("attrs")
        if re.search(r"\bdata-sequence-kernel\b", attrs, re.IGNORECASE):
            version = re.search(
                r"\bdata-sequence-kernel\s*=\s*([\"'])(?P<version>.*?)\1",
                attrs,
                re.IGNORECASE | re.DOTALL,
            )
            blocks.append((version.group("version") if version else "", match.group("body")))
    if len(blocks) != 1:
        raise ValueError(f"Expected exactly one sequence kernel {tag} block, found {len(blocks)}.")
    version, body = blocks[0]
    if version != SEQUENCE_CONTRACT_VERSION:
        raise ValueError(f"Sequence kernel {tag} version must be {SEQUENCE_CONTRACT_VERSION}.")
    return body


def extract_sequence_kernel_digest(html: str) -> str:
    """Hash the exact shared sequence kernel style and script contents."""

    style = _sequence_kernel_block(html, "style").encode("utf-8")
    script = _sequence_kernel_block(html, "script").encode("utf-8")
    payload = b"sequence-kernel-v1\0" + style + b"\0" + script
    return hashlib.sha256(payload).hexdigest()


def lint_self_contained_resources(html: str) -> List[str]:
    """Reject resources or runtime APIs that can leave the single HTML file."""

    parser = _parse(html)
    errors = list(parser.errors)
    for tag, name, value in parser.attribute_events:
        if name == "srcset" and value.strip():
            errors.append("The srcset resource candidate list is forbidden.")
        elif name in RESOURCE_ATTRIBUTES and not _allowed_embedded_reference(value):
            errors.append(f"External or relative resource is forbidden: {tag}[{name}]={value}")
        elif name in LINK_ATTRIBUTES and not _allowed_embedded_reference(value):
            errors.append(f"External or relative link is forbidden: {tag}[{name}]={value}")
    for css in parser.styles:
        normalized = _decode_css_escapes(css)
        if re.search(r"@import\b", normalized, re.IGNORECASE):
            errors.append("CSS @import is forbidden.")
        if re.search(r"(?:-webkit-)?image-set\s*\(", normalized, re.IGNORECASE):
            errors.append("CSS image-set resources are forbidden.")
        for match in CSS_URL_RE.finditer(normalized):
            if not _allowed_embedded_reference(match.group(2)):
                errors.append(f"External or relative CSS url is forbidden: {match.group(2)}")
    script = _decode_javascript_escapes("\n".join(parser.scripts))
    for pattern in NETWORK_SCRIPT_PATTERNS:
        if pattern.search(script):
            errors.append(f"Runtime network or dynamic-code API is forbidden: {pattern.pattern}")
    return errors


def _deduplicate(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a self-contained HTML diagram.")
    parser.add_argument("path", type=Path, help="HTML artifact to validate")
    parser.add_argument("--type", required=True, dest="diagram_type", help="diagram family")
    parser.add_argument(
        "--allow-candidates",
        action="store_true",
        help="allow candidate tabs for an explicitly requested calibration atlas",
    )
    args = parser.parse_args(argv)
    try:
        html = args.path.read_text(encoding="utf-8")
        errors = lint_self_contained_resources(html)
        errors.extend(lint_template_identity(html, args.diagram_type))
        if args.diagram_type == "system-architecture":
            errors.extend(lint_system_architecture(html, allow_candidates=args.allow_candidates))
        else:
            errors.extend(lint_title_description_stacking(html))
        identity = _parse(html)
        requires_sequence = args.diagram_type == "code-sequence" or any(
            (
                attrs.get("data-template-family", "").strip(),
                attrs.get("data-template-id", "").strip(),
            )
            in SEQUENCE_OWNER_TEMPLATES
            for attrs in identity.main_attrs
        )
        if requires_sequence or "data-sequence-canvas" in html:
            errors.extend(lint_sequence_contract(html))
        policy = load_family_policies()
        completed = {
            relative
            for paths in policy["migration_batches"].values()
            for relative in paths
        }
        for attrs in identity.main_attrs:
            family = attrs.get("data-template-family", "").strip()
            template_id = attrs.get("data-template-id", "").strip()
            if f"{family}/{template_id}.html" in completed:
                errors.extend(lint_generic_contract(html, family, template_id, policy))
                errors.extend(lint_adaptive_kernel(html))
    except (OSError, UnicodeError, ValueError) as exc:
        errors = [str(exc)]
    errors = _deduplicate(errors)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
