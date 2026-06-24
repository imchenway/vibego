"""项目配置仓库，负责在 SQLite 与 JSON 间同步。

该模块承担以下职责：
1. 初始化 SQLite 数据库并在首次运行时从 JSON 导入数据，同时保留原始 JSON 备份；
2. 提供项目增删改查接口，所有写操作都会回写最新 JSON 以兼容旧逻辑；
3. 提供 ProjectConfig 所需的数据结构，供 master 与其他脚本复用。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeDataMigrationResult:
    """记录一次项目运行期数据库 slug 迁移结果。"""

    old_slug: str
    new_slug: str
    source_db: Path
    target_db: Path
    action: str
    rewritten_tables: tuple[str, ...] = ()
    backup_path: Optional[Path] = None


class RuntimeDataMigrationConflictError(RuntimeError):
    """新旧 slug 运行期数据库均有业务数据时抛出，避免覆盖用户数据。"""


_RUNTIME_DB_SAFE_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")
_RUNTIME_DB_REPLACEABLE_METADATA_TABLES = {"codex_trusted_paths"}


def _runtime_db_path(data_dir: Path, slug: str) -> Path:
    """根据已归一化 slug 构造运行期数据库路径，并阻断异常路径穿透。"""

    normalized = (slug or "").strip().lower()
    if not _RUNTIME_DB_SAFE_SLUG_RE.fullmatch(normalized):
        raise RuntimeError(f"运行期数据库 slug 非法: {slug!r}")
    return data_dir / f"{normalized}.db"


def _runtime_migration_marker_path(source_db: Path, new_slug: str) -> Path:
    """成功迁移后写入幂等标记，避免旧 DB 保留期间下次启动误判冲突。"""

    return source_db.with_name(f"{source_db.name}.migrated-to-{new_slug}.marker")


def _list_sqlite_user_tables(conn: sqlite3.Connection) -> list[str]:
    """列出 SQLite 用户表，跳过 sqlite_sequence 等内部表。"""

    cursor = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name;
        """
    )
    return [str(row[0]) for row in cursor.fetchall()]


def _sqlite_table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    """判断表是否存在指定列，兼容不同版本运行期 DB 的增量迁移。"""

    cursor = conn.execute(f'PRAGMA table_info("{table_name}")')
    return any(str(row[1]) == column_name for row in cursor.fetchall())


def _runtime_db_has_business_rows(db_path: Path) -> bool:
    """判断目标运行期 DB 是否已有业务行；有业务行则不能被旧库覆盖。"""

    if not db_path.exists() or db_path.stat().st_size == 0:
        return False
    with sqlite3.connect(str(db_path)) as conn:
        for table_name in _list_sqlite_user_tables(conn):
            if table_name in _RUNTIME_DB_REPLACEABLE_METADATA_TABLES:
                continue
            cursor = conn.execute(f'SELECT 1 FROM "{table_name}" LIMIT 1;')
            if cursor.fetchone() is not None:
                return True
    return False


def _build_unique_backup_path(target_db: Path, backup_suffix: Optional[str]) -> Path:
    """生成不覆盖历史备份的目标库备份路径。"""

    suffix = backup_suffix or time.strftime("%Y%m%d-%H%M%S")
    candidate = target_db.with_name(f"{target_db.name}.bak-{suffix}")
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        indexed = target_db.with_name(f"{target_db.name}.bak-{suffix}-{index}")
        if not indexed.exists():
            return indexed
        index += 1


def _rewrite_runtime_db_project_slug(db_path: Path, old_slug: str, new_slug: str) -> tuple[str, ...]:
    """将复制后的运行期 DB 中所有 project_slug 列从旧 slug 改写到新 slug。"""

    rewritten_tables: list[str] = []
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            for table_name in _list_sqlite_user_tables(conn):
                if not _sqlite_table_has_column(conn, table_name, "project_slug"):
                    continue
                cursor = conn.execute(
                    f'UPDATE "{table_name}" SET project_slug = ? WHERE project_slug = ?;',
                    (new_slug, old_slug),
                )
                if cursor.rowcount:
                    rewritten_tables.append(table_name)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return tuple(rewritten_tables)


def migrate_runtime_data_for_slug_migrations(
    data_dir: Path,
    slug_migrations: Mapping[str, str],
    *,
    backup_suffix: Optional[str] = None,
) -> list[RuntimeDataMigrationResult]:
    """迁移项目 slug 改名后的运行期 SQLite DB，恢复命令/任务/历史入口。

    只在新 slug 目标库不存在或没有任何业务行时自动复制旧库；若目标库已有业务
    数据则 fail-closed，避免把用户在新 slug 下产生的数据覆盖掉。
    """

    results: list[RuntimeDataMigrationResult] = []
    if not slug_migrations:
        return results

    data_dir.mkdir(parents=True, exist_ok=True)
    for old_slug, new_slug in sorted(slug_migrations.items()):
        if old_slug == new_slug:
            continue
        source_db = _runtime_db_path(data_dir, old_slug)
        target_db = _runtime_db_path(data_dir, new_slug)
        marker_path = _runtime_migration_marker_path(source_db, new_slug)
        if not source_db.exists():
            results.append(
                RuntimeDataMigrationResult(
                    old_slug=old_slug,
                    new_slug=new_slug,
                    source_db=source_db,
                    target_db=target_db,
                    action="skipped_missing_source",
                )
            )
            continue
        backup_path: Optional[Path] = None
        if target_db.exists():
            if _runtime_db_has_business_rows(target_db):
                if marker_path.exists():
                    results.append(
                        RuntimeDataMigrationResult(
                            old_slug=old_slug,
                            new_slug=new_slug,
                            source_db=source_db,
                            target_db=target_db,
                            action="skipped_already_migrated",
                        )
                    )
                    continue
                raise RuntimeDataMigrationConflictError(
                    "目标运行期数据库已有业务数据，已阻断 slug 迁移覆盖: "
                    f"{source_db} -> {target_db}"
                )
            backup_path = _build_unique_backup_path(target_db, backup_suffix)
            shutil.copy2(target_db, backup_path)

        temp_db = target_db.with_name(f"{target_db.name}.tmp-slug-migration")
        shutil.copy2(source_db, temp_db)
        try:
            rewritten_tables = _rewrite_runtime_db_project_slug(temp_db, old_slug, new_slug)
            os.replace(temp_db, target_db)
            marker_path.write_text(
                json.dumps(
                    {
                        "old_slug": old_slug,
                        "new_slug": new_slug,
                        "source_db": str(source_db),
                        "target_db": str(target_db),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        finally:
            if temp_db.exists():
                temp_db.unlink()
        results.append(
            RuntimeDataMigrationResult(
                old_slug=old_slug,
                new_slug=new_slug,
                source_db=source_db,
                target_db=target_db,
                action="copied",
                rewritten_tables=rewritten_tables,
                backup_path=backup_path,
            )
        )
    return results


@dataclass(frozen=True)
class ProjectRecord:
    """描述单个项目的配置数据。"""

    bot_name: str
    bot_token: str
    project_slug: str
    default_model: str
    workdir: Optional[str]
    allowed_chat_id: Optional[int]
    legacy_name: Optional[str]

    def to_dict(self) -> dict:
        """转换为 JSON 序列化所需的字典。"""
        return {
            "bot_name": self.bot_name,
            "bot_token": self.bot_token,
            "project_slug": self.project_slug,
            "default_model": self.default_model,
            "workdir": self.workdir,
            "allowed_chat_id": self.allowed_chat_id,
            "name": self.legacy_name,
        }


class ProjectRepository:
    """项目配置仓库，封装所有读写逻辑。"""

    def __init__(self, db_path: Path, json_path: Path):
        """初始化仓库并自动创建所需的文件与目录。"""

        # 保存路径，确保目录存在
        self.db_path = db_path
        self.json_path = json_path
        # 记录本次启动修复出的 slug 迁移关系，供 Master 状态文件迁移复用。
        self.slug_migrations: dict[str, str] = {}
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        # 初始化数据库文件
        self._initialize()

    def _initialize(self) -> None:
        """初始化数据库，如果首次创建则执行 JSON 导入。"""
        first_create = not self.db_path.exists()
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_name TEXT NOT NULL UNIQUE,
                    bot_token TEXT NOT NULL,
                    project_slug TEXT NOT NULL UNIQUE,
                    default_model TEXT NOT NULL,
                    workdir TEXT,
                    allowed_chat_id INTEGER,
                    legacy_name TEXT,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                );
                """
            )
        if first_create:
            self._import_from_json()
        # 每次启动都执行数据修复，保证旧数据被规范化
        self._repair_records()
        # 若 slug 修复已在旧版本执行过，尝试从 projects.json 备份恢复旧 slug 映射，
        # 以便后续运行期 DB 迁移仍能找到原 commands/tasks 数据库。
        self._recover_slug_migrations_from_json_backups()
        # 启动时始终导出一次，确保 JSON 与数据库一致
        self._export_to_json(self.list_projects())

    def _connect(self) -> sqlite3.Connection:
        """创建数据库连接，统一启用行字典模式。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _import_from_json(self) -> None:
        """首次初始化时从 JSON 迁移数据，并保留备份文件。"""
        if not self.json_path.exists():
            return
        try:
            raw = json.loads(self.json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"解析 {self.json_path} 时失败: {exc}") from exc
        if not isinstance(raw, list):
            raise RuntimeError(f"{self.json_path} 内容必须是数组")
        records: List[ProjectRecord] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            records.append(
                ProjectRecord(
                    bot_name=str(item.get("bot_name") or ""),
                    bot_token=str(item.get("bot_token") or ""),
                    project_slug=str(item.get("project_slug") or ""),
                    default_model=str(item.get("default_model") or "codex"),
                    workdir=item.get("workdir"),
                    allowed_chat_id=self._normalize_int(item.get("allowed_chat_id")),
                    legacy_name=str(item.get("name") or "").strip() or None,
                )
            )
        if records:
            self._bulk_upsert(records)
        backup_path = self._build_backup_path()
        shutil.copy2(self.json_path, backup_path)

    def _build_backup_path(self) -> Path:
        """构造 JSON 备份路径，带有时间戳避免覆盖。"""
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return self.json_path.with_suffix(self.json_path.suffix + f".{timestamp}.bak")

    def list_projects(self) -> List[ProjectRecord]:
        """读取全部项目配置。"""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT bot_name, bot_token, project_slug, default_model,
                       workdir, allowed_chat_id, legacy_name
                FROM projects
                ORDER BY bot_name COLLATE NOCASE;
                """
            )
            rows = cursor.fetchall()
        return [self._normalize_record_fields(self._row_to_record(row, normalize=False)) for row in rows]

    def get_by_slug(self, slug: str) -> Optional[ProjectRecord]:
        """根据 project_slug 查询项目（忽略大小写以兼容历史数据）。"""
        slug = self._sanitize_slug(slug)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT bot_name, bot_token, project_slug, default_model,
                       workdir, allowed_chat_id, legacy_name
                FROM projects WHERE lower(project_slug) = lower(?);
                """,
                (slug,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._normalize_record_fields(self._row_to_record(row, normalize=False))

    def get_by_bot_name(self, bot_name: str) -> Optional[ProjectRecord]:
        """根据 bot 名查询项目。"""
        bot_name = self._sanitize_bot_name(bot_name)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT bot_name, bot_token, project_slug, default_model,
                       workdir, allowed_chat_id, legacy_name
                FROM projects WHERE lower(bot_name) = lower(?);
                """,
                (bot_name,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._normalize_record_fields(self._row_to_record(row, normalize=False))

    def insert_project(self, record: ProjectRecord) -> None:
        """新增项目记录。"""
        normalized = self._normalize_record_fields(record)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO projects (
                    bot_name, bot_token, project_slug, default_model,
                    workdir, allowed_chat_id, legacy_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'), strftime('%s','now'));
                """,
                (
                    normalized.bot_name,
                    normalized.bot_token,
                    normalized.project_slug,
                    normalized.default_model,
                    normalized.workdir,
                    normalized.allowed_chat_id,
                    normalized.legacy_name,
                ),
            )
            conn.commit()
        self._export_to_json(self.list_projects())

    def update_project(self, slug: str, record: ProjectRecord) -> None:
        """更新项目记录，slug 作为定位字段（匹配时忽略大小写）。"""
        normalized_slug = self._sanitize_slug(slug)
        normalized = self._normalize_record_fields(record)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.execute(
                """
                UPDATE projects
                SET bot_name = ?, bot_token = ?, project_slug = ?, default_model = ?,
                    workdir = ?, allowed_chat_id = ?, legacy_name = ?, updated_at = strftime('%s','now')
                WHERE lower(project_slug) = lower(?);
                """,
                (
                    normalized.bot_name,
                    normalized.bot_token,
                    normalized.project_slug,
                    normalized.default_model,
                    normalized.workdir,
                    normalized.allowed_chat_id,
                    normalized.legacy_name,
                    normalized_slug,
                ),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                raise ValueError(f"未找到项目 {slug}")
            conn.commit()
        self._export_to_json(self.list_projects())

    def delete_project(self, slug: str) -> None:
        """删除指定项目（匹配时忽略大小写）。"""
        normalized_slug = self._sanitize_slug(slug)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.execute(
                "DELETE FROM projects WHERE lower(project_slug) = lower(?);",
                (normalized_slug,),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                raise ValueError(f"未找到项目 {slug}")
            conn.commit()
        self._export_to_json(self.list_projects())

    def _bulk_upsert(self, records: Iterable[ProjectRecord]) -> None:
        """批量写入项目数据，用于初始化导入。"""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            for record in records:
                normalized = self._normalize_record_fields(record)
                conn.execute(
                    """
                    INSERT INTO projects (
                        bot_name, bot_token, project_slug, default_model,
                        workdir, allowed_chat_id, legacy_name, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'), strftime('%s','now'))
                    ON CONFLICT(bot_name) DO UPDATE SET
                        bot_token = excluded.bot_token,
                        project_slug = excluded.project_slug,
                        default_model = excluded.default_model,
                        workdir = excluded.workdir,
                        allowed_chat_id = excluded.allowed_chat_id,
                        legacy_name = excluded.legacy_name,
                        updated_at = strftime('%s','now');
                    """,
                    (
                        normalized.bot_name,
                        normalized.bot_token,
                        normalized.project_slug,
                        normalized.default_model,
                        normalized.workdir,
                        normalized.allowed_chat_id,
                        normalized.legacy_name,
                    ),
                )
            conn.commit()

    def _normalize_int(self, value: Optional[object]) -> Optional[int]:
        """将输入转换为整数或 None，兼容字符串类型。"""
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    def _sanitize_bot_name(self, bot_name: str) -> str:
        """去除多余空白与前导 @，统一 bot 名格式。"""
        cleaned = (bot_name or "").strip()
        if cleaned.startswith("@"):
            cleaned = cleaned[1:]
        return cleaned.strip()

    def _sanitize_slug(self, slug: str) -> str:
        """复用 master 侧逻辑，将 slug 归一化为小写并替换非法字符。"""
        text = (slug or "").strip().lower()
        # 与 scripts/models/common.sh::sanitize_slug 保持一致，避免配置层
        # 保存的 slug 与 worker 实际日志目录不一致。
        text = text.translate(str.maketrans({" ": "-", "/": "-", ":": "-", "\\": "-", "@": "-"}))
        text = re.sub(r"[^a-z0-9_-]", "", text)
        text = text.strip("-")
        return text or "project"

    def _sanitize_optional_text(self, value: Optional[str]) -> Optional[str]:
        """通用字符串清洗，空字符串回落为 None。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    def _normalize_record_fields(self, record: ProjectRecord) -> ProjectRecord:
        """返回字段已归一化的新记录，避免数据库遗留非法值。"""
        allowed_chat_id = self._normalize_int(record.allowed_chat_id)
        clean_bot = self._sanitize_bot_name(record.bot_name)
        slug_source = record.project_slug.strip() or clean_bot
        clean_slug = self._sanitize_slug(slug_source)
        clean_workdir = self._sanitize_optional_text(record.workdir)
        clean_legacy = self._sanitize_optional_text(record.legacy_name)
        default_model = (record.default_model or "codex").strip().lower() or "codex"
        return ProjectRecord(
            bot_name=clean_bot,
            bot_token=record.bot_token.strip(),
            project_slug=clean_slug,
            default_model=default_model,
            workdir=clean_workdir,
            allowed_chat_id=allowed_chat_id,
            legacy_name=clean_legacy,
        )

    def _align_slug_to_display_name(self, record: ProjectRecord) -> ProjectRecord:
        """升级修复时将运行 slug 收敛到展示名，保证 tmux 会话名对齐项目名。"""

        normalized = self._normalize_record_fields(record)
        if not normalized.legacy_name:
            return normalized
        display_slug = self._sanitize_slug(normalized.legacy_name)
        if display_slug == normalized.project_slug:
            return normalized
        return ProjectRecord(
            bot_name=normalized.bot_name,
            bot_token=normalized.bot_token,
            project_slug=display_slug,
            default_model=normalized.default_model,
            workdir=normalized.workdir,
            allowed_chat_id=normalized.allowed_chat_id,
            legacy_name=normalized.legacy_name,
        )

    def _row_to_record(self, row: sqlite3.Row, *, normalize: bool = True) -> ProjectRecord:
        """将数据库行转换为 ProjectRecord，可选是否立即归一化。"""
        record = ProjectRecord(
            bot_name=row["bot_name"],
            bot_token=row["bot_token"],
            project_slug=row["project_slug"],
            default_model=row["default_model"],
            workdir=row["workdir"],
            allowed_chat_id=self._normalize_int(row["allowed_chat_id"]),
            legacy_name=row["legacy_name"],
        )
        return self._normalize_record_fields(record) if normalize else record

    def _repair_records(self) -> None:
        """启动时统一修复遗留数据，确保 slug/bot name 无不合法字符。"""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT id, bot_name, bot_token, project_slug, default_model,
                       workdir, allowed_chat_id, legacy_name
                FROM projects;
                """
            )
            rows = cursor.fetchall()
        if not rows:
            return
        slug_owner: dict[str, int] = {}
        bot_owner: dict[str, int] = {}
        updates: List[tuple[int, ProjectRecord]] = []
        migrations: dict[str, str] = {}
        for row in rows:
            record = self._row_to_record(row, normalize=False)
            normalized = self._align_slug_to_display_name(record)
            current_id = row["id"]
            existing_slug_id = slug_owner.get(normalized.project_slug)
            if existing_slug_id is not None and existing_slug_id != current_id:
                raise RuntimeError(
                    f"项目 slug 归一化冲突: {normalized.project_slug} 已被记录 {existing_slug_id} 占用"
                )
            slug_owner[normalized.project_slug] = current_id
            existing_bot_id = bot_owner.get(normalized.bot_name)
            if existing_bot_id is not None and existing_bot_id != current_id:
                raise RuntimeError(
                    f"bot 名归一化冲突: {normalized.bot_name} 已被记录 {existing_bot_id} 占用"
                )
            bot_owner[normalized.bot_name] = current_id
            if normalized != record:
                updates.append((current_id, normalized))
            old_slug = self._sanitize_slug(record.project_slug)
            if old_slug != normalized.project_slug:
                migrations[old_slug] = normalized.project_slug
        if not updates:
            self.slug_migrations = migrations
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            for row_id, normalized in updates:
                conn.execute(
                    """
                    UPDATE projects
                    SET bot_name = ?, bot_token = ?, project_slug = ?, default_model = ?,
                        workdir = ?, allowed_chat_id = ?, legacy_name = ?, updated_at = strftime('%s','now')
                    WHERE id = ?;
                    """,
                    (
                        normalized.bot_name,
                        normalized.bot_token,
                        normalized.project_slug,
                        normalized.default_model,
                        normalized.workdir,
                        normalized.allowed_chat_id,
                        normalized.legacy_name,
                        row_id,
                    ),
                )
            conn.commit()
        self.slug_migrations = migrations
        logger.info("已修复 %s 条项目配置，统一 slug/bot 名格式", len(updates))

    def _recover_slug_migrations_from_json_backups(self) -> None:
        """从历史 projects.json 备份恢复已执行过的 slug 迁移映射。"""

        current_records = self.list_projects()
        if not current_records:
            return
        current_by_bot = {
            self._sanitize_bot_name(record.bot_name).casefold(): record
            for record in current_records
            if record.bot_name
        }
        current_by_workdir: dict[str, ProjectRecord | None] = {}
        for record in current_records:
            workdir = self._sanitize_optional_text(record.workdir)
            if not workdir:
                continue
            existing = current_by_workdir.get(workdir)
            # 同一 workdir 若被多个项目使用，不再用 workdir 做自动匹配。
            current_by_workdir[workdir] = None if existing is not None else record
        current_slug_owner = {record.project_slug: record for record in current_records}
        recovered: dict[str, str] = {}
        for backup_path in sorted(self.json_path.parent.glob(f"{self.json_path.name}*.bak*")):
            if backup_path == self.json_path:
                continue
            try:
                raw = json.loads(backup_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, list):
                continue
            for item in raw:
                if not isinstance(item, dict):
                    continue
                raw_old_slug = str(item.get("project_slug") or "").strip()
                if not raw_old_slug:
                    continue
                old_slug = self._sanitize_slug(raw_old_slug)
                bot_key = self._sanitize_bot_name(str(item.get("bot_name") or "")).casefold()
                raw_workdir = item.get("workdir")
                workdir = self._sanitize_optional_text(str(raw_workdir)) if raw_workdir is not None else None
                current = current_by_bot.get(bot_key) if bot_key else None
                if current is None and workdir:
                    current = current_by_workdir.get(workdir)
                if current is None or old_slug == current.project_slug:
                    continue
                owner = current_slug_owner.get(old_slug)
                if owner is not None and owner.project_slug != current.project_slug:
                    raise RuntimeError(
                        f"历史项目 slug {old_slug} 已被当前项目 {owner.project_slug} 占用，"
                        "无法自动恢复迁移映射"
                    )
                existing_target = recovered.get(old_slug) or self.slug_migrations.get(old_slug)
                if existing_target is not None and existing_target != current.project_slug:
                    raise RuntimeError(
                        f"历史项目 slug {old_slug} 同时指向 {existing_target} 与 {current.project_slug}"
                    )
                recovered[old_slug] = current.project_slug
        if not recovered:
            return
        self.slug_migrations.update(recovered)
        logger.info("已从 projects.json 备份恢复 %s 条项目 slug 迁移映射", len(recovered))

    def _export_to_json(self, records: Iterable[ProjectRecord]) -> None:
        """将数据库内容导出为 JSON，兼容旧逻辑并保留易读格式。"""
        payload = [record.to_dict() for record in records]
        tmp_path = self.json_path.with_suffix(self.json_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.json_path)


__all__ = [
    "ProjectRecord",
    "ProjectRepository",
    "RuntimeDataMigrationConflictError",
    "RuntimeDataMigrationResult",
    "migrate_runtime_data_for_slug_migrations",
]
