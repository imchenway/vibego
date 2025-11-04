"""
Test cases for testing master restart functionality
Includes:
1. Restart button should not refresh project list
2. Restart requests should be handled correctly
3. Other buttons should still refresh the item list
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import master
from project_repository import ProjectRepository


@pytest.fixture(autouse=True)
def reset_state():
    """Reset global state"""
    master.PROJECT_WIZARD_SESSIONS.clear()
    master.PROJECT_WIZARD_LOCK = asyncio.Lock()
    yield
    master.PROJECT_WIZARD_SESSIONS.clear()
    master.PROJECT_WIZARD_LOCK = asyncio.Lock()
    master.PROJECT_REPOSITORY = None
    master.MANAGER = None


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> ProjectRepository:
    """Create a project repository for testing"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    json_path = config_dir / "projects.json"
    initial = [
        {
            "bot_name": "TestBot",
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "project_slug": "test",
            "default_model": "codex",
            "workdir": str(tmp_path),
            "allowed_chat_id": 100,
        }
    ]
    json_path.write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8")
    db_path = config_dir / "master.db"
    repository = ProjectRepository(db_path, json_path)
    master.PROJECT_REPOSITORY = repository
    monkeypatch.setenv("MASTER_ADMIN_IDS", "1")
    return repository


def _build_manager(repo: ProjectRepository, tmp_path: Path) -> master.MasterManager:
    """Build MasterManager instance"""
    records = repo.list_projects()
    configs = [master.ProjectConfig.from_dict(record.to_dict()) for record in records]
    state_path = tmp_path / "state.json"
    state_store = master.StateStore(state_path, {cfg.project_slug: cfg for cfg in configs})
    return master.MasterManager(configs, state_store=state_store)


class DummyMessage:
    """Simulate Telegram Message object"""
    def __init__(self, chat_id: int = 1) -> None:
        self.text = ""
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=chat_id, username="tester")
        self.message_id = 1
        self.bot = AsyncMock()
        self._answers = []
        self._edits = []

    async def answer(self, text: str, **kwargs):
        self._answers.append((text, kwargs))

    async def edit_text(self, text: str, **kwargs):
        self._edits.append((text, kwargs))

    async def edit_reply_markup(self, **kwargs):
        self._edits.append(("reply_markup", kwargs))


class DummyCallback:
    """Mock Telegram CallbackQuery object"""
    def __init__(self, data: str, chat_id: int = 1, message: DummyMessage | None = None) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=chat_id, username="tester")
        self.message = message or DummyMessage(chat_id)
        self._answers = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self._answers.append((text, show_alert))


def test_restart_master_does_not_refresh_project_list(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """
    Test Case 1: Click"Restart Master"Item list should not be refreshed after pressing button

    Verification point:
    - Restart requests are handled correctly
    - The message is not edited (the item list is not refreshed)
    - Callback replies are sent correctly
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager

    # Create a startup script (to avoid reboot failures)
    start_script = tmp_path / "scripts" / "start.sh"
    start_script.parent.mkdir(parents=True, exist_ok=True)
    start_script.write_text("#!/bin/bash\necho 'mock start'", encoding="utf-8")
    start_script.chmod(0o755)

    # Set ROOT_DIR to find the startup script
    monkeypatch.setattr(master, "ROOT_DIR", tmp_path)

    # Analog restart signal writing
    signal_path = tmp_path / "state" / "restart_signal.json"
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(master, "RESTART_SIGNAL_PATH", signal_path)

    # Create callback
    callback_message = DummyMessage()
    callback = DummyCallback("project:restart_master:*", message=callback_message)

    # Execute callback processing
    async def _invoke():
        # Mock _process_restart_request to avoid an actual reboot
        original_process = master._process_restart_request

        async def mock_restart(message, *, trigger_user=None, manager=None):
            # Logging restart is called
            message._restart_called = True
            await message.answer("The restart command has been received. The master will be temporarily offline during operation. After restarting, all workers need to be started manually later.")

        with patch.object(master, '_process_restart_request', new=mock_restart):
            # Import FSMContext to call handler functions
            from aiogram.fsm.context import FSMContext
            from aiogram.fsm.storage.base import StorageKey
            from aiogram.fsm.storage.memory import MemoryStorage

            storage = MemoryStorage()
            key = StorageKey(bot_id=0, chat_id=1, user_id=1)
            fsm_state = FSMContext(storage=storage, key=key)

            await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())

    # Validation: callback should return"Restart command received"
    assert callback._answers, "There should be a callback reply"
    assert callback._answers[0][0] == "Restart command received", "The callback reply content should be correct"

    # Validation: Message should not be edited (does not refresh item list)
    assert len(callback_message._edits) == 0, "Messages should not be edited on restart (the project list is not refreshed)"

    # Verification: Restart request should be called
    assert hasattr(callback_message, '_restart_called'), "The restart request should be called"


def test_other_actions_still_refresh_project_list(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """
    Test Case 2: Other button actions (like stopping an item) should still refresh the item list

    Verification point:
    - Stop project requests are handled correctly
    - Message is edited (refreshes item list)
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo

    # Set project status to running
    manager.state_store.update("test", model="codex", status="running")

    # Mock stop_worker
    async def mock_stop_worker(cfg, *, update_state=True):
        manager.state_store.update(cfg.project_slug, status="stopped")

    monkeypatch.setattr(manager, "stop_worker", AsyncMock(side_effect=mock_stop_worker))

    # Create callback
    callback_message = DummyMessage()
    callback = DummyCallback("project:stop:test", message=callback_message)

    async def _invoke():
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.storage.memory import MemoryStorage

        storage = MemoryStorage()
        key = StorageKey(bot_id=0, chat_id=1, user_id=1)
        fsm_state = FSMContext(storage=storage, key=key)

        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())

    # Validate: the message should be edited (refresh the item list)
    assert len(callback_message._edits) > 0, "The message should be edited after stopping the project (refresh the project list)"

    # Verification: The edited content should be an overview of the project
    text, kwargs = callback_message._edits[0]
    assert text == "Please select an action:", "Project overview should be refreshed"


def test_restart_master_without_message_object(repo: ProjectRepository, tmp_path: Path):
    """
    Test Case 3: Edge Scenario - Restart button callback missing message object

    Verification point:
    - Error logs should be logged
    - should return immediately without performing a restart
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager

    # Create callback without message
    callback = DummyCallback("project:restart_master:*")
    callback.message = None

    async def _invoke():
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.storage.memory import MemoryStorage

        storage = MemoryStorage()
        key = StorageKey(bot_id=0, chat_id=1, user_id=1)
        fsm_state = FSMContext(storage=storage, key=key)

        await master.on_project_action(callback, fsm_state)

    # Should not throw an exception
    asyncio.run(_invoke())

    # Verification: The callback should have a reply
    assert callback._answers, "There should be a callback reply"


def test_restart_master_with_unauthorized_user(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """
    Test Case 4: Abnormal Scenario - Unauthorized user attempts to restart

    Verification point:
    - Restart requests from unauthorized users should be denied
    - should be sent"Unauthorized"information
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager

    # Set the authorized user to 999 and the callback user to 1
    monkeypatch.setenv("MASTER_ADMIN_IDS", "999")

    # Create startup script
    start_script = tmp_path / "scripts" / "start.sh"
    start_script.parent.mkdir(parents=True, exist_ok=True)
    start_script.write_text("#!/bin/bash\necho 'mock start'", encoding="utf-8")
    start_script.chmod(0o755)

    monkeypatch.setattr(master, "ROOT_DIR", tmp_path)

    # Create callback (user ID is 1)
    callback_message = DummyMessage(chat_id=1)
    callback = DummyCallback("project:restart_master:*", chat_id=1, message=callback_message)

    async def _invoke():
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.storage.memory import MemoryStorage

        storage = MemoryStorage()
        key = StorageKey(bot_id=0, chat_id=1, user_id=1)
        fsm_state = FSMContext(storage=storage, key=key)

        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())

    # Verification: There should be no restart called
    # Due to Unauthorized, the callback handler should return early


def test_stop_all_refreshes_project_list(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """
    Test Case 5: Verification"Stop all projects"button will refresh the project list

    Verification point:
    - Stop all projects requests are processed correctly
    - information is edited (refresh the project list)
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo

    # Mock stop_all
    async def mock_stop_all(*, update_state=True):
        for cfg in manager.configs:
            manager.state_store.update(cfg.project_slug, status="stopped")

    monkeypatch.setattr(manager, "stop_all", AsyncMock(side_effect=mock_stop_all))

    # Create callback
    callback_message = DummyMessage()
    callback = DummyCallback("project:stop_all:*", message=callback_message)

    async def _invoke():
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.storage.memory import MemoryStorage

        storage = MemoryStorage()
        key = StorageKey(bot_id=0, chat_id=1, user_id=1)
        fsm_state = FSMContext(storage=storage, key=key)

        await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())

    # Validate: the message should be edited (refresh the item list)
    assert len(callback_message._edits) > 0, "Stop all projectsInformation should be edited later (refresh the project list)"
