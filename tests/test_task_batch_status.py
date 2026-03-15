from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot  # noqa: E402
from tasks import TaskRecord  # noqa: E402


class DummyMessage:
    def __init__(self, *, chat_id: int = 1, user_id: int = 1, text: str | None = None):
        self.calls = []
        self.edits = []
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester")
        self.message_id = 100
        self.date = datetime.now(bot.UTC)
        self.text = text
        self.caption = None

    async def answer(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id + len(self.calls), chat=self.chat)

    async def edit_text(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.edits.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id, chat=self.chat)


class DummyCallback:
    def __init__(self, data: str, message: DummyMessage):
        self.data = data
        self.message = message
        self.answers = []
        self.from_user = SimpleNamespace(id=1, full_name="Tester")

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


def make_state(message: DummyMessage) -> tuple[FSMContext, MemoryStorage]:
    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=999, chat_id=message.chat.id, user_id=message.from_user.id),
    )
    return state, storage


def _task(task_id: str, *, status: str = "research", title: str | None = None) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        project_slug="demo",
        title=title or task_id,
        status=status,
        priority=3,
        task_type="requirement",
        tags=(),
        due_date=None,
        description="任务描述",
        parent_id=None,
        root_id=task_id,
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )


def test_task_list_view_includes_batch_status_button_and_limit_buttons(monkeypatch):
    class DummyService:
        async def paginate(self, **kwargs):
            return [], 1

        async def count_tasks(self, **kwargs):
            return 0

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    text, markup = asyncio.run(bot._build_task_list_view(status=None, page=1, limit=10))

    assert text.startswith("*任务列表*")
    rows = [[button.text for button in row] for row in markup.inline_keyboard]
    assert any("🔁 批量修改状态" in row for row in rows)
    assert any(row == ["✔️ 10条/页", "20条/页", "50条/页"] for row in rows)


def test_start_batch_status_from_task_list_enters_batch_view(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback(bot.TASK_BATCH_STATUS_START_CALLBACK, message)

    class DummyService:
        async def paginate(self, **kwargs):
            return [_task("TASK_0001", title="任务一"), _task("TASK_0002", title="任务二")], 1

        async def count_tasks(self, **kwargs):
            return 2

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    bot._init_task_view_context(message, bot._make_list_view_state(status=None, page=1, limit=10))

    async def _scenario() -> None:
        await bot.on_task_batch_status_start(callback)
        state = bot._peek_task_view(message.chat.id, message.message_id)
        assert state is not None
        assert state.kind == "batch_status"
        assert message.edits, "应将原任务列表消息切到批量状态视图"
        text, _parse_mode, markup, _kwargs = message.edits[-1]
        assert "批量修改任务状态" in text
        assert isinstance(markup, InlineKeyboardMarkup)

    asyncio.run(_scenario())


def test_batch_status_view_includes_limit_and_page_shortcuts(monkeypatch):
    class DummyService:
        async def paginate(self, **kwargs):
            return [_task("TASK_0001", title="任务一"), _task("TASK_0002", title="任务二")], 3

        async def count_tasks(self, **kwargs):
            return 6

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    text, markup = asyncio.run(
        bot._build_task_batch_status_view(
            status=None,
            page=2,
            limit=20,
            selected_task_ids=[],
            selected_task_order=[],
        )
    )

    assert "批量修改任务状态" in text
    rows = [[button.text for button in row] for row in markup.inline_keyboard]
    assert any(row == ["10条/页", "✔️ 20条/页", "50条/页"] for row in rows)
    assert any("✅ 全选当前页" in row for row in rows)
    assert any(any(text.startswith("🚀 确认批量修改状态") for text in row) for row in rows)


def test_batch_status_change_limit_preserves_selection_and_resets_page(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback(f"{bot.TASK_BATCH_STATUS_LIMIT_PREFIX}20", message)

    class DummyService:
        async def paginate(self, **kwargs):
            return [_task("TASK_0003", title="任务三"), _task("TASK_0004", title="任务四")], 2

        async def count_tasks(self, **kwargs):
            return 4

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    bot._init_task_view_context(
        message,
        bot._make_batch_status_view_state(
            status=None,
            page=3,
            limit=10,
            selected_task_ids=["TASK_0001"],
            selected_task_order=["TASK_0001"],
        ),
    )

    async def _scenario() -> None:
        await bot.on_task_batch_status_limit(callback)
        state = bot._peek_task_view(message.chat.id, message.message_id)
        assert state is not None
        assert state.kind == "batch_status"
        assert state.data["page"] == 1
        assert state.data["limit"] == 20
        assert state.data["selected_task_ids"] == ["TASK_0001"]
        assert state.data["selected_task_order"] == ["TASK_0001"]
        assert callback.answers[-1] == ("已切换为每页 20 条", False)

    asyncio.run(_scenario())


def test_batch_status_select_current_page_keeps_other_page_selection(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback(bot.TASK_BATCH_STATUS_SELECT_PAGE_CALLBACK, message)

    class DummyService:
        async def paginate(self, **kwargs):
            return [_task("TASK_0003", title="任务三"), _task("TASK_0004", title="任务四")], 3

        async def count_tasks(self, **kwargs):
            return 6

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    bot._init_task_view_context(
        message,
        bot._make_batch_status_view_state(
            status=None,
            page=2,
            limit=2,
            selected_task_ids=["TASK_0001"],
            selected_task_order=["TASK_0001"],
        ),
    )

    async def _scenario() -> None:
        await bot.on_task_batch_status_select_page(callback)
        state = bot._peek_task_view(message.chat.id, message.message_id)
        assert state is not None
        assert state.kind == "batch_status"
        assert state.data["selected_task_ids"] == ["TASK_0001", "TASK_0003", "TASK_0004"]
        assert state.data["selected_task_order"] == ["TASK_0001", "TASK_0003", "TASK_0004"]
        assert callback.answers[-1] == ("已全选当前页", False)

    asyncio.run(_scenario())


def test_batch_status_invert_current_page_only_toggles_visible_tasks(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback(bot.TASK_BATCH_STATUS_INVERT_PAGE_CALLBACK, message)

    class DummyService:
        async def paginate(self, **kwargs):
            return [_task("TASK_0003", title="任务三"), _task("TASK_0004", title="任务四")], 3

        async def count_tasks(self, **kwargs):
            return 6

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    bot._init_task_view_context(
        message,
        bot._make_batch_status_view_state(
            status=None,
            page=2,
            limit=2,
            selected_task_ids=["TASK_0001", "TASK_0003"],
            selected_task_order=["TASK_0001", "TASK_0003"],
        ),
    )

    async def _scenario() -> None:
        await bot.on_task_batch_status_invert_page(callback)
        state = bot._peek_task_view(message.chat.id, message.message_id)
        assert state is not None
        assert state.kind == "batch_status"
        assert state.data["selected_task_ids"] == ["TASK_0001", "TASK_0004"]
        assert state.data["selected_task_order"] == ["TASK_0001", "TASK_0004"]
        assert callback.answers[-1] == ("已反选当前页", False)

    asyncio.run(_scenario())


def test_batch_status_confirm_enters_status_choice(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback(bot.TASK_BATCH_STATUS_CONFIRM_CALLBACK, message)
    state, _storage = make_state(message)

    bot._init_task_view_context(
        message,
        bot._make_batch_status_view_state(
            status=None,
            page=1,
            limit=10,
            selected_task_ids=["TASK_0001", "TASK_0002"],
            selected_task_order=["TASK_0001", "TASK_0002"],
        ),
    )

    async def _scenario() -> None:
        await bot.on_task_batch_status_confirm(callback, state)
        assert await state.get_state() == bot.TaskBatchStatusStates.waiting_status.state
        data = await state.get_data()
        assert data["batch_status_task_ids"] == ["TASK_0001", "TASK_0002"]
        assert message.calls, "确认后应提示用户统一选择目标状态"
        text, _parse_mode, reply_markup, _kwargs = message.calls[-1]
        assert text == bot._build_task_batch_status_prompt()
        assert isinstance(reply_markup, ReplyKeyboardMarkup)

    asyncio.run(_scenario())


def test_batch_status_choice_updates_tasks_in_order_and_restores_list(monkeypatch):
    message = DummyMessage(text=bot._format_status("test"))
    state, _storage = make_state(message)
    origin_message = DummyMessage()

    asyncio.run(
        state.update_data(
            batch_status_task_ids=["TASK_0001", "TASK_0002"],
            batch_status_origin_message=origin_message,
            batch_status_status=None,
            batch_status_page=1,
            batch_status_limit=10,
            actor="Tester#1",
        )
    )
    asyncio.run(state.set_state(bot.TaskBatchStatusStates.waiting_status))

    tasks = {
        "TASK_0001": _task("TASK_0001", status="research", title="任务一"),
        "TASK_0002": _task("TASK_0002", status="test", title="任务二"),
    }
    updates: list[tuple[str, str, str]] = []

    async def fake_get_task(task_id: str):
        return tasks.get(task_id)

    async def fake_update_task(task_id: str, *, actor, status=None, **kwargs):
        updates.append((task_id, actor, status))
        tasks[task_id] = _task(task_id, status=status or "research", title=tasks[task_id].title)
        return tasks[task_id]

    async def fake_build_task_list_view(*, status, page, limit):
        return "*任务列表*", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)
    monkeypatch.setattr(bot, "_build_task_list_view", fake_build_task_list_view)

    async def _scenario() -> None:
        await bot.on_task_batch_status_choice(message, state)
        assert await state.get_state() is None
        assert updates == [("TASK_0001", "Tester#1", "test")]
        assert origin_message.edits, "执行完成后应恢复原任务列表视图"
        assert message.calls, "应向用户输出批量状态修改结果摘要"
        result_text = message.calls[-1][0]
        assert "批量修改任务状态结果" in result_text
        assert "成功：1" in result_text
        assert "跳过：1" in result_text

    asyncio.run(_scenario())


def test_batch_status_choice_done_schedules_parallel_cleanup(monkeypatch):
    message = DummyMessage(text=bot._format_status("done"))
    state, _storage = make_state(message)
    origin_message = DummyMessage()

    asyncio.run(
        state.update_data(
            batch_status_task_ids=["TASK_0001", "TASK_0002"],
            batch_status_origin_message=origin_message,
            batch_status_status=None,
            batch_status_page=1,
            batch_status_limit=10,
            actor="Tester#1",
        )
    )
    asyncio.run(state.set_state(bot.TaskBatchStatusStates.waiting_status))

    tasks = {
        "TASK_0001": _task("TASK_0001", status="research", title="任务一"),
        "TASK_0002": _task("TASK_0002", status="done", title="任务二"),
    }
    cleanup_calls: list[str] = []

    async def fake_get_task(task_id: str):
        return tasks.get(task_id)

    async def fake_update_task(task_id: str, *, actor, status=None, **kwargs):
        tasks[task_id] = _task(task_id, status=status or "research", title=tasks[task_id].title)
        return tasks[task_id]

    async def fake_build_task_list_view(*, status, page, limit):
        return "*任务列表*", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)
    monkeypatch.setattr(bot, "_build_task_list_view", fake_build_task_list_view)
    monkeypatch.setattr(bot, "_schedule_parallel_cleanup_for_done", cleanup_calls.append)

    async def _scenario() -> None:
        await bot.on_task_batch_status_choice(message, state)
        assert cleanup_calls == ["TASK_0001"], "只有实际切到 done 的任务才应触发并行清理"
        assert origin_message.edits

    asyncio.run(_scenario())
