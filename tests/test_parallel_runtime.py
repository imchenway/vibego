from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import parallel_runtime

from parallel_runtime import (
    BranchRef,
    RepoBranchSelection,
    collect_common_branch_refs,
    discover_git_repos,
    filter_common_branch_repo_options,
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


def test_discover_git_repos_includes_nested_repos_when_requested(tmp_path: Path) -> None:
    """批量分支选择需要看到所有 Git 库时，应支持返回嵌套仓库。"""

    root = tmp_path / "mall"
    _init_repo(root, {"README.md": "root\n"})
    _init_repo(root / "backend-java", {"service.txt": "backend\n"})
    _init_repo(root / "backend-java" / "infra-parent", {"pom.xml": "<project />\n"})

    repos = discover_git_repos(root, include_nested=True)
    keys = [repo_key for repo_key, _repo_path, _relative_path in repos]

    assert "__root__" in keys
    assert "backend-java" in keys
    assert "backend-java/infra-parent" in keys


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


def test_collect_common_branch_refs_builds_intersection_and_current_marker() -> None:
    """共同分支列表应只保留所有仓库共有的分支，并统计当前分支命中数。"""

    options = collect_common_branch_refs(
        [
            (
                "backend-java",
                [
                    BranchRef(name="develop", source="local", is_current=True),
                    BranchRef(name="release", source="local"),
                    BranchRef(name="origin/develop", source="remote", remote="origin"),
                ],
            ),
            (
                "frontend-admin",
                [
                    BranchRef(name="develop", source="local", is_current=True),
                    BranchRef(name="feature/demo", source="local"),
                    BranchRef(name="origin/develop", source="remote", remote="origin"),
                ],
            ),
        ]
    )

    assert [(item.name, item.source, item.current_count, item.total_repos) for item in options] == [
        ("develop", "local", 2, 2),
        ("origin/develop", "remote", 0, 2),
    ]


def test_filter_common_branch_repo_options_ignores_local_only_root_repo() -> None:
    """共同分支计算应忽略没有远端分支的本地根仓库。"""

    eligible, ignored = filter_common_branch_repo_options(
        [
            (
                "__root__",
                ".",
                [
                    BranchRef(name="main", source="local", is_current=True),
                ],
            ),
            (
                "backend-java",
                "backend-java",
                [
                    BranchRef(name="master", source="local", is_current=True),
                    BranchRef(name="origin/master", source="remote", remote="origin"),
                ],
            ),
            (
                "frontend-admin",
                "frontend-admin",
                [
                    BranchRef(name="master", source="local", is_current=True),
                    BranchRef(name="origin/master", source="remote", remote="origin"),
                ],
            ),
        ]
    )

    assert [repo_key for repo_key, _branches in eligible] == ["backend-java", "frontend-admin"]
    assert ignored == ["."]


def test_filter_common_branch_repo_options_keeps_root_repo_when_remote_exists() -> None:
    """根仓库一旦存在远端分支，就仍应参与共同分支计算。"""

    eligible, ignored = filter_common_branch_repo_options(
        [
            (
                "__root__",
                ".",
                [
                    BranchRef(name="main", source="local", is_current=True),
                    BranchRef(name="origin/master", source="remote", remote="origin"),
                ],
            ),
            (
                "backend-java",
                "backend-java",
                [
                    BranchRef(name="master", source="local", is_current=True),
                    BranchRef(name="origin/master", source="remote", remote="origin"),
                ],
            ),
        ]
    )

    assert [repo_key for repo_key, _branches in eligible] == ["__root__", "backend-java"]
    assert ignored == []


def test_prepare_parallel_workspace_copies_full_workdir_and_excludes_generated_dirs(tmp_path: Path) -> None:
    """整目录复刻时应保留普通/嵌套仓库内容，并排除生成物目录。"""

    source_root = tmp_path / "source"
    _init_repo(
        source_root,
        {
            "README.md": "root\n",
            ".gitignore": "target/\nlogs/\n.idea/\n",
        },
    )
    nested_repo = source_root / "service"
    _init_repo(
        nested_repo,
        {
            "src/main.py": "print('demo')\n",
            ".gitignore": "target/\nlogs/\n.idea/\n",
        },
    )
    (source_root / "notes.txt").write_text("需要完整复制\n", encoding="utf-8")
    (source_root / "target").mkdir()
    (source_root / "target" / "app.bin").write_text("bin\n", encoding="utf-8")
    (source_root / "logs").mkdir()
    (source_root / "logs" / "app.log").write_text("log\n", encoding="utf-8")
    (source_root / ".idea").mkdir()
    (source_root / ".idea" / "workspace.xml").write_text("<idea/>\n", encoding="utf-8")
    (source_root / "node_modules").mkdir()
    (source_root / "node_modules" / "demo.js").write_text("console.log('x')\n", encoding="utf-8")

    records = prepare_parallel_workspace(
        workspace_root=tmp_path / "workspace",
        task_id="TASK_0093",
        title="并行创建缺陷修复",
        source_root=source_root,
        selections=[
            RepoBranchSelection(
                repo_key="__root__",
                source_repo_path=source_root,
                selected_ref="HEAD",
                selected_remote=None,
                relative_path=".",
            ),
            RepoBranchSelection(
                repo_key="service",
                source_repo_path=nested_repo,
                selected_ref="HEAD",
                selected_remote=None,
                relative_path="service",
            ),
        ],
    )

    workspace_root = tmp_path / "workspace"
    assert (workspace_root / "notes.txt").read_text(encoding="utf-8") == "需要完整复制\n"
    assert (workspace_root / "service" / "src" / "main.py").exists()
    assert not (workspace_root / "target").exists()
    assert not (workspace_root / "logs").exists()
    assert not (workspace_root / ".idea").exists()
    assert not (workspace_root / "node_modules").exists()
    assert len(records) == 2


def test_prepare_parallel_workspace_skips_fetch_for_local_branch_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本地分支作为基线时，不应额外访问远端。"""

    source_root = tmp_path / "source"
    _init_repo(source_root, {"README.md": "root\n"})

    calls: list[tuple[tuple[str, ...], Path]] = []

    def fake_run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append((tuple(args), Path(cwd)))
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")

    monkeypatch.setattr(parallel_runtime, "_run_git", fake_run_git)

    records = prepare_parallel_workspace(
        workspace_root=tmp_path / "workspace",
        task_id="TASK_0100",
        title="本地分支跳过抓取",
        source_root=source_root,
        selections=[
            RepoBranchSelection(
                repo_key="__root__",
                source_repo_path=source_root,
                selected_ref="develop",
                selected_remote=None,
                relative_path=".",
            )
        ],
    )

    workspace_root = tmp_path / "workspace"
    fetch_calls = [args for args, cwd in calls if args[:3] == ("fetch", "--all", "--prune") and cwd == workspace_root]
    checkout_calls = [args for args, cwd in calls if args[:2] == ("checkout", "-B") and cwd == workspace_root]

    assert fetch_calls == []
    assert checkout_calls == [("checkout", "-B", records[0].task_branch, "develop")]


def test_prepare_parallel_workspace_fetches_only_selected_remote_repos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """仅选中远端分支的仓库需要抓取远端；本地分支仓库应直接检出。"""

    source_root = tmp_path / "source"
    _init_repo(source_root, {"README.md": "root\n"})
    nested_repo = source_root / "service"
    _init_repo(nested_repo, {"service.txt": "nested\n"})

    calls: list[tuple[tuple[str, ...], Path]] = []

    def fake_run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append((tuple(args), Path(cwd)))
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")

    monkeypatch.setattr(parallel_runtime, "_run_git", fake_run_git)

    records = prepare_parallel_workspace(
        workspace_root=tmp_path / "workspace",
        task_id="TASK_0101",
        title="混合本地远端基线",
        source_root=source_root,
        selections=[
            RepoBranchSelection(
                repo_key="__root__",
                source_repo_path=source_root,
                selected_ref="develop",
                selected_remote=None,
                relative_path=".",
            ),
            RepoBranchSelection(
                repo_key="service",
                source_repo_path=nested_repo,
                selected_ref="origin/develop",
                selected_remote="origin",
                relative_path="service",
            ),
        ],
    )

    workspace_root = tmp_path / "workspace"
    service_workspace = workspace_root / "service"
    root_fetch_calls = [args for args, cwd in calls if args[:3] == ("fetch", "--all", "--prune") and cwd == workspace_root]
    service_fetch_calls = [args for args, cwd in calls if args[:3] == ("fetch", "--all", "--prune") and cwd == service_workspace]
    root_checkout_calls = [args for args, cwd in calls if args[:2] == ("checkout", "-B") and cwd == workspace_root]
    service_checkout_calls = [args for args, cwd in calls if args[:2] == ("checkout", "-B") and cwd == service_workspace]

    assert root_fetch_calls == []
    assert service_fetch_calls == [("fetch", "--all", "--prune")]
    assert root_checkout_calls == [("checkout", "-B", records[0].task_branch, "develop")]
    assert service_checkout_calls == [("checkout", "-B", records[1].task_branch, "origin/develop")]
    assert calls.index((service_fetch_calls[0], service_workspace)) < calls.index((service_checkout_calls[0], service_workspace))


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
