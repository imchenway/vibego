"""任务持久化与业务逻辑。"""
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
    TaskAttachmentRecord,
    ensure_shanghai_iso,
    shanghai_now_iso,
)

TASK_PREFIX = "TASK_"
DEFAULT_LIMIT = 10


logger = logging.getLogger(__name__)


class TaskService:
    """封装任务相关的数据库操作。"""

    def __init__(self, db_path: Path, project_slug: str) -> None:
        """初始化服务实例，绑定数据库路径与项目标识。"""

        self.db_path = Path(db_path)
        self.project_slug = project_slug
        self._lock: Optional[asyncio.Lock] = None
        self._initialized = False
        self._valid_statuses = set(TASK_STATUSES)

    def _get_lock(self) -> asyncio.Lock:
        """惰性初始化内部锁，兼容 Python 3.9 未创建事件循环的场景。"""

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def initialize(self) -> None:
        """确保数据库结构存在，并执行必要的迁移逻辑。"""

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
        """创建或补全任务相关的全部表结构与索引。"""

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                project_slug TEXT NOT NULL,
                root_id TEXT NOT NULL,
                parent_id TEXT,
                related_task_id TEXT,
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
            CREATE TABLE IF NOT EXISTS task_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'document',
                display_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
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
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_attachments_task
            ON task_attachments(task_id, created_at)
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
        try:
            await db.execute("ALTER TABLE tasks ADD COLUMN related_task_id TEXT")
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
        """将遗留的 UTC 字符串转换为上海时区表示。"""

        db.row_factory = aiosqlite.Row
        tables: Sequence[tuple[str, str, tuple[str, ...]]] = (
            ("tasks", "id", ("created_at", "updated_at")),
            ("task_notes", "id", ("created_at",)),
            ("task_history", "id", ("created_at",)),
            ("task_attachments", "id", ("created_at",)),
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
        """将历史任务 ID 的连字符/点号改写为下划线格式，保障 Telegram 命令可点击。"""

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

        logger.info("检测到旧版任务 ID，开始迁移: project=%s", self.project_slug)
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
                        "任务 ID 迁移检测到无法规范化的值: project=%s value=%s",
                        self.project_slug,
                        old_id,
                    )
                    raise ValueError("任务 ID 迁移失败：存在无法规范化的 ID")
                if new_id != old_id and new_id in existing_ids:
                    logger.error(
                        "任务 ID 迁移检测到潜在冲突: project=%s old=%s new=%s",
                        self.project_slug,
                        old_id,
                        new_id,
                    )
                    raise ValueError("任务 ID 迁移冲突：目标 ID 已存在")
                if new_id in mapping.values() or new_id in mapping:
                    logger.error(
                        "任务 ID 迁移检测到冲突: project=%s old=%s new=%s",
                        self.project_slug,
                        old_id,
                        new_id,
                    )
                    raise ValueError("任务 ID 迁移冲突")
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
            "任务 ID 迁移完成: project=%s changed=%s",
            self.project_slug,
            len(mapping),
        )

    async def _archive_legacy_child_tasks(self, db: aiosqlite.Connection) -> None:
        """归档遗留的子任务，防止其继续出现在任务列表中。"""

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
            logger.info("已归档遗留子任务: project=%s count=%s", self.project_slug, changed)

    async def _drop_child_sequences_table(self, db: aiosqlite.Connection) -> None:
        """移除已废弃的子任务序列表，避免后续访问错误。"""

        await db.execute("DROP TABLE IF EXISTS child_sequences")

    async def _verify_status_values(self, db: aiosqlite.Connection) -> None:
        """校验任务表中的状态值是否符合当前合法枚举。"""

        async with db.execute(
            "SELECT DISTINCT status FROM tasks WHERE project_slug = ?",
            (self.project_slug,),
        ) as cursor:
            rows = await cursor.fetchall()
        for (status,) in rows:
            if status is None:
                logger.error(
                    "任务状态检查发现 NULL 值: project=%s",
                    self.project_slug,
                )
                continue
            normalized = self._normalize_status_token(status, context="integrity_check")
            if normalized not in self._valid_statuses:
                logger.error(
                    "任务状态检查发现无法识别的值: project=%s value=%s",
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
        related_task_id: Optional[str] = None,
        actor: Optional[str],
    ) -> TaskRecord:
        """创建顶级任务并写入初始历史记录。"""

        async with self._get_lock():
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
                canonical_related_task_id = self._canonical_task_id(related_task_id)
                canonical_related_task_id = (canonical_related_task_id or "").strip() or None
                await db.execute(
                    """
                    INSERT INTO tasks (
                        id, project_slug, root_id, parent_id, depth, lineage,
                        title, status, priority, task_type, tags, due_date, description, related_task_id,
                        created_at, updated_at, archived
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        canonical_related_task_id,
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
                    related_task_id=canonical_related_task_id,
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
        """按条件查询任务列表，支持分页与状态过滤（默认按更新时间倒序）。"""

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
        # 任务列表按更新时间倒序展示：符合“最近更新优先”的用户预期，也与关联任务选择视图保持一致。
        query.append("ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        sql = " ".join(query)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_task(row, context="list") for row in rows]

    async def list_recent_tasks(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        include_archived: bool = False,
    ) -> List[TaskRecord]:
        """按更新时间倒序返回任务列表，用于“最近更新”类视图。"""

        safe_limit = max(1, min(int(limit or DEFAULT_LIMIT), 50))
        safe_offset = max(int(offset or 0), 0)
        query = ["SELECT * FROM tasks WHERE project_slug = ?"]
        params: List[object] = [self.project_slug]
        if not include_archived:
            query.append("AND archived = 0")
        query.append("ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?")
        params.extend([safe_limit, safe_offset])
        sql = " ".join(query)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_task(row, context="recent") for row in rows]

    async def search_tasks(
        self,
        keyword: str,
        *,
        page: int,
        page_size: int = DEFAULT_LIMIT,
    ) -> Tuple[List[TaskRecord], int, int]:
        """按标题或描述模糊搜索任务，并返回结果与分页总数。"""

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
        """根据任务 ID 返回任务详情，不存在时返回 None。"""

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
        """更新任务字段并记录历史，返回最新任务。"""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            raise ValueError("任务不存在")
        task_id = canonical_task_id
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("BEGIN IMMEDIATE")
                row = await self._fetch_task_row(db, task_id)
                if row is None:
                    await db.execute("ROLLBACK")
                    raise ValueError("任务不存在")
                updates = []
                params: List[object] = []
                if title is not None and title != row["title"]:
                    updates.append("title = ?")
                    params.append(title)
                if status is not None:
                    normalized_status = self._normalize_status_token(status, context="update")
                    if normalized_status != status:
                        logger.warning(
                            "任务状态入参已自动修正: task_id=%s raw=%s normalized=%s",
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
            raise ValueError("任务不存在")
        return updated

    async def add_note(
        self,
        task_id: str,
        *,
        note_type: str,
        content: str,
        actor: Optional[str],
    ) -> TaskNoteRecord:
        """为任务追加备注，并同步写入历史记录。"""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            raise ValueError("任务不存在")
        task_id = canonical_task_id
        now = shanghai_now_iso()
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("BEGIN IMMEDIATE")
                task_row = await self._fetch_task_row(db, task_id)
                if task_row is None:
                    await db.execute("ROLLBACK")
                    raise ValueError("任务不存在")
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

    async def add_attachment(
        self,
        task_id: str,
        *,
        display_name: str,
        mime_type: str,
        path: str,
        kind: str = "document",
    ) -> TaskAttachmentRecord:
        """为任务追加附件记录。"""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            raise ValueError("任务不存在")
        task_id = canonical_task_id
        name = (display_name or "").strip() or Path(path).name
        mime = (mime_type or "application/octet-stream").strip() or "application/octet-stream"
        kind_token = (kind or "document").strip() or "document"
        path_text = (path or "").strip()
        if not path_text:
            raise ValueError("附件路径不能为空")
        now = shanghai_now_iso()
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("BEGIN IMMEDIATE")
                row = await self._fetch_task_row(db, task_id)
                if row is None:
                    await db.execute("ROLLBACK")
                    raise ValueError("任务不存在")
                cursor = await db.execute(
                    """
                    INSERT INTO task_attachments(task_id, kind, display_name, mime_type, path, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, kind_token, name, mime, path_text, now),
                )
                attach_id = cursor.lastrowid
                await db.commit()
        return TaskAttachmentRecord(
            id=attach_id,
            task_id=task_id,
            display_name=name,
            mime_type=mime,
            path=path_text,
            kind=kind_token,
            created_at=now,
        )

    async def list_notes(self, task_id: str) -> List[TaskNoteRecord]:
        """列出指定任务的所有备注，按时间升序排列。"""

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

    async def list_attachments(self, task_id: str) -> List[TaskAttachmentRecord]:
        """列出任务附件，按时间倒序返回。"""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            return []
        task_id = canonical_task_id
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(
                """
                SELECT * FROM task_attachments
                WHERE task_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (task_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            TaskAttachmentRecord(
                id=row["id"],
                task_id=row["task_id"],
                display_name=row["display_name"],
                mime_type=row["mime_type"],
                path=row["path"],
                kind=row["kind"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def list_history(self, task_id: str) -> List[TaskHistoryRecord]:
        """返回任务的历史记录列表。"""

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
        """记录任务相关的动作事件。"""

        canonical_task_id = self._canonical_task_id(task_id)
        if not canonical_task_id:
            raise ValueError("任务不存在")
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
                logger.warning("事件 payload 序列化失败: task_id=%s error=%s", task_id, exc)
                payload_text = None
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("BEGIN IMMEDIATE")
                row = await self._fetch_task_row(db, task_id)
                if row is None:
                    await db.execute("ROLLBACK")
                    raise ValueError("任务不存在")
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
        """通过归档标记实现逻辑删除，并返回最新任务状态。"""

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
        """基于页码拉取任务列表，并返回总页数。"""

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
        """统计满足条件的任务数量，用于分页。"""

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
        """将当前数据库备份到指定路径。"""

        target_path = target_path.expanduser()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as source:
                async with aiosqlite.connect(target_path) as dest:
                    await dest.execute("PRAGMA foreign_keys = OFF")
                    await source.backup(dest)
                    await dest.commit()

    @staticmethod
    def _convert_task_id_token(value: Optional[str]) -> Optional[str]:
        """统一任务 ID 的分隔符，兼容历史格式。"""

        if value is None:
            return None
        token = value.replace("-", "_").replace(".", "_")
        token = re.sub(r"_+", "_", token)
        if token.startswith("TASK"):
            suffix = token[4:]
            if suffix and not suffix.startswith("_"):
                # 旧格式 TASK0001/TASK0001_1 需要补下划线
                token = f"TASK_{suffix}"
            else:
                token = f"TASK{suffix}"
        return token

    def _canonical_task_id(self, value: Optional[str]) -> Optional[str]:
        """将外部传入的任务 ID 规范化为统一格式。"""

        if value is None:
            return None
        token = value.strip()
        if not token:
            return token
        token = token.upper()
        return self._convert_task_id_token(token)

    def _write_id_migration_report(self, mapping: Dict[str, str]) -> None:
        """将任务 ID 迁移结果记录为 JSON 报告，便于排查。"""

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
                "写入任务 ID 迁移报告失败: project=%s error=%s",
                self.project_slug,
                exc,
            )

    async def _fetch_task_row(self, db: aiosqlite.Connection, task_id: str):
        """从数据库查询指定任务的原始行。"""

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
        """自增并返回 root 任务序列号。"""

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
        """写入任务历史记录，自动补齐时间戳。"""

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
        """将状态字符串标准化，兼容遗留 design 并记录异常数据。"""

        if not value:
            logger.warning("检测到空任务状态，已回退默认: context=%s", context)
            return TASK_STATUSES[0]
        token = str(value).strip().lower()
        mapped = STATUS_ALIASES.get(token, token)
        if mapped not in self._valid_statuses:
            logger.warning(
                "检测到未知任务状态: value=%s mapped=%s context=%s",
                value,
                mapped,
                context,
            )
            return mapped
        if mapped != token:
            logger.info(
                "任务状态已根据别名转换: raw=%s normalized=%s context=%s",
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
        """将 sqlite row 转换为 TaskRecord 实例。"""

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
            related_task_id=row["related_task_id"] if "related_task_id" in row.keys() else None,
            parent_id=row["parent_id"],
            root_id=row["root_id"],
            depth=row["depth"],
            lineage=row["lineage"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived=bool(row["archived"]),
        )
