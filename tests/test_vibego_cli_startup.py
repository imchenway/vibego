from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import pytest

config = importlib.import_module("vibego_cli.config")
cli_main = importlib.import_module("vibego_cli.main")


class _FakeProcess:
    """模拟 master 子进程，避免测试真实拉起后台进程。"""

    def __init__(self, *, pid: int, poll_result: int | None) -> None:
        self.pid = pid
        self._poll_result = poll_result

    def poll(self) -> int | None:
        """返回预设轮询结果，模拟进程存活或已退出。"""

        return self._poll_result


def _patch_start_prerequisites(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """统一打桩 start 前置依赖，确保测试仅聚焦启动收口逻辑。"""

    monkeypatch.setattr(config, "MASTER_PID_FILE", tmp_path / "master.pid")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "vibe.log")
    monkeypatch.setattr(cli_main, "_load_env_or_fail", lambda: {"MASTER_BOT_TOKEN": "token"})
    monkeypatch.setattr(cli_main, "_ensure_projects_assets", lambda: None)
    monkeypatch.setattr(cli_main, "_find_foreign_processes", lambda _root: [])
    monkeypatch.setattr(cli_main, "_collect_active_workers", lambda: [])
    monkeypatch.setattr(cli_main, "python_version_ok", lambda: True)
    monkeypatch.setattr(cli_main, "check_cli_dependencies", lambda: [])
    monkeypatch.setattr(cli_main, "_find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli_main,
        "_ensure_virtualenv",
        lambda _root: (tmp_path / "python", tmp_path / "pip"),
    )
    monkeypatch.setattr(cli_main, "_build_master_env", lambda _env: {})
    monkeypatch.setattr(cli_main, "_schedule_start_notification", lambda _env: False)
    monkeypatch.setattr(cli_main.time, "sleep", lambda _seconds: None)


def test_command_start_clears_stale_pid_file_when_master_exits_early(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """master 启动即退出时，应清理刚写入的 pid，避免后续误判已启动。"""

    _patch_start_prerequisites(monkeypatch, tmp_path)

    monkeypatch.setattr(
        cli_main.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(pid=4321, poll_result=1),
    )

    with pytest.raises(RuntimeError, match="master 进程启动失败"):
        cli_main.command_start(argparse.Namespace())

    assert not config.MASTER_PID_FILE.exists()


def test_command_start_ignores_dead_master_pid_before_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """若 pid 文件残留但进程已死，start 应自动清理并继续拉起新 master。"""

    _patch_start_prerequisites(monkeypatch, tmp_path)
    config.MASTER_PID_FILE.write_text("99999", encoding="utf-8")
    monkeypatch.setattr(cli_main, "_pid_alive", lambda pid: pid == 4321)

    calls = {"popen": 0}

    def fake_popen(*_args, **_kwargs):
        calls["popen"] += 1
        return _FakeProcess(pid=4321, poll_result=None)

    monkeypatch.setattr(cli_main.subprocess, "Popen", fake_popen)

    cli_main.command_start(argparse.Namespace())

    assert calls["popen"] == 1
    assert config.MASTER_PID_FILE.read_text(encoding="utf-8") == "4321"
