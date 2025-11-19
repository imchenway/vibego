import asyncio
import json
from types import SimpleNamespace
from datetime import datetime, timezone
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc  # Python<3.11 兼容 UTC 常量

import bot
from tasks.models import TaskRecord, TaskHistoryRecord
from aiogram.types import Chat, InlineKeyboardMarkup, Message, User


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


class EditableDummyMessage:
    def __init__(self, *, chat_id: int = 42, message_id: int = 500):
        self.chat = SimpleNamespace(id=chat_id)
        self.message_id = message_id
        self.from_user = SimpleNamespace(id=777, full_name="Tester")
        self.edits = []
        self.calls = []
        self.sent_messages = []

    async def edit_text(self, text, parse_mode=None, reply_markup=None, **kwargs):
        self.edits.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return SimpleNamespace(message_id=self.message_id, chat=self.chat)

    async def answer(self, text, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        sent = SimpleNamespace(
            message_id=self.message_id + len(self.calls),
            chat=self.chat,
        )
        self.sent_messages.append(sent)
        return sent


def test_task_detail_actions_include_back_button():
    task = TaskRecord(
        id="TASK_100",
        project_slug="proj",
        title="测试任务",
        status="todo",
        description="描述",
    )
    markup = bot._build_task_actions(task)

    assert markup.inline_keyboard[-1][0].text == "⬅️ 返回任务列表"
    assert markup.inline_keyboard[-1][0].callback_data == bot.TASK_DETAIL_BACK_CALLBACK


def test_task_detail_back_callback_triggers_command(monkeypatch):
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
        message_id=50,
        date=datetime.now(UTC),
        chat=chat,
        text="任务详情",
        from_user=bot_user,
    )
    callback = DummyCallback(base_message, human_user, bot.TASK_DETAIL_BACK_CALLBACK)

    asyncio.run(bot.on_task_detail_back(callback))  # type: ignore[arg-type]

    assert callback.answers and callback.answers[-1]["text"] is None
    assert captured["bot"] is dummy_bot
    update = captured["update"]
    assert update.message.text == "/task_list"
    assert update.message.from_user.id == human_user.id
    assert any(entity.type == "bot_command" for entity in update.message.entities or [])


def test_task_detail_back_callback_missing_context(monkeypatch):
    dummy_bot = SimpleNamespace()
    monkeypatch.setattr(bot, "current_bot", lambda: dummy_bot)

    captured = {}

    async def fake_feed_update(bot_obj, update):
        captured["bot"] = bot_obj

    monkeypatch.setattr(bot.dp, "feed_update", fake_feed_update)  # type: ignore[attr-defined]

    human_user = User.model_construct(id=123, is_bot=False, first_name="Tester")
    callback = DummyCallback(None, human_user, bot.TASK_DETAIL_BACK_CALLBACK)

    asyncio.run(bot.on_task_detail_back(callback))  # type: ignore[arg-type]

    assert callback.answers and callback.answers[-1]["show_alert"] is True
    assert "bot" not in captured


def test_task_detail_callback_edits_in_place(monkeypatch):
    async def fake_render(task_id: str):
        return f"任务详情：{task_id}", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot, "_render_task_detail", fake_render)

    message = EditableDummyMessage()
    callback = DummyCallback(message, SimpleNamespace(id=99, full_name="Tester"), "task:detail:TASK_EDIT")

    base_state = bot.TaskViewState(kind="list", data={"status": None, "page": 1, "limit": 10})
    bot._init_task_view_context(message, base_state)

    asyncio.run(bot.on_task_detail_callback(callback))  # type: ignore[arg-type]

    assert message.edits, "应通过编辑消息展示任务详情"
    top_state = bot._peek_task_view(message.chat.id, message.message_id)
    assert top_state is not None and top_state.kind == "detail"

    bot._clear_task_view(message.chat.id, message.message_id)


def test_task_detail_back_edits_to_previous_view(monkeypatch):
    async def fake_render_view(state):
        return "返回后的列表", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot, "_render_task_view_from_state", fake_render_view)

    message = EditableDummyMessage()
    callback = DummyCallback(message, SimpleNamespace(id=99, full_name="Tester"), bot.TASK_DETAIL_BACK_CALLBACK)

    list_state = bot.TaskViewState(kind="list", data={"status": None, "page": 1, "limit": 10})
    bot._init_task_view_context(message, list_state)
    bot._push_detail_view(message, "TASK_DETAIL")

    asyncio.run(bot.on_task_detail_back(callback))  # type: ignore[arg-type]

    assert message.edits, "应通过编辑消息恢复列表"
    assert callback.answers and callback.answers[-1]["text"] == "已返回任务列表"
    remaining_state = bot._peek_task_view(message.chat.id, message.message_id)
    assert remaining_state is not None and remaining_state.kind == "list"

    bot._clear_task_view(message.chat.id, message.message_id)


def _make_history_records(task_id: str, count: int) -> list[TaskHistoryRecord]:
    records: list[TaskHistoryRecord] = []
    for idx in range(count):
        records.append(
            TaskHistoryRecord(
                id=idx,
                task_id=task_id,
                field="status",
                old_value="todo",
                new_value="test",
                actor="tester",
                event_type=bot.HISTORY_EVENT_FIELD_CHANGE,
                payload=None,
                created_at=f"2025-10-20T0{idx % 10}:00:00+08:00",
            )
        )
    return records


def test_build_task_history_view_contains_return_button():
    task = TaskRecord(
        id="TASK_HISTORY",
        project_slug="proj",
        title="查看历史测试",
        status="test",
    )
    history = _make_history_records(task.id, bot.TASK_HISTORY_PAGE_SIZE + 2)

    text, markup, page, total_pages = bot._build_task_history_view(task, history, page=1)

    assert "任务 TASK_HISTORY 事件历史" in text
    assert total_pages == 2
    assert page == 1
    last_row = markup.inline_keyboard[-1]
    assert last_row[0].text == "⬅️ 返回任务详情"
    assert last_row[0].callback_data == f"{bot.TASK_HISTORY_BACK_CALLBACK}:{task.id}"


def test_task_history_callback_sends_code_block_message(monkeypatch):
    task_id = "TASK_HISTORY_EDIT"
    task = TaskRecord(
        id=task_id,
        project_slug="proj",
        title="分页历史",
        status="test",
    )
    history = _make_history_records(task_id, bot.TASK_HISTORY_PAGE_SIZE + 2)

    async def fake_render_history(target_id: str, page: int):
        assert target_id == task_id
        return bot._build_task_history_view(task, history, page=page)

    monkeypatch.setattr(bot, "_render_task_history", fake_render_history)

    message = EditableDummyMessage()
    detail_state = bot.TaskViewState(kind="detail", data={"task_id": task_id})
    bot._init_task_view_context(message, detail_state)

    callback = DummyCallback(message, SimpleNamespace(id=1, full_name="Tester"), f"task:history:{task_id}")

    asyncio.run(bot.on_task_history(callback))  # type: ignore[arg-type]

    assert not message.edits, "历史视图不应再编辑原消息"
    assert message.calls, "历史视图应通过发送新消息展示"
    latest_call = message.calls[-1]
    assert latest_call["text"].startswith("```\n")
    assert latest_call["text"].rstrip().endswith("```")
    assert latest_call["reply_markup"] is not None
    last_row = latest_call["reply_markup"].inline_keyboard[-1]
    assert any(button.callback_data == f"{bot.TASK_HISTORY_BACK_CALLBACK}:{task_id}" for button in last_row)

    sent_message = message.sent_messages[-1]
    top_state = bot._peek_task_view(sent_message.chat.id, sent_message.message_id)
    assert top_state is not None and top_state.kind == "history"
    expected_page = (len(history) + bot.TASK_HISTORY_PAGE_SIZE - 1) // bot.TASK_HISTORY_PAGE_SIZE
    assert top_state.data["page"] == expected_page

    bot._clear_task_view(sent_message.chat.id, sent_message.message_id)


def test_task_history_page_callback_updates_state(monkeypatch):
    task_id = "TASK_HISTORY_PAGE"
    task = TaskRecord(
        id=task_id,
        project_slug="proj",
        title="分页跳转",
        status="test",
    )
    history = _make_history_records(task_id, bot.TASK_HISTORY_PAGE_SIZE + 3)

    async def fake_render_history(target_id: str, page: int):
        assert target_id == task_id
        return bot._build_task_history_view(task, history, page=page)

    monkeypatch.setattr(bot, "_render_task_history", fake_render_history)

    message = EditableDummyMessage()
    detail_state = bot.TaskViewState(kind="detail", data={"task_id": task_id})
    bot._init_task_view_context(message, detail_state)

    history_callback = DummyCallback(message, SimpleNamespace(id=2, full_name="Tester"), f"task:history:{task_id}")
    asyncio.run(bot.on_task_history(history_callback))  # type: ignore[arg-type]

    sent_message = message.sent_messages[-1]
    history_message = EditableDummyMessage(chat_id=sent_message.chat.id, message_id=sent_message.message_id)

    page_callback = DummyCallback(
        history_message,
        SimpleNamespace(id=2, full_name="Tester"),
        f"{bot.TASK_HISTORY_PAGE_CALLBACK}:{task_id}:1",
    )
    asyncio.run(bot.on_task_history_page(page_callback))  # type: ignore[arg-type]

    assert not history_message.edits, "分页切换不应编辑原历史消息"
    assert history_message.calls, "分页切换应发送新的历史消息"
    top_state = bot._peek_task_view(history_message.chat.id, history_message.sent_messages[-1].message_id)
    assert top_state is not None and top_state.kind == "history"
    assert top_state.data["page"] == 1
    assert page_callback.answers and "已展示第 1/" in (page_callback.answers[-1]["text"] or "")

    bot._clear_task_view(history_message.chat.id, history_message.sent_messages[-1].message_id)


def test_history_view_respects_telegram_limit(monkeypatch):
    task_id = "TASK_HISTORY_LIMIT"
    task = TaskRecord(
        id=task_id,
        project_slug="proj",
        title="超长历史分页",
        status="todo",
    )
    # 构造足够长的历史记录，触发按字符限制拆分页。
    history: list[TaskHistoryRecord] = []
    long_payload = "模型自动摘要内容。" + "很长的描述" * 400
    for idx in range(12):
        history.append(
            TaskHistoryRecord(
                id=idx,
                task_id=task_id,
                field="description",
                old_value="",
                new_value=long_payload,
                actor="tester",
                event_type=bot.HISTORY_EVENT_MODEL_SUMMARY,
                payload=json.dumps({"content": long_payload}),
                created_at=f"2025-10-20T1{idx % 10}:00:00+08:00",
            )
        )

    text, markup, page, total_pages = bot._build_task_history_view(task, history, page=0)

    assert total_pages > max(1, (len(history) + bot.TASK_HISTORY_PAGE_SIZE - 1) // bot.TASK_HISTORY_PAGE_SIZE)
    assert page == total_pages
    assert markup.inline_keyboard[-1][0].text == "⬅️ 返回任务详情"
    assert len(bot._prepare_model_payload(text)) <= bot.TELEGRAM_MESSAGE_LIMIT


def test_history_view_truncates_when_limit_is_small(monkeypatch):
    task_id = "TASK_HISTORY_TRUNC"
    task = TaskRecord(
        id=task_id,
        project_slug="proj",
        title="超限截断提示",
        status="todo",
    )
    record = TaskHistoryRecord(
        id=1,
        task_id=task_id,
        field="description",
        old_value="",
        new_value="极长的测试内容" * 200,
        actor="tester",
        event_type=bot.HISTORY_EVENT_MODEL_SUMMARY,
        payload=json.dumps({"content": "极长的测试内容" * 200}),
        created_at="2025-10-20T10:00:00+08:00",
    )

    monkeypatch.setattr(bot, "TELEGRAM_MESSAGE_LIMIT", 120)
    text, _markup, page, total_pages = bot._build_task_history_view(task, [record], page=1)

    assert page == total_pages == 1
    assert "⚠️" in text
    assert len(bot._prepare_model_payload(text)) <= bot.TELEGRAM_MESSAGE_LIMIT


def test_task_history_back_returns_detail(monkeypatch):
    task_id = "TASK_HISTORY_BACK"
    task = TaskRecord(
        id=task_id,
        project_slug="proj",
        title="返回详情",
        status="test",
    )
    history = _make_history_records(task_id, bot.TASK_HISTORY_PAGE_SIZE + 1)

    async def fake_render_history(target_id: str, page: int):
        assert target_id == task_id
        return bot._build_task_history_view(task, history, page=page)

    async def fake_render_detail(target_id: str):
        assert target_id == task_id
        return "任务详情：返回", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot, "_render_task_history", fake_render_history)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render_detail)

    message = EditableDummyMessage()
    detail_state = bot.TaskViewState(kind="detail", data={"task_id": task_id})
    bot._init_task_view_context(message, detail_state)

    history_callback = DummyCallback(message, SimpleNamespace(id=3, full_name="Tester"), f"task:history:{task_id}")
    asyncio.run(bot.on_task_history(history_callback))  # type: ignore[arg-type]

    sent_message = message.sent_messages[-1]
    history_message = EditableDummyMessage(chat_id=sent_message.chat.id, message_id=sent_message.message_id)

    back_callback = DummyCallback(
        history_message,
        SimpleNamespace(id=3, full_name="Tester"),
        f"{bot.TASK_HISTORY_BACK_CALLBACK}:{task_id}",
    )
    asyncio.run(bot.on_task_history_back(back_callback))  # type: ignore[arg-type]

    assert not history_message.edits, "返回详情不应编辑原历史消息"
    assert history_message.calls, "返回详情应发送新的任务详情消息"
    detail_message = history_message.sent_messages[-1]
    top_state = bot._peek_task_view(detail_message.chat.id, detail_message.message_id)
    assert top_state is not None and top_state.kind == "detail"
    assert bot._peek_task_view(history_message.chat.id, history_message.message_id) is None
    assert back_callback.answers and back_callback.answers[-1]["text"] == "已返回任务详情"

    bot._clear_task_view(detail_message.chat.id, detail_message.message_id)
