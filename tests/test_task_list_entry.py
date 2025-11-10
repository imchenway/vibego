import asyncio
from datetime import datetime, UTC
from types import MethodType, SimpleNamespace
import pytest
import bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, InlineKeyboardMarkup, Message, User
from tasks.models import TaskRecord
from tasks.service import TaskService


class DummyCallback:
    def __init__(self, message, user, data):
        self.message = message
        self.from_user = user
        self.data = data
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(
            {
                "text": text,
                "show_alert": show_alert,
            }
        )

class DummyMessage:
    def __init__(self, text=""):
        self.text = text
        self.calls = []
        self.edits = []
        self.chat = SimpleNamespace(id=1)
        self.from_user = SimpleNamespace(id=1, full_name="Tester")
        self.message_id = 100

    async def answer(self, text, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return SimpleNamespace(message_id=len(self.calls))

    async def edit_text(self, text, parse_mode=None, reply_markup=None, **kwargs):
        self.edits.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return SimpleNamespace(message_id=len(self.edits))


def make_state(message: DummyMessage) -> tuple[FSMContext, MemoryStorage]:
    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=999, chat_id=message.chat.id, user_id=message.from_user.id),
    )
    return state, storage

def test_task_list_view_contains_create_button(monkeypatch):
    class DummyService:
        async def paginate(self, **kwargs):
            return [], 1

        async def count_tasks(self, **kwargs):
            return 0

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    text, markup = asyncio.run(bot._build_task_list_view(status=None, page=1, limit=10))

    assert text.startswith("*task list*")
    buttons = [button.text for row in markup.inline_keyboard for button in row]
    assert "🔍 search tasks" in buttons
    assert "➕ Create tasks" in buttons


def test_task_list_view_renders_entries_with_icons(monkeypatch):
    task = TaskRecord(
        id="TASK_9001",
        project_slug="demo",
        title="Fix login issues",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="",
        parent_id=None,
        root_id="TASK_9001",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    class DummyService:
        async def paginate(self, **kwargs):
            return [task], 1

        async def count_tasks(self, **kwargs):
            return 1

    monkeypatch.setattr(bot, "TASK_SERVICE", DummyService())

    text, markup = asyncio.run(bot._build_task_list_view(status=None, page=1, limit=10))

    assert "- 🛠️ Fix login issues" not in text
    assert "- ⚪ Fix login issues" not in text
    detail_buttons = [
        button.text
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("task:detail")
    ]
    assert detail_buttons
    status_icon = bot._status_icon(task.status)
    type_icon = bot.TASK_TYPE_EMOJIS.get(task.task_type) or "⚪"
    expected_prefix = f"{status_icon} {type_icon} "
    assert detail_buttons[0].startswith(expected_prefix)
    assert "Fix login issues" in detail_buttons[0]


def test_task_list_create_callback_forwards_command(monkeypatch):
    dummy_bot = SimpleNamespace()
    monkeypatch.setattr(bot, "current_bot", lambda: dummy_bot)

    captured = {}

    async def fake_feed_update(bot_obj, update):
        captured["bot"] = bot_obj
        captured["update"] = update

    monkeypatch.setattr(bot.dp, "feed_update", fake_feed_update)  # type: ignore[attr-defined]

    chat = Chat.model_construct(id=1, type="private")
    bot_user = User.model_construct(id=999, is_bot=True, first_name="Bot")
    human_user = User.model_construct(id=123, is_bot=False, first_name="Tester")
    base_message = Message.model_construct(
        message_id=42,
        date=datetime.now(UTC),
        chat=chat,
        text="*task list*",
        from_user=bot_user,
    )
    callback = DummyCallback(base_message, human_user, bot.TASK_LIST_CREATE_CALLBACK)

    asyncio.run(bot.on_task_list_create(callback))  # type: ignore[arg-type]

    assert callback.answers and callback.answers[-1]["text"] is None
    assert captured["bot"] is dummy_bot
    update = captured["update"]
    assert update.message.text == "/task_new"
    assert update.message.from_user.id == human_user.id
    assert any(entity.type == "bot_command" for entity in update.message.entities or [])


def test_worker_create_button_triggers_task_new(monkeypatch):
    captured = {}

    async def fake_dispatch(message, actor):
        captured["message"] = message
        captured["actor"] = actor

    monkeypatch.setattr(bot, "_dispatch_task_new_command", fake_dispatch)

    chat = Chat.model_construct(id=2, type="private")
    human_user = User.model_construct(id=321, is_bot=False, first_name="Tester")
    message = Message.model_construct(
        message_id=77,
        date=datetime.now(UTC),
        chat=chat,
        text=bot.WORKER_CREATE_TASK_BUTTON_TEXT,
        from_user=human_user,
    )

    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=999, chat_id=chat.id, user_id=human_user.id),
    )

    async def _scenario():
        await state.set_state(bot.TaskCreateStates.waiting_title.state)
        await bot.on_task_create_button(message, state)
        assert await state.get_state() is None

    asyncio.run(_scenario())

    assert captured["message"] is message
    assert captured["actor"] is human_user


def test_compose_task_button_label_truncates_but_keeps_status():
    long_title = "This is a very long task title to verify that the status icon remains at the end of the button and is not truncated or lost"
    task = TaskRecord(
        id="TASK_LONG",
        project_slug="demo",
        title=long_title,
        status="test",
        priority=3,
        task_type="defect",
        tags=(),
        due_date=None,
        description="",
        parent_id=None,
        root_id="TASK_LONG",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    label = bot._compose_task_button_label(task, max_length=40)
    status_icon = bot._status_icon(task.status)
    assert status_icon
    type_icon = bot.TASK_TYPE_EMOJIS.get(task.task_type) or "⚪"
    expected_prefix = f"{status_icon} {type_icon} "
    assert label.startswith(expected_prefix)
    assert len(label) <= 40
    assert "…" in label


@pytest.mark.parametrize(
    "case",
    [
        {
            "name": "normal_case",
            "title": "Fix login issues",
            "status": "research",
            "task_type": "task",
            "max_length": 60,
            "expect_prefix": f"{bot._status_icon('research')} {bot.TASK_TYPE_EMOJIS['task']} ",
            "expect_contains": "Fix login issues",
            "expect_ellipsis": False,
        },
        {
            "name": "no_status",
            "title": "Without status",
            "status": "",
            "task_type": "task",
            "max_length": 30,
            "expect_prefix": f"{bot.TASK_TYPE_EMOJIS['task']} ",
            "expect_contains": "Without status",
            "expect_ellipsis": False,
        },
        {
            "name": "unknown_status",
            "title": "unknown status",
            "status": "blocked",
            "task_type": "task",
            "max_length": 30,
            "expect_prefix": f"{bot.TASK_TYPE_EMOJIS['task']} ",
            "expect_contains": "unknown status",
            "expect_ellipsis": False,
        },
        {
            "name": "no_type",
            "title": "Untyped tasks",
            "status": "research",
            "task_type": None,
            "max_length": 40,
            "expect_prefix": f"{bot._status_icon('research')} ⚪ ",
            "expect_contains": "Untyped tasks",
            "expect_ellipsis": False,
        },
        {
            "name": "long_title_truncated",
            "title": "This title is super long and needs to be cut off to fit into the button.",
            "status": "test",
            "task_type": "defect",
            "max_length": 20,
            "expect_prefix": f"{bot._status_icon('test')} {bot.TASK_TYPE_EMOJIS['defect']} ",
            "expect_contains": "This title is super long",
            "expect_ellipsis": True,
        },
        {
            "name": "tight_limit",
            "title": "Very short limit",
            "status": "test",
            "task_type": "risk",
            "max_length": 8,
            "expect_prefix": f"{bot._status_icon('test')} {bot.TASK_TYPE_EMOJIS['risk']} ",
            "expect_exact": "🧪 ⚠️ Ve…",
            "expect_ellipsis": True,
        },
        {
            "name": "empty_title",
            "title": "",
            "status": "done",
            "task_type": "requirement",
            "max_length": 20,
            "expect_prefix": f"{bot._status_icon('done')} {bot.TASK_TYPE_EMOJIS['requirement']} ",
            "expect_exact": "✅ 📌 -",
            "expect_ellipsis": False,
        },
        {
            "name": "emoji_title",
            "title": "🔥 Emergency treatment",
            "status": "done",
            "task_type": "risk",
            "max_length": 25,
            "expect_prefix": f"{bot._status_icon('done')} {bot.TASK_TYPE_EMOJIS['risk']} ",
            "expect_contains": "🔥 Emergency treatment",
            "expect_ellipsis": True,
        },
        {
            "name": "multibyte_length",
            "title": "Multibyte header test",
            "status": "research",
            "task_type": "defect",
            "max_length": 15,
            "expect_prefix": f"{bot._status_icon('research')} {bot.TASK_TYPE_EMOJIS['defect']} ",
            "expect_contains": "Multibyte header test",
            "expect_ellipsis": True,
        },
        {
            "name": "status_alias",
            "title": "Alias state",
            "status": "Research",
            "task_type": "task",
            "max_length": 30,
            "expect_prefix": f"{bot._status_icon('Research')} {bot.TASK_TYPE_EMOJIS['task']} ",
            "expect_contains": "Alias state",
            "expect_ellipsis": False,
        },
    ],
    ids=lambda case: case["name"],
)
def test_compose_task_button_label_various_cases(case):
    task = TaskRecord(
        id=f"TASK_CASE_{case['name']}",
        project_slug="demo",
        title=case["title"],
        status=case["status"],
        priority=3,
        task_type=case["task_type"],
        tags=(),
        due_date=None,
        description="",
        parent_id=None,
        root_id=f"TASK_CASE_{case['name']}",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    label = bot._compose_task_button_label(task, max_length=case["max_length"])

    assert len(label) <= case["max_length"]
    expected_prefix = case.get("expect_prefix")
    if expected_prefix is not None:
        assert label.startswith(expected_prefix)
    content_segment = label[len(expected_prefix) :] if expected_prefix else label
    expected_contains = case.get("expect_contains")
    if expected_contains:
        normalized_expected = expected_contains.strip()
        if "…" in content_segment:
            assert normalized_expected.startswith(content_segment.rstrip("…"))
        else:
            assert normalized_expected in content_segment
    if "expect_exact" in case:
        assert label == case["expect_exact"]
    if "expect_ellipsis" in case:
        if case["expect_ellipsis"]:
            assert "…" in label
        else:
            assert "…" not in label


def test_task_list_search_flow(monkeypatch):
    message = DummyMessage()
    user = SimpleNamespace(id=123, is_bot=False)
    callback = DummyCallback(message, user, f"{bot.TASK_LIST_SEARCH_CALLBACK}:-:1:10")
    state, _storage = make_state(message)

    task = TaskRecord(
        id="TASK_0001",
        project_slug="demo",
        title="Fix login issues",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="Login interface exception",
        parent_id=None,
        root_id="TASK_0001",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    async def fake_search(self, keyword, *, page, page_size):
        assert keyword == "Log in"
        return [task], 1, 1

    monkeypatch.setattr(
        bot.TASK_SERVICE,
        "search_tasks",
        MethodType(fake_search, bot.TASK_SERVICE),
    )

    async def _scenario():
        await bot.on_task_list_search(callback, state)  # type: ignore[arg-type]
        assert await state.get_state() == bot.TaskListSearchStates.waiting_keyword.state
        assert message.calls
        assert "Please enter task search keywords" in message.calls[-1]["text"]
        assert callback.answers and callback.answers[-1]["text"] == "Please enter search keywords"

        user_message = DummyMessage(text="Log in")
        await bot.on_task_list_search_keyword(user_message, state)
        assert await state.get_state() is None
        # Appears in MarkdownV2 mode * or _ formatting placeholder
        assert message.edits
        header_text = message.edits[-1]["text"]
        expected_headers = ("*Task search results*", "\\*Task search results\\*", "_Task search results_")
        assert any(header in header_text for header in expected_headers)
        assert "- 🛠️ Fix login issues" not in message.edits[-1]["text"]
        assert "- ⚪ Fix login issues" not in message.edits[-1]["text"]
        assert user_message.calls and "Search completed" in user_message.calls[-1]["text"]
        markup: InlineKeyboardMarkup = message.edits[-1]["reply_markup"]
        detail_buttons = [
            button.text
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.startswith("task:detail")
        ]
        assert detail_buttons
        status_icon = bot._status_icon(task.status)
        type_icon = bot.TASK_TYPE_EMOJIS.get(task.task_type) or "⚪"
        expected_prefix = f"{status_icon} {type_icon} "
        assert detail_buttons[0].startswith(expected_prefix)
        assert "Fix login issues" in detail_buttons[0]

    asyncio.run(_scenario())


def test_task_list_search_cancel_restores_list(monkeypatch):
    message = DummyMessage()
    user = SimpleNamespace(id=123, is_bot=False)
    callback = DummyCallback(message, user, f"{bot.TASK_LIST_SEARCH_CALLBACK}:research:2:5")
    state, _storage = make_state(message)

    async def fake_list_view(status, page, limit):
        return "*task list*", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot, "_build_task_list_view", fake_list_view)

    async def _scenario():
        await bot.on_task_list_search(callback, state)  # type: ignore[arg-type]
        cancel_message = DummyMessage(text="Cancel")
        await bot.on_task_list_search_keyword(cancel_message, state)
        assert await state.get_state() is None
        # Appears in MarkdownV2 mode * or _ formatting placeholder
        assert message.edits
        header_text = message.edits[-1]["text"]
        expected_headers = ("*task list*", "\\*task list\\*", "_task list_")
        assert any(header in header_text for header in expected_headers)
        assert cancel_message.calls and "Task list has been returned" in cancel_message.calls[-1]["text"]

    asyncio.run(_scenario())


def test_task_service_search_tasks(tmp_path):
    db_path = tmp_path / "tasks.db"
    service = TaskService(db_path, "demo")

    async def _scenario():
        await service.initialize()
        await service.create_root_task(
            title="repairLog inFunction",
            status="research",
            priority=2,
            task_type="task",
            tags=(),
            due_date=None,
            description="Handling Log in interface error reports",
            actor="tester",
        )
        await service.create_root_task(
            title="Write deployment documentation",
            status="test",
            priority=3,
            task_type="task",
            tags=(),
            due_date=None,
            description="wiki Documentation updates",
            actor="tester",
        )
        results, pages, total = await service.search_tasks("Log in", page=1, page_size=10)
        return results, pages, total

    results, pages, total = asyncio.run(_scenario())
    assert total == 1
    assert pages == 1
    assert results[0].title == "repairLog inFunction"


def test_task_service_search_tasks_empty_keyword(tmp_path):
    service = TaskService(tmp_path / "tasks.db", "demo")

    async def _scenario():
        await service.initialize()
        return await service.search_tasks("", page=1, page_size=10)

    results, pages, total = asyncio.run(_scenario())
    assert results == []
    assert pages == 0
    assert total == 0


def test_format_task_detail_with_special_chars_markdown_v2(monkeypatch):
    """Test fix: avoid double escaping special characters in MarkdownV2 mode"""
    # Emulate MarkdownV2 mode
    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", True)
    monkeypatch.setattr(bot, "_IS_MARKDOWN", False)

    # Create tasks that contain special characters
    task = TaskRecord(
        id="TASK_0001",
        project_slug="demo",
        title="repairLog in-question (v2.0) [urgent]",
        status="research",
        priority=3,
        task_type="defect",
        tags=(),
        due_date=None,
        description="Login interface exception! Need to fix test_case.example",
        parent_id="TASK_0000",
        root_id="TASK_0001",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    detail_text = bot._format_task_detail(task, notes=[])

    # In MarkdownV2 mode, special characters should be left intact (not manually escaped)
    # Follow-up by _prepare_model_payload() unified escaping
    assert "repairLog in-question (v2.0) [urgent]" in detail_text
    assert "Login interface exception! Need to fix test_case.example" in detail_text
    assert "TASK_0000" in detail_text

    # Make sure there are no double escapes (e.g. \\- or \\( ）
    assert "\\-" not in detail_text  # Avoid \- being escaped again
    assert "\\(" not in detail_text
    assert "\\[" not in detail_text
    assert "\\!" not in detail_text


def test_format_task_detail_with_special_chars_legacy_markdown(monkeypatch):
    """Test backward compatibility: keep manual escaping in legacy Markdown mode"""
    # Simulate traditional Markdown mode
    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", False)
    monkeypatch.setattr(bot, "_IS_MARKDOWN", True)

    task = TaskRecord(
        id="TASK_0002",
        project_slug="demo",
        title="repair_Log inquestion",
        status="test",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="test*describe*",
        parent_id=None,
        root_id="TASK_0002",
        depth=0,
        lineage="0002",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    detail_text = bot._format_task_detail(task, notes=[])

    # In traditional Markdown mode, special characters should be escaped manually
    # _ and * exist _MARKDOWN_ESCAPE_RE will be escaped
    assert "repair\\_Log inquestion" in detail_text  # _ should be escaped as \_
    assert "test\\*describe\\*" in detail_text  # * should be escaped as \*


@pytest.mark.parametrize(
    "title,status,task_type,description",
    [
        ("Fix [critical] bug", "research", "defect", "API endpoint /users fails"),
        ("Update API (v2.0)", "test", "task", "Refactor code: clean up"),
        ("Task#123! solve problems.", "done", "requirement", "describe: done-test"),
        ("Test_case.example", "research", "task", "File path: /path/to/file.txt"),
        ("Contains special symbols: ~`>#+=|{}", "test", "risk", "Things to note"),
    ],
)
def test_format_task_detail_various_special_chars(monkeypatch, title, status, task_type, description):
    """testProcessing of various special characters in exist MarkdownV2 mode"""
    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", True)
    monkeypatch.setattr(bot, "_IS_MARKDOWN", False)

    task = TaskRecord(
        id="TASK_TEST",
        project_slug="demo",
        title=title,
        status=status,
        priority=3,
        task_type=task_type,
        tags=(),
        due_date=None,
        description=description,
        parent_id=None,
        root_id="TASK_TEST",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    # should not throw exception
    detail_text = bot._format_task_detail(task, notes=[])

    # The title and describe should remain as is (in exist MarkdownV2 mode)
    assert title in detail_text
    assert description in detail_text
