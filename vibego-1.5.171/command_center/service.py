"""命令管理的数据库访问与业务逻辑。"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import aiosqlite

from tasks.models import shanghai_now_iso
from .models import CommandDefinition, CommandHistoryRecord


class CommandError(RuntimeError):
    """命令管理通用异常。"""


class CommandAlreadyExistsError(CommandError):
    """命令名称重复。"""


class CommandAliasConflictError(CommandError):
    """命令别名与现有记录冲突。"""


class CommandNotFoundError(CommandError):
    """命令不存在。"""


class CommandHistoryNotFoundError(CommandError):
    """命令执行历史不存在。"""


class CommandService:
    """封装命令定义与执行历史的存取逻辑。"""

    NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{2,63}$")
    ALIAS_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    MAX_TITLE_LENGTH = 80
    MAX_DESCRIPTION_LENGTH = 400
    MAX_COMMAND_LENGTH = 1024
    MAX_ALIAS_COUNT = 10
    MIN_TIMEOUT = 5
    MAX_TIMEOUT = 3600

    def __init__(
        self,
        db_path: Path,
        project_slug: str,
        *,
        scope: str = "project",
        history_project_slug: Optional[str] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.project_slug = project_slug
        self.scope = (scope or "project").strip() or "project"
        self.history_project_slug = history_project_slug or project_slug
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """创建所需的表结构与索引。"""

        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await self._create_tables(db)
            await db.commit()
        self._initialized = True

    async def list_commands(self) -> List[CommandDefinition]:
        """返回当前项目下全部命令。"""

        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT *
                FROM commands
                WHERE project_slug = ?
                ORDER BY title COLLATE NOCASE, name COLLATE NOCASE
                """,
                (self.project_slug,),
            )
            rows = await cursor.fetchall()
            ids = [row["id"] for row in rows]
            alias_map = await self._fetch_aliases(db, ids)
        return [self._row_to_definition(row, alias_map.get(row["id"])) for row in rows]

    async def create_command(
        self,
        *,
        name: str,
        title: str,
        command: str,
        description: str = "",
        timeout: Optional[int] = None,
        aliases: Sequence[str] | None = None,
        enabled: bool = True,
    ) -> CommandDefinition:
        """新增命令，确保名称与别名均唯一。"""

        await self.initialize()
        normalized_name = self._normalize_identifier(name)
        sanitized_name = self._sanitize_name(name)
        sanitized_title = self._sanitize_title(title) or sanitized_name
        sanitized_command = self._sanitize_command(command)
        sanitized_description = self._sanitize_description(description)
        timeout_value = self._sanitize_timeout(timeout)
        alias_tuples = self._sanitize_aliases(aliases or ())
        now = shanghai_now_iso()

        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                try:
                    cursor = await db.execute(
                        """
                        INSERT INTO commands (
                            project_slug,
                            scope,
                            name,
                            normalized_name,
                            title,
                            command,
                            description,
                            timeout,
                            enabled,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self.project_slug,
                            self.scope,
                            sanitized_name,
                            normalized_name,
                            sanitized_title,
                            sanitized_command,
                            sanitized_description,
                            timeout_value,
                            1 if enabled else 0,
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise CommandAlreadyExistsError("命令名称已存在") from exc
                command_id = cursor.lastrowid
                if alias_tuples:
                    await self._insert_aliases(db, command_id, alias_tuples)
                await db.commit()

        definition = CommandDefinition(
            id=int(command_id),
            project_slug=self.project_slug,
            scope=self.scope,
            name=sanitized_name,
            title=sanitized_title,
            command=sanitized_command,
            description=sanitized_description,
            timeout=timeout_value,
            enabled=enabled,
            created_at=now,
            updated_at=now,
            aliases=tuple(alias for alias, _ in alias_tuples),
        )
        return definition

    async def update_command(
        self,
        command_id: int,
        *,
        name: Optional[str] = None,
        title: Optional[str] = None,
        command: Optional[str] = None,
        description: Optional[str] = None,
        timeout: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> CommandDefinition:
        """更新命令的基础字段。"""

        await self.initialize()
        fields: List[str] = []
        values: List[object] = []
        if name is not None:
            sanitized_name = self._sanitize_name(name)
            fields.extend(["name = ?", "normalized_name = ?"])
            values.extend([sanitized_name, self._normalize_identifier(name)])
        if title is not None:
            sanitized_title = self._sanitize_title(title)
            if not sanitized_title:
                raise ValueError("命令标题不能为空")
            fields.append("title = ?")
            values.append(sanitized_title)
        if command is not None:
            fields.append("command = ?")
            values.append(self._sanitize_command(command))
        if description is not None:
            fields.append("description = ?")
            values.append(self._sanitize_description(description))
        if timeout is not None:
            fields.append("timeout = ?")
            values.append(self._sanitize_timeout(timeout))
        if enabled is not None:
            fields.append("enabled = ?")
            values.append(1 if enabled else 0)
        if not fields:
            return await self.get_command(command_id)
        fields.append("updated_at = ?")
        now = shanghai_now_iso()
        values.append(now)
        values.extend([self.project_slug, command_id])

        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                try:
                    cursor = await db.execute(
                        f"""
                        UPDATE commands
                        SET {', '.join(fields)}
                        WHERE project_slug = ? AND id = ?
                        """,
                        tuple(values),
                    )
                except sqlite3.IntegrityError as exc:
                    raise CommandAlreadyExistsError("命令名称或别名冲突") from exc
                if cursor.rowcount == 0:
                    raise CommandNotFoundError("命令不存在或已被删除")
                await db.commit()

        return await self.get_command(command_id)

    async def delete_command(self, command_id: int) -> None:
        """删除指定命令，依赖外键自动清理由其衍生的别名与历史。"""

        await self.initialize()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                cursor = await db.execute(
                    """
                    DELETE FROM commands
                    WHERE project_slug = ? AND id = ?
                    """,
                    (self.project_slug, command_id),
                )
                if cursor.rowcount == 0:
                    raise CommandNotFoundError("命令不存在或已被删除")
                await db.commit()

    async def replace_aliases(self, command_id: int, aliases: Sequence[str]) -> Tuple[str, ...]:
        """以新列表覆盖命令别名。"""

        await self.initialize()
        alias_tuples = self._sanitize_aliases(aliases)
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                cursor = await db.execute(
                    """
                    SELECT 1 FROM commands
                    WHERE project_slug = ? AND id = ?
                    """,
                    (self.project_slug, command_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise CommandNotFoundError("命令不存在，无法更新别名")
                await db.execute(
                    """
                    DELETE FROM command_aliases
                    WHERE project_slug = ? AND command_id = ?
                    """,
                    (self.project_slug, command_id),
                )
                if alias_tuples:
                    await self._insert_aliases(db, command_id, alias_tuples)
                await db.commit()
        return tuple(alias for alias, _ in alias_tuples)

    async def get_command(self, command_id: int) -> CommandDefinition:
        """按 ID 获取命令。"""

        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT *
                FROM commands
                WHERE project_slug = ? AND id = ?
                """,
                (self.project_slug, command_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise CommandNotFoundError("命令不存在")
            alias_map = await self._fetch_aliases(db, [command_id])
        return self._row_to_definition(row, alias_map.get(command_id))

    async def resolve_by_trigger(self, trigger: str) -> Optional[CommandDefinition]:
        """根据名称或别名匹配命令，匹配不区分大小写。"""

        await self.initialize()
        normalized = self._normalize_identifier(trigger)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT c.*
                FROM commands AS c
                LEFT JOIN command_aliases AS a
                    ON a.command_id = c.id AND a.project_slug = c.project_slug
                WHERE c.project_slug = ?
                  AND (c.normalized_name = ? OR a.normalized_alias = ?)
                LIMIT 1
                """,
                (self.project_slug, normalized, normalized),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            alias_map = await self._fetch_aliases(db, [row["id"]])
        return self._row_to_definition(row, alias_map.get(row["id"]))

    async def record_history(
        self,
        command_id: int,
        *,
        trigger: Optional[str],
        actor_id: Optional[int],
        actor_username: Optional[str],
        actor_name: Optional[str],
        exit_code: Optional[int],
        status: str,
        output: Optional[str],
        error: Optional[str],
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> CommandHistoryRecord:
        """写入命令执行历史记录。"""

        await self.initialize()
        command = await self.get_command(command_id)
        started = started_at or shanghai_now_iso()
        finished = finished_at or shanghai_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            cursor = await db.execute(
                """
                INSERT INTO command_history (
                    command_id,
                    project_slug,
                    command_name,
                    trigger,
                    actor_id,
                    actor_username,
                    actor_name,
                    exit_code,
                    status,
                    output,
                    error,
                    started_at,
                    finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.id,
                    self.history_project_slug,
                    command.name,
                    trigger,
                    actor_id,
                    actor_username,
                    actor_name,
                    exit_code,
                    status,
                    output,
                    error,
                    started,
                    finished,
                ),
            )
            await db.commit()
        return CommandHistoryRecord(
            id=cursor.lastrowid,
            command_id=command.id,
            project_slug=self.history_project_slug,
            command_name=command.name,
            command_title=command.title,
            trigger=trigger,
            actor_id=actor_id,
            actor_username=actor_username,
            actor_name=actor_name,
            exit_code=exit_code,
            status=status,
            output=output,
            error=error,
            started_at=started,
            finished_at=finished,
        )

    async def list_history(self, limit: int = 10) -> List[CommandHistoryRecord]:
        """列出最近的执行记录。"""

        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT h.*, c.title AS command_title
                FROM command_history AS h
                LEFT JOIN commands AS c
                    ON c.id = h.command_id AND c.project_slug = h.project_slug
                WHERE h.project_slug = ?
                ORDER BY h.id DESC
                LIMIT ?
                """,
                (self.history_project_slug, limit),
            )
            rows = await cursor.fetchall()
        records = [
            CommandHistoryRecord(
                id=row["id"],
                command_id=row["command_id"],
                project_slug=row["project_slug"],
                command_name=row["command_name"],
                command_title=row["command_title"],
                trigger=row["trigger"],
                actor_id=row["actor_id"],
                actor_username=row["actor_username"],
                actor_name=row["actor_name"],
                exit_code=row["exit_code"],
                status=row["status"],
                output=row["output"],
                error=row["error"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
            )
            for row in rows
        ]
        for record in records:
            record.ensure_timestamps()
        return records

    async def get_history_record(self, history_id: int) -> CommandHistoryRecord:
        """按主键返回单条执行记录。"""

        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT h.*, c.title AS command_title
                FROM command_history AS h
                LEFT JOIN commands AS c
                    ON c.id = h.command_id AND c.project_slug = h.project_slug
                WHERE h.project_slug = ? AND h.id = ?
                LIMIT 1
                """,
                (self.history_project_slug, history_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise CommandHistoryNotFoundError("命令执行记录不存在")
        record = CommandHistoryRecord(
            id=row["id"],
            command_id=row["command_id"],
            project_slug=row["project_slug"],
            command_name=row["command_name"],
            command_title=row["command_title"],
            trigger=row["trigger"],
            actor_id=row["actor_id"],
            actor_username=row["actor_username"],
            actor_name=row["actor_name"],
            exit_code=row["exit_code"],
            status=row["status"],
            output=row["output"],
            error=row["error"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
        record.ensure_timestamps()
        return record

    async def _create_tables(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_slug TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'project',
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                title TEXT NOT NULL,
                command TEXT NOT NULL,
                description TEXT,
                timeout INTEGER NOT NULL DEFAULT 120,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._ensure_scope_column(db)
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_commands_unique_name
            ON commands(project_slug, normalized_name)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS command_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id INTEGER NOT NULL,
                project_slug TEXT NOT NULL,
                alias TEXT NOT NULL,
                normalized_alias TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(command_id) REFERENCES commands(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_command_alias_value
            ON command_aliases(project_slug, normalized_alias)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_command_alias_command
            ON command_aliases(command_id)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS command_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id INTEGER NOT NULL,
                project_slug TEXT NOT NULL,
                command_name TEXT NOT NULL,
                trigger TEXT,
                actor_id INTEGER,
                actor_username TEXT,
                actor_name TEXT,
                exit_code INTEGER,
                status TEXT NOT NULL,
                output TEXT,
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                FOREIGN KEY(command_id) REFERENCES commands(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_command_history_project
            ON command_history(project_slug, command_id)
            """
        )

    async def _ensure_scope_column(self, db: aiosqlite.Connection) -> None:
        """确保 commands 表包含 scope 列，兼容旧版本数据库。"""

        cursor = await db.execute("PRAGMA table_info(commands)")
        rows = await cursor.fetchall()
        column_names = {row[1] for row in rows}
        if "scope" in column_names:
            return
        await db.execute("ALTER TABLE commands ADD COLUMN scope TEXT NOT NULL DEFAULT 'project'")
        await db.execute("UPDATE commands SET scope = 'project' WHERE scope IS NULL OR scope = ''")

    async def _fetch_aliases(
        self,
        db: aiosqlite.Connection,
        command_ids: Sequence[int],
    ) -> Dict[int, Tuple[str, ...]]:
        if not command_ids:
            return {}
        placeholder = ",".join("?" for _ in command_ids)
        cursor = await db.execute(
            f"""
            SELECT command_id, alias
            FROM command_aliases
            WHERE command_id IN ({placeholder})
            ORDER BY alias COLLATE NOCASE
            """,
            tuple(command_ids),
        )
        rows = await cursor.fetchall()
        alias_map: Dict[int, List[str]] = {}
        for row in rows:
            alias_map.setdefault(row["command_id"], []).append(row["alias"])
        return {key: tuple(values) for key, values in alias_map.items()}

    async def _insert_aliases(
        self,
        db: aiosqlite.Connection,
        command_id: int,
        alias_tuples: Sequence[Tuple[str, str]],
    ) -> None:
        now = shanghai_now_iso()
        try:
            await db.executemany(
                """
                INSERT INTO command_aliases (
                    command_id,
                    project_slug,
                    alias,
                    normalized_alias,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (command_id, self.project_slug, alias, normalized, now)
                    for alias, normalized in alias_tuples
                ],
            )
        except sqlite3.IntegrityError as exc:
            raise CommandAliasConflictError("别名已被其他命令使用") from exc

    def _row_to_definition(
        self,
        row: aiosqlite.Row,
        aliases: Optional[Tuple[str, ...]],
    ) -> CommandDefinition:
        return CommandDefinition(
            id=row["id"],
            project_slug=row["project_slug"],
            scope=row["scope"] or "project",
            name=row["name"],
            title=row["title"],
            command=row["command"],
            description=row["description"] or "",
            timeout=row["timeout"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            aliases=aliases or (),
        )

    def _sanitize_name(self, value: str) -> str:
        candidate = (value or "").strip()
        if not candidate or not self.NAME_PATTERN.match(candidate):
            raise ValueError("命令名称需以字母开头，长度 3-64，仅允许字母数字下划线或连字符")
        return candidate

    def _sanitize_title(self, value: str) -> str:
        candidate = (value or "").strip()
        if not candidate:
            return ""
        if len(candidate) > self.MAX_TITLE_LENGTH:
            raise ValueError(f"命令标题长度不可超过 {self.MAX_TITLE_LENGTH} 字符")
        return candidate

    def _sanitize_command(self, value: str) -> str:
        candidate = (value or "").strip()
        if not candidate:
            raise ValueError("命令内容不能为空")
        if len(candidate) > self.MAX_COMMAND_LENGTH:
            raise ValueError(f"命令内容长度不可超过 {self.MAX_COMMAND_LENGTH} 字符")
        return candidate

    def _sanitize_description(self, value: Optional[str]) -> str:
        candidate = (value or "").strip()
        if len(candidate) > self.MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"命令描述长度不可超过 {self.MAX_DESCRIPTION_LENGTH} 字符")
        return candidate

    def _sanitize_timeout(self, value: Optional[int]) -> int:
        if value is None:
            return 120
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            raise ValueError("超时需为整数秒") from None
        numeric = max(self.MIN_TIMEOUT, min(self.MAX_TIMEOUT, numeric))
        return numeric

    def _sanitize_aliases(self, aliases: Sequence[str]) -> List[Tuple[str, str]]:
        cleaned: List[Tuple[str, str]] = []
        seen: set[str] = set()
        for alias in aliases:
            candidate = (alias or "").strip()
            if not candidate:
                continue
            if not self.ALIAS_PATTERN.match(candidate):
                raise ValueError("别名需以字母开头，仅允许字母数字下划线或连字符")
            normalized = self._normalize_identifier(candidate)
            if normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append((candidate, normalized))
            if len(cleaned) >= self.MAX_ALIAS_COUNT:
                break
        return cleaned

    def _normalize_identifier(self, value: str) -> str:
        return self.normalize_identifier(value)

    @staticmethod
    def normalize_identifier(value: str) -> str:
        return (value or "").strip().casefold()
