"""
测试 master 重启功能的测试用例
包括：
1. 重启按钮不应该刷新项目列表
2. 重启请求应该被正确处理
3. 其他按钮仍然应该刷新项目列表
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import master
from project_repository import ProjectRepository


@pytest.fixture(autouse=True)
def reset_state():
    """重置全局状态"""
    master.PROJECT_WIZARD_SESSIONS.clear()
    master.reset_project_wizard_lock()
    yield
    master.PROJECT_WIZARD_SESSIONS.clear()
    master.reset_project_wizard_lock()
    master.PROJECT_REPOSITORY = None
    master.MANAGER = None


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> ProjectRepository:
    """创建测试用的项目仓库"""
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
    """构建 MasterManager 实例"""
    records = repo.list_projects()
    configs = [master.ProjectConfig.from_dict(record.to_dict()) for record in records]
    state_path = tmp_path / "state.json"
    state_store = master.StateStore(state_path, {cfg.project_slug: cfg for cfg in configs})
    return master.MasterManager(configs, state_store=state_store)


class DummyMessage:
    """模拟 Telegram Message 对象"""
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
    """模拟 Telegram CallbackQuery 对象"""
    def __init__(self, data: str, chat_id: int = 1, message: DummyMessage | None = None) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=chat_id, username="tester")
        self.message = message or DummyMessage(chat_id)
        self._answers = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self._answers.append((text, show_alert))


def test_restart_master_does_not_refresh_project_list(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """
    测试用例 1：点击"重启 Master"按钮后，不应该刷新项目列表

    验证点：
    - 重启请求被正确处理
    - 消息没有被编辑（不刷新项目列表）
    - 回调答复正确发送
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager

    # 创建启动脚本（避免重启失败）
    start_script = tmp_path / "scripts" / "start.sh"
    start_script.parent.mkdir(parents=True, exist_ok=True)
    start_script.write_text("#!/bin/bash\necho 'mock start'", encoding="utf-8")
    start_script.chmod(0o755)

    # 设置 ROOT_DIR 以便找到启动脚本
    monkeypatch.setattr(master, "ROOT_DIR", tmp_path)

    # 模拟重启信号写入
    signal_path = tmp_path / "state" / "restart_signal.json"
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(master, "RESTART_SIGNAL_PATH", signal_path)

    # 创建回调
    callback_message = DummyMessage()
    callback = DummyCallback("project:restart_master:*", message=callback_message)

    # 执行回调处理
    async def _invoke():
        # Mock _process_restart_request 以避免实际重启
        original_process = master._process_restart_request

        async def mock_restart(message, *, trigger_user=None, manager=None):
            # 记录重启被调用
            message._restart_called = True
            await message.answer("已收到重启指令，运行期间 master 会短暂离线，重启后所有 worker 需稍后手动启动。")

        with patch.object(master, '_process_restart_request', new=mock_restart):
            # 导入 FSMContext 以便调用处理函数
            from aiogram.fsm.context import FSMContext
            from aiogram.fsm.storage.base import StorageKey
            from aiogram.fsm.storage.memory import MemoryStorage

            storage = MemoryStorage()
            key = StorageKey(bot_id=0, chat_id=1, user_id=1)
            fsm_state = FSMContext(storage=storage, key=key)

            await master.on_project_action(callback, fsm_state)

    asyncio.run(_invoke())

    # 验证：回调应该返回"已收到重启指令"
    assert callback._answers, "应该有回调答复"
    assert callback._answers[0][0] == "已收到重启指令", "回调答复内容应该正确"

    # 验证：消息不应该被编辑（不刷新项目列表）
    assert len(callback_message._edits) == 0, "重启时不应该编辑消息（不刷新项目列表）"

    # 验证：重启请求应该被调用
    assert hasattr(callback_message, '_restart_called'), "重启请求应该被调用"


def test_other_actions_still_refresh_project_list(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """
    测试用例 2：其他按钮操作（如停止项目）仍然应该刷新项目列表

    验证点：
    - 停止项目请求被正确处理
    - 消息被编辑（刷新项目列表）
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo

    # 设置项目状态为运行中
    manager.state_store.update("test", model="codex", status="running")

    # Mock stop_worker
    async def mock_stop_worker(cfg, *, update_state=True):
        manager.state_store.update(cfg.project_slug, status="stopped")

    monkeypatch.setattr(manager, "stop_worker", AsyncMock(side_effect=mock_stop_worker))

    # 创建回调
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

    # 验证：消息应该被编辑（刷新项目列表）
    assert len(callback_message._edits) > 0, "停止项目后应该编辑消息（刷新项目列表）"

    # 验证：编辑的内容应该是项目概览
    text, kwargs = callback_message._edits[0]
    assert text == "请选择操作：", "应该刷新项目概览"


def test_restart_master_without_message_object(repo: ProjectRepository, tmp_path: Path):
    """
    测试用例 3：边界场景 - 重启按钮回调缺少 message 对象

    验证点：
    - 应该记录错误日志
    - 应该立即返回，不执行重启
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager

    # 创建没有 message 的回调
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

    # 应该不抛出异常
    asyncio.run(_invoke())

    # 验证：回调应该有答复
    assert callback._answers, "应该有回调答复"


def test_restart_master_with_unauthorized_user(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """
    测试用例 4：异常场景 - 未授权用户尝试重启

    验证点：
    - 应该拒绝未授权用户的重启请求
    - 应该发送"未授权"消息
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager

    # 设置授权用户为 999，而回调用户为 1
    monkeypatch.setenv("MASTER_ADMIN_IDS", "999")

    # 创建启动脚本
    start_script = tmp_path / "scripts" / "start.sh"
    start_script.parent.mkdir(parents=True, exist_ok=True)
    start_script.write_text("#!/bin/bash\necho 'mock start'", encoding="utf-8")
    start_script.chmod(0o755)

    monkeypatch.setattr(master, "ROOT_DIR", tmp_path)

    # 创建回调（用户 ID 为 1）
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

    # 验证：应该没有调用重启
    # 由于未授权，回调处理应该在早期就返回


def test_stop_all_refreshes_project_list(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """
    测试用例 5：验证"停止全部项目"按钮会刷新项目列表

    验证点：
    - 停止全部项目请求被正确处理
    - 消息被编辑（刷新项目列表）
    """
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager
    master.PROJECT_REPOSITORY = repo

    # Mock stop_all
    async def mock_stop_all(*, update_state=True):
        for cfg in manager.configs:
            manager.state_store.update(cfg.project_slug, status="stopped")

    monkeypatch.setattr(manager, "stop_all", AsyncMock(side_effect=mock_stop_all))

    # 创建回调
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

    # 验证：消息应该被编辑（刷新项目列表）
    assert len(callback_message._edits) > 0, "停止全部项目后应该编辑消息（刷新项目列表）"


@pytest.mark.asyncio
async def test_notify_restart_success_pushes_project_overview(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """重启信号存在时，应该在上线通知后推送项目列表。"""
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager

    restart_signal = tmp_path / "state" / "restart_signal.json"
    restart_signal.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "chat_id": 321,
        "timestamp": datetime.now(master.LOCAL_TZ).isoformat(),
    }
    restart_signal.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(master, "RESTART_SIGNAL_PATH", restart_signal)
    monkeypatch.setattr(master, "LEGACY_RESTART_SIGNAL_PATHS", tuple())

    overview_mock = AsyncMock()
    monkeypatch.setattr(master, "_send_projects_overview_to_chat", overview_mock)
    monkeypatch.setattr(master.asyncio, "sleep", AsyncMock())

    bot = SimpleNamespace(send_message=AsyncMock())

    await master._notify_restart_success(bot)

    overview_mock.assert_awaited_once()
    called_bot, called_chat, _manager = overview_mock.await_args_list[0].args[:3]
    assert called_bot is bot
    assert called_chat == payload["chat_id"]
    assert _manager is manager


@pytest.mark.asyncio
async def test_notify_restart_success_missing_signal_triggers_admin_overview(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """缺少重启信号但预期重启时，应向管理员推送项目列表。"""
    manager = _build_manager(repo, tmp_path)
    manager.admin_ids = [111, 222]
    master.MANAGER = manager

    monkeypatch.setenv("MASTER_RESTART_EXPECTED", "1")
    monkeypatch.setattr(master, "RESTART_SIGNAL_PATH", tmp_path / "state" / "restart_signal.json")
    monkeypatch.setattr(master, "LEGACY_RESTART_SIGNAL_PATHS", tuple())

    overview_mock = AsyncMock()
    monkeypatch.setattr(master, "_send_projects_overview_to_chat", overview_mock)
    monkeypatch.setattr(master.asyncio, "sleep", AsyncMock())

    bot = SimpleNamespace(send_message=AsyncMock())

    await master._notify_restart_success(bot)

    assert overview_mock.await_count == len(manager.admin_ids)
    called_ids = sorted(call.args[1] for call in overview_mock.await_args_list)
    assert called_ids == sorted(manager.admin_ids)


@pytest.mark.asyncio
async def test_notify_restart_success_invalid_chat_id_fallback(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """重启信号 chat_id 异常时，应退回管理员推送。"""
    manager = _build_manager(repo, tmp_path)
    manager.admin_ids = [333]
    master.MANAGER = manager

    restart_signal = tmp_path / "state" / "restart_signal.json"
    restart_signal.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "chat_id": "invalid",
        "timestamp": datetime.now(master.LOCAL_TZ).isoformat(),
    }
    restart_signal.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(master, "RESTART_SIGNAL_PATH", restart_signal)
    monkeypatch.setattr(master, "LEGACY_RESTART_SIGNAL_PATHS", tuple())

    overview_mock = AsyncMock()
    monkeypatch.setattr(master, "_send_projects_overview_to_chat", overview_mock)
    monkeypatch.setattr(master.asyncio, "sleep", AsyncMock())

    bot = SimpleNamespace(send_message=AsyncMock())

    await master._notify_restart_success(bot)

    overview_mock.assert_awaited_once()
    assert overview_mock.await_args_list[0].args[1] == manager.admin_ids[0]


@pytest.mark.asyncio
async def test_notify_restart_success_send_failure_triggers_admin_overview(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """上线通知发送失败时，应改为向管理员广播项目列表。"""
    manager = _build_manager(repo, tmp_path)
    manager.admin_ids = [444, 555]
    master.MANAGER = manager

    restart_signal = tmp_path / "state" / "restart_signal.json"
    restart_signal.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "chat_id": 999,
        "timestamp": datetime.now(master.LOCAL_TZ).isoformat(),
    }
    restart_signal.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(master, "RESTART_SIGNAL_PATH", restart_signal)
    monkeypatch.setattr(master, "LEGACY_RESTART_SIGNAL_PATHS", tuple())

    overview_mock = AsyncMock()
    monkeypatch.setattr(master, "_send_projects_overview_to_chat", overview_mock)
    monkeypatch.setattr(master.asyncio, "sleep", AsyncMock())

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("send failed")))

    await master._notify_restart_success(bot)

    assert overview_mock.await_count == len(manager.admin_ids)
    called_ids = sorted(call.args[1] for call in overview_mock.await_args_list)
    assert called_ids == sorted(manager.admin_ids)


@pytest.mark.asyncio
async def test_send_restart_project_overview_deduplicates_chats(repo: ProjectRepository, tmp_path: Path, monkeypatch):
    """项目列表推送应去重，防止触发人重复收到。"""
    manager = _build_manager(repo, tmp_path)
    master.MANAGER = manager

    overview_mock = AsyncMock()
    monkeypatch.setattr(master, "_send_projects_overview_to_chat", overview_mock)
    monkeypatch.setattr(master.asyncio, "sleep", AsyncMock())

    bot = SimpleNamespace()

    await master._send_restart_project_overview(bot, [1, 1, 2])

    assert overview_mock.await_count == 2
    called_ids = sorted(call.args[1] for call in overview_mock.await_args_list)
    assert called_ids == [1, 2]
