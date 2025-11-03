"""命令行文本解析工具。"""
from __future__ import annotations

import re
from typing import Dict, Tuple

SEGMENT_SPLIT_RE = re.compile(r"(?<!\\)\|")
ESCAPED_PIPE_RE = re.compile(r"\\\|")


def _split_segments(raw: str) -> list[str]:
    """按未转义的 `|` 分割字符串，并恢复转义管道。"""

    if not raw:
        return []
    parts = []
    buf = []
    escape = False
    for ch in raw:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == "|":
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf).strip())
    cleaned = [ESCAPED_PIPE_RE.sub("|", part) for part in parts if part]
    return cleaned


def parse_structured_text(raw: str) -> Tuple[str, Dict[str, str]]:
    """解析命令参数，返回主体文本与 key=value 字段。"""

    segments = _split_segments(raw)
    if not segments:
        return "", {}
    body = segments[0]
    extra: Dict[str, str] = {}
    for segment in segments[1:]:
        if "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key:
            extra[key] = value
    return body, extra


def parse_simple_kv(raw: str) -> Dict[str, str]:
    """仅解析 key=value 片段，忽略主体文本。"""

    _, extra = parse_structured_text(raw)
    return extra
