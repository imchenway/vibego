from __future__ import annotations

import os
from pathlib import Path
import importlib

import pytest

config = importlib.import_module("vibego_cli.config")
cli_main = importlib.import_module("vibego_cli.main")


def _bin_dir(venv_dir: Path) -> Path:
    """返回 venv 的 bin/Scripts 目录。"""

    return venv_dir / ("Scripts" if os.name == "nt" else "bin")


def test_ensure_virtualenv_recreates_when_python_or_pip_broken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """当 runtime venv 的 python/pip 断链时应自动重建，避免启动卡死。"""

    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(config, "RUNTIME_DIR", runtime_dir)

    venv_dir = runtime_dir / "venv"
    bin_dir = _bin_dir(venv_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)

    # 模拟“Homebrew 升级 Python 后 venv 指向旧 Cellar 路径”的断链场景：
    # - python 存在但为断链
    # - pip 直接缺失
    broken_target = tmp_path / "missing" / "python3.11"
    (bin_dir / "python3.11").symlink_to(broken_target)
    (bin_dir / "python").symlink_to("python3.11")

    calls: dict[str, int] = {"install": 0}

    def fake_install(_req_file: Path, *, pip_executable: Path) -> None:
        assert pip_executable.name in {"pip", "pip.exe", "pip3", "pip3.11"}
        calls["install"] += 1

    monkeypatch.setattr(cli_main, "install_requirements", fake_install)

    python_exec, pip_exec = cli_main._ensure_virtualenv(tmp_path)
    assert python_exec.exists()
    assert pip_exec.exists()
    assert calls["install"] == 1


def test_ensure_virtualenv_keeps_existing_when_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """当 runtime venv 完整时不应误删目录，避免不必要的重装。"""

    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(config, "RUNTIME_DIR", runtime_dir)

    # 先生成 requirements 副本，再写 marker，确保 marker 时间戳更新，跳过依赖安装。
    req_file = config.ensure_worker_requirements_copy()

    venv_dir = runtime_dir / "venv"
    bin_dir = _bin_dir(venv_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    python_path = bin_dir / "python"
    pip_path = bin_dir / "pip"
    python_path.write_text("# dummy python\n", encoding="utf-8")
    pip_path.write_text("# dummy pip\n", encoding="utf-8")

    marker = venv_dir / ".requirements.timestamp"
    marker.touch()
    os.utime(marker, (req_file.stat().st_mtime + 10, req_file.stat().st_mtime + 10))

    def fail_install(*_args, **_kwargs) -> None:
        raise AssertionError("不应触发 install_requirements")

    monkeypatch.setattr(cli_main, "install_requirements", fail_install)

    python_exec, pip_exec = cli_main._ensure_virtualenv(tmp_path)
    assert python_exec == python_path
    assert pip_exec == pip_path
    assert python_path.read_text(encoding="utf-8") == "# dummy python\n"
    assert pip_path.read_text(encoding="utf-8") == "# dummy pip\n"
