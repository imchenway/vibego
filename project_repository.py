"""Project configuration repository syncing SQLite and JSON storage.

Responsibilities:
1. Initialise the SQLite database and import data from JSON on first run while
   keeping a backup of the original JSON file.
2. Provide CRUD helpers that always write the latest JSON for legacy
   compatibility.
3. Expose data structures consumed by the master process and related scripts.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectRecord:
    """Represent the configuration data for a single project."""

    bot_name: str
    bot_token: str
    project_slug: str
    default_model: str
    workdir: Optional[str]
    allowed_chat_id: Optional[int]
    legacy_name: Optional[str]

    def to_dict(self) -> dict:
        """Return a dictionary representation ready for JSON serialisation."""
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
    """Repository wrapper that encapsulates project configuration I/O."""

    def __init__(self, db_path: Path, json_path: Path):
        """Initialise the repository and ensure required files exist."""

        # Store paths and create parent directories.
        self.db_path = db_path
        self.json_path = json_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        # Prepare the database file.
        self._initialize()

    def _initialize(self) -> None:
        """Initialise the database and import JSON data on first launch."""
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
        # Repair legacy records on every startup to keep data normalised.
        self._repair_records()
        # Export once on startup so JSON mirrors the database.
        self._export_to_json(self.list_projects())

    def _connect(self) -> sqlite3.Connection:
        """Create a database connection with row dictionaries enabled."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _import_from_json(self) -> None:
        """Import data from JSON during the first initialisation and keep a backup."""
        if not self.json_path.exists():
            return
        try:
            raw = json.loads(self.json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse {self.json_path}: {exc}") from exc
        if not isinstance(raw, list):
            raise RuntimeError(f"The content of {self.json_path} must be an array.")
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
        """Build a timestamped JSON backup path to avoid overwriting files."""
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return self.json_path.with_suffix(self.json_path.suffix + f".{timestamp}.bak")

    def list_projects(self) -> List[ProjectRecord]:
        """Return all project configurations."""
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
        """Look up a project by slug (case-insensitive for legacy data)."""
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
        """Look up a project by bot name."""
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
        """Insert a new project record."""
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
        """Update a project using its slug (case-insensitive) as the identifier."""
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
                raise ValueError(f"Project {slug} not found")
            conn.commit()
        self._export_to_json(self.list_projects())

    def delete_project(self, slug: str) -> None:
        """Delete a project by slug (case-insensitive)."""
        normalized_slug = self._sanitize_slug(slug)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.execute(
                "DELETE FROM projects WHERE lower(project_slug) = lower(?);",
                (normalized_slug,),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                raise ValueError(f"Project {slug} not found")
            conn.commit()
        self._export_to_json(self.list_projects())

    def _bulk_upsert(self, records: Iterable[ProjectRecord]) -> None:
        """Insert or update multiple project records, used for bootstrap imports."""
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
        """Convert the input to an int or None, handling string representations."""
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    def _sanitize_bot_name(self, bot_name: str) -> str:
        """Trim whitespace and leading @ symbols to normalise bot names."""
        cleaned = (bot_name or "").strip()
        if cleaned.startswith("@"):
            cleaned = cleaned[1:]
        return cleaned.strip()

    def _sanitize_slug(self, slug: str) -> str:
        """Normalise slugs to lowercase and replace illegal characters."""
        text = (slug or "").strip().lower()
        text = text.replace(" ", "-").replace("/", "-").replace("\\", "-")
        text = text.strip("-")
        return text or "project"

    def _sanitize_optional_text(self, value: Optional[str]) -> Optional[str]:
        """Clean optional text values and return None for empty strings."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    def _normalize_record_fields(self, record: ProjectRecord) -> ProjectRecord:
        """Return a normalised record to avoid persisting invalid values."""
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

    def _row_to_record(self, row: sqlite3.Row, *, normalize: bool = True) -> ProjectRecord:
        """Convert a database row into ProjectRecord, optionally normalising it."""
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
        """Repair legacy data on startup to ensure slugs and bot names are valid."""
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
        for row in rows:
            record = self._row_to_record(row, normalize=False)
            normalized = self._normalize_record_fields(record)
            current_id = row["id"]
            existing_slug_id = slug_owner.get(normalized.project_slug)
            if existing_slug_id is not None and existing_slug_id != current_id:
                raise RuntimeError(
                    f"Slug normalisation conflict: {normalized.project_slug} is already used by record {existing_slug_id}"
                )
            slug_owner[normalized.project_slug] = current_id
            existing_bot_id = bot_owner.get(normalized.bot_name)
            if existing_bot_id is not None and existing_bot_id != current_id:
                raise RuntimeError(
                    f"Bot name normalisation conflict: {normalized.bot_name} is already used by record {existing_bot_id}"
                )
            bot_owner[normalized.bot_name] = current_id
            if normalized != record:
                updates.append((current_id, normalized))
        if not updates:
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
        logger.info("Repaired %s project records and normalised slug/bot formats", len(updates))

    def _export_to_json(self, records: Iterable[ProjectRecord]) -> None:
        """Export the database content to JSON, keeping it readable for legacy flows."""
        payload = [record.to_dict() for record in records]
        tmp_path = self.json_path.with_suffix(self.json_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.json_path)


__all__ = ["ProjectRecord", "ProjectRepository"]
