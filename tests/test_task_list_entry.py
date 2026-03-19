import asyncio
from datetime import datetime, timezone
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc  # Python<3.11 兼容 UTC 常量
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

    assert text.startswith("*任务列表*")
    buttons = [button.text for row in markup.inline_keyboard for button in row]
    assert "🔍 搜索任务" in buttons
    assert "➕ 创建任务" in buttons


def test_task_list_view_renders_entries_without_task_type_icons(monkeypatch):
    task = TaskRecord(
        id="TASK_9001",
        project_slug="demo",
        title="修复登录问题",
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

    assert "- 🛠️ 修复登录问题" not in text
    assert "- ⚪ 修复登录问题" not in text
    detail_buttons = [
        button.text
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("task:detail")
    ]
    assert detail_buttons
    status_icon = bot._status_icon(task.status)
    expected_prefix = f"{status_icon} " if status_icon else ""
    assert detail_buttons[0].startswith(expected_prefix)
    assert all(icon not in detail_buttons[0] for icon in bot.TASK_TYPE_EMOJIS.values())
    assert "⚪" not in detail_buttons[0]
    assert "修复登录问题" in detail_buttons[0]


def test_task_list_view_sorts_by_updated_at_desc(monkeypatch, tmp_path):
    """任务列表视图：按更新时间倒序（最近更新优先），且旧任务更新后会置顶。"""

    service = TaskService(tmp_path / "tasks.db", "demo")
    asyncio.run(service.initialize())
    monkeypatch.setattr(bot, "TASK_SERVICE", service)

    import tasks.service as task_service_module

    times = iter(
        [
            "2025-01-01T00:00:00+08:00",
            "2025-01-02T00:00:00+08:00",
            "2025-01-03T00:00:00+08:00",
            "2025-01-04T00:00:00+08:00",
            "2025-01-05T00:00:00+08:00",
            "2025-01-06T00:00:00+08:00",
            "2025-01-07T00:00:00+08:00",
        ]
    )
    monkeypatch.setattr(task_service_module, "shanghai_now_iso", lambda: next(times))

    for idx in range(6):
        asyncio.run(
            service.create_root_task(
                title=f"任务{idx + 1}",
                status="research",
                priority=3,
                task_type="task",
                tags=(),
                due_date=None,
                description="",
                actor="tester",
            )
        )

    # 更新最早创建的任务，使其 updated_at 最新，应在列表中置顶。
    asyncio.run(service.update_task("TASK_0001", actor="tester", title="任务1（已更新）"))

    _text, markup = asyncio.run(bot._build_task_list_view(status=None, page=1, limit=10))
    task_ids = [
        button.callback_data.split(":")[2]
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("task:detail:")
    ]
    assert task_ids[:6] == [
        "TASK_0001",
        "TASK_0006",
        "TASK_0005",
        "TASK_0004",
        "TASK_0003",
        "TASK_0002",
    ]


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
        text="*任务列表*",
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
    long_title = "这是一个非常长的任务标题，用于验证状态图标仍然保留在按钮末尾，不会被截断或丢失"
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
    expected_prefix = f"{status_icon} {task.id} "
    assert label.startswith(expected_prefix)
    assert task.id in label
    assert all(icon not in label for icon in bot.TASK_TYPE_EMOJIS.values())
    assert "⚪" not in label
    assert len(label) <= 40
    assert "…" in label


@pytest.mark.parametrize(
    "case",
    [
        {
            "name": "normal_case",
            "task_id": "TASK_0001",
            "title": "修复登录问题",
            "status": "research",
            "task_type": "task",
            "max_length": 60,
            "expect_prefix": f"{bot._status_icon('research')} TASK_0001 ",
            "expect_code": "TASK_0001",
            "expect_contains": "修复登录问题",
            "expect_ellipsis": False,
        },
        {
            "name": "no_status",
            "task_id": "TASK_0002",
            "title": "不含状态",
            "status": "",
            "task_type": "task",
            "max_length": 30,
            "expect_exact": "TASK_0002 不含状态",
            "expect_code": "TASK_0002",
            "expect_contains": "不含状态",
            "expect_ellipsis": False,
        },
        {
            "name": "unknown_status",
            "task_id": "TASK_0003",
            "title": "未知状态",
            "status": "blocked",
            "task_type": "task",
            "max_length": 30,
            "expect_exact": "TASK_0003 未知状态",
            "expect_code": "TASK_0003",
            "expect_contains": "未知状态",
            "expect_ellipsis": False,
        },
        {
            "name": "no_type",
            "task_id": "TASK_0004",
            "title": "无类型任务",
            "status": "research",
            "task_type": None,
            "max_length": 40,
            "expect_prefix": f"{bot._status_icon('research')} TASK_0004 ",
            "expect_code": "TASK_0004",
            "expect_contains": "无类型任务",
            "expect_ellipsis": False,
        },
        {
            "name": "long_title_truncated",
            "task_id": "TASK_0005",
            "title": "这个标题超级超级长，需要被截断才能放进按钮里",
            "status": "test",
            "task_type": "defect",
            "max_length": 24,
            "expect_prefix": f"{bot._status_icon('test')} TASK_0005 ",
            "expect_code": "TASK_0005",
            "expect_contains": "这个标题超级超级长",
            "expect_ellipsis": True,
        },
        {
            "name": "tight_limit",
            "task_id": "TASK_0006",
            "title": "极短限制",
            "status": "test",
            "task_type": "risk",
            "max_length": 18,
            "expect_prefix": f"{bot._status_icon('test')} TASK_0006 ",
            "expect_code": "TASK_0006",
            "expect_contains": "极短限制",
            "expect_ellipsis": False,
        },
        {
            "name": "empty_title",
            "task_id": "TASK_0007",
            "title": "",
            "status": "done",
            "task_type": "requirement",
            "max_length": 20,
            "expect_prefix": f"{bot._status_icon('done')} TASK_0007 ",
            "expect_code": "TASK_0007",
            "expect_exact": "✅ TASK_0007 -",
            "expect_ellipsis": False,
        },
        {
            "name": "emoji_title",
            "task_id": "TASK_0008",
            "title": "🔥 紧急处理",
            "status": "done",
            "task_type": "risk",
            "max_length": 25,
            "expect_prefix": f"{bot._status_icon('done')} TASK_0008 ",
            "expect_code": "TASK_0008",
            "expect_contains": "🔥 紧急处理",
            "expect_ellipsis": False,
        },
        {
            "name": "multibyte_length",
            "task_id": "TASK_0009",
            "title": "多字节标题测试",
            "status": "research",
            "task_type": "defect",
            "max_length": 20,
            "expect_prefix": f"{bot._status_icon('research')} TASK_0009 ",
            "expect_code": "TASK_0009",
            "expect_contains": "多字节标题测试",
            "expect_ellipsis": False,
        },
        {
            "name": "status_alias",
            "task_id": "TASK_0010",
            "title": "Alias 状态",
            "status": "Research",
            "task_type": "task",
            "max_length": 30,
            "expect_prefix": f"{bot._status_icon('Research')} TASK_0010 ",
            "expect_code": "TASK_0010",
            "expect_contains": "Alias 状态",
            "expect_ellipsis": False,
        },
    ],
    ids=lambda case: case["name"],
)
def test_compose_task_button_label_various_cases(case):
    task = TaskRecord(
        id=case["task_id"],
        project_slug="demo",
        title=case["title"],
        status=case["status"],
        priority=3,
        task_type=case["task_type"],
        tags=(),
        due_date=None,
        description="",
        parent_id=None,
        root_id=case["task_id"],
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
    expected_contains = case.get("expect_contains")
    if expected_contains:
        assert expected_contains.strip() in label
    expected_code = case.get("expect_code")
    if expected_code:
        assert expected_code in label
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
        title="修复登录问题",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="登录接口异常",
        parent_id=None,
        root_id="TASK_0001",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    async def fake_search(self, keyword, *, page, page_size):
        assert keyword == "登录"
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
        assert "请输入任务搜索关键词" in message.calls[-1]["text"]
        assert callback.answers and callback.answers[-1]["text"] == "请输入搜索关键词"

        user_message = DummyMessage(text="登录")
        await bot.on_task_list_search_keyword(user_message, state)
        assert await state.get_state() is None
        # 在 MarkdownV2 模式下会出现 * 或 _ 的格式化占位
        assert message.edits
        header_text = message.edits[-1]["text"]
        expected_headers = ("*任务搜索结果*", "\\*任务搜索结果\\*", "_任务搜索结果_")
        assert any(header in header_text for header in expected_headers)
        assert "- 🛠️ 修复登录问题" not in message.edits[-1]["text"]
        assert "- ⚪ 修复登录问题" not in message.edits[-1]["text"]
        assert user_message.calls and "搜索完成" in user_message.calls[-1]["text"]
        markup: InlineKeyboardMarkup = message.edits[-1]["reply_markup"]
        detail_buttons = [
            button.text
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.startswith("task:detail")
        ]
        assert detail_buttons
        status_icon = bot._status_icon(task.status)
        expected_prefix = f"{status_icon} {task.id} " if status_icon else f"{task.id} "
        assert detail_buttons[0].startswith(expected_prefix)
        assert all(icon not in detail_buttons[0] for icon in bot.TASK_TYPE_EMOJIS.values())
        assert "⚪" not in detail_buttons[0]
        assert task.id in detail_buttons[0]
        assert "修复登录问题" in detail_buttons[0]

    asyncio.run(_scenario())


def test_compose_task_button_label_does_not_include_task_type_icons():
    """列表按钮不应包含任务类型图标（📌🐞🛠️⚠️/⚪），避免列表信息噪声。"""
    for task_type in [*bot.TASK_TYPE_EMOJIS.keys(), None]:
        task = TaskRecord(
            id=f"TASK_{task_type or 'none'}",
            project_slug="demo",
            title="按钮标题",
            status="research",
            priority=3,
            task_type=task_type,
            tags=(),
            due_date=None,
            description="",
            parent_id=None,
            root_id=f"TASK_{task_type or 'none'}",
            depth=0,
            lineage="0001",
            created_at="2025-01-01T00:00:00+08:00",
            updated_at="2025-01-01T00:00:00+08:00",
            archived=False,
        )
        label = bot._compose_task_button_label(task, max_length=60)
        assert all(icon not in label for icon in bot.TASK_TYPE_EMOJIS.values())
        assert "⚪" not in label
        assert task.id in label


def test_compose_task_button_label_appends_running_suffix():
    """存在活动会话时，任务标题末尾应追加运行中图标。"""

    task = TaskRecord(
        id="TASK_RUNNING",
        project_slug="demo",
        title="并行中的任务",
        status="research",
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description="",
        parent_id=None,
        root_id="TASK_RUNNING",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    label = bot._compose_task_button_label(task, max_length=60, is_session_running=True)
    assert label == "🔍 TASK_RUNNING 并行中的任务 ▶️"


def test_build_task_list_view_marks_running_tasks(monkeypatch):
    """任务列表命中活动会话时，应把运行中图标透传到详情按钮文案。"""

    task = TaskRecord(
        id="TASK_RUNNING",
        project_slug="demo",
        title="并行中的任务",
        status="research",
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description="",
        parent_id=None,
        root_id="TASK_RUNNING",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    async def fake_paginate(*, status, page, page_size, exclude_statuses=None):
        assert status is None
        assert page == 1
        assert page_size == 10
        return [task], 1

    async def fake_count_tasks(*, status, include_archived, exclude_statuses=None):
        assert status is None
        assert include_archived is False
        return 1

    async def fake_running_task_ids():
        return {"TASK_RUNNING"}

    monkeypatch.setattr(bot.TASK_SERVICE, "paginate", fake_paginate)
    monkeypatch.setattr(bot.TASK_SERVICE, "count_tasks", fake_count_tasks)
    monkeypatch.setattr(bot, "_list_running_task_ids_for_task_list", fake_running_task_ids, raising=False)

    text, markup = asyncio.run(bot._build_task_list_view(status=None, page=1, limit=10))

    assert "任务列表" in text
    detail_buttons = [
        button.text
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("task:detail:")
    ]
    assert detail_buttons == ["🔍 TASK_RUNNING 并行中的任务 ▶️"]


def test_task_list_search_cancel_restores_list(monkeypatch):
    message = DummyMessage()
    user = SimpleNamespace(id=123, is_bot=False)
    callback = DummyCallback(message, user, f"{bot.TASK_LIST_SEARCH_CALLBACK}:research:2:5")
    state, _storage = make_state(message)

    async def fake_list_view(status, page, limit):
        return "*任务列表*", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot, "_build_task_list_view", fake_list_view)

    async def _scenario():
        await bot.on_task_list_search(callback, state)  # type: ignore[arg-type]
        cancel_message = DummyMessage(text="取消")
        await bot.on_task_list_search_keyword(cancel_message, state)
        assert await state.get_state() is None
        # 在 MarkdownV2 模式下会出现 * 或 _ 的格式化占位
        assert message.edits
        header_text = message.edits[-1]["text"]
        expected_headers = ("*任务列表*", "\\*任务列表\\*", "_任务列表_")
        assert any(header in header_text for header in expected_headers)
        assert cancel_message.calls and "已返回任务列表" in cancel_message.calls[-1]["text"]

    asyncio.run(_scenario())


def test_task_service_search_tasks(tmp_path):
    db_path = tmp_path / "tasks.db"
    service = TaskService(db_path, "demo")

    async def _scenario():
        await service.initialize()
        await service.create_root_task(
            title="修复登录功能",
            status="research",
            priority=2,
            task_type="task",
            tags=(),
            due_date=None,
            description="处理登录接口报错",
            actor="tester",
        )
        await service.create_root_task(
            title="编写部署文档",
            status="test",
            priority=3,
            task_type="task",
            tags=(),
            due_date=None,
            description="wiki 文档更新",
            actor="tester",
        )
        results, pages, total = await service.search_tasks("登录", page=1, page_size=10)
        return results, pages, total

    results, pages, total = asyncio.run(_scenario())
    assert total == 1
    assert pages == 1
    assert results[0].title == "修复登录功能"


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
    """测试修复：在 MarkdownV2 模式下避免双重转义特殊字符"""
    # 模拟 MarkdownV2 模式
    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", True)
    monkeypatch.setattr(bot, "_IS_MARKDOWN", False)

    # 创建包含特殊字符的任务
    task = TaskRecord(
        id="TASK_0001",
        project_slug="demo",
        title="修复登录-问题 (v2.0) [紧急]",
        status="research",
        priority=3,
        task_type="defect",
        tags=(),
        due_date=None,
        description="登录接口异常! 需要修复 test_case.example",
        parent_id="TASK_0000",
        root_id="TASK_0001",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    detail_text = bot._format_task_detail(task, notes=[])

    # 在 MarkdownV2 模式下，特殊字符应该保持原样（不手动转义）
    # 后续由 _prepare_model_payload() 统一转义
    assert "修复登录-问题 (v2.0) [紧急]" in detail_text
    assert "登录接口异常! 需要修复 test_case.example" in detail_text
    assert "TASK_0000" in detail_text
    assert "📊 状态：" not in detail_text
    expected_type = bot._strip_task_type_emoji(bot._format_task_type("defect"))
    assert f"📂 类型：{expected_type}" in detail_text

    # 确保没有双重转义（例如 \\- 或 \\( ）
    assert "\\-" not in detail_text  # 避免 \- 再次被转义
    assert "\\(" not in detail_text
    assert "\\[" not in detail_text
    assert "\\!" not in detail_text


def test_format_task_detail_defect_sections_keep_special_chars_in_markdown_v2(monkeypatch):
    """缺陷详情的四字段在 MarkdownV2 模式下也应保持原始特殊字符。"""

    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", True)
    monkeypatch.setattr(bot, "_IS_MARKDOWN", False)

    task = TaskRecord(
        id="TASK_DEFECT_SECTIONS",
        project_slug="demo",
        title="缺陷标题",
        status="research",
        priority=3,
        task_type="defect",
        tags=(),
        due_date=None,
        description=(
            "前置条件：\n已登录 [测试环境](入口)\n\n"
            "复现步骤：\n点击 [登录](按钮)\n\n"
            "现状：\n页面没有 success_message!\n\n"
            "预期效果：\n显示 success_message!"
        ),
        parent_id=None,
        root_id="TASK_DEFECT_SECTIONS",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    detail_text = bot._format_task_detail(task, notes=[])

    assert "📌 前置条件：已登录 [测试环境](入口)" in detail_text
    assert "🧪 复现步骤：点击 [登录](按钮)" in detail_text
    assert "🧭 现状：页面没有 success_message!" in detail_text
    assert "🎯 预期效果：显示 success_message!" in detail_text
    assert "\\[" not in detail_text
    assert "\\(" not in detail_text
    assert "\\!" not in detail_text


def test_format_task_detail_task_sections_keep_special_chars_in_markdown_v2(monkeypatch):
    """优化详情的双字段在 MarkdownV2 模式下也应保持原始特殊字符。"""

    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", True)
    monkeypatch.setattr(bot, "_IS_MARKDOWN", False)

    task = TaskRecord(
        id="TASK_TASK_SECTIONS",
        project_slug="demo",
        title="优化标题",
        status="research",
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description="当前效果：\n点击 [提交](按钮) 需要两次\n\n期望效果：\n一次点击即可 success_message!",
        parent_id=None,
        root_id="TASK_TASK_SECTIONS",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    detail_text = bot._format_task_detail(task, notes=[])

    assert "当前效果：点击 [提交](按钮) 需要两次" in detail_text
    assert "期望效果：一次点击即可 success_message!" in detail_text
    assert "\\[" not in detail_text
    assert "\\(" not in detail_text
    assert "\\!" not in detail_text


def test_format_task_detail_with_special_chars_legacy_markdown(monkeypatch):
    """测试向后兼容：在传统 Markdown 模式下保持手动转义"""
    # 模拟传统 Markdown 模式
    monkeypatch.setattr(bot, "_IS_MARKDOWN_V2", False)
    monkeypatch.setattr(bot, "_IS_MARKDOWN", True)

    task = TaskRecord(
        id="TASK_0002",
        project_slug="demo",
        title="修复_登录问题",
        status="test",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="测试*描述*",
        parent_id=None,
        root_id="TASK_0002",
        depth=0,
        lineage="0002",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    detail_text = bot._format_task_detail(task, notes=[])

    # 在传统 Markdown 模式下，应该手动转义特殊字符
    # _ 和 * 在 _MARKDOWN_ESCAPE_RE 中会被转义
    assert "修复\\_登录问题" in detail_text  # _ 应该被转义为 \_
    assert "测试\\*描述\\*" in detail_text  # * 应该被转义为 \*
    expected_type = bot._strip_task_type_emoji(bot._format_task_type("task"))
    assert f"📂 类型：{expected_type}" in detail_text


@pytest.mark.parametrize(
    "title,status,task_type,description",
    [
        ("Fix [critical] bug", "research", "defect", "API endpoint /users fails"),
        ("Update API (v2.0)", "test", "task", "Refactor code: clean up"),
        ("任务#123! 解决问题.", "done", "requirement", "描述: 完成-测试"),
        ("Test_case.example", "research", "task", "File path: /path/to/file.txt"),
        ("含特殊符号: ~`>#+=|{}", "test", "risk", "注意事项"),
    ],
)
def test_format_task_detail_various_special_chars(monkeypatch, title, status, task_type, description):
    """测试各种特殊字符在 MarkdownV2 模式下的处理"""
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

    # 不应该抛出异常
    detail_text = bot._format_task_detail(task, notes=[])

    # 标题和描述应该保持原样（在 MarkdownV2 模式下）
    assert title in detail_text
    assert description in detail_text
    expected_type = bot._strip_task_type_emoji(bot._format_task_type(task_type))
    assert f"📂 类型：{expected_type}" in detail_text
