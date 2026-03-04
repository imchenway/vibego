from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientError
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
