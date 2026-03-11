"""并行开发运行时与 Git 操作辅助。"""
from __future__ import annotations

import asyncio
import fnmatch
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
    is_current: bool = False


@dataclass(slots=True)
class CommonBranchRef:
    """描述所有 Git 仓库都共同拥有的候选分支。"""

    name: str
    source: str  # local / remote
    remote: Optional[str] = None
    current_count: int = 0
    total_repos: int = 0


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
class CodexTrustedPathRecord:
    """描述 vibego 托管的 Codex trusted 路径记录。"""

    path: str
    project_slug: str
    scope: str
    owner_key: str
    previous_trust_level: Optional[str]
    managed_by_vibego: bool
    created_at: str
    updated_at: str


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
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def _current_branch_name(repo_path: Path) -> Optional[str]:
    """返回仓库当前本地分支名；Detached/失败时返回 None。"""

    result = _run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo_path)
    branch_name = result.stdout.strip()
    return branch_name or None if result.returncode == 0 else None


def _extract_status_paths(status_output: str) -> list[str]:
    """从 git status --short 输出中提取变更路径。"""

    paths: list[str] = []
    for raw_line in (status_output or "").splitlines():
        line = raw_line.rstrip()
        if len(line) < 4:
            continue
        path_text = line[3:].strip().strip('"')
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1].strip().strip('"')
        if path_text:
            paths.append(path_text)
    return paths


def _resolve_push_remote(repo_path: Path, repo: ParallelRepoRecord) -> Optional[str]:
    """解析仓库推送/合并应使用的 remote。"""

    current_branch = _current_branch_name(repo_path)
    candidate_branches: list[str] = []
    if current_branch:
        candidate_branches.append(current_branch)
    if repo.selected_base_ref and "/" not in repo.selected_base_ref:
        candidate_branches.append(repo.selected_base_ref)

    seen: set[str] = set()
    for branch_name in candidate_branches:
        if not branch_name or branch_name in seen:
            continue
        seen.add(branch_name)
        remote = _run_git(["config", f"branch.{branch_name}.remote"], cwd=repo_path)
        remote_name = remote.stdout.strip()
        if remote.returncode == 0 and remote_name:
            return remote_name

    if repo.selected_remote:
        return repo.selected_remote

    remotes = _run_git(["remote"], cwd=repo_path)
    if remotes.returncode != 0:
        return None
    remote_names = [line.strip() for line in remotes.stdout.splitlines() if line.strip()]
    if len(remote_names) == 1:
        return remote_names[0]
    return None


def _direct_child_repo_names(repo_path: Path, repos: Sequence[ParallelRepoRecord], current_repo_key: str) -> set[str]:
    """收集当前仓库下直系/间接嵌套仓库在当前仓库视角的首层目录名。"""

    child_names: set[str] = set()
    for item in repos:
        if item.repo_key == current_repo_key:
            continue
        try:
            relative_path = Path(item.workspace_repo_path).resolve().relative_to(repo_path.resolve())
        except ValueError:
            continue
        if not relative_path.parts:
            continue
        child_names.add(relative_path.parts[0])
    return child_names


def _is_meta_only_status(repo: ParallelRepoRecord, repos: Sequence[ParallelRepoRecord], status_output: str) -> bool:
    """判断当前仓库是否只有子仓库指针/共享目录等元信息变化。"""

    changed_paths = _extract_status_paths(status_output)
    if not changed_paths:
        return False

    repo_path = Path(repo.workspace_repo_path)
    allowed_top_levels = _direct_child_repo_names(repo_path, repos, repo.repo_key)
    if repo.repo_key == "__root__":
        allowed_top_levels.update({"docs", ".idea"})
    if not allowed_top_levels:
        return False

    for changed_path in changed_paths:
        first_part = Path(changed_path).parts[0] if Path(changed_path).parts else ""
        if first_part not in allowed_top_levels:
            return False
    return True


def _repo_key_for(base_dir: Path, repo_path: Path) -> tuple[str, str]:
    rel = repo_path.relative_to(base_dir)
    rel_text = str(rel).strip()
    if not rel_text or rel_text == ".":
        return "__root__", "."
    key = rel_text.replace("\\", "/")
    return key, key


def _relative_repo_parts(relative_path: str) -> tuple[str, ...]:
    """将仓库相对路径标准化为可比较的层级元组。"""

    normalized = str(relative_path or ".").replace("\\", "/").strip("/")
    if not normalized or normalized == ".":
        return ()
    return tuple(part for part in normalized.split("/") if part and part != ".")


def _is_descendant_path(candidate: tuple[str, ...], parent: tuple[str, ...]) -> bool:
    """判断 candidate 是否为 parent 的严格子路径。"""

    if not parent or len(candidate) <= len(parent):
        return False
    return candidate[: len(parent)] == parent


def discover_git_repos(
    base_dir: Path,
    *,
    max_depth: int = 4,
    include_nested: bool = False,
) -> list[tuple[str, Path, str]]:
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
    if include_nested:
        return [(key, path, rel) for key, (path, rel) in ordered]

    filtered: list[tuple[str, Path, str]] = []
    kept_paths: list[tuple[str, tuple[str, ...]]] = []
    for key, (path, rel) in ordered:
        rel_parts = _relative_repo_parts(rel)
        # 根仓库允许与独立子仓库共存；但非根仓库一旦被选中，就跳过其子孙仓库，
        # 避免并行 workspace 中父子路径重叠，导致 git clone 报“目标目录非空”。
        if any(parent_key != "__root__" and _is_descendant_path(rel_parts, parent_parts) for parent_key, parent_parts in kept_paths):
            continue
        filtered.append((key, path, rel))
        kept_paths.append((key, rel_parts))
    return filtered


def get_current_branch_state(repo_path: Path) -> tuple[str, Optional[str]]:
    """读取仓库当前分支展示文案与可高亮的本地分支名。"""

    symbolic = _run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo_path)
    current_branch = symbolic.stdout.strip()
    if symbolic.returncode == 0 and current_branch:
        return current_branch, current_branch

    detached = _run_git(["rev-parse", "--verify", "HEAD"], cwd=repo_path)
    if detached.returncode == 0:
        return "Detached HEAD", None

    return "读取失败", None


def list_branch_refs(repo_path: Path, *, current_local_branch: Optional[str] = None) -> list[BranchRef]:
    """列出单仓库的本地+远端分支。"""

    refs: list[BranchRef] = []
    local = _run_git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd=repo_path)
    if local.returncode == 0:
        for line in local.stdout.splitlines():
            name = line.strip()
            if name:
                refs.append(BranchRef(name=name, source="local", is_current=(name == current_local_branch)))

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
    return sorted(
        dedup.values(),
        key=lambda item: (
            0 if item.is_current else 1,
            0 if item.source == "local" else 1,
            item.name.casefold(),
        ),
    )


def collect_common_branch_refs(
    repo_branch_options: Sequence[tuple[str, Sequence[BranchRef]]],
) -> list[CommonBranchRef]:
    """收集所有 Git 仓库共同拥有的分支，并统计当前分支命中数。"""

    if not repo_branch_options:
        return []

    intersection_keys: Optional[set[tuple[str, str]]] = None
    branch_meta: dict[tuple[str, str], BranchRef] = {}
    current_counts: dict[tuple[str, str], int] = {}

    for _repo_key, branches in repo_branch_options:
        repo_map: dict[tuple[str, str], BranchRef] = {}
        for branch in branches:
            key = (branch.source, branch.name)
            repo_map[key] = branch
            branch_meta[key] = branch
            if branch.is_current:
                current_counts[key] = current_counts.get(key, 0) + 1
        repo_keys = set(repo_map)
        intersection_keys = repo_keys if intersection_keys is None else intersection_keys & repo_keys

    if not intersection_keys:
        return []

    total_repos = len(repo_branch_options)
    results = [
        CommonBranchRef(
            name=branch_meta[key].name,
            source=branch_meta[key].source,
            remote=branch_meta[key].remote,
            current_count=current_counts.get(key, 0),
            total_repos=total_repos,
        )
        for key in intersection_keys
    ]
    return sorted(
        results,
        key=lambda item: (
            0 if item.current_count == item.total_repos and item.total_repos > 0 else 1,
            -item.current_count,
            0 if item.source == "local" else 1,
            item.name.casefold(),
        ),
    )


def filter_common_branch_repo_options(
    repo_branch_options: Sequence[tuple[str, str, Sequence[BranchRef]]],
) -> tuple[list[tuple[str, Sequence[BranchRef]]], list[str]]:
    """过滤共同分支计算范围。

    仅忽略“没有任何远端分支的本地根仓库”，其它仓库继续参与共同分支计算。
    """

    eligible: list[tuple[str, Sequence[BranchRef]]] = []
    ignored: list[str] = []
    for repo_key, relative_path, branches in repo_branch_options:
        if repo_key == "__root__" and not any(branch.source == "remote" for branch in branches):
            ignored.append(relative_path or ".")
            continue
        eligible.append((repo_key, branches))
    return eligible, ignored


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


def _validate_parallel_selection_paths(selections: Sequence[RepoBranchSelection]) -> None:
    """校验并行副本目标路径，阻断非根仓库之间的父子路径重叠。"""

    resolved: list[tuple[str, str, tuple[str, ...]]] = []
    for selection in selections:
        rel_text = str(selection.relative_path or ".").replace("\\", "/").strip() or "."
        rel_parts = _relative_repo_parts(rel_text)
        for prev_repo_key, prev_rel_text, prev_parts in resolved:
            # 根目录副本允许与其他仓库并存；只拦截非根仓库之间的相同/嵌套路径。
            if not rel_parts or not prev_parts:
                continue
            same_path = rel_parts == prev_parts
            nested_path = _is_descendant_path(rel_parts, prev_parts) or _is_descendant_path(prev_parts, rel_parts)
            if same_path or nested_path:
                raise RuntimeError(
                    "并行仓库路径重叠："
                    f"{prev_repo_key}({prev_rel_text}) 与 {selection.repo_key}({rel_text})。"
                    "请仅保留父仓库或子仓库其一。"
                )
        resolved.append((selection.repo_key, rel_text, rel_parts))


DEFAULT_PARALLEL_COPY_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "target",
    "build",
    "dist",
    "node_modules",
    "logs",
    ".idea",
    ".DS_Store",
    ".pnpm-store",
    ".gradle",
)


def _normalize_gitignore_pattern(raw_line: str) -> Optional[str]:
    """标准化 .gitignore 行，仅保留用于复制排除的有效模式。"""

    line = (raw_line or "").strip()
    if not line or line.startswith("#") or line.startswith("!"):
        return None
    if line.startswith("\\#"):
        line = line[1:]
    normalized = line.lstrip("/").rstrip("/")
    return normalized or None


def _collect_common_gitignore_patterns(source_root: Path) -> set[str]:
    """提取工作目录下全部 .gitignore 的交集模式。"""

    pattern_sets: list[set[str]] = []
    for gitignore_file in sorted(source_root.rglob(".gitignore")):
        try:
            lines = gitignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        current_patterns = {
            normalized
            for normalized in (_normalize_gitignore_pattern(line) for line in lines)
            if normalized is not None
        }
        if current_patterns:
            pattern_sets.append(current_patterns)
    if not pattern_sets:
        return set()
    return set.intersection(*pattern_sets)


def _should_ignore_copy_entry(
    source_root: Path,
    current_dir: Path,
    name: str,
    *,
    exclude_patterns: set[str],
) -> bool:
    """判断复制工作目录时当前条目是否应被排除。"""

    normalized_name = name.strip()
    if normalized_name in exclude_patterns:
        return True
    try:
        current_rel = current_dir.relative_to(source_root)
    except ValueError:
        current_rel = Path(".")
    rel_path = Path(name) if str(current_rel) == "." else current_rel / name
    rel_text = rel_path.as_posix()
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(normalized_name, pattern) or fnmatch.fnmatch(rel_text, pattern):
            return True
    return False


def _copy_parallel_workspace_tree(*, source_root: Path, workspace_root: Path) -> None:
    """完整复制 workdir，同时排除默认生成物目录与全部 .gitignore 的交集模式。"""

    exclude_patterns = set(DEFAULT_PARALLEL_COPY_EXCLUDE_PATTERNS)
    exclude_patterns.update(_collect_common_gitignore_patterns(source_root))

    def _ignore(current_dir: str, names: list[str]) -> set[str]:
        current_path = Path(current_dir)
        return {
            name
            for name in names
            if _should_ignore_copy_entry(
                source_root,
                current_path,
                name,
                exclude_patterns=exclude_patterns,
            )
        }

    shutil.copytree(source_root, workspace_root, ignore=_ignore, dirs_exist_ok=False)


def _link_shared_docs_dir(*, source_root: Path, workspace_root: Path) -> None:
    """让并行工作区根级 docs 直接共享真实项目根级 docs。"""

    source_docs = Path(source_root) / "docs"
    workspace_docs = Path(workspace_root) / "docs"
    source_docs.mkdir(parents=True, exist_ok=True)

    if workspace_docs.is_symlink() or workspace_docs.is_file():
        workspace_docs.unlink(missing_ok=True)
    elif workspace_docs.is_dir():
        shutil.rmtree(workspace_docs, ignore_errors=True)

    workspace_docs.symlink_to(source_docs, target_is_directory=True)


def _write_workspace_idea_vcs_mappings(*, workspace_root: Path) -> None:
    """为并行工作区生成 IDEA 多仓库 Git 映射，便于直接查看所有分支。"""

    repo_entries = discover_git_repos(workspace_root, include_nested=True)
    if not repo_entries:
        return

    idea_dir = Path(workspace_root) / ".idea"
    idea_dir.mkdir(parents=True, exist_ok=True)
    mappings: list[str] = []
    for _repo_key, _repo_path, relative_path in repo_entries:
        if relative_path == ".":
            directory = "$PROJECT_DIR$"
        else:
            normalized = str(relative_path).replace("\\", "/").strip("/")
            directory = f"$PROJECT_DIR$/{normalized}"
        mappings.append(f'    <mapping directory="{directory}" vcs="Git" />')

    content = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<project version=\"4\">",
            "  <component name=\"VcsDirectoryMappings\">",
            *mappings,
            "  </component>",
            "</project>",
            "",
        ]
    )
    (idea_dir / "vcs.xml").write_text(content, encoding="utf-8")


def prepare_parallel_workspace(
    *,
    workspace_root: Path,
    task_id: str,
    title: str,
    selections: Sequence[RepoBranchSelection],
    source_root: Optional[Path] = None,
) -> list[ParallelRepoRecord]:
    """根据所选基线分支创建并行副本。"""

    root = Path(workspace_root)
    if root.exists():
        shutil.rmtree(root)
    if source_root is None:
        root.mkdir(parents=True, exist_ok=True)
        _validate_parallel_selection_paths(selections)
    else:
        source_base = Path(source_root).resolve()
        root.parent.mkdir(parents=True, exist_ok=True)
        _copy_parallel_workspace_tree(source_root=source_base, workspace_root=root)
        _link_shared_docs_dir(source_root=source_base, workspace_root=root)
        _write_workspace_idea_vcs_mappings(workspace_root=root)

    task_branch = build_parallel_branch_name(task_id, title)
    records: list[ParallelRepoRecord] = []
    try:
        for selection in selections:
            target = root / selection.relative_path if selection.relative_path != "." else root
            if source_root is None:
                target.parent.mkdir(parents=True, exist_ok=True)
                clone = _run_git(["clone", str(selection.source_repo_path), str(target)], cwd=root)
                if clone.returncode != 0:
                    raise RuntimeError(clone.stderr.strip() or clone.stdout.strip() or f"克隆失败: {selection.repo_key}")
            elif not target.exists():
                raise RuntimeError(f"并行目录中缺少仓库：{selection.relative_path or '.'}")

            # 仅当用户选中了远端分支时才刷新远端引用；
            # 本地分支直接基于副本内已有分支检出，避免不必要的远端访问。
            if selection.selected_remote:
                fetch = _run_git(["fetch", "--all", "--prune"], cwd=target)
                if fetch.returncode != 0:
                    raise RuntimeError(fetch.stderr.strip() or fetch.stdout.strip() or f"抓取分支失败: {selection.repo_key}")

            current_local_branch = _current_branch_name(target)
            preserve_dirty_worktree = selection.selected_remote is None and (
                selection.selected_ref == "HEAD" or current_local_branch == selection.selected_ref
            )
            if preserve_dirty_worktree:
                checkout = _run_git(["checkout", "-b", task_branch], cwd=target)
            else:
                checkout = _run_git(["checkout", "-B", task_branch, selection.selected_ref], cwd=target)
                if checkout.returncode == 0:
                    reset = _run_git(["reset", "--hard", "HEAD"], cwd=target)
                    if reset.returncode != 0:
                        raise RuntimeError(reset.stderr.strip() or reset.stdout.strip() or f"重置并行工作区失败: {selection.repo_key}")
                    clean = _run_git(["clean", "-fd"], cwd=target)
                    if clean.returncode != 0:
                        raise RuntimeError(clean.stderr.strip() or clean.stdout.strip() or f"清理并行工作区失败: {selection.repo_key}")
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
        if _is_meta_only_status(repo, repos, status.stdout):
            results.append(RepoOperationResult(repo.repo_key, repo_name, True, "skipped", "仅包含子仓库/共享目录元信息变更，已跳过"))
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

        push_remote = _resolve_push_remote(repo_path, repo)
        if not push_remote:
            results.append(RepoOperationResult(repo.repo_key, repo_name, True, "committed", "已本地提交，未配置远端，已跳过推送"))
            continue
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
        push_remote = _resolve_push_remote(repo_path, repo)
        if not push_remote:
            results.append(RepoOperationResult(repo.repo_key, repo_name, True, "skipped", "未配置远端，已跳过自动合并"))
            continue
        fetch = _run_git(["fetch", "--all", "--prune"], cwd=repo_path)
        if fetch.returncode != 0:
            results.append(RepoOperationResult(repo.repo_key, repo_name, False, "failed", fetch.stderr.strip() or "git fetch 失败"))
            break

        target_branch = repo.selected_base_ref.split("/", 1)[1] if "/" in repo.selected_base_ref and repo.selected_base_ref.count("/") == 1 else repo.selected_base_ref.split("/")[-1]
        if repo.selected_base_ref.startswith(push_remote + "/"):
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
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_trusted_paths (
                    path TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    owner_key TEXT NOT NULL,
                    previous_trust_level TEXT,
                    managed_by_vibego INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_codex_trusted_paths_scope_owner
                ON codex_trusted_paths(project_slug, scope, owner_key, updated_at)
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

    async def upsert_trusted_path(
        self,
        *,
        path: str,
        scope: str,
        owner_key: str,
        previous_trust_level: Optional[str],
        managed_by_vibego: bool,
    ) -> CodexTrustedPathRecord:
        """新增或更新 Codex trusted 路径登记。"""

        await self.initialize()
        now = shanghai_now_iso()
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO codex_trusted_paths (
                        path, project_slug, scope, owner_key, previous_trust_level,
                        managed_by_vibego, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        project_slug = excluded.project_slug,
                        scope = excluded.scope,
                        owner_key = excluded.owner_key,
                        previous_trust_level = excluded.previous_trust_level,
                        managed_by_vibego = excluded.managed_by_vibego,
                        updated_at = excluded.updated_at
                    """,
                    (
                        path,
                        self.project_slug,
                        scope,
                        owner_key,
                        previous_trust_level,
                        1 if managed_by_vibego else 0,
                        now,
                        now,
                    ),
                )
                await db.commit()
        record = await self.get_trusted_path(path)
        assert record is not None
        return record

    async def get_trusted_path(self, path: str) -> Optional[CodexTrustedPathRecord]:
        """读取单条 Codex trusted 路径登记。"""

        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM codex_trusted_paths WHERE path = ?",
                (path,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return CodexTrustedPathRecord(
            path=row["path"],
            project_slug=row["project_slug"],
            scope=row["scope"],
            owner_key=row["owner_key"],
            previous_trust_level=row["previous_trust_level"],
            managed_by_vibego=bool(row["managed_by_vibego"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def list_trusted_paths(self, *, scope: Optional[str] = None) -> list[CodexTrustedPathRecord]:
        """按范围列出 Codex trusted 路径登记。"""

        await self.initialize()
        query = "SELECT * FROM codex_trusted_paths"
        args: tuple[object, ...] = ()
        if scope:
            query += " WHERE scope = ?"
            args = (scope,)
        query += " ORDER BY path"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, args) as cursor:
                rows = await cursor.fetchall()
        return [
            CodexTrustedPathRecord(
                path=row["path"],
                project_slug=row["project_slug"],
                scope=row["scope"],
                owner_key=row["owner_key"],
                previous_trust_level=row["previous_trust_level"],
                managed_by_vibego=bool(row["managed_by_vibego"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def delete_trusted_path(self, path: str) -> None:
        """删除一条 Codex trusted 路径登记。"""

        await self.initialize()
        async with self._get_lock():
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM codex_trusted_paths WHERE path = ?", (path,))
                await db.commit()
