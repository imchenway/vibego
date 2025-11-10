import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import aiosqlite

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot
from tasks.models import TaskHistoryRecord, TaskNoteRecord, TaskRecord
from tasks.service import TaskService



class DummyMessage:
    def __init__(self):
        self.calls = []
        self.edits = []
        self.chat = SimpleNamespace(id=1)
        self.from_user = SimpleNamespace(id=1, full_name="Tester")
        self.message_id = 100
        self.sent_messages = []

    async def answer(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append((text, parse_mode, reply_markup, kwargs))
        sent = SimpleNamespace(message_id=self.message_id + len(self.calls), chat=self.chat)
        self.sent_messages.append(sent)
        return sent

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


def _make_task(
    *,
    task_id: str,
    title: str,
    status: str,
    depth: int = 0,
    task_type: str | None = None,
) -> TaskRecord:
    """Construct task records for testing."""

    return TaskRecord(
        id=task_id,
        project_slug="demo",
        title=title,
        status=status,
        priority=3,
        task_type=task_type,
        tags=(),
        due_date=None,
        description="",
        parent_id=None if depth == 0 else "TASK_PARENT",
        root_id="TASK_ROOT",
        depth=depth,
        lineage="0001" if depth == 0 else "0001.0001",
        archived=False,
    )

TYPE_UNSET = bot._format_task_type(None)
TYPE_REQUIREMENT = bot._format_task_type("requirement")
DESC_PROMPT_PREFIX = "The current description is shown below."


@pytest.mark.parametrize(
    "task, expected",
    [
        (
            _make_task(
                task_id="TASK_0001",
                title="Research tasks",
                status="research",
                task_type="requirement",
            ),
            "- 📌 Research tasks",
        ),
        (
            _make_task(
                task_id="TASK_0002",
                title="",
                status="research",
                task_type="defect",
            ),
            "- 🐞 -",
        ),
        (
            _make_task(
                task_id="TASK_0003",
                title="subtask",
                status="research",
                depth=1,
                task_type=None,
            ),
            "  - ⚪ subtask",
        ),
    ],
)
def test_format_task_list_entry(task: TaskRecord, expected: str):
    result = bot._format_task_list_entry(task)
    assert result == expected


def test_task_service_description(tmp_path: Path):
    async def _scenario() -> None:
        svc = TaskService(tmp_path / "tasks.db", "demo")
        await svc.initialize()
        task = await svc.create_root_task(
            title="Test tasks",
            status="research",
            priority=3,
            task_type="task",
            tags=(),
            due_date=None,
            description="initial description",
            actor="tester",
        )
        assert task.description == "initial description"
        assert task.task_type == "task"

        updated = await svc.update_task(
            task.id,
            actor="tester",
            description="new description",
            task_type="defect",
        )
        assert updated.description == "new description"
        assert updated.task_type == "defect"

        fetched = await svc.get_task(task.id)
        assert fetched is not None
        assert fetched.description == "new description"
        assert fetched.task_type == "defect"

    asyncio.run(_scenario())


def test_create_task_does_not_write_history(tmp_path: Path):
    async def _scenario() -> None:
        svc = TaskService(tmp_path / "tasks.db", "demo")
        await svc.initialize()
        task = await svc.create_root_task(
            title="Historyless task",
            status="research",
            priority=2,
            task_type="task",
            tags=(),
            due_date=None,
            description="",
            actor="tester",
        )
        history = await svc.list_history(task.id)
        assert history == []

    asyncio.run(_scenario())


def test_status_update_is_not_tracked_in_history(tmp_path: Path):
    async def _scenario() -> None:
        svc = TaskService(tmp_path / "tasks.db", "demo")
        await svc.initialize()
        task = await svc.create_root_task(
            title="Stateful task",
            status="research",
            priority=3,
            task_type="task",
            tags=(),
            due_date=None,
            description="",
            actor="tester",
        )
        updated = await svc.update_task(
            task.id,
            actor="tester",
            status="test",
        )
        assert updated.status == "test"
        history = await svc.list_history(task.id)
        assert history == []

    asyncio.run(_scenario())


def test_add_note_is_not_tracked_in_history(tmp_path: Path):
    async def _scenario() -> None:
        svc = TaskService(tmp_path / "tasks.db", "demo")
        await svc.initialize()
        task = await svc.create_root_task(
            title="Noted task",
            status="research",
            priority=3,
            task_type="task",
            tags=(),
            due_date=None,
            description="",
            actor="tester",
        )
        await svc.add_note(
            task.id,
            note_type="misc",
            content="memo",
            actor="tester",
        )
        history = await svc.list_history(task.id)
        assert history == []

    asyncio.run(_scenario())


def test_format_local_time_conversion():
    assert bot._format_local_time("2025-01-01T00:00:00+08:00") == "2025-01-01 00:00"
    assert bot._format_local_time("invalid") == "invalid"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("requirement", "requirement"),
        ("need", "requirement"),
        ("Req", "requirement"),
        ("feature", "requirement"),
        ("defect", "defect"),
        ("bug", "defect"),
        ("defect", "defect"),
        ("task", "task"),
        ("Task", "task"),
        ("risk", "risk"),
        ("risk", "risk"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_task_type_variants(raw, expected):
    assert bot._normalize_task_type(raw) == expected


def test_format_task_detail_without_history():
    task = _make_task(task_id="TASK_0100", title="Test tasks", status="research", task_type="requirement")
    notes = (
        TaskNoteRecord(
            id=1,
            task_id=task.id,
            note_type="research",
            content="First note",
            created_at="2025-01-01T00:00:00+08:00",
        ),
    )

    result = bot._format_task_detail(task, notes=notes)
    lines = result.splitlines()
    assert lines[0] == "📝 Title: " + bot._escape_markdown_text("Test tasks")
    expected_task_id = bot._format_task_command(task.id)
    assert lines[1] == f"🏷️ Task ID: {expected_task_id}"
    assert lines[2] == f"⚙️ Status: {bot._format_status(task.status)}"
    assert lines[3] == f"🚦 Priority: {bot._format_priority(task.priority)}"
    assert lines[4] == f"📂 Type: {bot._format_task_type(task.task_type)}"
    assert lines[5] == "🖊️ Description: None"
    assert any(line.startswith("📅 Created At:") for line in lines)
    assert any(line.startswith("🔁 Updated At:") for line in lines)
    assert "💬 Note record:" not in result
    assert "Change history" not in result
    assert "First note" not in result


def test_format_task_detail_misc_note_without_label():
    task = _make_task(task_id="TASK_0110", title="Unlabeled Task", status="research")
    notes = (
        TaskNoteRecord(
            id=1,
            task_id=task.id,
            note_type="misc",
            content="Note content without tags",
            created_at="2025-02-02T12:00:00+08:00",
        ),
    )
    result = bot._format_task_detail(task, notes=notes)
    lines = result.splitlines()
    note_lines = [line for line in lines if line.startswith("- ")]
    assert not note_lines, "The comment line should no longer be displayed after the comment is removed"
    assert "Remark" not in result


def test_task_note_flow_defaults_to_misc(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    state, _storage = make_state(message)
    service = TaskService(tmp_path / "tasks.db", "demo")
    monkeypatch.setattr(bot, "TASK_SERVICE", service)

    async def scenario() -> None:
        await service.initialize()
        task = await service.create_root_task(
            title="Test tasks",
            status="research",
            priority=3,
            task_type="requirement",
            tags=(),
            due_date=None,
            description="",
            actor="tester#2",
        )
        await state.set_state(bot.TaskNoteStates.waiting_task_id)
        message.text = task.id
        await bot.on_note_task_id(message, state)
        current_state = await state.get_state()
        assert current_state == bot.TaskNoteStates.waiting_content.state
        assert message.calls, "You should be prompted to enter the remark content"
        assert message.calls[-1][0] == "Please enter the remark content:"

        content_message = DummyMessage()
        content_message.chat = message.chat
        content_message.from_user = message.from_user
        content_message.text = "This is new Remark content"

        await bot.on_note_content(content_message, state)
        assert await state.get_state() is None

        notes = await service.list_notes(task.id)
        assert notes, "Remark should have been written"
        assert notes[-1].note_type == "misc", "Default type should be misc"
        assert any("Remark added:" in call[0] for call in content_message.calls), "A success message should be output"

    asyncio.run(scenario())


def test_task_history_callback(monkeypatch):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=123)
    callback = DummyCallback("task:history:TASK_0200", message)

    task = _make_task(task_id="TASK_0200", title="History Task", status="test")

    async def fake_get_task(task_id: str):
        assert task_id == task.id
        return task

    history_records = [
        TaskHistoryRecord(
            id=1,
            task_id=task.id,
            field="title",
            old_value="old title",
            new_value="History Task",
            actor="tester",
            event_type="field_change",
            payload=None,
            created_at="2025-01-01T00:00:00+08:00",
        ),
        TaskHistoryRecord(
            id=2,
            task_id=task.id,
            field="status",
            old_value="research",
            new_value="test",
            actor=None,
            event_type="field_change",
            payload=None,
            created_at="2025-01-02T00:00:00+08:00",
        ),
    ]

    async def fake_list_history(task_id: str):
        assert task_id == task.id
        return history_records

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)
    async def fake_list_notes(task_id: str):
        assert task_id == task.id
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "list_notes", fake_list_notes)

    bot._init_task_view_context(message, bot.TaskViewState(kind="detail", data={"task_id": task.id}))

    asyncio.run(bot.on_task_history(callback))

    assert not message.edits, "Historical messages should no longer be edited."
    assert message.calls, "Historical messages should be displayed via new messages"
    sent_text, parse_mode_value, reply_markup, _kwargs = message.calls[-1]
    assert parse_mode_value is not None
    assert sent_text.startswith("```\n")
    assert "Task TASK_0200 event history" in sent_text
    assert "Title: History Task" in sent_text
    assert "Update Title" in sent_text
    assert "  - Title: old title -> History Task" in sent_text
    assert "Update Status" in sent_text
    assert "  - Status: 🔍 Researching -> 🧪 Testing" in sent_text
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[-1][0].callback_data == f"{bot.TASK_HISTORY_BACK_CALLBACK}:{task.id}"
    assert callback.answers and callback.answers[-1][0] == "Displayed history"

    latest_sent = message.sent_messages[-1]
    bot._clear_task_view(latest_sent.chat.id, latest_sent.message_id)


def test_push_model_success(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0001", message)
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1)
    state, _storage = make_state(message)

    task = TaskRecord(
        id="TASK_0001",
        project_slug="demo",
        title="Research tasks",
        status="research",
        priority=3,
        task_type="requirement",
        tags=(),
        due_date=None,
        description="Things that need to be investigated",
        parent_id=None,
        root_id="TASK_0001",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0001"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def fake_list_history(task_id: str):
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)

    recorded: list[tuple[int, str, DummyMessage]] = []
    ack_calls: list[tuple[int, Path | None, DummyMessage | None]] = []
    logged_events: list[tuple[str, dict]] = []

    async def fake_log_event(task_id: str, **kwargs):
        logged_events.append((task_id, kwargs))

    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
    ):
        assert not ack_immediately
        recorded.append((chat_id, prompt, reply_to))
        assert reply_to is message
        return True, tmp_path / "session.jsonl"

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append((chat_id, session_path, reply_to))

    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_supplement.state
        assert callback.answers and callback.answers[0][0] == "Please add a task description, or choose Skip/Cancel."
        assert not recorded
        assert message.calls
        prompt_text, _, prompt_markup, _ = message.calls[0]
        assert prompt_text == bot._build_push_supplement_prompt()
        assert prompt_markup is not None

        skip_message = DummyMessage()
        skip_message.text = bot.SKIP_TEXT
        with pytest.raises(SkipHandler):
            await bot.on_task_push_model_supplement(skip_message, state)

        assert recorded
        chat_id, payload, reply_to = recorded[0]
        assert chat_id == message.chat.id
        assert reply_to is message
        lines = payload.splitlines()
        assert lines[0] == bot.VIBE_PHASE_PROMPT
        assert "Task Title: Research tasks" in payload
        assert "Task Code: /TASK_0001" in payload
        assert "\\_" not in payload
        assert "Task Description: Things that need to be investigated" in payload
        assert "Task Notes: -" in payload
        assert "Supplementary Description: -" in payload
        assert payload.endswith("Task execution history for traceability: -")
        assert await state.get_state() is None
        final_text, _, final_markup, _ = message.calls[-1]
        expected_block, _ = bot._wrap_text_in_code_block(payload)
        assert final_text == f"Pushed to model:\n{expected_block}"
        assert isinstance(final_markup, ReplyKeyboardMarkup)
        final_buttons = [button.text for row in final_markup.keyboard for button in row]
        assert bot.WORKER_MENU_BUTTON_TEXT in final_buttons
        assert bot.WORKER_CREATE_TASK_BUTTON_TEXT in final_buttons
        assert ack_calls and ack_calls[0][2] is message
        assert not logged_events, "Push-to-model actions should no longer enter task history"

    asyncio.run(_scenario())


def test_push_model_test_push(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0002", message)
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1)
    state, _storage = make_state(message)

    task = TaskRecord(
        id="TASK_0002",
        project_slug="demo",
        title="Test tasks",
        status="test",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="",
        parent_id=None,
        root_id="TASK_0002",
        depth=0,
        lineage="0002",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    async def fake_get_task(task_id: str):
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def fake_list_history(task_id: str):
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)

    recorded: list[tuple[int, str, DummyMessage]] = []
    ack_calls: list[tuple[int, Path | None, DummyMessage | None]] = []
    logged_events: list[tuple[str, dict]] = []

    async def fake_log_event(task_id: str, **kwargs):
        logged_events.append((task_id, kwargs))

    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
    ):
        assert not ack_immediately
        recorded.append((chat_id, prompt, reply_to))
        assert reply_to is message
        return True, tmp_path / "session.jsonl"

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append((chat_id, session_path, reply_to))

    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_supplement.state
        assert callback.answers and callback.answers[0][0] == "Please add a task description, or choose Skip/Cancel."
        assert message.calls
        prompt_text, _, prompt_markup, _ = message.calls[0]
        assert prompt_text == bot._build_push_supplement_prompt()
        assert prompt_markup is not None

        input_message = DummyMessage()
        input_message.text = "Supplementary explanation content"
        with pytest.raises(SkipHandler):
            await bot.on_task_push_model_supplement(input_message, state)

        assert recorded
        chat_id, payload, reply_to = recorded[0]
        assert chat_id == message.chat.id
        assert reply_to is message
        lines = payload.splitlines()
        assert lines[0] == bot.VIBE_PHASE_PROMPT
        assert "Task Title: Test tasks" in payload
        assert "Task Notes: -" in payload
        assert "Supplementary Description: Supplementary explanation content" in payload
        assert "Task execution history for traceability: -" in payload
        assert "Additional notes on the testing phase:" not in payload
        assert await state.get_state() is None
        final_text, _, final_markup, _ = message.calls[-1]
        expected_block, _ = bot._wrap_text_in_code_block(payload)
        assert final_text == f"Pushed to model:\n{expected_block}"
        assert isinstance(final_markup, ReplyKeyboardMarkup)
        final_buttons = [button.text for row in final_markup.keyboard for button in row]
        assert bot.WORKER_MENU_BUTTON_TEXT in final_buttons
        assert bot.WORKER_CREATE_TASK_BUTTON_TEXT in final_buttons
        assert ack_calls and ack_calls[0][2] is message
        assert message.calls and "Pushed to model" in message.calls[-1][0]
        assert not logged_events, "Push-to-model actions should no longer enter task history"

    asyncio.run(_scenario())


def test_push_model_done_push(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0004", message)
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1)
    state, _storage = make_state(message)

    task = TaskRecord(
        id="TASK_0004",
        project_slug="demo",
        title="Complete the task",
        status="done",
        priority=1,
        task_type="task",
        tags=(),
        due_date=None,
        description="",
        parent_id=None,
        root_id="TASK_0004",
        depth=0,
        lineage="0004",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    async def fake_get_task(task_id: str):
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    async def fake_list_history(task_id: str):
        return []
    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)
    recorded: list[tuple[int, str, DummyMessage]] = []
    ack_calls: list[tuple[int, Path | None, DummyMessage | None]] = []
    logged_events: list[tuple[str, dict]] = []

    async def fake_log_event(task_id: str, **kwargs):
        logged_events.append((task_id, kwargs))

    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
    ):
        assert not ack_immediately
        recorded.append((chat_id, prompt, reply_to))
        assert reply_to is message
        return True, tmp_path / "session.jsonl"

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append((chat_id, session_path, reply_to))

    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        assert recorded, "Completion stage should be sent /compact"
        _, payload, reply_to = recorded[0]
        assert reply_to is message
        assert payload == "/compact"
        assert callback.answers and callback.answers[0][0] == "has been pushed to model"
        assert message.calls
        preview_text, preview_mode, _, _ = message.calls[0]
        expected_block, expected_mode = bot._wrap_text_in_code_block("/compact")
        assert preview_text == f"Pushed to model:\n{expected_block}"
        assert preview_mode == expected_mode
        assert ack_calls and ack_calls[0][2] is message
        assert await state.get_state() is None
        assert not logged_events, "Push-to-model actions should no longer enter task history"

    asyncio.run(_scenario())


def test_history_context_respects_limits(monkeypatch):
    history_items = [
        TaskHistoryRecord(
            id=index + 1,
            task_id="TASK_1000",
            field="title",
            old_value=f"old value{index}",
            new_value=f"new value{index}",
            actor="tester",
            event_type="field_change",
            payload=None,
            created_at=f"2025-01-01T00:00:{index:02d}+08:00",
        )
        for index in range(60)
    ]

    async def fake_list_history(task_id: str):
        return history_items

    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)

    async def scenario():
        return await bot._build_history_context_for_model("TASK_1000")

    context, count = asyncio.run(scenario())
    assert count == bot.MODEL_HISTORY_MAX_ITEMS
    assert len(context) <= bot.MODEL_HISTORY_MAX_CHARS
    assert "old value0" not in context
    assert "new value59" in context


def test_push_model_missing_task(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:UNKNOWN", message)
    state, _storage = make_state(message)

    async def fake_get_task(task_id: str):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    asyncio.run(bot.on_task_push_model(callback, state))

    assert callback.answers and callback.answers[0][0] == "Task does not exist"
    assert not message.calls


def test_push_model_prompt_not_duplicated(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0005", message)
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_0005",
        title="No duplicate prompt",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0005"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_supplement.state
        assert len(message.calls) == 1

        duplicate = DummyCallback("task:push_model:TASK_0005", message)
        await bot.on_task_push_model(duplicate, state)

        assert duplicate.answers
        assert duplicate.answers[-1][0] == bot.PUSH_MODEL_SUPPLEMENT_IN_PROGRESS_TEXT
        assert len(message.calls) == 1

    asyncio.run(_scenario())


def test_push_model_duplicate_prompt_after_state_clear(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_LOCK", message)
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_LOCK",
        title="Lock prompts",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_LOCK"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_supplement.state
        assert len(message.calls) == 1
        await state.clear()
        duplicate = DummyCallback("task:push_model:TASK_LOCK", message)
        await bot.on_task_push_model(duplicate, state)
        assert duplicate.answers
        assert duplicate.answers[-1][0] == bot.PUSH_MODEL_SUPPLEMENT_IN_PROGRESS_TEXT
        assert len(message.calls) == 1

    asyncio.run(_scenario())
    bot._release_push_supplement_lock(message.chat.id, task.id)


def test_push_model_lock_released_after_completion(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_UNLOCK", message)
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_UNLOCK",
        title="Unlock prompts",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_UNLOCK"
        return task

    async def fake_list_history(task_id: str):
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)

    recorded: list[str] = []
    ack_calls: list[tuple[int, Path | None, DummyMessage | None]] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        assert not ack_immediately
        recorded.append(prompt)
        return True, tmp_path / "session.jsonl"

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append((chat_id, session_path, reply_to))

    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    async def fake_log_event(task_id: str, **kwargs):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_supplement.state

        skip_message = DummyMessage()
        skip_message.text = bot.SKIP_TEXT
        skip_message.chat = message.chat
        skip_message.from_user = message.from_user
        with pytest.raises(SkipHandler):
            await bot.on_task_push_model_supplement(skip_message, state)

        assert await state.get_state() is None
        assert recorded
        assert ack_calls

        second_callback = DummyCallback("task:push_model:TASK_UNLOCK", message)
        await bot.on_task_push_model(second_callback, state)
        assert len(message.calls) >= 2
        assert second_callback.answers
        assert second_callback.answers[-1][0] == "Please add a task description, or choose Skip/Cancel."

    asyncio.run(_scenario())
    bot._release_push_supplement_lock(message.chat.id, task.id)


def test_init_session_offset_prefers_persisted_value(monkeypatch, tmp_path: Path):
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("existing\n", encoding="utf-8")
    session_key = str(session_file)
    store_path = tmp_path / "session_offsets.json"
    monkeypatch.setattr(bot, "SESSION_OFFSET_STORE_PATH", store_path)
    original_store = dict(bot.SESSION_OFFSET_STORE)
    original_offsets = dict(bot.SESSION_OFFSETS)
    bot.SESSION_OFFSET_STORE.clear()
    bot.SESSION_OFFSETS.clear()
    bot.SESSION_OFFSET_STORE[session_key] = 5
    try:
        bot._init_session_offset(session_file)
        assert bot.SESSION_OFFSETS[session_key] == 5
    finally:
        bot.SESSION_OFFSET_STORE.clear()
        bot.SESSION_OFFSET_STORE.update(original_store)
        bot.SESSION_OFFSETS.clear()
        bot.SESSION_OFFSETS.update(original_offsets)


def test_init_session_offset_defaults_to_tail(monkeypatch, tmp_path: Path):
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("A" * 42, encoding="utf-8")
    session_key = str(session_file)
    store_path = tmp_path / "session_offsets.json"
    monkeypatch.setattr(bot, "SESSION_OFFSET_STORE_PATH", store_path)
    original_store = dict(bot.SESSION_OFFSET_STORE)
    original_offsets = dict(bot.SESSION_OFFSETS)
    bot.SESSION_OFFSET_STORE.clear()
    bot.SESSION_OFFSETS.clear()
    try:
        bot._init_session_offset(session_file)
        assert bot.SESSION_OFFSETS[session_key] == session_file.stat().st_size
    finally:
        bot.SESSION_OFFSET_STORE.clear()
        bot.SESSION_OFFSET_STORE.update(original_store)
        bot.SESSION_OFFSETS.clear()
        bot.SESSION_OFFSETS.update(original_offsets)


def test_store_session_offset_writes_without_event_loop(monkeypatch, tmp_path: Path):
    store_path = tmp_path / "session_offsets.json"
    monkeypatch.setattr(bot, "SESSION_OFFSET_STORE_PATH", store_path)
    original_store = dict(bot.SESSION_OFFSET_STORE)
    original_offsets = dict(bot.SESSION_OFFSETS)
    bot.SESSION_OFFSET_STORE.clear()
    bot.SESSION_OFFSETS.clear()
    try:
        bot._store_session_offset("session-key", 99)
        assert "session-key" in bot.SESSION_OFFSET_STORE
        data = json.loads(store_path.read_text(encoding="utf-8"))
        assert data["session-key"] == 99
    finally:
        bot.SESSION_OFFSET_STORE.clear()
        bot.SESSION_OFFSET_STORE.update(original_store)
        bot.SESSION_OFFSETS.clear()
        bot.SESSION_OFFSETS.update(original_offsets)


def test_build_bug_report_intro_plain_task_id():
    task = _make_task(task_id="TASK_0055", title="Edit describeTask", status="test")
    intro = bot._build_bug_report_intro(task)
    assert "/TASK_0055" in intro
    assert "\\_" not in intro


def test_build_bug_preview_plain_task_id():
    task = _make_task(task_id="TASK_0055", title="Edit describeTask", status="test")
    preview = bot._build_bug_preview_text(
        task=task,
        description="defectdescribe",
        reproduction="step",
        logs="log",
        reporter="Tester#007",
    )
    assert "Task code: /TASK_0055" in preview
    assert "\\_" not in preview


def test_bug_report_auto_push_success(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=321)
    message.from_user = SimpleNamespace(id=321, full_name="Tester")
    message.text = "✅ Confirm submission"
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_AUTO",
        title="Automatically push tasks",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == task.id
        return task

    add_note_called = False

    async def fake_add_note(task_id: str, *, note_type: str, content: str, actor: str):
        nonlocal add_note_called
        add_note_called = True
        return TaskNoteRecord(
            id=1,
            task_id=task_id,
            note_type=note_type,
            content=content,
            created_at="2025-01-01T00:00:00+08:00",
        )

    logged_events: list[dict] = []

    async def fake_log_event(task_id: str, **kwargs):
        logged_events.append({"task_id": task_id, **kwargs})

    push_calls: list[tuple[int, Optional[str], Optional[str]]] = []

    async def fake_push(
        target_task: TaskRecord,
        *,
        chat_id: int,
        reply_to,
        supplement: Optional[str],
        actor: Optional[str],
        is_bug_report: bool | None = None,
    ):
        assert reply_to is message
        push_calls.append((chat_id, supplement, actor))
        return True, "AUTO_PROMPT", tmp_path / "session.jsonl"

    ack_calls: list[tuple[int, Path | None, DummyMessage | None]] = []

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append((chat_id, session_path, reply_to))

    async def fake_render_detail(task_id: str):
        assert task_id == task.id
        return "TaskDetails:- \n- Example", ReplyKeyboardMarkup(keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "add_note", fake_add_note)
    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render_detail)

    async def scenario() -> Optional[str]:
        await state.set_state(bot.TaskBugReportStates.waiting_confirm)
        await state.update_data(
            task_id=task.id,
            description="defectdescribe",
            reproduction="step",
            logs="log",
            reporter="Tester#001",
        )
        await bot.on_task_bug_confirm(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert push_calls and push_calls[0][0] == message.chat.id
    assert push_calls[0][1] is None
    assert push_calls[0][2] == "Tester#001"
    assert ack_calls and ack_calls[0][0] == message.chat.id
    assert ack_calls[0][2] is message
    assert state_value is None
    assert logged_events and logged_events[0]["task_id"] == task.id
    assert add_note_called is False

    payload = logged_events[0]["payload"]
    assert payload["action"] == "bug_report"
    assert payload["description"] == "defectdescribe"
    assert payload["reproduction"] == "step"
    assert payload["logs"] == "log"
    assert payload["reporter"] == "Tester#001"
    assert payload["has_reproduction"] is True
    assert payload["has_logs"] is True

    assert len(message.calls) == 1
    push_text, push_mode, push_markup, push_kwargs = message.calls[0]
    expected_block, expected_mode = bot._wrap_text_in_code_block("AUTO_PROMPT")
    assert push_text == f"Pushed to model:\n{expected_block}"
    assert push_mode == expected_mode
    assert isinstance(push_markup, ReplyKeyboardMarkup)
    assert push_kwargs.get("disable_notification") is False


def test_bug_report_auto_push_skipped_when_status_not_supported(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=654)
    message.from_user = SimpleNamespace(id=654, full_name="Tester")
    message.text = "Confirm submission"
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_SKIP",
        title="Task is not supported",
        status="unknown",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        return task

    add_note_called = False

    async def fake_add_note(task_id: str, *, note_type: str, content: str, actor: str):
        nonlocal add_note_called
        add_note_called = True
        return TaskNoteRecord(
            id=2,
            task_id=task_id,
            note_type=note_type,
            content=content,
            created_at="2025-01-02T00:00:00+08:00",
        )

    async def fake_render_detail(task_id: str):
        return "Details:-", ReplyKeyboardMarkup(keyboard=[])

    push_called = False

    async def fake_push(*args, **kwargs):
        nonlocal push_called
        push_called = True
        return True, "SHOULD_NOT_CALL", tmp_path / "session.jsonl"

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "add_note", fake_add_note)
    logged_payloads: list[dict] = []

    async def fake_log_event(*args, **kwargs):
        logged_payloads.append(kwargs.get("payload", {}))
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render_detail)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push)
    monkeypatch.setattr(bot, "_send_session_ack", lambda *args, **kwargs: None)

    async def scenario() -> Optional[str]:
        await state.set_state(bot.TaskBugReportStates.waiting_confirm)
        await state.update_data(
            task_id=task.id,
            description="describe",
            reproduction="",
            logs="",
            reporter="Tester",
        )
        await bot.on_task_bug_confirm(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert push_called is False
    assert state_value is None
    assert add_note_called is False
    assert logged_payloads and logged_payloads[0]["action"] == "bug_report"
    assert len(message.calls) == 1
    warning_text, _, warning_markup, _ = message.calls[0]
    assert "The current status does not support automatic push to the model." in warning_text
    assert isinstance(warning_markup, ReplyKeyboardMarkup)


def test_handle_model_response_ignores_non_summary(monkeypatch, tmp_path: Path):
    calls: list[tuple] = []

    async def fake_log(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(bot, "_log_model_reply_event", fake_log)
    bot.PENDING_SUMMARIES.clear()
    session_path = tmp_path / "session.jsonl"
    session_path.write_text("", encoding="utf-8")

    async def scenario() -> None:
        await bot._handle_model_response(
            chat_id=1,
            session_key=str(session_path),
            session_path=session_path,
            event_offset=1,
            content="Normal reply /TASK_0001",
        )

    asyncio.run(scenario())
    bot.PENDING_SUMMARIES.clear()
    assert not calls, "Normal model replies should not be written to history"


def test_handle_model_response_keeps_summary_history(monkeypatch, tmp_path: Path):
    logged: list[dict] = []

    async def fake_log_event(task_id: str, **kwargs):
        logged.append({"task_id": task_id, **kwargs})

    logged_replies: list[tuple] = []

    async def fake_log_reply(*args, **kwargs):
        logged_replies.append((args, kwargs))

    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)
    monkeypatch.setattr(bot, "_log_model_reply_event", fake_log_reply)

    session_path = tmp_path / "summary.jsonl"
    session_path.write_text("", encoding="utf-8")
    session_key = str(session_path)
    request_id = "req123"

    bot.PENDING_SUMMARIES.clear()
    bot.PENDING_SUMMARIES[session_key] = bot.PendingSummary(
        task_id="TASK_0001",
        request_id=request_id,
        actor="tester",
        session_key=session_key,
        session_path=session_path,
        created_at=time.monotonic(),
    )

    async def scenario() -> None:
        await bot._handle_model_response(
            chat_id=1,
            session_key=session_key,
            session_path=session_path,
            event_offset=42,
            content=f"SUMMARY_REQUEST_ID::{request_id}\nSummary content",
        )

    asyncio.run(scenario())
    assert bot.PENDING_SUMMARIES.get(session_key) is None
    assert logged, "Summary should be written into history"
    payload = logged[0]
    assert payload["event_type"] == "model_summary"
    assert payload["task_id"] == "TASK_0001"
    assert not logged_replies, "The summary process should not trigger model_reply Dropped into the library"
    bot.PENDING_SUMMARIES.clear()


def test_handle_model_response_accepts_escaped_summary_tag(monkeypatch, tmp_path: Path):
    logged: list[dict] = []

    async def fake_log_event(task_id: str, **kwargs):
        logged.append({"task_id": task_id, **kwargs})

    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)

    session_path = tmp_path / "summary-escaped.jsonl"
    session_path.write_text("", encoding="utf-8")
    session_key = str(session_path)
    request_id = "req_escape"

    bot.PENDING_SUMMARIES.clear()
    bot.PENDING_SUMMARIES[session_key] = bot.PendingSummary(
        task_id="TASK_0002",
        request_id=request_id,
        actor="tester",
        session_key=session_key,
        session_path=session_path,
        created_at=time.monotonic(),
        buffer="Prefix SUMMARY\\_REQUEST\\_ID::other",
    )

    async def scenario() -> None:
        await bot._handle_model_response(
            chat_id=1,
            session_key=session_key,
            session_path=session_path,
            event_offset=77,
            content=f"SUMMARY\\_REQUEST\\_ID::{request_id}\nSummary contentContains\\_Underline",
        )

    asyncio.run(scenario())
    assert bot.PENDING_SUMMARIES.get(session_key) is None
    assert logged, "Summary should be written into history"
    payload = logged[0]
    assert payload["event_type"] == "model_summary"
    stored_payload = payload["payload"] or {}
    assert "SUMMARY_REQUEST_ID" in stored_payload.get("content", "")
    assert "\\_" not in stored_payload.get("content", ""), "Summary contentshould be stripped of escaping"
    bot.PENDING_SUMMARIES.clear()


@pytest.mark.parametrize(
    "summary_line",
    [
        "SUMMARY_REQUEST_ID: req_multi",
        "SUMMARY_REQUEST_ID：req_multi",
        "**SUMMARY_REQUEST_ID：req_multi**",
    ],
)
def test_handle_model_response_accepts_colon_variants(monkeypatch, tmp_path: Path, summary_line: str):
    logged: list[dict] = []

    async def fake_log_event(task_id: str, **kwargs):
        logged.append({"task_id": task_id, **kwargs})

    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)

    session_path = tmp_path / "summary-colon.jsonl"
    session_path.write_text("", encoding="utf-8")
    session_key = str(session_path)
    request_id = "req_multi"

    bot.PENDING_SUMMARIES.clear()
    bot.PENDING_SUMMARIES[session_key] = bot.PendingSummary(
        task_id="TASK_0003",
        request_id=request_id,
        actor="tester",
        session_key=session_key,
        session_path=session_path,
        created_at=time.monotonic(),
    )

    async def scenario() -> None:
        await bot._handle_model_response(
            chat_id=1,
            session_key=session_key,
            session_path=session_path,
            event_offset=7,
            content=f"{summary_line}\nSummary body",
        )

    asyncio.run(scenario())
    assert bot.PENDING_SUMMARIES.get(session_key) is None
    assert logged, "Summary should be written into history"
    payload = logged[0]
    assert payload["event_type"] == "model_summary"
    assert payload["task_id"] == "TASK_0003"
    bot.PENDING_SUMMARIES.clear()


def test_task_summary_command_triggers_request(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.text = "/task_summary_request_TASK_0200"
    message.chat = SimpleNamespace(id=200)
    message.from_user = SimpleNamespace(id=200, full_name="Tester")

    base_task = TaskRecord(
        id="TASK_0200",
        project_slug="demo",
        title="SummaryTask",
        status="research",
        priority=2,
        description="illustrate",
        parent_id=None,
        root_id="TASK_0200",
        depth=0,
        lineage="0200",
        archived=False,
    )
    updated_task = TaskRecord(
        id="TASK_0200",
        project_slug="demo",
        title="SummaryTask",
        status="test",
        priority=2,
        description="illustrate",
        parent_id=None,
        root_id="TASK_0200",
        depth=0,
        lineage="0200",
        archived=False,
    )

    updates: list[tuple] = []
    dispatch_calls: list[tuple] = []
    log_calls: list[tuple] = []

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0200"
        return base_task

    async def fake_update_task(task_id: str, *, actor, status=None, **kwargs):
        updates.append((task_id, actor, status))
        assert status == "test"
        return updated_task

    async def fake_list_notes(task_id: str):
        return []

    async def fake_history(task_id: str):
        return ("History:\n- Project Entries", 1)

    session_path = tmp_path / "summary_session.jsonl"
    session_path.write_text("", encoding="utf-8")

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool):
        assert ack_immediately is False
        dispatch_calls.append((chat_id, prompt))
        return True, session_path

    async def fake_log_task_action(*args, **kwargs):
        log_calls.append((args, kwargs))

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_notes", fake_list_notes)
    monkeypatch.setattr(bot, "_build_history_context_for_model", fake_history)
    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_log_task_action", fake_log_task_action)

    bot.PENDING_SUMMARIES.clear()

    async def scenario() -> None:
        await bot.on_task_summary_command(message)

    asyncio.run(scenario())

    assert updates, "Task status should be updated to test"
    assert dispatch_calls, "A summary request should be pushed to the model"
    prompt_text = dispatch_calls[0][1]
    assert prompt_text.startswith("Entering the summary stage...")
    assert "\nTask code: /TASK_0200\nSUMMARY_REQUEST_ID::" in prompt_text
    assert message.calls, "The user should be prompted with the processing results"
    reply_text, _, _, _ = message.calls[-1]
    assert "Task status automatically adjusted to 'test'." in reply_text
    assert bot.PENDING_SUMMARIES, "The summary context to be Dropped into the library should be recorded"
    args, kwargs = log_calls[0]
    payload = kwargs["payload"]
    assert payload.get("status_auto_updated") is True
    bot.PENDING_SUMMARIES.clear()


def test_task_summary_command_skips_status_when_already_test(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.text = "/task_summary_request_TASK_0300"
    message.chat = SimpleNamespace(id=300)
    message.from_user = SimpleNamespace(id=300, full_name="Tester")

    task = TaskRecord(
        id="TASK_0300",
        project_slug="demo",
        title="Already have Test tasks",
        status="test",
        priority=2,
        description="illustrate",
        parent_id=None,
        root_id="TASK_0300",
        depth=0,
        lineage="0300",
        archived=False,
    )

    session_path = tmp_path / "summary_session2.jsonl"
    session_path.write_text("", encoding="utf-8")

    async def fake_get_task(task_id: str):
        return task

    async def fake_update_task(*args, **kwargs):
        raise AssertionError("update should not be called when the status is already test_task")

    async def fake_list_notes(task_id: str):
        return []

    async def fake_history(task_id: str):
        return ("", 0)

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool):
        return True, session_path

    async def fake_log_task_action(*args, **kwargs):
        pass

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_notes", fake_list_notes)
    monkeypatch.setattr(bot, "_build_history_context_for_model", fake_history)
    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_log_task_action", fake_log_task_action)

    bot.PENDING_SUMMARIES.clear()

    async def scenario() -> None:
        await bot.on_task_summary_command(message)

    asyncio.run(scenario())
    reply_text, _, _, _ = message.calls[-1]
    assert "TaskStatus has been automatically updated to \"Testing\"" not in reply_text
    bot.PENDING_SUMMARIES.clear()


def test_task_summary_command_handles_missing_task(monkeypatch):
    message = DummyMessage()
    message.text = "/task_summary_request_TASK_0400"
    message.chat = SimpleNamespace(id=400)
    message.from_user = SimpleNamespace(id=400, full_name="Tester")

    async def fake_get_task(task_id: str):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> None:
        await bot.on_task_summary_command(message)

    asyncio.run(scenario())
    reply_text, _, _, _ = message.calls[-1]
    assert reply_text == "Task does not exist"


def test_task_summary_command_accepts_alias_without_underscores(monkeypatch):
    message = DummyMessage()
    message.text = "/tasksummaryrequestTASK_0500"
    message.chat = SimpleNamespace(id=500)
    message.from_user = SimpleNamespace(id=500, full_name="Tester")

    captured: dict[str, str] = {}

    async def fake_get_task(task_id: str):
        captured["task_id"] = task_id
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> None:
        await bot.on_task_summary_command(message)

    asyncio.run(scenario())
    assert captured.get("task_id") == "TASK_0500"
    reply_text, _, _, _ = message.calls[-1]
    assert reply_text == "Task does not exist"


def test_task_summary_command_alias_requires_task_id():
    message = DummyMessage()
    message.text = "/tasksummaryrequest"
    message.chat = SimpleNamespace(id=501)
    message.from_user = SimpleNamespace(id=501, full_name="Tester")

    async def scenario() -> None:
        await bot.on_task_summary_command(message)

    asyncio.run(scenario())
    reply_text, _, _, _ = message.calls[-1]
    assert reply_text == "Please provide a task ID, for example: /task_summary_request_TASK_0001"


def test_ensure_session_watcher_rebinds_pointer(monkeypatch, tmp_path: Path):
    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_LOCK_REQUIRED", False)

    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_COMPACT_STATE.clear()
    bot.CHAT_REPLY_COUNT.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()

    delivered_calls: list[tuple[int, Path]] = []

    async def fake_deliver(chat_id: int, session_path: Path) -> bool:
        delivered_calls.append((chat_id, session_path))
        return False

    monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)

    class DummyTask:
        def __init__(self):
            self._done = False

        def done(self) -> bool:
            return self._done

        def cancel(self) -> None:
            self._done = True

    created_tasks: list = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        return DummyTask()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    result = asyncio.run(bot._ensure_session_watcher(123))

    assert result == session_file
    assert bot.CHAT_SESSION_MAP[123] == str(session_file)
    assert delivered_calls == [(123, session_file)]
    assert isinstance(bot.CHAT_WATCHERS[123], DummyTask)

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - best effort cleanup
            pass

    # Clean up global state to avoid affecting other use cases
    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_COMPACT_STATE.clear()
    bot.CHAT_REPLY_COUNT.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()


@pytest.mark.parametrize(
    "status,description,expected_checks",
    [
        (
            "research",
            "describeA",
            (
                ("startswith", f"{bot.VIBE_PHASE_PROMPT}\nTask Title: CaseTask"),
                ("contains", "Task Description: describeA"),
                ("contains", "Task Notes: -"),
                ("endswith", "Task execution history for traceability: -"),
            ),
        ),
        (
            "research",
            None,
            (
                ("startswith", f"{bot.VIBE_PHASE_PROMPT}\nTask Title: CaseTask"),
                ("contains", "Task Description: -"),
                ("contains", "Task Notes: -"),
                ("endswith", "Task execution history for traceability: -"),
            ),
        ),
        (
            "test",
            "testillustrate",
            (
                ("startswith", f"{bot.VIBE_PHASE_PROMPT}\nTask Title: CaseTask"),
                ("contains", "Task Description: testillustrate"),
                ("contains", "Task Notes: -"),
                ("endswith", "Task execution history for traceability: -"),
            ),
        ),
        (
            "test",
            " ",
            (
                ("startswith", f"{bot.VIBE_PHASE_PROMPT}\nTask Title: CaseTask"),
                ("contains", "Task Description: -"),
                ("contains", "Task Notes: -"),
                ("endswith", "Task execution history for traceability: -"),
            ),
        ),
        (
            "done",
            "",
            (("equals", "/compact"),),
        ),
        (
            "done",
            "Completed",
            (("equals", "/compact"),),
        ),
    ],
)
def test_build_model_push_payload_cases(status, description, expected_checks):
    task = TaskRecord(
        id="TASK_CHECK",
        project_slug="demo",
        title="CaseTask",
        status=status,
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description=description,
        parent_id=None,
        root_id="TASK_CHECK",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    payload = bot._build_model_push_payload(task)
    for kind, expected in expected_checks:
        if kind == "contains":
            assert expected in payload
        elif kind == "equals":
            assert payload == expected
        elif kind == "startswith":
            assert payload.startswith(expected)
        elif kind == "endswith":
            assert payload.endswith(expected)
        else:
            raise AssertionError(f"Unknown assertion type {kind}")


def test_build_model_push_payload_with_supplement():
    task = TaskRecord(
        id="TASK_CHECK_SUP",
        project_slug="demo",
        title="Additional examples",
        status="test",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="originaldescribe",
        parent_id=None,
        root_id="TASK_CHECK_SUP",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    history = "2025-01-01T10:00:00+08:00 | push to model(result=success)\nSupplementary Description: old supplement"

    payload = bot._build_model_push_payload(task, supplement="Supplementary content", history=history)
    lines = payload.splitlines()
    assert lines[0] == bot.VIBE_PHASE_PROMPT
    assert "Task Description: originaldescribe" in payload
    assert "Task Code: /TASK_CHECK_SUP" in payload
    assert "\\_" not in payload
    assert "Task Notes: -" in payload
    assert "Supplementary Description: Supplementary content" in payload
    assert "Task execution history for traceability:" in payload
    assert "2025-01-01T10:00:00+08:00 | push to model(result=success)" in payload
    assert "Supplementary Description: old supplement" in payload
    history_intro_index = payload.index("Task execution history for traceability:")
    assert payload.index("Supplementary Description: Supplementary content") < history_intro_index
    assert payload.endswith("Supplementary Description: old supplement")
    assert "## testing phase" not in payload
    assert "Additional notes on the testing phase:" not in payload


def test_build_model_push_payload_without_history_formatting():
    task = TaskRecord(
        id="TASK_NO_HISTORY",
        project_slug="demo",
        title="noneHistory Task",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="describeB",
        parent_id=None,
        root_id="TASK_NO_HISTORY",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    payload = bot._build_model_push_payload(task)
    assert payload.splitlines()[0] == bot.VIBE_PHASE_PROMPT
    assert "Task Notes: -" in payload
    assert "Task execution history for traceability: -" in payload
    assert payload.endswith("Task execution history for traceability: -")
    assert "needResearch problem analysis stage" not in payload


def test_build_model_push_payload_with_notes():
    task = TaskRecord(
        id="TASK_CHECK_NOTES",
        project_slug="demo",
        title="RemarkTask",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="describeB",
        parent_id=None,
        root_id="TASK_CHECK_NOTES",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    notes = [
        TaskNoteRecord(
            id=1,
            task_id=task.id,
            note_type="misc",
            content="First note",
            created_at="2025-01-01T00:00:00+08:00",
        ),
        TaskNoteRecord(
            id=2,
            task_id=task.id,
            note_type="research",
            content="The second Remark\nContains newlines",
            created_at="2025-01-02T00:00:00+08:00",
        ),
    ]

    payload = bot._build_model_push_payload(task, notes=notes)
    assert "Task Notes: First note；The second Remark / Contains newlines" in payload
    assert payload.startswith(bot.VIBE_PHASE_PROMPT)


def test_build_model_push_payload_skips_bug_notes():
    task = TaskRecord(
        id="TASK_SKIP_BUG",
        project_slug="demo",
        title="defectRemarkneglect",
        status="test",
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description="describeC",
        parent_id=None,
        root_id="TASK_SKIP_BUG",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    notes = [
        TaskNoteRecord(
            id=1,
            task_id=task.id,
            note_type="bug",
            content="defectDetails\nNeeds repair",
            created_at="2025-01-03T00:00:00+08:00",
        ),
        TaskNoteRecord(
            id=2,
            task_id=task.id,
            note_type="misc",
            content="Still need to follow up",
            created_at="2025-01-04T00:00:00+08:00",
        ),
    ]

    payload = bot._build_model_push_payload(task, notes=notes)
    assert "defectDetails" not in payload
    assert "Needs repair" not in payload
    assert "Task Notes: Still need to follow up" in payload
    assert "defectRecords (last 3)" not in payload
    assert payload.startswith(bot.VIBE_PHASE_PROMPT)


def test_build_model_push_payload_removes_legacy_bug_header():
    task = _make_task(task_id="TASK_LEGACY", title="Compatible with old title", status="test")
    legacy_history = "defectRecords (last 3): \n2025-01-02 10:00 | History synced"

    payload = bot._build_model_push_payload(task, history=legacy_history)

    assert "defectRecords (last 3)" not in payload
    assert "2025-01-02 10:00 | History synced" in payload
    assert "Task execution history for traceability:" in payload


# --- Task description edit interaction ---


def _extract_reply_labels(markup: ReplyKeyboardMarkup | None) -> list[str]:
    if not isinstance(markup, ReplyKeyboardMarkup):
        return []
    labels: list[str] = []
    for row in markup.keyboard:
        for button in row:
            labels.append(button.text)
    return labels


def test_task_desc_edit_shows_menu_options(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("task:desc_edit:TASK_EDIT", message)
    state, _storage = make_state(message)

    task = _make_task(task_id="TASK_EDIT", title="ExampleTask", status="research")
    task.description = "originaldescribe"

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_EDIT"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> tuple[str | None, dict]:
        await bot.on_task_desc_edit(callback, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("task_id") == "TASK_EDIT"
    assert data.get("current_description") == "originaldescribe"
    assert callback.answers and callback.answers[-1] == (None, False)
    assert len(message.calls) >= 3, "The menu and original describe should be displayed first and then prompted for input."
    first_text, _parse_mode, first_markup, _ = message.calls[0]
    assert DESC_PROMPT_PREFIX in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)
    labels = _extract_reply_labels(first_markup)
    assert any(bot.TASK_DESC_CLEAR_TEXT in label for label in labels)
    assert any(bot.TASK_DESC_CANCEL_TEXT in label for label in labels)
    assert any(bot.TASK_DESC_REPROMPT_TEXT in label for label in labels)
    third_text, _, third_markup, _ = message.calls[2]
    assert "Send the new task description or choose an action from the menu." in third_text
    assert third_markup is None


def test_task_edit_description_redirects_to_fsm(monkeypatch):
    message = DummyMessage()
    state, _storage = make_state(message)
    task = _make_task(task_id="TASK_EDIT", title="ExampleTask", status="research")
    task.description = "originaldescribe"

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_EDIT"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", actor="Tester#1")
        await state.set_state(bot.TaskEditStates.waiting_field_choice)
        message.text = "describe"
        await bot.on_edit_field_choice(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("task_id") == "TASK_EDIT"
    assert data.get("current_description") == "originaldescribe"
    assert len(message.calls) >= 3
    first_text, _, first_markup, _ = message.calls[0]
    assert DESC_PROMPT_PREFIX in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)


def test_task_desc_reprompt_menu_replays_prompt():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", current_description="olddescribe")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        message.text = f"1. {bot.TASK_DESC_REPROMPT_TEXT}"
        await bot.on_task_desc_input(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("current_description") == "olddescribe"
    assert len(message.calls) >= 3
    first_text, _, first_markup, _ = message.calls[-3]
    assert DESC_PROMPT_PREFIX in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)


def test_task_desc_input_clear_menu_enters_confirm():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", actor="Tester#1", current_description="olddescribe")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        message.text = bot.TASK_DESC_CLEAR_TEXT
        await bot.on_task_desc_input(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert data.get("new_description") == ""
    assert message.calls, "A confirmation prompt should be sent"
    confirm_text, _, confirm_markup, _ = message.calls[-1]
    assert "Please confirm the updated task description:" in confirm_text
    assert isinstance(confirm_markup, ReplyKeyboardMarkup)
    labels = _extract_reply_labels(confirm_markup)
    assert any(bot.TASK_DESC_CONFIRM_TEXT in label for label in labels)
    assert any(bot.TASK_DESC_RETRY_TEXT in label for label in labels)


def test_task_desc_input_moves_to_confirm():
    message = DummyMessage()
    message.text = "new description"
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", actor="Tester#1", current_description="olddescribe")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_input(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert data.get("new_description") == "new description"
    assert message.calls, "A confirmation prompt should be sent"
    confirm_text, _, confirm_markup, _ = message.calls[-1]
    assert "Please confirm the updated task description:" in confirm_text
    assert isinstance(confirm_markup, ReplyKeyboardMarkup)


def test_task_desc_input_cancel_text():
    message = DummyMessage()
    message.text = "Cancel"
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        await state.update_data(task_id="TASK_EDIT", current_description="olddescribe")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_input(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and message.calls[-1][0] == "Task description editing cancelled."


def test_task_desc_input_cancel_menu_button():
    message = DummyMessage()
    message.text = f"1. {bot.TASK_DESC_CANCEL_TEXT}"
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        await state.update_data(task_id="TASK_EDIT", current_description="olddescribe")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_input(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and message.calls[-1][0] == "Task description editing cancelled."


def test_task_desc_input_rejects_too_long():
    message = DummyMessage()
    message.text = "x" * (bot.DESCRIPTION_MAX_LENGTH + 1)
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        await state.update_data(task_id="TASK_EDIT", current_description="olddescribe")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_input(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert len(message.calls) >= 4, "If it is too long, you need to prompt for input again."
    warn_text, _, warn_markup, _ = message.calls[0]
    assert "cannot exceed" in warn_text
    assert isinstance(warn_markup, ReplyKeyboardMarkup)
    tail_text, _, tail_markup, _ = message.calls[-1]
    assert "Send the new task description or choose an action from the menu." in tail_text
    assert tail_markup is None


def test_task_desc_confirm_updates_description(monkeypatch):
    message = DummyMessage()
    state, _storage = make_state(message)

    updated_task = _make_task(task_id="TASK_EDIT", title="describeTask", status="research")
    update_calls: list[tuple[str, str, str]] = []

    async def fake_update(task_id: str, *, actor: str, description: str):
        update_calls.append((task_id, actor, description))
        updated_task.description = description
        return updated_task

    async def fake_render(task_id: str):
        assert task_id == "TASK_EDIT"
        return "TaskDetails: Example", ReplyKeyboardMarkup(keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)

    async def scenario() -> str | None:
        message.text = bot.TASK_DESC_CONFIRM_TEXT
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="finally describe",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert update_calls == [("TASK_EDIT", "Tester#1", "finally describe")]
    assert message.calls and "Task description updated" in message.calls[0][0]
    assert any("Task description updated" in text for text, *_ in message.calls)


def test_task_desc_confirm_requires_state():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        await state.clear()
        message.text = bot.TASK_DESC_CONFIRM_TEXT
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and "The session has expired, please operate again." in message.calls[0][0]


def test_task_desc_retry_returns_to_input(monkeypatch):
    message = DummyMessage()
    state, _storage = make_state(message)

    task = _make_task(task_id="TASK_EDIT", title="describeTask", status="research")
    task.description = "originaldescribe"

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_EDIT"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> tuple[str | None, dict]:
        message.text = bot.TASK_DESC_RETRY_TEXT
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="draftdescribe",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("new_description") is None
    assert len(message.calls) >= 4
    first_text, _, first_markup, _ = message.calls[0]
    assert "Returned to the description input stage" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)
    assert any(DESC_PROMPT_PREFIX in text for text, *_ in message.calls)


def test_task_desc_confirm_missing_description_reprompts():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        message.text = bot.TASK_DESC_CONFIRM_TEXT
        await state.update_data(
            task_id="TASK_EDIT",
            current_description="stillolddescribe",
            actor="Tester#1",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("new_description") is None
    assert len(message.calls) >= 4
    first_text, _, first_markup, _ = message.calls[0]
    assert "The draft description has expired" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)
    assert any("stillolddescribe" in text for text, *_ in message.calls)


def test_task_desc_retry_task_missing(monkeypatch):
    message = DummyMessage()
    state, _storage = make_state(message)

    async def fake_get_task(task_id: str):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> str | None:
        message.text = bot.TASK_DESC_RETRY_TEXT
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="draftdescribe",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and "Task not found. The editing process has ended." in message.calls[0][0]


def test_task_desc_confirm_update_failure(monkeypatch):
    message = DummyMessage()
    state, _storage = make_state(message)

    async def fake_update(task_id: str, *, actor: str, description: str):
        raise ValueError("Unable to update describe")

    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update)

    async def scenario() -> str | None:
        message.text = bot.TASK_DESC_CONFIRM_TEXT
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="Exceptiondescribe",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and message.calls[0][0] == "Unable to update describe"


def test_task_desc_confirm_unknown_message_prompts_menu():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = "Enter whatever you want"
        await state.update_data(task_id="TASK_EDIT", new_description="draft", actor="Tester#1")
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert message.calls and ("Please use the button in the menu" in message.calls[-1][0] or "Currently in the confirmation stage" in message.calls[-1][0])
    assert isinstance(message.calls[-1][2], ReplyKeyboardMarkup)


def test_task_desc_confirm_cancel_menu_exits():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = bot.TASK_DESC_CANCEL_TEXT
        await state.update_data(task_id="TASK_EDIT", new_description="draft")
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and message.calls[-1][0] == "Task description editing cancelled."


def test_task_desc_legacy_callback_reprompts_input():
    message = DummyMessage()
    callback = DummyCallback(f"{bot.TASK_DESC_INPUT_CALLBACK}:TASK_EDIT", message)
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", current_description="olddescribe")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_legacy_callback(callback, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("current_description") == "olddescribe"
    assert callback.answers and callback.answers[-1] == ("Task description editing is now available from the menu. Please use the menu options.", True)
    assert len(message.calls) >= 3
    first_text, _, first_markup, _ = message.calls[0]
    assert DESC_PROMPT_PREFIX in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)


def test_task_desc_legacy_callback_replays_confirm():
    message = DummyMessage()
    callback = DummyCallback(f"{bot.TASK_DESC_CONFIRM_CALLBACK}:TASK_EDIT", message)
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", new_description="draftdescribe")
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_legacy_callback(callback, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert data.get("new_description") == "draftdescribe"
    assert callback.answers and callback.answers[-1] == ("Task description editing is now available from the menu. Please use the menu options.", True)
    assert message.calls and "Please confirm the updated task description:" in message.calls[-1][0]
    assert isinstance(message.calls[-1][2], ReplyKeyboardMarkup)


def test_format_history_description_push_model_includes_supplement():
    record = TaskHistoryRecord(
        id=1,
        task_id="TASK_001",
        field="",
        old_value=None,
        new_value="old supplement",
        actor="tester",
        event_type=bot.HISTORY_EVENT_TASK_ACTION,
        payload=json.dumps(
            {
                "action": "push_model",
                "result": "success",
                "model": "codex",
                "supplement": "Latest addition describe",
            }
        ),
        created_at="2025-01-01T00:00:00+08:00",
    )

    text = bot._format_history_description(record)
    assert "Result: success" in text
    assert "Model: codex" in text
    assert "Supplementary Description: Latest addition describe" in text


def test_normalize_task_id_accepts_legacy_variants():
    assert bot._normalize_task_id("/TASK-0001") == "TASK_0001"
    assert bot._normalize_task_id("TASK-0002.3") == "TASK_0002_3"
    assert bot._normalize_task_id("/TASK0035") == "TASK_0035"
    assert bot._normalize_task_id("/task_show") is None
    assert bot._normalize_task_id("/TASK_0001@demo_bot") == "TASK_0001"


def test_format_task_command_respects_markdown_escape(monkeypatch):
    monkeypatch.setattr(bot, "_IS_MARKDOWN", True)
    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", False)
    assert bot._format_task_command("TASK_0001") == "/TASK\\_0001"
    monkeypatch.setattr(bot, "_IS_MARKDOWN", False)
    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", True)
    assert bot._format_task_command("TASK_0001") == "/TASK_0001"


def test_is_cancel_message_handles_menu_button():
    assert bot._is_cancel_message(bot.TASK_DESC_CANCEL_TEXT)
    assert bot._is_cancel_message(f"2. {bot.TASK_DESC_CANCEL_TEXT}")
    assert not bot._is_cancel_message("Continue editing")


def test_on_text_handles_quick_task_lookup(monkeypatch):
    message = DummyMessage()
    message.text = "/TASK_0007"
    calls: list[tuple[DummyMessage, str]] = []

    async def fake_reply(detail_message: DummyMessage, task_id: str) -> None:
        calls.append((detail_message, task_id))

    monkeypatch.setattr(bot, "_reply_task_detail_message", fake_reply)

    asyncio.run(bot.on_text(message))

    assert calls == [(message, "TASK_0007")]


def test_on_text_ignores_regular_commands(monkeypatch):
    message = DummyMessage()
    message.text = "/task_show"

    async def fake_reply(detail_message: DummyMessage, task_id: str) -> None:  # pragma: no cover
        raise AssertionError("should not triggerTaskDetailsreply")

    monkeypatch.setattr(bot, "_reply_task_detail_message", fake_reply)

    asyncio.run(bot.on_text(message))


def test_on_text_skips_model_dispatch_during_task_creation(monkeypatch):
    message = DummyMessage()
    message.text = "Implement customer portal"

    async def fake_dispatch(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("model dispatch should be suppressed during wizard input")

    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_dispatch)

    state, _storage = make_state(message)

    async def scenario() -> None:
        await state.set_state(bot.TaskCreateStates.waiting_title.state)
        await bot.on_text(message, state)

    asyncio.run(scenario())


def test_on_text_ignores_numbered_skip_without_state(monkeypatch):
    message = DummyMessage()
    message.text = f"1. {bot.SKIP_TEXT}"
    dispatch_calls: list[tuple] = []

    async def fake_dispatch(*_args, **_kwargs):  # pragma: no cover
        dispatch_calls.append((_args, _kwargs))

    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_dispatch)

    asyncio.run(bot.on_text(message))

    assert not dispatch_calls, "Skip menu input should not reach model dispatch"
    assert not message.calls, "No extra prompts should be sent for suppressed skip input"


def test_on_text_ignores_numbered_cancel_without_state(monkeypatch):
    message = DummyMessage()
    message.text = f"2. {bot.TASK_DESC_CANCEL_TEXT}"
    dispatch_calls: list[tuple] = []

    async def fake_dispatch(*_args, **_kwargs):  # pragma: no cover
        dispatch_calls.append((_args, _kwargs))

    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_dispatch)

    asyncio.run(bot.on_text(message))

    assert not dispatch_calls, "Cancel menu input should not reach model dispatch"
    assert not message.calls, "Suppressing cancel input should stay silent"


def test_handle_prompt_dispatch_rejects_menu_control():
    message = DummyMessage()
    menu_input = f"1. {bot.SKIP_TEXT}"

    asyncio.run(bot._handle_prompt_dispatch(message, menu_input))

    assert not message.calls, "Menu control inputs should be ignored silently"


def test_dispatch_prompt_to_model_rejects_menu_control(monkeypatch):
    replies: list[tuple[int, str]] = []

    async def fake_reply(chat_id: int, text: str, reply_to=None, **kwargs):  # pragma: no cover
        replies.append((chat_id, text))

    def fake_tmux(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Menu control prompt should never reach tmux")

    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)
    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux)

    result = asyncio.run(
        bot._dispatch_prompt_to_model(
            1234,
            f"2. {bot.TASK_DESC_CANCEL_TEXT}",
            reply_to=None,
        )
    )

    assert result == (False, None)
    assert replies, "User should be informed that menu control inputs are ignored"
    assert "ignored" in replies[0][1], "Reply must explain why the prompt was dropped"


def test_on_task_quick_command_handles_slash_task(monkeypatch):
    message = DummyMessage()
    message.text = "/TASK_0042"
    calls: list[tuple[DummyMessage, str]] = []

    async def fake_reply(detail_message: DummyMessage, task_id: str) -> None:
        calls.append((detail_message, task_id))

    monkeypatch.setattr(bot, "_reply_task_detail_message", fake_reply)

    asyncio.run(bot.on_task_quick_command(message))

    assert calls == [(message, "TASK_0042")]


def test_task_service_migrates_legacy_ids(tmp_path: Path):
    async def _scenario() -> tuple[TaskRecord, TaskRecord, TaskRecord, list[TaskNoteRecord], list[TaskHistoryRecord], str, dict]:
        db_path = tmp_path / "legacy.db"
        first_service = TaskService(db_path, "legacy")
        await first_service.initialize()

        created = "2025-01-01T00:00:00+08:00"
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
            """
            INSERT INTO tasks (
                id, project_slug, root_id, parent_id, depth, lineage,
                title, status, priority, task_type, tags, due_date, description,
                created_at, updated_at, archived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TASK-0001",
                "legacy",
                "TASK-0001",
                None,
                0,
                "0001",
                "Root Task",
                "research",
                3,
                "task",
                "[]",
                None,
                "",
                created,
                created,
                0,
            ),
        )
            await db.execute(
            """
            INSERT INTO tasks (
                id, project_slug, root_id, parent_id, depth, lineage,
                title, status, priority, task_type, tags, due_date, description,
                created_at, updated_at, archived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TASK-0001.1",
                "legacy",
                "TASK-0001",
                "TASK-0001",
                1,
                "0001.0001",
                "subtask",
                "test",
                2,
                "task",
                "[]",
                None,
                "subtaskdescribe",
                created,
                created,
                0,
            ),
        )
            await db.execute(
            """
            INSERT INTO tasks (
                id, project_slug, root_id, parent_id, depth, lineage,
                title, status, priority, task_type, tags, due_date, description,
                created_at, updated_at, archived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TASK0002",
                "legacy",
                "TASK0002",
                None,
                0,
                "0002",
                "secondRoot Task",
                "research",
                3,
                "task",
                "[]",
                None,
                "",
                created,
                created,
                0,
            ),
        )
            await db.execute(
            "INSERT INTO task_notes(task_id, note_type, content, created_at) VALUES (?, ?, ?, ?)",
            ("TASK-0001", "misc", "Remark content", created),
        )
            await db.execute(
            """
            INSERT INTO task_history(task_id, field, old_value, new_value, actor, event_type, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TASK-0001",
                "status",
                "research",
                "test",
                "tester",
                "field_change",
                None,
                created,
            ),
        )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS child_sequences(task_id TEXT PRIMARY KEY, last_child INTEGER NOT NULL)"
            )
            await db.execute(
            "INSERT INTO child_sequences(task_id, last_child) VALUES (?, ?)",
            ("TASK-0001", 1),
        )
            await db.commit()

        migrated_service = TaskService(db_path, "legacy")
        await migrated_service.initialize()

        root = await migrated_service.get_task("TASK-0001")
        child = await migrated_service.get_task("TASK-0001.1")
        second_root = await migrated_service.get_task("TASK0002")
        notes = await migrated_service.list_notes("TASK-0001")
        history = await migrated_service.list_history("TASK-0001")

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='child_sequences'"
            ) as cursor:
                row = await cursor.fetchone()
            child_sequence_exists = row is not None

        report_dir = db_path.parent / "backups"
        reports = list(report_dir.glob("legacy_id_migration_*.json"))
        report_data = json.loads(reports[0].read_text()) if reports else {}

        return root, child, second_root, notes, history, child_sequence_exists, report_data

    root, child, second_root, notes, history, child_sequence_exists, report_data = asyncio.run(_scenario())

    assert root and root.id == "TASK_0001"
    assert child and child.id == "TASK_0001_1"
    assert child.archived is True
    assert second_root and second_root.id == "TASK_0002"
    assert notes and notes[0].task_id == "TASK_0001"
    assert history and history[0].task_id == "TASK_0001"
    assert not child_sequence_exists
    assert report_data.get("changed") == 3


def test_task_list_outputs_detail_buttons(monkeypatch, tmp_path: Path):
    async def _scenario() -> tuple[DummyMessage, str]:
        svc = TaskService(tmp_path / "tasks.db", "demo")
        await svc.initialize()
        task = await svc.create_root_task(
            title="List example",
            status="research",
            priority=3,
            task_type="task",
            tags=(),
            due_date=None,
            description="describeA",
            actor="tester",
        )
        monkeypatch.setattr(bot, "TASK_SERVICE", svc)

        message = DummyMessage()
        message.text = "/task_list"
        message.chat = SimpleNamespace(id=1)
        message.from_user = SimpleNamespace(full_name="Tester", id=1)
        await bot.on_task_list(message)
        return message, task.id

    message, task_id = asyncio.run(_scenario())
    assert message.calls, "A list message should be generated"
    text, parse_mode, markup, _ = message.calls[0]
    lines = text.splitlines()
    assert lines[:3] == [
        "*task list*",
        "Filter state: all",
        "Paging info: page 1/1, 10 items per page, total 1",
    ]
    assert "- 🛠️ List example" not in text
    assert "- ⚪ List example" not in text
    assert f"[{task_id}]" not in text
    assert markup is not None
    status_rows: list[list] = []
    for row in markup.inline_keyboard:
        if any(btn.callback_data.startswith("task:detail") for btn in row):
            break
        status_rows.append(row)
    assert status_rows, "Status filter button row should exist"
    first_row = status_rows[0]
    assert first_row[0].text == "✔️ ⭐ All"
    assert all(not btn.text.lstrip().startswith(tuple("0123456789")) for row in status_rows for btn in row)
    options_count = len(bot.STATUS_FILTER_OPTIONS)
    if options_count <= 4:
        assert len(status_rows) == 1
        assert len(status_rows[0]) == options_count
    else:
        assert all(len(row) <= 3 for row in status_rows), "There should be no more than three status buttons per row"
    assert any(
        btn.callback_data == "task:list_page:-:1:10"
        for row in status_rows
        for btn in row
    ), "Should contain a button to filter all"
    detail_texts = [
        btn.text
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data == f"task:detail:{task_id}"
    ]
    assert detail_texts, "Should contain a button to jump to details"
    assert "🛠️" in detail_texts[0], "Details button text should display type icon"


def test_task_desc_confirm_numeric_input_1_confirms(monkeypatch):
    """Test input numbers"1"Confirm update action should be triggered"""
    message = DummyMessage()
    state, _storage = make_state(message)

    update_calls = []

    async def fake_update_task(task_id: str, *, actor: str, **kwargs) -> TaskRecord:
        update_calls.append((task_id, actor, kwargs.get("description")))
        return _make_task(task_id=task_id, title="Task", status="research")

    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)

    async def fake_render_task_detail(task_id: str):
        return "TaskDetails", None

    monkeypatch.setattr(bot, "_render_task_detail", fake_render_task_detail)

    async def scenario() -> str | None:
        message.text = "1"  # Enter the number 1, which should correspond to the first option"Confirm update"
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="new descriptioncontent",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None, "The status should be cleared after confirmation"
    assert update_calls == [("TASK_EDIT", "Tester#1", "new descriptioncontent")], "Update Task should be called"
    assert message.calls and "Task description updated" in message.calls[0][0]


def test_task_desc_confirm_numeric_input_2_retries(monkeypatch):
    """Test input numbers"2"A re-enter action should be triggered"""
    message = DummyMessage()
    state, _storage = make_state(message)

    task = _make_task(task_id="TASK_EDIT", title="describeTask", status="research")
    task.description = "originaldescribe"

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_EDIT"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> tuple[str | None, dict]:
        message.text = "2"  # Enter the number 2, which should correspond to the second option"Re-enter"
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="draftdescribe",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state, "Should return to input state"
    assert data.get("new_description") is None, "draftdescribe should be cleared"
    assert len(message.calls) >= 4
    first_text, _, first_markup, _ = message.calls[0]
    assert "Returned to the description input stage" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)


def test_task_desc_confirm_numeric_input_3_cancels():
    """Test input numbers"3"Cancel operation should be triggered"""
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = "3"  # Enter the number 3, which should correspond to the third option"Cancel"
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="draftdescribe",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None, "CancelThe status should be cleared after"
    assert message.calls and "Task description editing cancelled." in message.calls[0][0]
    _, _, markup, _ = message.calls[0]
    assert isinstance(markup, ReplyKeyboardMarkup), "The main menu keyboard should be displayed"


def test_task_desc_confirm_numeric_input_with_prefix():
    """Test that numbered button text such as "1. ✅ Confirm update" is recognized"""
    message = DummyMessage()
    state, _storage = make_state(message)

    update_calls = []

    async def fake_update_task(task_id: str, *, actor: str, **kwargs) -> TaskRecord:
        update_calls.append((task_id, actor, kwargs.get("description")))
        return _make_task(task_id=task_id, title="Task", status="research")

    def monkeypatch_update():
        import bot as bot_module
        original_update = bot_module.TASK_SERVICE.update_task
        bot_module.TASK_SERVICE.update_task = fake_update_task
        return original_update

    async def fake_render_task_detail(task_id: str):
        return "TaskDetails", None

    def monkeypatch_render():
        import bot as bot_module
        original_render = bot_module._render_task_detail
        bot_module._render_task_detail = fake_render_task_detail
        return original_render

    async def scenario() -> str | None:
        message.text = "1. ✅ Confirm update"  # Full button text with sequence number and emoji
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="new descriptioncontent",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)

        # Temporary replacement function
        original_update = monkeypatch_update()
        original_render = monkeypatch_render()

        try:
            await bot.on_task_desc_confirm_stage_text(message, state)
            return await state.get_state()
        finally:
            # Restore original function
            bot.TASK_SERVICE.update_task = original_update
            bot._render_task_detail = original_render

    state_value = asyncio.run(scenario())

    assert state_value is None, "The status should be cleared after confirmation"
    assert update_calls == [("TASK_EDIT", "Tester#1", "new descriptioncontent")], "Update Task should be called"
    assert message.calls and "Task description updated" in message.calls[0][0]


def test_task_desc_confirm_text_input_still_works():
    """Test text input directly (e.g."confirm", "Cancel")still valid"""
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = "Cancel"  # Enter text directly
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="draftdescribe",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None, "CancelThe status should be cleared after"
    assert message.calls and "Task description editing cancelled." in message.calls[0][0]


def test_task_desc_confirm_invalid_numeric_input():
    """Test input of invalid numbers (e.g."0", "99")Should be prompted to reselect"""
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = "99"  # Number out of range
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="draftdescribe",
            actor="Tester#1",
            current_description="olddescribe",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    # Should remain in the confirm state and prompt the user
    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert message.calls
    assert "Currently in the confirmation stage" in message.calls[0][0] or "Please select" in message.calls[0][0]
