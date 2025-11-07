"""Persistence and business logic for the task subsystem."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import aiosqlite

from .constants import STATUS_ALIASES, TASK_STATUSES
from .models import (
    TaskHistoryRecord,
    TaskNoteRecord,
    TaskRecord,
    ensure_shanghai_iso,
    shanghai_now_iso,
)

TASK_PREFIX = "TASK_"
DEFAULT_LIMIT = 10


logger = logging.getLogger(__name__)


class TaskService:
    """Wrap task-related database operations."""

    def __init__(self, db_path: Path, project_slug: str) -> None:
        """Initialise the service with the database path and project slug."""

        self.db_path = Path(db_path)
        self.project_slug = project_slug
        self._lock = asyncio.Lock()
        self._initialized = False
        self._valid_statuses = set(TASK_STATUSES)

    async def initialize(self) -> None:
        """Ensure the schema exists and run required migrations."""

        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("PRAGMA journal_mode = WAL")
            await self._create_tables(db)
            await self._migrate_timezones(db)
            await self._migrate_task_ids_to_underscore(db)
            await self._verify_status_values(db)
            await self._archive_legacy_child_tasks(db)
            await self._drop_child_sequences_table(db)
            await db.commit()
        self._initialized = True

    async def _create_tables(self, db: aiosqlite.Connection) -> None:
        """Create or augment all tables and indexes for tasks."""

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                project_slug TEXT NOT NULL,
                root_id TEXT NOT NULL,
                parent_id TEXT,
                depth INTEGER NOT NULL DEFAULT 0,
                lineage TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 3,
                task_type TEXT,
                tags TEXT,
                due_date TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(parent_id) REFERENCES tasks(id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS task_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                note_type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                actor TEXT,
                event_type TEXT NOT NULL DEFAULT 'field_change',
                payload TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
            """
        )
        try:
            await db.execute(
                "ALTER TABLE task_history ADD COLUMN event_type TEXT NOT NULL DEFAULT 'field_change'"
            )
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        try:
            await db.execute("ALTER TABLE task_history ADD COLUMN payload TEXT")
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS task_sequences (
                project_slug TEXT PRIMARY KEY,
                last_root INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_project_lineage
            ON tasks(project_slug, archived, lineage)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_project_status
            ON tasks(project_slug, status)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notes_task
            ON task_notes(task_id, created_at)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_history_task
            ON task_history(task_id, created_at)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_history_task_event
            ON task_history(task_id, event_type, created_at)
            """
        )
        try:
            await db.execute("ALTER TABLE tasks ADD COLUMN description TEXT")
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        try:
            await db.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT")
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_project_type
            ON tasks(project_slug, task_type)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_project_title
            ON tasks(project_slug, title)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_project_description
            ON tasks(project_slug, description)
            """
        )

    async def _migrate_timezones(self, db: aiosqlite.Connection) -> None:
        """Convert legacy UTC timestamps to their Shanghai equivalents."""

        db.row_factory = aiosqlite.Row
        tables: Sequence[tuple[str, str, tuple[str, ...]]] = (
            ("tasks", "id", ("created_at", "updated_at")),
            ("task_notes", "id", ("created_at",)),
            ("task_history", "id", ("created_at",)),
        )
        for table, pk, columns in tables:
            column_list = ", ".join(columns)
            where_clause = " OR ".join(f"{column} LIKE '%Z'" for column in columns)
            sql = f"SELECT {pk}, {column_list} FROM {table}"
            if where_clause:
                sql += f" WHERE {where_clause}"
            async with db.execute(sql) as cursor:
                rows = await cursor.fetchall()
            if not rows:
                continue
            for row in rows:
                updates: dict[str, str] = {}
                for column in columns:
                    original = row[column]
                    converted = ensure_shanghai_iso(original)
                    if converted is not None and converted != original:
                        updates[column] = converted
                if not updates:
                    continue
                assignments = ", ".join(f"{column} = ?" for column in updates)
                params = list(updates.values())
                params.append(row[pk])
                await db.execute(
                    f"UPDATE {table} SET {assignments} WHERE {pk} = ?",
                    params,
                )

        for legacy, target in STATUS_ALIASES.items():
            await db.execute(
                "UPDATE tasks SET status=? WHERE status=?",
                (target, legacy),
            )
            await db.execute(
                "UPDATE task_history SET new_value=? WHERE new_value=?",
                (target, legacy),
            )
            await db.execute(
                "UPDATE task_history SET old_value=? WHERE old_value=?",
                (target, legacy),
            )
            await db.execute(
                "UPDATE task_notes SET note_type=? WHERE note_type=?",
                (target, legacy),
            )

    async def _migrate_task_ids_to_underscore(self, db: aiosqlite.Connection) -> None:
        """Rewrite legacy task IDs with underscores so Telegram commands remain clickable."""

        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id FROM tasks
            WHERE project_slug = ?
              AND (
                  instr(id, '-') > 0
                  OR instr(id, '.') > 0
                  OR (substr(id, 1, 4) = 'TASK' AND substr(id, 5, 1) != '_')
              )
            LIMIT 1
            """,
            (self.project_slug,),
        ) as cursor:
            legacy_row = await cursor.fetchone()
        if not legacy_row:
            return

        logger.info("Detected legacy task IDs, starting migration: project=%s", self.project_slug)
        await db.execute("PRAGMA foreign_keys = OFF")
        await db.execute("PRAGMA defer_foreign_keys = ON")
        mapping: Dict[str, str] = {}
        try:
            async with db.execute(
                """
                SELECT id FROM tasks
                WHERE project_slug = ?
                ORDER BY LENGTH(id) DESC
                """,
                (self.project_slug,),
            ) as cursor:
                rows = await cursor.fetchall()

            existing_ids = {row["id"] for row in rows}

            for row in rows:
                old_id = row["id"]
                new_id = self._canonical_task_id(old_id)
                if new_id == old_id:
                    continue
                if new_id is None:
                    logger.error(
                        "Task ID migration encountered a non-normalisable value: project=%s value=%s",
                        self.project_slug,
                        old_id,
                    )
                    raise ValueError("Task ID migration failed: unable to normalise ID")
                if new_id != old_id and new_id in existing_ids:
                    logger.error(
                        "Task ID migration detected a potential conflict: project=%s old=%s new=%s",
                        self.project_slug,
                        old_id,
                        new_id,
                    )
                    raise ValueError("Task ID migration conflict: target ID already exists")
                if new_id in mapping.values() or new_id in mapping:
                    logger.error(
                        "Task ID migration detected a conflict: project=%s old=%s new=%s",
                        self.project_slug,
                        old_id,
                        new_id,
                    )
                    raise ValueError("Task ID migration conflict")
                mapping[old_id] = new_id

            if not mapping:
                return

            await db.executemany(
                "UPDATE tasks SET id = ? WHERE id = ?",
                [(new_id, old_id) for old_id, new_id in mapping.items()],
            )
            await db.executemany(
                "UPDATE tasks SET parent_id = ? WHERE parent_id = ?",
                [(new_id, old_id) for old_id, new_id in mapping.items()],
            )
            await db.executemany(
                "UPDATE tasks SET root_id = ? WHERE root_id = ?",
                [(new_id, old_id) for old_id, new_id in mapping.items()],
            )
            for table in ("task_notes", "task_history"):
                await db.executemany(
                    f"UPDATE {table} SET task_id = ? WHERE task_id = ?",
                    [(new_id, old_id) for old_id, new_id in mapping.items()],
                )
        finally:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("PRAGMA defer_foreign_keys = OFF")

        self._write_id_migration_report(mapping)
        logger.info(
            "Task ID migration completed: project=%s changed=%s",
            self.project_slug,
            len(mapping),
        )

    async def _archive_legacy_child_tasks(self, db: aiosqlite.Connection) -> None:
        """Archive legacy child tasks so they stop appearing in listings."""

        now = shanghai_now_iso()
        cursor = await db.execute(
            """
            UPDATE tasks
            SET archived = 1,
                updated_at = ?
            WHERE project_slug = ?
              AND parent_id IS NOT NULL
              AND archived = 0
            """,
            (now, self.project_slug),
        )
        try:
            changed = cursor.rowcount or 0
        except AttributeError:
            changed = 0
        await cursor.close()
        if changed > 0:
            logger.info("Archived legacy child tasks: project=%s count=%s", self.project_slug, changed)

    async def _drop_child_sequences_table(self, db: aiosqlite.Connection) -> None:
        """Remove the defunct child sequence table to prevent stale lookups."""

        await db.execute("DROP TABLE IF EXISTS child_sequences")

    async def _verify_status_values(self, db: aiosqlite.Connection) -> None:
        """Validate task status values against the allowed enumeration."""

        async with db.execute(
            "SELECT DISTINCT status FROM tasks WHERE project_slug = ?",
            (self.project_slug,),
        ) as cursor:
            rows = await cursor.fetchall()
        for (status,) in rows:
            if status is None:
                logger.error(
                    "Task status integrity check found NULL value: project=%s",
                    self.project_slug,
                )
                continue
            normalized = self._normalize_status_token(status, context="integrity_check")
            if normalized not in self._valid_statuses:
                logger.error(
                    "Task status integrity check found unknown value: project=%s value=%s",
                    self.project_slug,
                    status,
                )
    async def create_root_task(
        self,
        *,
        title: str,
        status: str,
        priority: int,
        task_type: str,
        tags: Sequence[str],
        due_date: Optional[str],
        description: Optional[str] = None,
        actor: Optional[str],
    ) -> TaskRecord:
        """Create a root task and capture the initial history entry."""

        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("BEGIN IMMEDIATE")
                root_seq = await self._next_root_sequence(db)
                task_id = f"{TASK_PREFIX}{root_seq:04d}"
                lineage = f"{root_seq:04d}"
                now = shanghai_now_iso()
                tags_json = json.dumps(list(tags)) if tags else "[]"
                normalized_status = self._normalize_status_token(status, context="create_root")
                await db.execute(
                    """
                    INSERT INTO tasks (
                        id, project_slug, root_id, parent_id, depth, lineage,
                        title, status, priority, task_type, tags, due_date, description,
                        created_at, updated_at, archived
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        self.project_slug,
                        task_id,
                        None,
                        0,
                        lineage,
                        title,
                        normalized_status,
                        priority,
                        task_type,
                        tags_json,
                        due_date,
                        description or "",
                        now,
                        now,
                        0,
                    ),
                )
                await db.commit()
                return TaskRecord(
                    id=task_id,
                    project_slug=self.project_slug,
                    title=title,
                    status=normalized_status,
                    priority=priority,
                    task_type=task_type,
                    tags=tuple(tags),
                    due_date=due_date,
                    description=description or "",
                    parent_id=None,
                    root_id=task_id,
                    depth=0,
                    lineage=lineage,
                    created_at=now,
                    updated_at=now,
                    archived=False,
                )

    async def list_tasks(
        self,
        *,
        status: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        include_archived: bool = False,
        exclude_statuses: Optional[Sequence[str]] = None,
    ) -> List[TaskRecord]:
        """List tasks with optional filters, status exclusions, and pagination."""

        query = [
            "SELECT * FROM tasks WHERE project_slug = ?",
        ]
        params: List[object] = [self.project_slug]
        if not include_archived:
            query.append("AND archived = 0")
        if status:
            query.append("AND status = ?")
            params.append(status)
        elif exclude_statuses:
            placeholders = ", ".join("?" for _ in exclude_statuses)
            query.append(f"AND status NOT IN ({placeholders})")
            params.extend(exclude_statuses)
        query.append("ORDER BY lineage ASC LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        sql = " ".join(query)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_task(row, context="list") for row in rows]

    async def search_tasks(
        self,
        keyword: str,
        *,
        page: int,
        page_size: int = DEFAULT_LIMIT,
    ) -> Tuple[List[TaskRecord], int, int]:
        """Search tasks by title or description and return results, pages, and totals."""

        if page_size <= 0:
            page_size = DEFAULT_LIMIT
        page = max(page, 1)
        trimmed = (keyword or "").strip()
        if not trimmed:
            return [], 0, 0
        like_pattern = f"%{trimmed}%"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")

            count_sql = (
                "SELECT COUNT(1) AS c FROM tasks "
                "WHERE project_slug = ? AND archived = 0 "
                "AND (title LIKE ? OR description LIKE ?)"
            )
            params = [self.project_slug, like_pattern, like_pattern]
            async with db.execute(count_sql, params) as cursor:
                row = await cursor.fetchone()
            total = int(row["c"] if row else 0)
            if total == 0:
                return [], 0, 0

            offset = (page - 1) * page_size
            query_sql = (
                "SELECT * FROM tasks "
                "WHERE project_slug = ? AND archived = 0 "
                "AND (title LIKE ? OR description LIKE ?) "
                "ORDER BY updated_at DESC, id ASC LIMIT ? OFFSET ?"
            )
            query_params = [self.project_slug, like_pattern, like_pattern, page_size, offset]
            async with db.execute(query_sql, query_params) as cursor:
                rows = await cursor.fetchall()

        pages = (total + page_size - 1) // page_size if page_size else 1
        return [self._row_to_task(row, context="search") for row in rows], pages, total

    async def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """Return a task by ID, or ``None`` when it does not exist."""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            return None
        task_id = canonical_task_id
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(
                "SELECT * FROM tasks WHERE project_slug = ? AND id = ?",
                (self.project_slug, task_id),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row, context="get") if row else None

    async def update_task(
        self,
        task_id: str,
        *,
        actor: Optional[str],
        title: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[int] = None,
        task_type: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        due_date: Optional[str] = None,
        description: Optional[str] = None,
        archived: Optional[bool] = None,
    ) -> TaskRecord:
        """Update a task, write history entries, and return the refreshed record."""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            raise ValueError("Task does not exist")
        task_id = canonical_task_id
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("BEGIN IMMEDIATE")
                row = await self._fetch_task_row(db, task_id)
                if row is None:
                    await db.execute("ROLLBACK")
                    raise ValueError("Task does not exist")
                updates = []
                params: List[object] = []
                if title is not None and title != row["title"]:
                    updates.append("title = ?")
                    params.append(title)
                if status is not None:
                    normalized_status = self._normalize_status_token(status, context="update")
                    if normalized_status != status:
                        logger.warning(
                            "Task status input corrected automatically: task_id=%s raw=%s normalized=%s",
                            task_id,
                            status,
                            normalized_status,
                        )
                    status_value = normalized_status
                else:
                    status_value = None
                if status_value is not None and status_value != row["status"]:
                    updates.append("status = ?")
                    params.append(status_value)
                if priority is not None and priority != row["priority"]:
                    updates.append("priority = ?")
                    params.append(priority)
                if task_type is not None and task_type != row["task_type"]:
                    updates.append("task_type = ?")
                    params.append(task_type)
                if tags is not None:
                    tags_json = json.dumps(list(tags))
                    if tags_json != row["tags"]:
                        updates.append("tags = ?")
                        params.append(tags_json)
                if due_date is not None and due_date != row["due_date"]:
                    updates.append("due_date = ?")
                    params.append(due_date)
                if description is not None and description != row["description"]:
                    updates.append("description = ?")
                    params.append(description)
                if archived is not None:
                    archived_int = 1 if archived else 0
                    if archived_int != row["archived"]:
                        updates.append("archived = ?")
                        params.append(archived_int)
                if updates:
                    now = shanghai_now_iso()
                    updates.append("updated_at = ?")
                    params.append(now)
                    params.append(task_id)
                    await db.execute(
                        f"UPDATE tasks SET {' , '.join(updates)} WHERE id = ?",
                        params,
                    )
                await db.commit()
        updated = await self.get_task(task_id)
        if updated is None:
            raise ValueError("Task does not exist")
        return updated

    async def add_note(
        self,
        task_id: str,
        *,
        note_type: str,
        content: str,
        actor: Optional[str],
    ) -> TaskNoteRecord:
        """Append a note to a task (notes no longer create task history entries)."""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            raise ValueError("Task does not exist")
        task_id = canonical_task_id
        now = shanghai_now_iso()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("BEGIN IMMEDIATE")
                task_row = await self._fetch_task_row(db, task_id)
                if task_row is None:
                    await db.execute("ROLLBACK")
                    raise ValueError("Task does not exist")
                cursor = await db.execute(
                    """
                    INSERT INTO task_notes(task_id, note_type, content, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (task_id, note_type, content, now),
                )
                note_id = cursor.lastrowid
                await db.commit()
        return TaskNoteRecord(
            id=note_id,
            task_id=task_id,
            note_type=note_type,
            content=content,
            created_at=now,
        )

    async def list_notes(self, task_id: str) -> List[TaskNoteRecord]:
        """Return every note for a task ordered by creation time."""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            return []
        task_id = canonical_task_id
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(
                """
                SELECT * FROM task_notes WHERE task_id = ? ORDER BY created_at ASC
                """,
                (task_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            TaskNoteRecord(
                id=row["id"],
                task_id=row["task_id"],
                note_type=row["note_type"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def list_history(self, task_id: str) -> List[TaskHistoryRecord]:
        """Return the full history list for a task."""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            return []
        task_id = canonical_task_id
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(
                """
                SELECT * FROM task_history WHERE task_id = ? ORDER BY created_at ASC
                """,
                (task_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            TaskHistoryRecord(
                id=row["id"],
                task_id=row["task_id"],
                field=row["field"],
                old_value=row["old_value"],
                new_value=row["new_value"],
                actor=row["actor"],
                event_type=(row["event_type"] if "event_type" in row.keys() else None) or "field_change",
                payload=row["payload"] if "payload" in row.keys() else None,
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def log_task_event(
        self,
        task_id: str,
        *,
        event_type: str,
        actor: Optional[str],
        field: str = "",
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> None:
        """Record a structured task event."""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            raise ValueError("Task does not exist")
        task_id = canonical_task_id

        event_token = (event_type or "task_action").strip() or "task_action"
        if payload is None:
            payload_text: Optional[str] = None
        elif isinstance(payload, str):
            payload_text = payload
        else:
            try:
                payload_text = json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                logger.warning("Failed to serialise event payload: task_id=%s error=%s", task_id, exc)
                payload_text = None
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("BEGIN IMMEDIATE")
                row = await self._fetch_task_row(db, task_id)
                if row is None:
                    await db.execute("ROLLBACK")
                    raise ValueError("Task does not exist")
                await self._insert_history(
                    db,
                    task_id,
                    field,
                    old_value,
                    new_value,
                    actor,
                    event_type=event_token,
                    payload=payload_text,
                    created_at=created_at,
                )
                await db.commit()

    async def delete_task(self, task_id: str, *, actor: Optional[str]) -> TaskRecord:
        """Perform a logical delete by marking the task archived and return the state."""

        updated = await self.update_task(task_id, actor=actor, archived=True)
        return updated

    async def paginate(
        self,
        *,
        status: Optional[str],
        page: int,
        page_size: int = DEFAULT_LIMIT,
        exclude_statuses: Optional[Sequence[str]] = None,
    ) -> Tuple[List[TaskRecord], int]:
        """Fetch a specific page of tasks and return both the page data and total count."""

        total = await self.count_tasks(
            status=status,
            include_archived=False,
            exclude_statuses=exclude_statuses,
        )
        offset = max(page - 1, 0) * page_size
        tasks = await self.list_tasks(
            status=status,
            limit=page_size,
            offset=offset,
            exclude_statuses=exclude_statuses,
        )
        pages = (total + page_size - 1) // page_size if page_size else 1
        return tasks, pages

    async def count_tasks(
        self,
        *,
        status: Optional[str],
        include_archived: bool,
        exclude_statuses: Optional[Sequence[str]] = None,
    ) -> int:
        """Count tasks that satisfy the provided filters."""

        query = "SELECT COUNT(1) AS c FROM tasks WHERE project_slug = ?"
        params: List[object] = [self.project_slug]
        if not include_archived:
            query += " AND archived = 0"
        if status:
            query += " AND status = ?"
            params.append(status)
        elif exclude_statuses:
            placeholders = ", ".join("?" for _ in exclude_statuses)
            query += f" AND status NOT IN ({placeholders})"
            params.extend(exclude_statuses)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
        return int(row["c"] if row else 0)

    async def backup(self, target_path: Path) -> None:
        """Backup the current database to the target path."""

        target_path = target_path.expanduser()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as source:
                async with aiosqlite.connect(target_path) as dest:
                    await dest.execute("PRAGMA foreign_keys = OFF")
                    await source.backup(dest)
                    await dest.commit()

    @staticmethod
    def _convert_task_id_token(value: Optional[str]) -> Optional[str]:
        """Normalise task ID separators to remain compatible with legacy formats."""

        if value is None:
            return None
        token = value.replace("-", "_").replace(".", "_")
        token = re.sub(r"_+", "_", token)
        if token.startswith("TASK"):
            suffix = token[4:]
            if suffix and not suffix.startswith("_"):
                # Legacy formats like TASK0001/TASK0001_1 require an underscore.
                token = f"TASK_{suffix}"
            else:
                token = f"TASK{suffix}"
        return token

    def _canonical_task_id(self, value: Optional[str]) -> Optional[str]:
        """Normalise externally provided task IDs into the canonical format."""

        if value is None:
            return None
        token = value.strip()
        if not token:
            return token
        token = token.upper()
        return self._convert_task_id_token(token)

    def _write_id_migration_report(self, mapping: Dict[str, str]) -> None:
        """Write a JSON report describing task ID migration results."""

        if not mapping:
            return
        try:
            report_dir = self.db_path.parent / "backups"
            report_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            report_path = report_dir / f"{self.project_slug}_id_migration_{timestamp}.json"
            payload = {
                "project_slug": self.project_slug,
                "migrated_at": datetime.now().isoformat(),
                "changed": len(mapping),
                "items": [
                    {"old_id": old_id, "new_id": new_id}
                    for old_id, new_id in sorted(mapping.items())
                ],
            }
            report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception as exc:
            logger.warning(
                "Failed to write task ID migration report: project=%s error=%s",
                self.project_slug,
                exc,
            )

    async def _fetch_task_row(self, db: aiosqlite.Connection, task_id: str):
        """Fetch the raw task row from the database."""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            return None
        task_id = canonical_task_id
        async with db.execute(
            "SELECT * FROM tasks WHERE project_slug = ? AND id = ?",
            (self.project_slug, task_id),
        ) as cursor:
            return await cursor.fetchone()

    async def _next_root_sequence(self, db: aiosqlite.Connection) -> int:
        """Increment and return the next root task sequence."""

        async with db.execute(
            "SELECT last_root FROM task_sequences WHERE project_slug = ?",
            (self.project_slug,),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            new_value = int(row["last_root"]) + 1
        else:
            new_value = 1
        await db.execute(
            """
            INSERT INTO task_sequences(project_slug, last_root)
            VALUES(?, ?)
            ON CONFLICT(project_slug) DO UPDATE SET last_root = excluded.last_root
            """,
            (self.project_slug, new_value),
        )
        return new_value

    async def _insert_history(
        self,
        db: aiosqlite.Connection,
        task_id: str,
        field: str,
        old_value: Optional[str],
        new_value: Optional[str],
        actor: Optional[str],
        *,
        event_type: str = "field_change",
        payload: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> None:
        """Insert a task history entry while filling timestamps automatically."""

        normalized = ensure_shanghai_iso(created_at) if created_at else None
        timestamp = normalized or shanghai_now_iso()
        await db.execute(
            """
            INSERT INTO task_history(task_id, field, old_value, new_value, actor, event_type, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                field,
                old_value,
                new_value,
                actor,
                event_type or "field_change",
                payload,
                timestamp,
            ),
        )

    def _normalize_status_token(self, value: Optional[str], *, context: str) -> str:
        """Normalise status strings, providing compatibility with legacy aliases."""

        if not value:
            logger.warning("Encountered empty task status; falling back to default: context=%s", context)
            return TASK_STATUSES[0]
        token = str(value).strip().lower()
        mapped = STATUS_ALIASES.get(token, token)
        if mapped not in self._valid_statuses:
            logger.warning(
                "Unknown task status detected: value=%s mapped=%s context=%s",
                value,
                mapped,
                context,
            )
            return mapped
        if mapped != token:
            logger.info(
                "Task status converted via alias: raw=%s normalized=%s context=%s",
                value,
                mapped,
                context,
            )
        return mapped

    def _row_to_task(
        self,
        row: aiosqlite.Row,
        *,
        context: str,
    ) -> TaskRecord:
        """Convert a sqlite row into a ``TaskRecord`` instance."""

        tags_raw = row["tags"] or "[]"
        try:
            tags_data = tuple(json.loads(tags_raw))
        except json.JSONDecodeError:
            tags_data = tuple(filter(None, (tag.strip() for tag in tags_raw.split(","))))
        normalized_status = self._normalize_status_token(row["status"], context=f"{context}:{row['id']}")
        return TaskRecord(
            id=row["id"],
            project_slug=row["project_slug"],
            title=row["title"],
            status=normalized_status,
            priority=row["priority"],
            task_type=row["task_type"] if "task_type" in row.keys() else None,
            tags=tags_data,
            due_date=row["due_date"],
            description=(row["description"] or "") if "description" in row.keys() else "",
            parent_id=row["parent_id"],
            root_id=row["root_id"],
            depth=row["depth"],
            lineage=row["lineage"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived=bool(row["archived"]),
        )
