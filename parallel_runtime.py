"""并行开发运行时与 Git 操作辅助。"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

import aiosqlite

from tasks import TaskRecord
from tasks.models import shanghai_now_iso


@dataclass(slots=True)
class BranchRef:
    """描述单个可选分支引用。"""

    name: str
    source: str  # local / remote
    remote: Optional[str] = None


@dataclass(slots=True)
class ParallelRepoRecord:
    """描述并行任务下单仓库的状态。"""

    repo_key: str
    source_repo_path: str
    workspace_repo_path: str
    selected_base_ref: str
    selected_remote: Optional[str]
    task_branch: str
    commit_status: str = "pending"
    merge_status: str = "pending"
    last_error: Optional[str] = None


@dataclass(slots=True)
class ParallelSessionRecord:
    """描述并行会话的核心字段。"""

    id: int
    task_id: str
    project_slug: str
    title_snapshot: str
    workspace_root: str
    tmux_session: str
    pointer_file: str
    task_branch: str
    status: str
    created_at: str
    updated_at: str
    last_error: Optional[str] = None
    last_commit_at: Optional[str] = None
    last_merge_at: Optional[str] = None
    deleted_at: Optional[str] = None


@dataclass(slots=True)
class RepoBranchSelection:
    """描述创建并行副本时用户为单个仓库选中的基线分支。"""

    repo_key: str
    source_repo_path: Path
    selected_ref: str
    selected_remote: Optional[str]
    relative_path: str


@dataclass(slots=True)
class RepoOperationResult:
    """描述单仓库操作结果。"""

    repo_key: str
    repo_name: str
    ok: bool
    status: str
    message: str


@dataclass(slots=True)
class ParallelCommitResult:
    """描述并行提交的汇总结果。"""

    results: list[RepoOperationResult]

    @property
    def failed(self) -> bool:
        return any(not item.ok for item in self.results)

    @property
    def has_changes(self) -> bool:
        return any(item.status in {"success", "pushed"} for item in self.results)


@dataclass(slots=True)
class ParallelMergeResult:
    """描述并行合并的汇总结果。"""

    results: list[RepoOperationResult]

    @property
    def failed(self) -> bool:
        return any(not item.ok for item in self.results)


def _run_git(args: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def _repo_key_for(base_dir: Path, repo_path: Path) -> tuple[str, str]:
    rel = repo_path.relative_to(base_dir)
    rel_text = str(rel).strip()
    if not rel_text or rel_text == ".":
        return "__root__", "."
    key = rel_text.replace("\\", "/")
    return key, key


def discover_git_repos(base_dir: Path, *, max_depth: int = 4) -> list[tuple[str, Path, str]]:
    """发现根目录下的全部 Git 仓库。"""

    base = Path(base_dir).resolve()
    repos: dict[str, tuple[Path, str]] = {}

    git_dir = base / ".git"
    if git_dir.is_dir() or git_dir.is_file():
        key, rel = _repo_key_for(base, base)
        repos[key] = (base, rel)

    pattern = str(base / "**" / ".git")
    for candidate in base.glob("**/.git"):
        repo_path = candidate.parent.resolve()
        try:
            relative = repo_path.relative_to(base)
        except ValueError:
            continue
        if len(relative.parts) > max_depth:
            continue
        if ".git" in relative.parts:
            continue
        key, rel = _repo_key_for(base, repo_path)
        repos[key] = (repo_path, rel)

    ordered = sorted(repos.items(), key=lambda item: (0 if item[0] == "__root__" else item[0].count("/"), item[0]))
    return [(key, path, rel) for key, (path, rel) in ordered]


def list_branch_refs(repo_path: Path) -> list[BranchRef]:
    """列出单仓库的本地+远端分支。"""

    refs: list[BranchRef] = []
    local = _run_git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd=repo_path)
    if local.returncode == 0:
        for line in local.stdout.splitlines():
            name = line.strip()
            if name:
                refs.append(BranchRef(name=name, source="local"))

    remote = _run_git(["for-each-ref", "--format=%(refname:short)", "refs/remotes"], cwd=repo_path)
    if remote.returncode == 0:
        for line in remote.stdout.splitlines():
            name = line.strip()
            if not name or name.endswith("/HEAD"):
                continue
            remote_name = name.split("/", 1)[0] if "/" in name else None
            refs.append(BranchRef(name=name, source="remote", remote=remote_name))

    dedup: dict[tuple[str, str], BranchRef] = {}
    for ref in refs:
        dedup[(ref.source, ref.name)] = ref
    return sorted(dedup.values(), key=lambda item: (0 if item.source == "local" else 1, item.name.casefold()))


def build_parallel_branch_name(task_id: str, title: str) -> str:
    """生成并行任务分支名。"""

    safe_title = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", (title or "").strip())
    safe_title = re.sub(r"-{2,}", "-", safe_title).strip("-")
    base = f"{task_id}-{safe_title}" if safe_title else task_id
    if len(base) > 72:
        base = base[:72].rstrip("-")
    return base


def _commit_prefix(task_type: Optional[str]) -> str:
    mapping = {
        "defect": "fix",
        "requirement": "feat",
        "task": "chore",
        "risk": "chore",
    }
    return mapping.get((task_type or "").strip().lower(), "chore")


def build_parallel_commit_message(task: TaskRecord, repo_name: str) -> tuple[str, str]:
    """生成单仓库并行提交的 commit message。"""

    subject = f"{_commit_prefix(task.task_type)}({task.id}): {(task.title or '').strip() or task.id}"
    body = "\n".join(
        [
            f"任务编码: /{task.id}",
            f"任务标题: {(task.title or '').strip() or task.id}",
            "提交方式: vibego parallel commit",
            f"仓库: {repo_name}",
        ]
    )
    return subject, body


def prepare_parallel_workspace(
    *,
    workspace_root: Path,
    task_id: str,
    title: str,
    selections: Sequence[RepoBranchSelection],
) -> list[ParallelRepoRecord]:
    """根据所选基线分支创建并行副本。"""

    root = Path(workspace_root)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    task_branch = build_parallel_branch_name(task_id, title)
    created: list[Path] = []
    records: list[ParallelRepoRecord] = []
    try:
        for selection in selections:
            target = root / selection.relative_path if selection.relative_path != "." else root
            target.parent.mkdir(parents=True, exist_ok=True)
            clone = _run_git(["clone", str(selection.source_repo_path), str(target)], cwd=root)
            if clone.returncode != 0:
                raise RuntimeError(clone.stderr.strip() or clone.stdout.strip() or f"克隆失败: {selection.repo_key}")
            created.append(target)

            fetch = _run_git(["fetch", "--all", "--prune"], cwd=target)
            if fetch.returncode != 0:
                raise RuntimeError(fetch.stderr.strip() or fetch.stdout.strip() or f"抓取分支失败: {selection.repo_key}")

            checkout = _run_git(["checkout", "-B", task_branch, selection.selected_ref], cwd=target)
            if checkout.returncode != 0:
                raise RuntimeError(checkout.stderr.strip() or checkout.stdout.strip() or f"切换任务分支失败: {selection.repo_key}")

            records.append(
                ParallelRepoRecord(
                    repo_key=selection.repo_key,
                    source_repo_path=str(selection.source_repo_path),
                    workspace_repo_path=str(target),
                    selected_base_ref=selection.selected_ref,
                    selected_remote=selection.selected_remote,
                    task_branch=task_branch,
                )
            )
        return records
    except Exception:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        raise


def commit_parallel_repos(
    *,
    task: TaskRecord,
    repos: Sequence[ParallelRepoRecord],
) -> ParallelCommitResult:
    """提交并推送所有有改动的并行仓库。"""

    results: list[RepoOperationResult] = []
    for repo in repos:
        repo_path = Path(repo.workspace_repo_path)
        repo_name = Path(repo.repo_key).name if repo.repo_key != "__root__" else Path(repo.source_repo_path).name

        status = _run_git(["status", "--short"], cwd=repo_path)
        if status.returncode != 0:
            results.append(RepoOperationResult(repo.repo_key, repo_name, False, "failed", status.stderr.strip() or "读取状态失败"))
            continue
        if not status.stdout.strip():
            results.append(RepoOperationResult(repo.repo_key, repo_name, True, "skipped", "无改动，已跳过"))
            continue

        add = _run_git(["add", "-A"], cwd=repo_path)
        if add.returncode != 0:
            results.append(RepoOperationResult(repo.repo_key, repo_name, False, "failed", add.stderr.strip() or "git add 失败"))
            continue

        subject, body = build_parallel_commit_message(task, repo_name)
        commit = _run_git(["commit", "-m", subject, "-m", body], cwd=repo_path)
        if commit.returncode != 0:
            if "nothing to commit" in (commit.stdout + commit.stderr).lower():
                results.append(RepoOperationResult(repo.repo_key, repo_name, True, "skipped", "无暂存改动，已跳过"))
            else:
                results.append(RepoOperationResult(repo.repo_key, repo_name, False, "failed", commit.stderr.strip() or "git commit 失败"))
            continue

        push_remote = repo.selected_remote or "origin"
        push = _run_git(["push", "--set-upstream", push_remote, repo.task_branch], cwd=repo_path)
        if push.returncode != 0:
            results.append(RepoOperationResult(repo.repo_key, repo_name, False, "failed", push.stderr.strip() or "git push 失败"))
            continue

        results.append(RepoOperationResult(repo.repo_key, repo_name, True, "pushed", "提交并推送成功"))
    return ParallelCommitResult(results=results)


def merge_parallel_repos(
    *,
    task: TaskRecord,
    repos: Sequence[ParallelRepoRecord],
) -> ParallelMergeResult:
    """将并行任务分支合并回各仓库所选基线分支。"""

    results: list[RepoOperationResult] = []
    for repo in repos:
        repo_path = Path(repo.workspace_repo_path)
        repo_name = Path(repo.repo_key).name if repo.repo_key != "__root__" else Path(repo.source_repo_path).name
        fetch = _run_git(["fetch", "--all", "--prune"], cwd=repo_path)
        if fetch.returncode != 0:
            results.append(RepoOperationResult(repo.repo_key, repo_name, False, "failed", fetch.stderr.strip() or "git fetch 失败"))
            break

        target_branch = repo.selected_base_ref.split("/", 1)[1] if "/" in repo.selected_base_ref and repo.selected_base_ref.count("/") == 1 else repo.selected_base_ref.split("/")[-1]
        if repo.selected_base_ref.startswith((repo.selected_remote or "origin") + "/"):
            checkout = _run_git(["checkout", "-B", target_branch, repo.selected_base_ref], cwd=repo_path)
        else:
            checkout = _run_git(["checkout", target_branch], cwd=repo_path)
        if checkout.returncode != 0:
            results.append(RepoOperationResult(repo.repo_key, repo_name, False, "failed", checkout.stderr.strip() or "切换基线分支失败"))
            break

        merge_message = f"merge({task.id}): {(task.title or '').strip() or task.id}"
        merge = _run_git(["merge", "--no-ff", repo.task_branch, "-m", merge_message], cwd=repo_path)
        if merge.returncode != 0:
            results.append(RepoOperationResult(repo.repo_key, repo_name, False, "failed", merge.stderr.strip() or merge.stdout.strip() or "自动合并失败"))
            break

        push_remote = repo.selected_remote or "origin"
        push = _run_git(["push", push_remote, target_branch], cwd=repo_path)
        if push.returncode != 0:
            results.append(RepoOperationResult(repo.repo_key, repo_name, False, "failed", push.stderr.strip() or "推送基线分支失败"))
            break

        results.append(RepoOperationResult(repo.repo_key, repo_name, True, "merged", f"已自动合并到 {target_branch}"))
    return ParallelMergeResult(results=results)


def delete_parallel_workspace(
    *,
    workspace_root: Path,
    tmux_session: Optional[str] = None,
    binder_pid_file: Optional[Path] = None,
) -> None:
    """删除并行目录并清理其 tmux / binder。"""

    if tmux_session:
        subprocess.run(["tmux", "-u", "kill-session", "-t", tmux_session], check=False, capture_output=True, text=True)

    if binder_pid_file and binder_pid_file.exists():
        try:
            pid = int(binder_pid_file.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            pid = 0
        if pid > 0:
            subprocess.run(["kill", str(pid)], check=False, capture_output=True, text=True)
        binder_pid_file.unlink(missing_ok=True)

    shutil.rmtree(Path(workspace_root), ignore_errors=True)


class ParallelSessionStore:
    """并行会话持久化。"""

    def __init__(self, db_path: Path, project_slug: str):
        self.db_path = Path(db_path)
        self.project_slug = project_slug
        self._initialized = False
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def initialize(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS parallel_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL UNIQUE,
                    project_slug TEXT NOT NULL,
                    title_snapshot TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    tmux_session TEXT NOT NULL,
                    pointer_file TEXT NOT NULL,
                    task_branch TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_commit_at TEXT,
                    last_merge_at TEXT,
                    deleted_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS parallel_session_repos (
                    session_id INTEGER NOT NULL,
                    repo_key TEXT NOT NULL,
                    source_repo_path TEXT NOT NULL,
                    workspace_repo_path TEXT NOT NULL,
                    selected_base_ref TEXT NOT NULL,
                    selected_remote TEXT,
                    task_branch TEXT NOT NULL,
                    commit_status TEXT NOT NULL DEFAULT 'pending',
                    merge_status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT,
                    PRIMARY KEY(session_id, repo_key),
                    FOREIGN KEY(session_id) REFERENCES parallel_sessions(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_parallel_sessions_project_status
                ON parallel_sessions(project_slug, status, updated_at)
                """
            )
            await db.commit()
        self._initialized = True

    async def upsert_session(
        self,
        *,
        task_id: str,
        title_snapshot: str,
        workspace_root: str,
        tmux_session: str,
        pointer_file: str,
        task_branch: str,
        status: str,
        repos: Sequence[ParallelRepoRecord],
        last_error: Optional[str] = None,
    ) -> ParallelSessionRecord:
        await self.initialize()
        now = shanghai_now_iso()
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    "SELECT id, created_at FROM parallel_sessions WHERE task_id = ?",
                    (task_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    cursor = await db.execute(
                        """
                        INSERT INTO parallel_sessions (
                            task_id, project_slug, title_snapshot, workspace_root,
                            tmux_session, pointer_file, task_branch, status,
                            last_error, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            self.project_slug,
                            title_snapshot,
                            workspace_root,
                            tmux_session,
                            pointer_file,
                            task_branch,
                            status,
                            last_error,
                            now,
                            now,
                        ),
                    )
                    session_id = int(cursor.lastrowid)
                    created_at = now
                else:
                    session_id = int(row["id"])
                    created_at = row["created_at"]
                    await db.execute(
                        """
                        UPDATE parallel_sessions
                        SET title_snapshot = ?, workspace_root = ?, tmux_session = ?,
                            pointer_file = ?, task_branch = ?, status = ?, last_error = ?,
                            updated_at = ?, deleted_at = NULL
                        WHERE task_id = ?
                        """,
                        (
                            title_snapshot,
                            workspace_root,
                            tmux_session,
                            pointer_file,
                            task_branch,
                            status,
                            last_error,
                            now,
                            task_id,
                        ),
                    )
                    await db.execute("DELETE FROM parallel_session_repos WHERE session_id = ?", (session_id,))

                for repo in repos:
                    await db.execute(
                        """
                        INSERT INTO parallel_session_repos (
                            session_id, repo_key, source_repo_path, workspace_repo_path,
                            selected_base_ref, selected_remote, task_branch,
                            commit_status, merge_status, last_error
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            repo.repo_key,
                            repo.source_repo_path,
                            repo.workspace_repo_path,
                            repo.selected_base_ref,
                            repo.selected_remote,
                            repo.task_branch,
                            repo.commit_status,
                            repo.merge_status,
                            repo.last_error,
                        ),
                    )
                await db.commit()
        return ParallelSessionRecord(
            id=session_id,
            task_id=task_id,
            project_slug=self.project_slug,
            title_snapshot=title_snapshot,
            workspace_root=workspace_root,
            tmux_session=tmux_session,
            pointer_file=pointer_file,
            task_branch=task_branch,
            status=status,
            created_at=created_at,
            updated_at=now,
            last_error=last_error,
        )

    async def get_session(self, task_id: str) -> Optional[ParallelSessionRecord]:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM parallel_sessions WHERE task_id = ?",
                (task_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return ParallelSessionRecord(
            id=row["id"],
            task_id=row["task_id"],
            project_slug=row["project_slug"],
            title_snapshot=row["title_snapshot"],
            workspace_root=row["workspace_root"],
            tmux_session=row["tmux_session"],
            pointer_file=row["pointer_file"],
            task_branch=row["task_branch"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_error=row["last_error"],
            last_commit_at=row["last_commit_at"],
            last_merge_at=row["last_merge_at"],
            deleted_at=row["deleted_at"],
        )

    async def list_repos(self, task_id: str) -> list[ParallelRepoRecord]:
        session = await self.get_session(task_id)
        if session is None:
            return []
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM parallel_session_repos
                WHERE session_id = ?
                ORDER BY repo_key
                """,
                (session.id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            ParallelRepoRecord(
                repo_key=row["repo_key"],
                source_repo_path=row["source_repo_path"],
                workspace_repo_path=row["workspace_repo_path"],
                selected_base_ref=row["selected_base_ref"],
                selected_remote=row["selected_remote"],
                task_branch=row["task_branch"],
                commit_status=row["commit_status"],
                merge_status=row["merge_status"],
                last_error=row["last_error"],
            )
            for row in rows
        ]

    async def update_status(
        self,
        task_id: str,
        *,
        status: str,
        last_error: Optional[str] = None,
        last_commit_at: Optional[str] = None,
        last_merge_at: Optional[str] = None,
        deleted_at: Optional[str] = None,
    ) -> None:
        await self.initialize()
        now = shanghai_now_iso()
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    UPDATE parallel_sessions
                    SET status = ?, last_error = ?, last_commit_at = COALESCE(?, last_commit_at),
                        last_merge_at = COALESCE(?, last_merge_at), deleted_at = COALESCE(?, deleted_at),
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (status, last_error, last_commit_at, last_merge_at, deleted_at, now, task_id),
                )
                await db.commit()

    async def update_repo_status(
        self,
        task_id: str,
        repo_key: str,
        *,
        commit_status: Optional[str] = None,
        merge_status: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        session = await self.get_session(task_id)
        if session is None:
            return
        await self.initialize()
        fields: list[str] = []
        values: list[object] = []
        if commit_status is not None:
            fields.append("commit_status = ?")
            values.append(commit_status)
        if merge_status is not None:
            fields.append("merge_status = ?")
            values.append(merge_status)
        if last_error is not None:
            fields.append("last_error = ?")
            values.append(last_error)
        if not fields:
            return
        values.extend([session.id, repo_key])
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    f"UPDATE parallel_session_repos SET {', '.join(fields)} WHERE session_id = ? AND repo_key = ?",
                    values,
                )
                await db.commit()
