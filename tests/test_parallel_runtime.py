from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import parallel_runtime
from tasks import TaskRecord

from parallel_runtime import (
    BranchRef,
    RepoBranchSelection,
    build_parallel_branch_name,
    commit_parallel_repos,
    collect_common_branch_refs,
    delete_parallel_workspace,
    discover_git_repos,
    filter_common_branch_repo_options,
    get_current_branch_state,
    list_branch_refs,
    merge_parallel_repos,
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


def test_run_git_forces_utf8_replace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """git 子进程应固定使用 utf-8 + replace，避免系统 locale 导致解码崩溃。"""

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(parallel_runtime.subprocess, "run", fake_run)

    result = parallel_runtime._run_git(["status", "--short"], cwd=tmp_path)

    assert result.returncode == 0
    assert captured["args"] == ["git", "status", "--short"]
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
    env = captured["kwargs"]["env"]
    assert "http_proxy" not in env
    assert "https_proxy" not in env
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert "all_proxy" not in env
    assert "ALL_PROXY" not in env


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


def _current_branch(repo: Path) -> str:
    """读取仓库当前分支名，避免测试依赖 main/master 默认值。"""

    return _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()


def _make_task(task_id: str = "TASK_0100", title: str = "并行工作区修复") -> TaskRecord:
    """构造并行提交测试所需的最小任务对象。"""

    return TaskRecord(
        id=task_id,
        project_slug="demo",
        title=title,
        status="research",
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description="测试并行运行时",
        parent_id=None,
        root_id=task_id,
        depth=0,
        lineage="0100",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )


def _init_bare_remote(remote: Path) -> None:
    """初始化 bare 远端，供 push/merge 场景验证。"""

    remote.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)


def test_build_parallel_branch_name_uses_default_prefix() -> None:
    """未指定前缀时，应统一落到默认分支组。"""

    assert build_parallel_branch_name("TASK_0108", "类目编码校验") == "vibego/TASK_0108-类目编码校验"


def test_build_parallel_branch_name_uses_custom_prefix() -> None:
    """指定前缀时，应生成 prefix/TASK_... 结构。"""

    assert (
        build_parallel_branch_name("TASK_0109", "类目编码校验", prefix="TRADE114")
        == "TRADE114/TASK_0109-类目编码校验"
    )


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
    assert not (workspace_root / ".idea" / "workspace.xml").exists()
    assert (workspace_root / ".idea" / "vcs.xml").exists()
    assert not (workspace_root / "node_modules").exists()
    assert len(records) == 2


def test_prepare_parallel_workspace_links_root_docs_to_source_docs(tmp_path: Path) -> None:
    """并行工作区根级 docs 应直接共享真实项目 docs，而不是保留副本。"""

    source_root = tmp_path / "source"
    _init_repo(source_root, {"README.md": "root\n"})
    (source_root / "docs").mkdir(parents=True, exist_ok=True)
    (source_root / "docs" / "TASK_0079.md").write_text("真实项目文档\n", encoding="utf-8")

    prepare_parallel_workspace(
        workspace_root=tmp_path / "workspace",
        task_id="TASK_0079",
        title="共享真实 docs",
        source_root=source_root,
        selections=[
            RepoBranchSelection(
                repo_key="__root__",
                source_repo_path=source_root,
                selected_ref="HEAD",
                selected_remote=None,
                relative_path=".",
            )
        ],
    )

    workspace_docs = tmp_path / "workspace" / "docs"
    assert workspace_docs.is_symlink()
    assert workspace_docs.resolve() == (source_root / "docs").resolve()
    assert (workspace_docs / "TASK_0079.md").read_text(encoding="utf-8") == "真实项目文档\n"


def test_prepare_parallel_workspace_creates_missing_source_docs_before_linking(tmp_path: Path) -> None:
    """真实项目缺少 docs 时，应先自动创建，再让工作区 docs 指向它。"""

    source_root = tmp_path / "source"
    _init_repo(source_root, {"README.md": "root\n"})

    prepare_parallel_workspace(
        workspace_root=tmp_path / "workspace",
        task_id="TASK_0080",
        title="自动创建 docs",
        source_root=source_root,
        selections=[
            RepoBranchSelection(
                repo_key="__root__",
                source_repo_path=source_root,
                selected_ref="HEAD",
                selected_remote=None,
                relative_path=".",
            )
        ],
    )

    source_docs = source_root / "docs"
    workspace_docs = tmp_path / "workspace" / "docs"
    assert source_docs.exists()
    assert source_docs.is_dir()
    assert workspace_docs.is_symlink()
    assert workspace_docs.resolve() == source_docs.resolve()


def test_prepare_parallel_workspace_generates_idea_vcs_mappings_for_nested_repos(tmp_path: Path) -> None:
    """并行工作区应补齐 IDEA 的多仓库 Git 映射，便于直接查看所有分支。"""

    source_root = tmp_path / "source"
    _init_repo(source_root, {"README.md": "root\n"})
    nested_repo = source_root / "service"
    _init_repo(nested_repo, {"src/main.py": "print('demo')\n"})

    prepare_parallel_workspace(
        workspace_root=tmp_path / "workspace",
        task_id="TASK_0107",
        title="生成 IDEA Git 映射",
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

    vcs_xml = (tmp_path / "workspace" / ".idea" / "vcs.xml").read_text(encoding="utf-8")

    assert '<mapping directory="$PROJECT_DIR$" vcs="Git" />' in vcs_xml
    assert '<mapping directory="$PROJECT_DIR$/service" vcs="Git" />' in vcs_xml


def test_delete_parallel_workspace_keeps_shared_source_docs(tmp_path: Path) -> None:
    """删除并行工作区时，只能删除 docs 链接本身，不能删除真实项目 docs。"""

    source_docs = tmp_path / "source" / "docs"
    source_docs.mkdir(parents=True, exist_ok=True)
    (source_docs / "TASK_0079.md").write_text("保留\n", encoding="utf-8")

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "docs").symlink_to(source_docs, target_is_directory=True)

    delete_parallel_workspace(workspace_root=workspace_root)

    assert not workspace_root.exists()
    assert source_docs.exists()
    assert (source_docs / "TASK_0079.md").read_text(encoding="utf-8") == "保留\n"


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


def test_prepare_parallel_workspace_preserves_dirty_changes_for_current_local_branch(tmp_path: Path) -> None:
    """当前分支创建并行任务分支时，应保留未提交改动进入并行目录。"""

    source_root = tmp_path / "source"
    _init_repo(source_root, {"README.md": "root\n"})
    current_branch = _current_branch(source_root)
    (source_root / "README.md").write_text("dirty root\n", encoding="utf-8")
    (source_root / "draft.txt").write_text("untracked\n", encoding="utf-8")

    records = prepare_parallel_workspace(
        workspace_root=tmp_path / "workspace",
        task_id="TASK_0102",
        title="保留当前分支未提交改动",
        source_root=source_root,
        selections=[
            RepoBranchSelection(
                repo_key="__root__",
                source_repo_path=source_root,
                selected_ref=current_branch,
                selected_remote=None,
                relative_path=".",
            )
        ],
    )

    workspace_root = tmp_path / "workspace"
    status = _git(workspace_root, "status", "--short", "--branch").stdout

    assert _current_branch(workspace_root) == records[0].task_branch
    assert (workspace_root / "README.md").read_text(encoding="utf-8") == "dirty root\n"
    assert (workspace_root / "draft.txt").read_text(encoding="utf-8") == "untracked\n"
    assert "README.md" in status
    assert "draft.txt" in status


def test_prepare_parallel_workspace_drops_dirty_changes_for_other_branch(tmp_path: Path) -> None:
    """切到非当前基线分支时，不应把当前工作区未提交改动带入并行目录。"""

    source_root = tmp_path / "source"
    _init_repo(source_root, {"README.md": "root\n"})
    current_branch = _current_branch(source_root)
    _git(source_root, "checkout", "-b", "develop")
    _git(source_root, "checkout", current_branch)
    (source_root / "README.md").write_text("dirty root\n", encoding="utf-8")
    (source_root / "draft.txt").write_text("untracked\n", encoding="utf-8")

    records = prepare_parallel_workspace(
        workspace_root=tmp_path / "workspace",
        task_id="TASK_0103",
        title="其他基线不带未提交改动",
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
    status = _git(workspace_root, "status", "--short", "--branch").stdout

    assert _current_branch(workspace_root) == records[0].task_branch
    assert (workspace_root / "README.md").read_text(encoding="utf-8") == "root\n"
    assert not (workspace_root / "draft.txt").exists()
    assert status.strip() == f"## {records[0].task_branch}"


def test_commit_parallel_repos_skips_meta_only_parent_and_keeps_real_child_commit(tmp_path: Path) -> None:
    """父仓库仅包含嵌套仓库指针变化时，应跳过空壳提交；真实子仓库仍正常本地提交。"""

    root = tmp_path / "workspace"
    _init_repo(root, {"README.md": "root\n"})
    child = root / "service"
    _init_repo(child, {"service.py": "print('v1')\n"})

    _git(root, "add", "service")
    _git(root, "commit", "-m", "track child")

    task_branch = "TASK_0104-meta"
    _git(root, "checkout", "-b", task_branch)
    _git(child, "checkout", "-b", task_branch)
    (child / "service.py").write_text("print('v2')\n", encoding="utf-8")

    result = commit_parallel_repos(
        task=_make_task(task_id="TASK_0104", title="跳过空壳父仓库提交"),
        repos=[
            parallel_runtime.ParallelRepoRecord(
                repo_key="__root__",
                source_repo_path=str(root),
                workspace_repo_path=str(root),
                selected_base_ref=_current_branch(root),
                selected_remote=None,
                task_branch=task_branch,
            ),
            parallel_runtime.ParallelRepoRecord(
                repo_key="service",
                source_repo_path=str(child),
                workspace_repo_path=str(child),
                selected_base_ref=_current_branch(child),
                selected_remote=None,
                task_branch=task_branch,
            ),
        ],
    )

    by_repo = {item.repo_key: item for item in result.results}

    assert by_repo["__root__"].ok is True
    assert by_repo["__root__"].status == "skipped"
    assert "元信息" in by_repo["__root__"].message
    assert by_repo["service"].ok is True
    assert by_repo["service"].status == "committed"
    assert "本地提交" in by_repo["service"].message
    assert _git(child, "log", "--oneline", "-n", "1").stdout.split()[1].startswith("chore(TASK_0104):")


def test_commit_parallel_repos_pushes_to_single_available_remote(tmp_path: Path) -> None:
    """未显式记录 selected_remote 时，若仓库仅有一个 remote，仍应正常推送任务分支。"""

    remote = tmp_path / "remote.git"
    _init_bare_remote(remote)

    repo = tmp_path / "repo"
    _init_repo(repo, {"README.md": "demo\n"})
    base_branch = _current_branch(repo)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", base_branch)
    task_branch = "TASK_0105-push"
    _git(repo, "checkout", "-b", task_branch)
    (repo / "README.md").write_text("demo v2\n", encoding="utf-8")

    result = commit_parallel_repos(
        task=_make_task(task_id="TASK_0105", title="唯一远端推送"),
        repos=[
            parallel_runtime.ParallelRepoRecord(
                repo_key="repo",
                source_repo_path=str(repo),
                workspace_repo_path=str(repo),
                selected_base_ref=base_branch,
                selected_remote=None,
                task_branch=task_branch,
            )
        ],
    )

    assert result.results[0].ok is True
    assert result.results[0].status == "pushed"
    remote_refs = _git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.splitlines()
    assert task_branch in remote_refs


def test_merge_parallel_repos_skips_repo_without_remote(tmp_path: Path) -> None:
    """无远端仓库在自动合并阶段应跳过，而不是默认 fetch/push origin 失败。"""

    repo = tmp_path / "repo"
    _init_repo(repo, {"README.md": "demo\n"})
    base_branch = _current_branch(repo)
    task_branch = "TASK_0106-merge"
    _git(repo, "checkout", "-b", task_branch)
    (repo / "README.md").write_text("demo v2\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "task change")

    result = merge_parallel_repos(
        task=_make_task(task_id="TASK_0106", title="无远端跳过自动合并"),
        repos=[
            parallel_runtime.ParallelRepoRecord(
                repo_key="repo",
                source_repo_path=str(repo),
                workspace_repo_path=str(repo),
                selected_base_ref=base_branch,
                selected_remote=None,
                task_branch=task_branch,
            )
        ],
    )

    assert result.results[0].ok is True
    assert result.results[0].status == "skipped"
    assert "远端" in result.results[0].message


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
