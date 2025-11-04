"""Data models used by the task subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SHANGHAI_TZ_NAME = "Asia/Shanghai"
try:
    SHANGHAI_TZ = ZoneInfo(_SHANGHAI_TZ_NAME)
except ZoneInfoNotFoundError:
    SHANGHAI_TZ = timezone(timedelta(hours=8))


def _format_shanghai(dt: datetime) -> str:
    """Format a datetime as an ISO8601 string in the Shanghai timezone."""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(SHANGHAI_TZ).replace(microsecond=0)
    return dt.isoformat()


def shanghai_now_iso() -> str:
    """Return the current time in Shanghai as an ISO8601 string."""

    return _format_shanghai(datetime.now(SHANGHAI_TZ))


def ensure_shanghai_iso(value: Optional[str]) -> Optional[str]:
    """Normalize an ISO string to the Shanghai timezone representation."""

    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _format_shanghai(parsed)


@dataclass(slots=True)
class TaskRecord:
    """Core fields that describe a single task."""

    id: str
    project_slug: str
    title: str
    status: str
    priority: int = 3
    task_type: Optional[str] = None
    tags: Sequence[str] = field(default_factory=tuple)
    due_date: Optional[str] = None
    description: str = ""
    parent_id: Optional[str] = None
    root_id: Optional[str] = None
    depth: int = 0
    lineage: Optional[str] = None
    created_at: str = field(default_factory=shanghai_now_iso)
    updated_at: str = field(default_factory=shanghai_now_iso)
    archived: bool = False


@dataclass(slots=True)
class TaskNoteRecord:
    """Note information attached to a task."""

    id: int
    task_id: str
    note_type: str
    content: str
    created_at: str = field(default_factory=shanghai_now_iso)


@dataclass(slots=True)
class TaskHistoryRecord:
    """Historical trace of changes to task fields."""

    id: int
    task_id: str
    field: str
    old_value: Optional[str]
    new_value: Optional[str]
    actor: Optional[str]
    event_type: str
    payload: Optional[str]
    created_at: str = field(default_factory=shanghai_now_iso)
