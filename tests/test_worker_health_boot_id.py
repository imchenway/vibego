"""
测试 worker 健康检查的 boot_id 机制。

目的：
- run_bot.log 采用追加写入（>>），历史 “Telegram 连接正常” 可能导致误判
- 通过 boot_id 将本次启动的握手标记与历史日志隔离
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

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


def test_project_slug_drops_shell_unsafe_punctuation_for_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """带标点的项目 slug 应与 run_bot.sh 一样归一到同一个运行目录。"""

    cfg = master.ProjectConfig.from_dict(
        {
            "bot_name": "Zeus.",
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "project_slug": "Zeus.",
            "default_model": "codex",
            "workdir": str(tmp_path),
            "allowed_chat_id": 100,
        }
    )
    state_path = tmp_path / "state.json"
    manager = master.MasterManager(
        [cfg],
        state_store=master.StateStore(state_path, {cfg.project_slug: cfg}),
    )
    log_root = tmp_path / "logs"
    monkeypatch.setattr(master, "LOG_ROOT_PATH", log_root)

    pid_path, run_log = manager._worker_runtime_paths(cfg, "codex")

    assert cfg.project_slug == "zeus"
    assert pid_path == log_root / "codex" / "zeus" / "bot.pid"
    assert run_log == log_root / "codex" / "zeus" / "run_bot.log"


def test_state_store_migrates_legacy_punctuated_slug_from_disk(tmp_path: Path) -> None:
    """状态文件中的旧 zeus. key 应迁移到新 zeus key，避免丢失运行态。"""

    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "zeus.": {
                    "model": "codex",
                    "status": "running",
                    "chat_id": 100,
                    "actual_username": "HyphaZeusBot",
                    "telegram_user_id": 8439549268,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = master.ProjectConfig.from_dict(
        {
            "bot_name": "Zeus.",
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "project_slug": "Zeus.",
            "default_model": "codex",
            "workdir": str(tmp_path),
        }
    )

    store = master.StateStore(state_path, {cfg.project_slug: cfg})

    assert cfg.project_slug == "zeus"
    assert "zeus." not in store.data
    assert store.data["zeus"].status == "running"
    assert store.data["zeus"].chat_id == 100
    assert store.data["zeus"].actual_username == "HyphaZeusBot"


def test_state_store_migrates_slug_alias_from_repository_repair(tmp_path: Path) -> None:
    """展示名迁移后的新 slug 应继承旧自定义 slug 下的运行态。"""

    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cckgwmsappcore": {
                    "model": "codex",
                    "status": "running",
                    "chat_id": 100,
                    "actual_username": "CckgWmsAppCoreBot",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = master.ProjectConfig.from_dict(
        {
            "bot_name": "CckgWmsBot",
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "project_slug": "wms",
            "name": "WMS",
            "default_model": "codex",
            "workdir": str(tmp_path),
        }
    )

    store = master.StateStore(
        state_path,
        {cfg.project_slug: cfg},
        slug_aliases={"cckgwmsappcore": "wms"},
    )

    assert "cckgwmsappcore" not in store.data
    assert store.data["wms"].status == "running"
    assert store.data["wms"].chat_id == 100
    assert store.data["wms"].actual_username == "CckgWmsAppCoreBot"


@pytest.mark.asyncio
async def test_run_worker_keeps_starting_when_health_timeout_but_pid_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """健康检查超时时若 worker 进程仍存活，项目列表不能回落成“未启动”。"""

    manager = _build_manager(tmp_path)
    cfg = manager.require_project("test")
    log_root = tmp_path / "logs"
    pid_dir = log_root / "codex" / cfg.project_slug
    pid_dir.mkdir(parents=True)

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        # 模拟 run_bot.sh 返回后后台 worker 已写出 pid，但握手日志尚未出现。
        (pid_dir / "bot.pid").write_text("12345\n", encoding="utf-8")
        return DummyProcess()

    monkeypatch.setattr(master, "LOG_ROOT_PATH", log_root)
    monkeypatch.setattr(master, "_list_tmux_session_names", lambda: ["vibe-test"])
    monkeypatch.setattr(master, "ensure_codex_project_trust", lambda *args, **kwargs: None)
    monkeypatch.setattr(master.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(manager, "_health_check_worker", AsyncMock(return_value="握手超时"))
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: pid == 12345)

    with pytest.raises(RuntimeError, match="握手超时"):
        await manager.run_worker(cfg)

    state = manager.state_store.data[cfg.project_slug]
    assert state.status == "starting"
    assert state.model == "codex"


def test_projects_overview_treats_starting_worker_as_non_startable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启动中项目应展示停止入口，避免用户重复点击启动造成会话冲突。"""

    manager = _build_manager(tmp_path)
    cfg = manager.require_project("test")
    manager.state_store.update("test", status="starting")
    log_root = tmp_path / "logs"
    pid_dir = log_root / "codex" / cfg.project_slug
    pid_dir.mkdir(parents=True)
    (pid_dir / "bot.pid").write_text("12345\n", encoding="utf-8")

    monkeypatch.setattr(master, "LOG_ROOT_PATH", log_root)
    monkeypatch.setattr(master, "_list_tmux_session_names", lambda: ["vibe-test"])
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: pid == 12345)

    text, markup = master._projects_overview(manager)

    assert text == "请选择操作："
    assert markup is not None
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert any(label.startswith("⏳ 启动中") for label in labels)
    assert not any(label.startswith("▶️ 启动 (codex)") for label in labels)


def test_projects_overview_marks_running_worker_degraded_when_tmux_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 仍存活但模型 tmux 主会话缺失时，应展示修复入口而不是停止按钮。"""

    manager = _build_manager(tmp_path)
    cfg = manager.require_project("test")
    manager.state_store.update("test", status="running")
    log_root = tmp_path / "logs"
    pid_dir = log_root / "codex" / cfg.project_slug
    pid_dir.mkdir(parents=True)
    (pid_dir / "bot.pid").write_text("12345\n", encoding="utf-8")
    (pid_dir / "run_bot.log").write_text("xxx\nTelegram 连接正常\n", encoding="utf-8")

    monkeypatch.setattr(master, "LOG_ROOT_PATH", log_root)
    monkeypatch.setattr(master, "_list_tmux_session_names", lambda: [])
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: pid == 12345)

    text, markup = master._projects_overview(manager)

    assert text == "请选择操作："
    assert manager.state_store.data[cfg.project_slug].status == "degraded"
    assert markup is not None
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert any(label.startswith("⚠️ 修复/重启") for label in labels)
    assert not any(label.startswith("⛔️ 停止 (") for label in labels)


def test_projects_overview_marks_stale_running_worker_stopped_when_pid_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """state 仍为 running 但 pid 已不存在时，项目列表刷新应降级为可启动。"""

    manager = _build_manager(tmp_path)
    cfg = manager.require_project("test")
    manager.state_store.update("test", status="running")
    log_root = tmp_path / "logs"
    monkeypatch.setattr(master, "LOG_ROOT_PATH", log_root)

    text, markup = master._projects_overview(manager)

    assert text == "请选择操作："
    assert manager.state_store.data[cfg.project_slug].status == "stopped"
    assert markup is not None
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert any(label.startswith("▶️ 启动") for label in labels)


@pytest.mark.asyncio
async def test_run_worker_repairs_degraded_worker_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """点击 degraded 项目的修复入口时，应先清理旧 worker 再重新启动。"""

    manager = _build_manager(tmp_path)
    cfg = manager.require_project("test")
    manager.state_store.update("test", status="degraded")
    log_root = tmp_path / "logs"
    pid_dir = log_root / "codex" / cfg.project_slug
    pid_dir.mkdir(parents=True)
    (pid_dir / "bot.pid").write_text("12345\n", encoding="utf-8")

    events: list[str] = []

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_stop_worker(_cfg: master.ProjectConfig, *, update_state: bool = True):
        events.append("stop")
        if update_state:
            manager.state_store.update(_cfg.project_slug, status="stopped", boot_id="")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        events.append("launch")
        return DummyProcess()

    monkeypatch.setattr(master, "LOG_ROOT_PATH", log_root)
    monkeypatch.setattr(master, "_list_tmux_session_names", lambda: [])
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: pid == 12345)
    monkeypatch.setattr(master, "ensure_codex_project_trust", lambda *args, **kwargs: None)
    monkeypatch.setattr(master.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(manager, "stop_worker", fake_stop_worker)
    monkeypatch.setattr(manager, "_health_check_worker", AsyncMock(return_value=None))

    chosen = await manager.run_worker(cfg)

    assert chosen == "codex"
    assert events[:2] == ["stop", "launch"]


def test_reconcile_worker_state_marks_healthy_alive_worker_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """项目列表刷新前应以存活 pid + 握手日志纠正 stale stopped 状态。"""

    manager = _build_manager(tmp_path)
    cfg = manager.require_project("test")
    manager.state_store.update("test", status="stopped")
    log_root = tmp_path / "logs"
    pid_dir = log_root / "codex" / cfg.project_slug
    pid_dir.mkdir(parents=True)
    (pid_dir / "bot.pid").write_text("12345\n", encoding="utf-8")
    (pid_dir / "run_bot.log").write_text("xxx\nTelegram 连接正常\n", encoding="utf-8")

    monkeypatch.setattr(master, "LOG_ROOT_PATH", log_root)
    monkeypatch.setattr(master, "_list_tmux_session_names", lambda: ["vibe-test"])
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: pid == 12345)

    manager.reconcile_worker_states()

    assert manager.state_store.data[cfg.project_slug].status == "running"


def test_worker_health_timeout_default_covers_worker_connectivity_timeout() -> None:
    """master 健康检查默认等待时间不能短于 worker 自身 Telegram 握手超时。"""

    assert master.WORKER_HEALTH_TIMEOUT >= 35.0


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
