"""Async persistence service for reusable command presets."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

import aiosqlite

from tasks.models import shanghai_now_iso

from .models import CommandPresetRecord


class CommandPresetService:
    """Store and manage command presets per project."""

    def __init__(self, db_path: Path, project_slug: str) -> None:
        self.db_path = Path(db_path)
        self.project_slug = project_slug or "default"
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Ensure the database schema exists."""

        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("PRAGMA journal_mode = WAL")
                await self._create_tables(db)
                await db.commit()
            self._initialized = True

    async def list_presets(self, page: int, page_size: int) -> List[CommandPresetRecord]:
        """Return paginated presets for the current project."""

        await self.initialize()
        safe_page = max(1, page)
        safe_size = max(1, page_size)
        offset = (safe_page - 1) * safe_size
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, project_slug, title, command, workdir,
                       require_confirmation, created_at, updated_at
                FROM command_presets
                WHERE project_slug = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (self.project_slug, safe_size, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    async def list_all_presets(self) -> List[CommandPresetRecord]:
        """Return all presets for the current project ordered by update time."""

        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, project_slug, title, command, workdir,
                       require_confirmation, created_at, updated_at
                FROM command_presets
                WHERE project_slug = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (self.project_slug,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    async def count_presets(self) -> int:
        """Return the preset count for the current project."""

        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM command_presets WHERE project_slug = ?",
                (self.project_slug,),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0] if row else 0)

    async def get_preset(self, preset_id: int) -> Optional[CommandPresetRecord]:
        """Fetch a preset by identifier."""

        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, project_slug, title, command, workdir,
                       require_confirmation, created_at, updated_at
                FROM command_presets
                WHERE project_slug = ? AND id = ?
                """,
                (self.project_slug, preset_id),
            ) as cursor:
                row = await cursor.fetchone()
        return self._row_to_record(row) if row else None

    async def create_preset(
        self,
        *,
        title: str,
        command: str,
        workdir: Optional[str],
        require_confirmation: bool,
    ) -> CommandPresetRecord:
        """Create and return a new preset."""

        await self.initialize()
        now = shanghai_now_iso()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO command_presets (
                        project_slug, title, command, workdir,
                        require_confirmation, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.project_slug,
                        title,
                        command,
                        workdir,
                        1 if require_confirmation else 0,
                        now,
                        now,
                    ),
                )
                await db.commit()
                preset_id = cursor.lastrowid
        record = await self.get_preset(int(preset_id))
        if record is None:
            raise RuntimeError("Failed to create command preset")
        return record

    async def update_preset(
        self,
        preset_id: int,
        *,
        title: str,
        command: str,
        workdir: Optional[str],
        require_confirmation: bool,
    ) -> Optional[CommandPresetRecord]:
        """Update preset fields and return the refreshed record."""

        await self.initialize()
        now = shanghai_now_iso()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    """
                    UPDATE command_presets
                    SET title = ?, command = ?, workdir = ?,
                        require_confirmation = ?, updated_at = ?
                    WHERE id = ? AND project_slug = ?
                    """,
                    (
                        title,
                        command,
                        workdir,
                        1 if require_confirmation else 0,
                        now,
                        preset_id,
                        self.project_slug,
                    ),
                )
                await db.commit()
                if cursor.rowcount == 0:
                    return None
        return await self.get_preset(preset_id)

    async def toggle_confirmation(self, preset_id: int) -> Optional[CommandPresetRecord]:
        """Invert the confirmation flag and return the updated record."""

        record = await self.get_preset(preset_id)
        if record is None:
            return None
        return await self.update_preset(
            preset_id,
            title=record.title,
            command=record.command,
            workdir=record.workdir,
            require_confirmation=not record.require_confirmation,
        )

    async def delete_preset(self, preset_id: int) -> bool:
        """Remove a preset."""

        await self.initialize()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM command_presets WHERE id = ? AND project_slug = ?",
                    (preset_id, self.project_slug),
                )
                await db.commit()
                return cursor.rowcount > 0

    async def _create_tables(self, db: aiosqlite.Connection) -> None:
        """Create the command preset schema."""

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS command_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_slug TEXT NOT NULL,
                title TEXT NOT NULL,
                command TEXT NOT NULL,
                workdir TEXT,
                require_confirmation INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_command_presets_project
            ON command_presets(project_slug, updated_at DESC, id DESC)
            """
        )

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> CommandPresetRecord:
        """Convert a database row into a dataclass instance."""

        return CommandPresetRecord(
            id=int(row["id"]),
            project_slug=str(row["project_slug"]),
            title=str(row["title"]),
            command=str(row["command"]),
            workdir=row["workdir"],
            require_confirmation=bool(row["require_confirmation"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
