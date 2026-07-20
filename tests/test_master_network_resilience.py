from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientError
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import master
from project_repository import ProjectRepository


@pytest.fixture(autouse=True)
def reset_state():
    """重置 master 的全局状态，避免测试之间互相污染。"""

    master.PROJECT_WIZARD_SESSIONS.clear()
    master.reset_project_wizard_lock()
    yield
    master.PROJECT_WIZARD_SESSIONS.clear()
    master.reset_project_wizard_lock()
    master.PROJECT_REPOSITORY = None
    master.MANAGER = None


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> ProjectRepository:
    """构造最小可运行的项目仓库。"""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    json_path = config_dir / "projects.json"
    initial = [
        {
            "bot_name": "SampleBot",
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "project_slug": "sample",
            "default_model": "codex",
            "workdir": str(tmp_path),
            "allowed_chat_id": 1,
        }
    ]
    json_path.write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8")
    db_path = config_dir / "master.db"
    repository = ProjectRepository(db_path, json_path)
    master.PROJECT_REPOSITORY = repository
    monkeypatch.setenv("MASTER_ADMIN_IDS", "1")
    return repository


def _build_manager(repo: ProjectRepository, tmp_path: Path) -> master.MasterManager:
    records = repo.list_projects()
    configs = [master.ProjectConfig.from_dict(record.to_dict()) for record in records]
    state_path = tmp_path / "state.json"
    state_store = master.StateStore(state_path, {cfg.project_slug: cfg for cfg in configs})
    return master.MasterManager(configs, state_store=state_store)


def _build_fsm_state(chat_id: int = 1, user_id: int = 1) -> tuple[MemoryStorage, FSMContext]:
    storage = MemoryStorage()
    key = StorageKey(bot_id=0, chat_id=chat_id, user_id=user_id)
    return storage, FSMContext(storage=storage, key=key)


class DummyMessage:
    """模拟 Telegram Message。"""

    def __init__(self, chat_id: int = 1) -> None:
        self.text = ""
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=chat_id, username="tester")
        self.message_id = 1
        self.bot = AsyncMock()
        self._answers: list[tuple[str, dict]] = []
        self._edits: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs):
        self._answers.append((text, kwargs))

    async def edit_text(self, text: str, **kwargs):
        self._edits.append((text, kwargs))


class DummyCallback:
    """模拟 Telegram CallbackQuery。"""

    def __init__(self, data: str, chat_id: int = 1, message: DummyMessage | None = None) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=chat_id)
        self.message = message or DummyMessage(chat_id)
        self._answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self._answers.append((text, show_alert))


class TelegramLimitMessage(DummyMessage):
    """模拟 Telegram 单条消息长度限制。"""

    async def answer(self, text: str, **kwargs):
        if len(text) > 4096:
            raise TelegramBadRequest(
                method="sendMessage",
                message="Bad Request: message is too long",
            )
        await super().answer(text, **kwargs)


def test_master_polling_retries_after_startup_network_timeout(monkeypatch):
    """Master polling 启动阶段遇到 Telegram 网络超时不应直接退出进程。"""

    class DummyDispatcher:
        """第一次 polling 超时，第二次成功退出，验证重试闭环。"""

        def __init__(self) -> None:
            self.calls = 0

        async def start_polling(self, bot):
            self.calls += 1
            if self.calls == 1:
                raise TelegramNetworkError(
                    method="getMe",
                    message="Request timeout error",
                )
            return None

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(master, "MASTER_POLLING_RETRY_DELAY", 0.01)
    monkeypatch.setattr(master.asyncio, "sleep", fake_sleep)
    dispatcher = DummyDispatcher()

    asyncio.run(master._run_master_polling(dispatcher, object()))

    assert dispatcher.calls == 2
    assert sleep_calls == [0.01]


def test_master_polling_retries_after_raw_aiohttp_socks_proxy_timeout(monkeypatch):
    """aiohttp-socks 未包装的代理超时也必须保活并重试。"""

    class ProxyTimeoutError(Exception):
        """模拟 aiohttp_socks._errors.ProxyTimeoutError 的独立异常层级。"""

    ProxyTimeoutError.__module__ = "aiohttp_socks._errors"

    class DummyDispatcher:
        def __init__(self) -> None:
            self.calls = 0

        async def start_polling(self, bot):
            self.calls += 1
            if self.calls == 1:
                raise ProxyTimeoutError("Proxy connection timed out: 60")
            return None

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(master, "MASTER_POLLING_RETRY_DELAY", 0.01)
    monkeypatch.setattr(master.asyncio, "sleep", fake_sleep)
    dispatcher = DummyDispatcher()

    asyncio.run(master._run_master_polling(dispatcher, object()))

    assert dispatcher.calls == 2
    assert sleep_calls == [0.01]


def test_master_polling_propagates_non_network_error(monkeypatch):
    """非网络异常必须继续失败，避免重试循环掩盖程序错误。"""

    class DummyDispatcher:
        async def start_polling(self, bot):
            raise RuntimeError("programming error")

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(master.asyncio, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="programming error"):
        asyncio.run(master._run_master_polling(DummyDispatcher(), object()))

    assert sleep_calls == []


def test_run_action_ack_before_run_worker(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """
    TDD 场景：点击项目启动时应先回调应答，再执行耗时启动。

    目标：避免 callback query 因耗时操作超时，导致用户看到“无响应”。
    """

    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo

    callback = DummyCallback("project:run:sample")
    _, fsm_state = _build_fsm_state()

    ack_observed = {"before_run": False}

    async def run_worker_override(cfg: master.ProjectConfig, model: str | None = None) -> str:
        # 记录 run_worker 执行前 callback 是否已答复。
        ack_observed["before_run"] = bool(callback._answers)
        await asyncio.sleep(0)
        return model or cfg.default_model

    run_mock = AsyncMock(side_effect=run_worker_override)
    monkeypatch.setattr(manager, "run_worker", run_mock)

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())

    run_mock.assert_awaited_once()
    assert ack_observed["before_run"], "应先 callback.answer 再执行 run_worker"


def test_run_action_refreshes_overview_when_worker_left_starting_after_failure(
    repo: ProjectRepository,
    tmp_path: Path,
    monkeypatch,
):
    """
    TDD 场景：启动流程报健康检查超时时，只要 worker 仍处于启动中，
    原项目列表消息也要刷新，避免用户继续看到“启动”按钮并重复点击。
    """

    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    cfg = manager.require_project("sample")
    log_root = tmp_path / "logs"
    pid_dir = log_root / "codex" / cfg.project_slug
    pid_dir.mkdir(parents=True)
    (pid_dir / "bot.pid").write_text("12345\n", encoding="utf-8")

    async def run_worker_override(cfg: master.ProjectConfig, model: str | None = None) -> str:
        manager.state_store.update(cfg.project_slug, status="starting")
        raise RuntimeError("握手超时")

    monkeypatch.setattr(manager, "run_worker", AsyncMock(side_effect=run_worker_override))
    monkeypatch.setattr(master, "LOG_ROOT_PATH", log_root)
    monkeypatch.setattr(master, "_list_tmux_session_names", lambda: ["vibe-sample"])
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: pid == 12345)

    callback = DummyCallback("project:run:sample")
    _, fsm_state = _build_fsm_state()

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())

    assert callback.message._answers[-1][0] == "操作失败: 握手超时"
    assert callback.message._edits, "失败后仍应刷新项目列表，展示最新运行态"
    _, kwargs = callback.message._edits[-1]
    labels = [button.text for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert any(label.startswith("⏳ 启动中") for label in labels)


def test_run_action_truncates_long_failure_message_before_reply(
    repo: ProjectRepository,
    tmp_path: Path,
    monkeypatch,
):
    """启动失败详情很长时，用户侧错误提示必须截断并继续刷新项目列表。"""

    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo

    async def run_worker_override(cfg: master.ProjectConfig, model: str | None = None) -> str:
        manager.state_store.update(cfg.project_slug, status="starting")
        raise RuntimeError("启动失败详情：" + ("x" * 10000))

    monkeypatch.setattr(manager, "run_worker", AsyncMock(side_effect=run_worker_override))

    callback = DummyCallback(
        "project:run:sample",
        message=TelegramLimitMessage(chat_id=1),
    )
    _, fsm_state = _build_fsm_state()

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())

    assert callback.message._answers, "应发送截断后的错误提示"
    assert len(callback.message._answers[-1][0]) <= 4096
    assert callback.message._edits, "错误提示发送成功后仍应刷新项目列表"


def test_truncate_text_for_telegram_keeps_diagnostic_tail() -> None:
    """截断启动失败提示时必须保留尾部根因，避免用户只看到旧日志前半段。"""

    text = (
        "操作失败: Vibego 启动失败\n"
        + ("old-stale-log\n" * 200)
        + "2026-06-29 14:09:59 Telegram 连通性检查失败：在 30.0 秒内未能与 Telegram 成功握手"
    )

    result = master._truncate_text_for_telegram(text, limit=500)

    assert len(result) <= 500
    assert result.startswith("操作失败: Vibego 启动失败")
    assert "内容过长已截断" in result
    assert "Telegram 连通性检查失败" in result


def test_run_action_refreshes_overview_when_failure_notice_send_fails(
    repo: ProjectRepository,
    tmp_path: Path,
    monkeypatch,
):
    """即使错误提示发送失败，也不能阻断项目列表刷新。"""

    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo

    async def run_worker_override(cfg: master.ProjectConfig, model: str | None = None) -> str:
        manager.state_store.update(cfg.project_slug, status="starting")
        raise RuntimeError("握手超时")

    class AlwaysFailMessage(DummyMessage):
        async def answer(self, text: str, **kwargs):
            raise TelegramBadRequest(
                method="sendMessage",
                message="Bad Request: retry later",
            )

    monkeypatch.setattr(manager, "run_worker", AsyncMock(side_effect=run_worker_override))

    callback = DummyCallback(
        "project:run:sample",
        message=AlwaysFailMessage(chat_id=1),
    )
    _, fsm_state = _build_fsm_state()

    async def _invoke():
        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())

    assert callback.message._edits, "错误提示失败后也应刷新项目列表"
    assert callback._answers[-1] == ("操作失败", True)


@pytest.mark.asyncio
async def test_send_projects_overview_retries_on_transient_network_error(
    repo: ProjectRepository,
    tmp_path: Path,
    monkeypatch,
):
    """
    TDD 场景：发送项目概览遇到瞬时网络错误时，应该重试而不是直接失败。
    """

    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo

    send_mock = AsyncMock(side_effect=[ClientError("net-1"), ClientError("net-2"), None])
    bot = SimpleNamespace(send_message=send_mock)

    async def noop_notify(_bot, _chat_id):
        return None

    monkeypatch.setattr(master, "_maybe_notify_update", noop_notify)
    monkeypatch.setattr(master, "_projects_overview", lambda _manager: ("请选择操作：", None))

    await master._send_projects_overview_to_chat(bot, 1, manager)

    assert send_mock.await_count == 3, "出现瞬时网络错误后应重试直至成功"


@pytest.mark.asyncio
async def test_run_worker_ensures_project_workdir_trust_before_launch(
    repo: ProjectRepository,
    tmp_path: Path,
    monkeypatch,
):
    """项目启动前应先确保 workdir 已具备 Codex trusted 权限。"""

    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    cfg = manager.require_project("sample")

    trust_calls: list[tuple[str, str]] = []
    launch_calls: list[tuple[str, ...]] = []

    def fake_ensure_codex_project_trust(project_path: Path, *, config_path: Path | None = None):
        trust_calls.append((str(project_path), str(config_path) if config_path is not None else ""))
        return SimpleNamespace(path=project_path, previous_trust_level=None, changed=False)

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        launch_calls.append(tuple(str(part) for part in cmd))
        assert trust_calls, "应先完成 trusted 校验，再启动 run_bot.sh"
        return DummyProcess()

    monkeypatch.setattr(master, "ensure_codex_project_trust", fake_ensure_codex_project_trust)
    monkeypatch.setattr(master, "CODEX_CONFIG_PATH", tmp_path / "codex-config.toml")
    monkeypatch.setattr(master.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(manager, "_health_check_worker", AsyncMock(return_value=None))

    chosen = await manager.run_worker(cfg)

    assert chosen == "codex"
    assert trust_calls == [(str(tmp_path), str(tmp_path / "codex-config.toml"))]
    assert launch_calls, "trusted 校验通过后应继续启动 worker"


@pytest.mark.asyncio
async def test_run_worker_fails_closed_when_project_workdir_trust_auto_config_fails(
    repo: ProjectRepository,
    tmp_path: Path,
    monkeypatch,
):
    """项目 workdir 权限自动配置失败时，应 fail-closed 并阻止启动。"""

    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    cfg = manager.require_project("sample")

    def fake_ensure_codex_project_trust(project_path: Path, *, config_path: Path | None = None):
        raise RuntimeError(f"无法自动写入 trusted：{project_path}")

    create_proc = AsyncMock()

    monkeypatch.setattr(master, "ensure_codex_project_trust", fake_ensure_codex_project_trust)
    monkeypatch.setattr(master, "CODEX_CONFIG_PATH", tmp_path / "codex-config.toml")
    monkeypatch.setattr(master.asyncio, "create_subprocess_exec", create_proc)

    with pytest.raises(RuntimeError, match="Codex trusted"):
        await manager.run_worker(cfg)

    create_proc.assert_not_called()


@pytest.mark.asyncio
async def test_run_worker_skips_codex_trust_for_copilot(
    repo: ProjectRepository,
    tmp_path: Path,
    monkeypatch,
):
    """Copilot 启动不应误走 Codex trusted 预处理。"""

    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo
    cfg = manager.require_project("sample")

    trust_calls: list[str] = []
    launch_calls: list[tuple[str, ...]] = []

    def fake_ensure_codex_project_trust(project_path: Path, *, config_path: Path | None = None):
        trust_calls.append(str(project_path))
        return SimpleNamespace(path=project_path, previous_trust_level=None, changed=False)

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        launch_calls.append(tuple(str(part) for part in cmd))
        return DummyProcess()

    monkeypatch.setattr(master, "ensure_codex_project_trust", fake_ensure_codex_project_trust)
    monkeypatch.setattr(master.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(manager, "_health_check_worker", AsyncMock(return_value=None))
    monkeypatch.setenv("MODEL_CMD", "python3")

    chosen = await manager.run_worker(cfg, model="copilot")

    assert chosen == "copilot"
    assert trust_calls == []
    assert launch_calls, "应直接继续启动 worker"
