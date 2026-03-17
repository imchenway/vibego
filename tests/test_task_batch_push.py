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
from aiogram.types import InlineKeyboardMarkup
import pytest

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


def test_task_list_view_includes_batch_push_button(monkeypatch):
    class DummyService:
        async def paginate(self, **kwargs):
            return [], 1

        async def count_tasks(self, **kwargs):
            return 0

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    text, markup = asyncio.run(bot._build_task_list_view(status=None, page=1, limit=10))

    assert text.startswith("*任务列表*")
    buttons = [button.text for row in markup.inline_keyboard for button in row]
    assert "🚀 批量推送任务" in buttons


def test_start_batch_push_from_task_list_enters_batch_view(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback(bot.TASK_BATCH_PUSH_START_CALLBACK, message)

    class DummyService:
        async def paginate(self, **kwargs):
            return [_task("TASK_0001", title="任务一"), _task("TASK_0002", title="任务二")], 1

        async def count_tasks(self, **kwargs):
            return 2

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    bot._init_task_view_context(message, bot._make_list_view_state(status=None, page=1, limit=10))

    async def _scenario() -> None:
        await bot.on_task_batch_push_start(callback)
        state = bot._peek_task_view(message.chat.id, message.message_id)
        assert state is not None
        assert state.kind == "batch_push"
        assert message.edits, "应将原任务列表消息切到批量选择视图"
        text, _parse_mode, markup, _kwargs = message.edits[-1]
        assert "批量推送任务" in text
        assert isinstance(markup, InlineKeyboardMarkup)

    asyncio.run(_scenario())


def test_batch_push_view_includes_page_shortcuts_before_confirm(monkeypatch):
    """批量勾选页应在确认按钮前提供“全选/反选当前页”快捷操作。"""

    class DummyService:
        async def paginate(self, **kwargs):
            return [_task("TASK_0001", title="任务一"), _task("TASK_0002", title="任务二")], 1

        async def count_tasks(self, **kwargs):
            return 2

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    text, markup = asyncio.run(
        bot._build_task_batch_push_view(
            status=None,
            page=1,
            limit=10,
            selected_task_ids=[],
            selected_task_order=[],
        )
    )

    assert "批量推送任务" in text
    rows = [[button.text for button in row] for row in markup.inline_keyboard]
    shortcut_row = next(row for row in rows if "✅ 全选当前页" in row)
    confirm_row = next(row for row in rows if any(text.startswith("🚀 确认批量推送") for text in row))
    assert shortcut_row == ["✅ 全选当前页", "🔁 反选当前页"]
    assert rows.index(shortcut_row) + 1 == rows.index(confirm_row)


def test_batch_push_select_current_page_keeps_other_page_selection(monkeypatch):
    """全选当前页只补齐当前页任务，并保留其他页已选结果。"""

    message = DummyMessage()
    callback = DummyCallback(bot.TASK_BATCH_PUSH_SELECT_PAGE_CALLBACK, message)

    class DummyService:
        async def paginate(self, **kwargs):
            return [_task("TASK_0003", title="任务三"), _task("TASK_0004", title="任务四")], 3

        async def count_tasks(self, **kwargs):
            return 6

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    bot._init_task_view_context(
        message,
        bot._make_batch_push_view_state(
            status=None,
            page=2,
            limit=2,
            selected_task_ids=["TASK_0001"],
            selected_task_order=["TASK_0001"],
        ),
    )

    async def _scenario() -> None:
        await bot.on_task_batch_push_select_page(callback)
        state = bot._peek_task_view(message.chat.id, message.message_id)
        assert state is not None
        assert state.kind == "batch_push"
        assert state.data["selected_task_ids"] == ["TASK_0001", "TASK_0003", "TASK_0004"]
        assert state.data["selected_task_order"] == ["TASK_0001", "TASK_0003", "TASK_0004"]
        assert callback.answers[-1] == ("已全选当前页", False)

    asyncio.run(_scenario())


def test_batch_push_invert_current_page_only_toggles_visible_tasks(monkeypatch):
    """反选当前页只切换当前页任务，不影响其他页已选项。"""

    message = DummyMessage()
    callback = DummyCallback(bot.TASK_BATCH_PUSH_INVERT_PAGE_CALLBACK, message)

    class DummyService:
        async def paginate(self, **kwargs):
            return [_task("TASK_0003", title="任务三"), _task("TASK_0004", title="任务四")], 3

        async def count_tasks(self, **kwargs):
            return 6

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    bot._init_task_view_context(
        message,
        bot._make_batch_push_view_state(
            status=None,
            page=2,
            limit=2,
            selected_task_ids=["TASK_0001", "TASK_0003"],
            selected_task_order=["TASK_0001", "TASK_0003"],
        ),
    )

    async def _scenario() -> None:
        await bot.on_task_batch_push_invert_page(callback)
        state = bot._peek_task_view(message.chat.id, message.message_id)
        assert state is not None
        assert state.kind == "batch_push"
        assert state.data["selected_task_ids"] == ["TASK_0001", "TASK_0004"]
        assert state.data["selected_task_order"] == ["TASK_0001", "TASK_0004"]
        assert callback.answers[-1] == ("已反选当前页", False)

    asyncio.run(_scenario())


def test_batch_push_confirm_skips_session_selection_when_only_main_session(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback(bot.TASK_BATCH_PUSH_CONFIRM_CALLBACK, message)
    state, _storage = make_state(message)

    bot._init_task_view_context(
        message,
        bot._make_batch_push_view_state(
            status=None,
            page=1,
            limit=10,
            selected_task_ids=["TASK_0001"],
            selected_task_order=["TASK_0001"],
        ),
    )

    async def fake_list_project_live_sessions():
        return [bot.SessionLiveEntry(key="main", label="💻 主会话（vibe）", tmux_session="vibe", kind="main")]

    monkeypatch.setattr(bot, "_list_project_live_sessions", fake_list_project_live_sessions)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")

    async def _scenario() -> None:
        await bot.on_task_batch_push_confirm(callback, state)
        assert await state.get_state() == bot.TaskBatchPushStates.waiting_choice.state
        data = await state.get_data()
        assert data["batch_task_ids"] == ["TASK_0001"]
        assert data["selected_existing_session_key"] == "main"
        assert message.calls, "只有主会话时应直接进入统一模式选择"
        assert bot._build_push_mode_prompt() == message.calls[-1][0]

    asyncio.run(_scenario())


def test_batch_push_confirm_with_multiple_sessions_opens_session_picker(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback(bot.TASK_BATCH_PUSH_CONFIRM_CALLBACK, message)
    state, _storage = make_state(message)

    bot._init_task_view_context(
        message,
        bot._make_batch_push_view_state(
            status=None,
            page=1,
            limit=10,
            selected_task_ids=["TASK_0001"],
            selected_task_order=["TASK_0001"],
        ),
    )

    async def fake_list_project_live_sessions():
        return [
            bot.SessionLiveEntry(key="main", label="💻 主会话（vibe）", tmux_session="vibe", kind="main"),
            bot.SessionLiveEntry(
                key="parallel:TASK_0115",
                label="/TASK_0115 并行会话",
                tmux_session="vibe-par-demo-115",
                kind="parallel",
                task_id="TASK_0115",
            ),
        ]

    monkeypatch.setattr(bot, "_list_project_live_sessions", fake_list_project_live_sessions)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")

    async def _scenario() -> None:
        await bot.on_task_batch_push_confirm(callback, state)
        assert await state.get_state() == bot.TaskBatchPushStates.waiting_existing_session.state
        assert message.edits, "多会话时应先让用户选择目标会话"
        text, _parse_mode, markup, _kwargs = message.edits[-1]
        assert bot._build_task_batch_push_existing_session_prompt(session_count=2) == text
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        assert bot.TASK_BATCH_PUSH_SESSION_MAIN_CALLBACK in callback_data
        assert f"{bot.TASK_BATCH_PUSH_SESSION_PARALLEL_PREFIX}TASK_0115" in callback_data

    asyncio.run(_scenario())


@pytest.mark.parametrize("model_name", ["codex", "copilot"])
def test_batch_push_mode_choice_dispatches_selected_tasks_in_order_with_queued_send_mode(monkeypatch, model_name):
    message = DummyMessage(text=bot.PUSH_MODE_PLAN)
    state, _storage = make_state(message)

    origin_message = DummyMessage()
    asyncio.run(
        state.update_data(
            batch_task_ids=["TASK_0001", "TASK_0002"],
            batch_origin_message=origin_message,
            batch_status=None,
            batch_page=1,
            batch_limit=10,
            selected_existing_session_key="main",
            actor="Tester#1",
            chat_id=origin_message.chat.id,
        )
    )
    asyncio.run(state.set_state(bot.TaskBatchPushStates.waiting_choice))

    tasks = {
        "TASK_0001": _task("TASK_0001", status="research", title="任务一"),
        "TASK_0002": _task("TASK_0002", status="test", title="任务二"),
    }
    pushed: list[tuple[str, str | None, str | None, object]] = []

    async def fake_get_task(task_id: str):
        return tasks.get(task_id)

    async def fake_push_task_to_model(task, *, chat_id, reply_to, supplement, actor, is_bug_report=False, push_mode=None, send_mode=None, dispatch_context=None):
        pushed.append((task.id, push_mode, send_mode, dispatch_context))
        return True, f"PROMPT:{task.id}", None

    async def fake_build_task_list_view(*, status, page, limit):
        return "*任务列表*", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task_to_model)
    monkeypatch.setattr(bot, "_build_task_list_view", fake_build_task_list_view)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", model_name)

    async def _scenario() -> None:
        await bot.on_task_batch_push_mode_choice(message, state)
        assert await state.get_state() is None
        assert pushed == [
            ("TASK_0001", bot.PUSH_MODE_PLAN, bot.PUSH_SEND_MODE_QUEUED, None),
            ("TASK_0002", bot.PUSH_MODE_PLAN, bot.PUSH_SEND_MODE_QUEUED, None),
        ]
        assert origin_message.edits, "执行完成后应恢复原任务列表视图"
        assert message.calls, "应向用户输出批量推送结果摘要"
        assert "批量推送结果" in message.calls[-1][0]

    asyncio.run(_scenario())
