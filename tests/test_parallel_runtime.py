from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from parallel_runtime import (
    RepoBranchSelection,
    discover_git_repos,
    get_current_branch_state,
    list_branch_refs,
    prepare_parallel_workspace,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """执行 git 命令，失败时直接让测试暴露上下文。"""

    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )


def _init_repo(repo: Path, files: dict[str, str] | None = None) -> None:
    """初始化最小 Git 仓库，供并行运行时测试复用。"""

    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    for relative_path, content in (files or {"README.md": "# demo\n"}).items():
        file_path = repo / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")


def test_discover_git_repos_skips_nested_repos_under_non_root_parent(tmp_path: Path) -> None:
    """并行仓库发现应跳过已被父仓库覆盖的嵌套仓库，避免后续 clone 路径冲突。"""

    root = tmp_path / "mall"
    _init_repo(root, {"README.md": "root\n"})
    _init_repo(root / "backend-java", {"service.txt": "backend\n"})
    _init_repo(root / "frontend-admin", {"index.html": "<html></html>\n"})
    _init_repo(root / "backend-java" / "infra-parent", {"pom.xml": "<project />\n"})
    _init_repo(root / "backend-java" / "infra-common", {"pom.xml": "<project />\n"})

    repos = discover_git_repos(root)
    keys = [repo_key for repo_key, _repo_path, _relative_path in repos]

    assert "__root__" in keys
    assert "backend-java" in keys
    assert "frontend-admin" in keys
    assert "backend-java/infra-parent" not in keys
    assert "backend-java/infra-common" not in keys


def test_prepare_parallel_workspace_rejects_overlapping_relative_paths(tmp_path: Path) -> None:
    """即使上游传入了父子重叠路径，也应给出明确错误而不是透传生硬的 git 报错。"""

    parent_repo = tmp_path / "sources" / "backend-java"
    nested_repo = tmp_path / "sources" / "infra-parent"
    _init_repo(parent_repo, {"infra-parent/pom.xml": "<project />\n"})
    _init_repo(nested_repo, {"pom.xml": "<project />\n"})

    with pytest.raises(RuntimeError, match="并行仓库路径重叠"):
        prepare_parallel_workspace(
            workspace_root=tmp_path / "workspace",
            task_id="TASK_0093",
            title="并行创建缺陷修复",
            selections=[
                RepoBranchSelection(
                    repo_key="backend-java",
                    source_repo_path=parent_repo,
                    selected_ref="HEAD",
                    selected_remote=None,
                    relative_path="backend-java",
                ),
                RepoBranchSelection(
                    repo_key="backend-java/infra-parent",
                    source_repo_path=nested_repo,
                    selected_ref="HEAD",
                    selected_remote=None,
                    relative_path="backend-java/infra-parent",
                ),
            ],
        )


def test_get_current_branch_state_marks_current_local_branch_first(tmp_path: Path) -> None:
    """应识别当前本地分支，并在分支列表中置顶且标记为当前。"""

    repo = tmp_path / "repo"
    _init_repo(repo, {"README.md": "demo\n"})
    _git(repo, "checkout", "-b", "develop")
    _git(repo, "branch", "feature/demo")

    current_label, current_local_branch = get_current_branch_state(repo)
    branches = list_branch_refs(repo, current_local_branch=current_local_branch)

    assert current_label == "develop"
    assert current_local_branch == "develop"
    assert branches[0].name == "develop"
    assert branches[0].is_current is True


def test_get_current_branch_state_returns_detached_head_when_not_on_branch(tmp_path: Path) -> None:
    """Detached HEAD 应展示专用文案，且不标记任何当前分支。"""

    repo = tmp_path / "repo"
    _init_repo(repo, {"README.md": "demo\n"})
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "--detach", head_sha)

    current_label, current_local_branch = get_current_branch_state(repo)
    branches = list_branch_refs(repo, current_local_branch=current_local_branch)

    assert current_label == "Detached HEAD"
    assert current_local_branch is None
    assert all(branch.is_current is False for branch in branches)


def test_get_current_branch_state_returns_read_failure_for_non_repo(tmp_path: Path) -> None:
    """非 Git 目录读取当前分支失败时，应返回统一兜底文案。"""

    plain_dir = tmp_path / "plain"
    plain_dir.mkdir(parents=True, exist_ok=True)

    current_label, current_local_branch = get_current_branch_state(plain_dir)

    assert current_label == "读取失败"
    assert current_local_branch is None
