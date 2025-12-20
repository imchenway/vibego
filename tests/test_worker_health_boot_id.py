"""
测试 worker 健康检查的 boot_id 机制。

目的：
- run_bot.log 采用追加写入（>>），历史 “Telegram 连接正常” 可能导致误判
- 通过 boot_id 将本次启动的握手标记与历史日志隔离
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import master


@pytest.fixture(autouse=True)
def reset_state():
    """重置全局状态，避免其他测试残留影响本用例。"""

    master.PROJECT_WIZARD_SESSIONS.clear()
    master.reset_project_wizard_lock()
    yield
    master.PROJECT_WIZARD_SESSIONS.clear()
    master.reset_project_wizard_lock()
    master.PROJECT_REPOSITORY = None
    master.MANAGER = None


def _build_manager(tmp_path: Path) -> master.MasterManager:
    """构造最小可用的 MasterManager 实例。"""

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    projects_path = config_dir / "projects.json"
    payload = [
        {
            "bot_name": "TestBot",
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "project_slug": "test",
            "default_model": "codex",
            "workdir": str(tmp_path),
            "allowed_chat_id": 100,
        }
    ]
    projects_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    configs = [master.ProjectConfig.from_dict(item) for item in payload]
    state_path = tmp_path / "state.json"
    store = master.StateStore(state_path, {cfg.project_slug: cfg for cfg in configs})
    return master.MasterManager(configs, state_store=store)


def test_log_contains_handshake_without_boot_id(tmp_path: Path) -> None:
    """不传 boot_id 时，只要包含握手标记就视为健康。"""

    manager = _build_manager(tmp_path)
    log_path = tmp_path / "run_bot.log"
    log_path.write_text("xxx\nTelegram 连接正常\n", encoding="utf-8")
    assert manager._log_contains_handshake(log_path) is True


def test_log_contains_handshake_with_boot_id_requires_marker(tmp_path: Path) -> None:
    """传入 boot_id 时，必须先出现对应 boot_id 行，否则不能误判为健康。"""

    manager = _build_manager(tmp_path)
    log_path = tmp_path / "run_bot.log"
    log_path.write_text("Telegram 连接正常\n", encoding="utf-8")
    assert manager._log_contains_handshake(log_path, boot_id="abc") is False


def test_log_contains_handshake_with_boot_id_must_be_after_boot_id(tmp_path: Path) -> None:
    """握手标记出现在 boot_id 之前时，不应视为当前启动的握手成功。"""

    manager = _build_manager(tmp_path)
    log_path = tmp_path / "run_bot.log"
    token = f"{master.WORKER_BOOT_ID_LOG_PREFIX}abc"
    log_path.write_text(f"Telegram 连接正常\n{token}\n", encoding="utf-8")
    assert manager._log_contains_handshake(log_path, boot_id="abc") is False


def test_log_contains_handshake_with_boot_id_detects_after_marker(tmp_path: Path) -> None:
    """boot_id 之后出现握手标记时，应视为健康。"""

    manager = _build_manager(tmp_path)
    log_path = tmp_path / "run_bot.log"
    token = f"{master.WORKER_BOOT_ID_LOG_PREFIX}abc"
    log_path.write_text(f"{token}\nxxx\nTelegram 连接正常\n", encoding="utf-8")
    assert manager._log_contains_handshake(log_path, boot_id="abc") is True


def test_log_contains_handshake_ignores_previous_boot_id(tmp_path: Path) -> None:
    """当日志包含多次启动记录时，应以当前 boot_id 为准，忽略旧握手。"""

    manager = _build_manager(tmp_path)
    log_path = tmp_path / "run_bot.log"
    old_token = f"{master.WORKER_BOOT_ID_LOG_PREFIX}old"
    new_token = f"{master.WORKER_BOOT_ID_LOG_PREFIX}new"
    log_path.write_text(
        f"{old_token}\nTelegram 连接正常\n{new_token}\n",
        encoding="utf-8",
    )
    assert manager._log_contains_handshake(log_path, boot_id="new") is False

    log_path.write_text(
        f"{old_token}\nTelegram 连接正常\n{new_token}\nTelegram 连接正常\n",
        encoding="utf-8",
    )
    assert manager._log_contains_handshake(log_path, boot_id="new") is True

