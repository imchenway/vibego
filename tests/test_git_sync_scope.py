from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAMES = (
    "git_pull_all.sh",
    "git_push_all.sh",
    "git_sync_all.sh",
)


def _run_script(script_name: str, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("未检测到 bash")

    command = [bash_bin, str(ROOT / "scripts" / script_name)]
    if script_name == "git_sync_all.sh":
        command.extend(("--parallel", "1"))
    command.extend(args)
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_git_all_scripts_default_to_current_working_directory(
    tmp_path: Path,
    script_name: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _run_script(script_name, workspace, "--max-depth", "0", "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"遍历目录: {workspace.resolve()}" in result.stdout


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_git_all_scripts_reject_explicit_empty_directory(
    tmp_path: Path,
    script_name: str,
) -> None:
    result = _run_script(
        script_name,
        tmp_path,
        "--dir",
        "",
        "--max-depth",
        "0",
        "--dry-run",
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "--dir 不能为空" in output
    assert "遍历目录:" not in output


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_git_all_scripts_reject_missing_directory_value(
    tmp_path: Path,
    script_name: str,
) -> None:
    result = _run_script(script_name, tmp_path, "--dir")

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "--dir 不能为空" in output
    assert "遍历目录:" not in output


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_git_all_scripts_do_not_embed_a_machine_global_repository_root(script_name: str) -> None:
    script_text = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")

    assert "/Users/david/hypha" not in script_text
    assert "当前工作目录" in script_text
