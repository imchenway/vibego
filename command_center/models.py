"""Dataclasses for command preset entities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class CommandPresetRecord:
    """Represent a reusable CLI command snippet."""

    id: int
    project_slug: str
    title: str
    command: str
    workdir: Optional[str]
    require_confirmation: bool
    created_at: str
    updated_at: str


COMMAND_SCOPE_GLOBAL = "global"
COMMAND_SCOPE_PROJECT = "project"
GLOBAL_COMMAND_PROJECT_SLUG = "__global__"


@dataclass(frozen=True, slots=True)
class ScopedCommandPreset:
    """Represent a preset with its logical ownership scope."""

    scope: str  # COMMAND_SCOPE_GLOBAL or COMMAND_SCOPE_PROJECT
    record: CommandPresetRecord
