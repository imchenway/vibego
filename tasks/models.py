"""任务相关的数据模型定义。"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Python 3.10 之前 dataclass 不支持 slots 参数，这里动态传参保持兼容。
_DATACLASS_SLOT_KW = {"slots": True} if sys.version_info >= (3, 10) else {}

_SHANGHAI_TZ_NAME = "Asia/Shanghai"
try:
    SHANGHAI_TZ = ZoneInfo(_SHANGHAI_TZ_NAME)
except ZoneInfoNotFoundError:
    SHANGHAI_TZ = timezone(timedelta(hours=8))


def _format_shanghai(dt: datetime) -> str:
    """格式化时间为上海时区 ISO8601 字符串（秒级，带偏移）。"""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(SHANGHAI_TZ).replace(microsecond=0)
    return dt.isoformat()


def shanghai_now_iso() -> str:
    """返回当前上海时区的 ISO8601 字符串。"""

    return _format_shanghai(datetime.now(SHANGHAI_TZ))


def ensure_shanghai_iso(value: Optional[str]) -> Optional[str]:
    """将任意 ISO 字符串规范化为上海时区表示。"""

    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _format_shanghai(parsed)


@dataclass(**_DATACLASS_SLOT_KW)
class TaskRecord:
    """表示单个任务的核心字段集合。"""

    id: str
    project_slug: str
    title: str
    status: str
    priority: int = 3
    task_type: Optional[str] = None
    tags: Sequence[str] = field(default_factory=tuple)
    due_date: Optional[str] = None
    description: str = ""
    related_task_id: Optional[str] = None
    parent_id: Optional[str] = None
    root_id: Optional[str] = None
    depth: int = 0
    lineage: Optional[str] = None
    created_at: str = field(default_factory=shanghai_now_iso)
    updated_at: str = field(default_factory=shanghai_now_iso)
    archived: bool = False


@dataclass(**_DATACLASS_SLOT_KW)
class TaskNoteRecord:
    """描述附加在任务上的备注信息。"""

    id: int
    task_id: str
    note_type: str
    content: str
    created_at: str = field(default_factory=shanghai_now_iso)


@dataclass(**_DATACLASS_SLOT_KW)
class TaskHistoryRecord:
    """记录任务字段的历史变更信息。"""

    id: int
    task_id: str
    field: str
    old_value: Optional[str]
    new_value: Optional[str]
    actor: Optional[str]
    event_type: str
    payload: Optional[str]
    created_at: str = field(default_factory=shanghai_now_iso)


@dataclass(**_DATACLASS_SLOT_KW)
class TaskAttachmentRecord:
    """描述绑定到任务的附件信息。"""

    id: int
    task_id: str
    display_name: str
    mime_type: str
    path: str
    kind: str = "document"
    created_at: str = field(default_factory=shanghai_now_iso)
