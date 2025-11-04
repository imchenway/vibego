"""Utilities for parsing command-style text payloads."""
from __future__ import annotations

import re
from typing import Dict, Tuple

SEGMENT_SPLIT_RE = re.compile(r"(?<!\\)\|")
ESCAPED_PIPE_RE = re.compile(r"\\\|")


def _split_segments(raw: str) -> list[str]:
    """Split by unescaped ``|`` characters and restore escaped pipes."""

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
    """Parse command arguments and return the body together with key/value fields."""

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
    """Parse only ``key=value`` segments and ignore the body text."""

    _, extra = parse_structured_text(raw)
    return extra
