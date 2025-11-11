"""命令管理相关的数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from tasks.models import shanghai_now_iso, ensure_shanghai_iso


@dataclass(slots=True)
class CommandDefinition:
    """记录单条命令的元数据。"""

    id: int
    project_slug: str
    name: str
    title: str
    command: str
    scope: str = "project"
    description: str = ""
    timeout: int = 120
    enabled: bool = True
    created_at: str = field(default_factory=shanghai_now_iso)
    updated_at: str = field(default_factory=shanghai_now_iso)
    aliases: Tuple[str, ...] = ()


@dataclass(slots=True)
class CommandHistoryRecord:
    """描述命令执行的审计记录。"""

    id: int
    command_id: int
    project_slug: str
    command_name: str
    command_title: Optional[str]
    trigger: Optional[str]
    actor_id: Optional[int]
    actor_username: Optional[str]
    actor_name: Optional[str]
    exit_code: Optional[int]
    status: str
    output: Optional[str]
    error: Optional[str]
    started_at: str = field(default_factory=shanghai_now_iso)
    finished_at: str = field(default_factory=shanghai_now_iso)

    def ensure_timestamps(self) -> None:
        """规范化时间字符串，便于外部展示。"""

        self.started_at = ensure_shanghai_iso(self.started_at) or self.started_at
        self.finished_at = ensure_shanghai_iso(self.finished_at) or self.finished_at
