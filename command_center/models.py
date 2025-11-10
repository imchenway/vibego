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
