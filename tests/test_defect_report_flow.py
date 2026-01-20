import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

import bot
from aiogram.types import InlineKeyboardMarkup
from tasks.models import TaskRecord


class DummyState:
    """精简版 FSMContext，用于单元测试状态推进。"""

    def __init__(self, data=None, state=None):
        self._data = dict(data or {})
        self._state = state

    async def clear(self):
        self._data.clear()
        self._state = None

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def set_state(self, state):
        self._state = state

    async def get_data(self):
        return dict(self._data)

    @property
    def data(self):
        return dict(self._data)

    @property
    def state(self):
        return self._state


class DummyMessage:
    """精简版 Message，用于记录 answer 调用参数。"""

    def __init__(self, text: Optional[str] = None):
        self.text = text
        self.caption = None
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(id=1)
        self.from_user = SimpleNamespace(id=1, full_name="Tester")
        self.bot = SimpleNamespace(username="tester_bot")
        self.date = datetime.now(bot.UTC)
        self.photo = []
        self.document = None
        self.voice = None
        self.video = None
        self.audio = None
        self.animation = None
        self.video_note = None
        self.media_group_id = None

    async def answer(self, text, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(message_id=len(self.calls), chat=self.chat)


class DummyCallback:
    """精简版 CallbackQuery，用于模拟按钮点击。"""

    def __init__(self, data: str, message: DummyMessage):
        self.data = data
        self.message = message
        self.answers: list[tuple[Optional[str], bool]] = []
        self.from_user = message.from_user

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


def _make_task(*, task_id: str, title: str, status: str) -> TaskRecord:
    """构造测试用任务记录。"""

    return TaskRecord(
        id=task_id,
        project_slug="demo",
        title=title,
        status=status,
        priority=bot.DEFAULT_PRIORITY,
        task_type="task",
        tags=(),
        due_date=None,
        description="",
        related_task_id=None,
        parent_id=None,
        root_id=task_id,
        depth=0,
        lineage="0001",
        archived=False,
    )


def test_bug_report_callback_enters_defect_report_flow(monkeypatch):
    """点击“报告缺陷”应进入“创建缺陷任务”新流程（等待输入标题）。"""

    origin = _make_task(task_id="TASK_0001", title="触发任务", status="research")

    async def fake_get_task(task_id: str):
        assert task_id == origin.id
        return origin

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    message = DummyMessage()
    callback = DummyCallback("task:bug_report:TASK_0001", message)
    state = DummyState()

    asyncio.run(bot.on_task_bug_report(callback, state))

    assert state.state == bot.TaskDefectReportStates.waiting_title
    assert state.data.get("origin_task_id") == origin.id
    assert "pending_attachments" in state.data
    assert callback.answers and callback.answers[0][0] == "请输入缺陷标题"
    assert message.calls and "创建缺陷任务" in message.calls[-1]["text"]


def test_defect_report_description_advances_to_confirm_when_only_attachments(monkeypatch, tmp_path: Path):
    """描述阶段仅发送附件（无文字）应直接进入确认阶段，并保留已收集附件。"""

    origin = _make_task(task_id="TASK_0001", title="触发任务", status="research")

    async def fake_get_task(task_id: str):
        assert task_id == origin.id
        return origin

    async def fake_collect(_message, _dir, *, processed):
        saved = [
            bot.TelegramSavedAttachment(
                kind="document",
                display_name="log.txt",
                mime_type="text/plain",
                absolute_path=tmp_path / "log.txt",
                relative_path="./data/log.txt",
            )
        ]
        return saved, "", processed

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_collect_generic_media_group", fake_collect)

    state = DummyState(
        data={
            "origin_task_id": origin.id,
            "reporter": "Tester",
            "title": "缺陷标题",
            "pending_attachments": [],
            "processed_media_groups": [],
        },
        state=bot.TaskDefectReportStates.waiting_description,
    )
    message = DummyMessage(text="")

    asyncio.run(bot.on_task_defect_report_description(message, state))

    assert state.state == bot.TaskDefectReportStates.waiting_confirm
    assert state.data.get("pending_attachments")
    assert any("附件列表" in call["text"] for call in message.calls)
    assert message.calls and message.calls[-1]["text"] == "是否创建该缺陷任务？"
    assert not any("缺陷描述可选" in call["text"] for call in message.calls)


def test_defect_report_description_can_skip_to_confirm(monkeypatch):
    """描述阶段选择“跳过”后应进入确认阶段，并允许描述为空。"""

    origin = _make_task(task_id="TASK_0001", title="触发任务", status="research")

    async def fake_get_task(task_id: str):
        assert task_id == origin.id
        return origin

    async def fake_collect(_message, _dir, *, processed):
        return [], "", processed

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: Path("."))
    monkeypatch.setattr(bot, "_collect_generic_media_group", fake_collect)

    state = DummyState(
        data={
            "origin_task_id": origin.id,
            "reporter": "Tester",
            "title": "缺陷标题",
            "pending_attachments": [],
            "processed_media_groups": [],
        },
        state=bot.TaskDefectReportStates.waiting_description,
    )
    message = DummyMessage(text=bot.SKIP_TEXT)

    asyncio.run(bot.on_task_defect_report_description(message, state))

    assert state.state == bot.TaskDefectReportStates.waiting_confirm
    assert state.data.get("description", "") == ""
    assert any("描述：暂无" in call["text"] for call in message.calls)


def test_defect_report_confirm_creates_task_and_binds_attachments(monkeypatch, tmp_path: Path):
    """确认创建后应创建缺陷任务、绑定附件，并展示新任务详情。"""

    origin = _make_task(task_id="TASK_0001", title="触发任务", status="research")

    async def fake_get_task(task_id: str):
        assert task_id == origin.id
        return origin

    created_args: dict[str, Any] = {}

    async def fake_create_root_task(**kwargs):
        created_args.update(kwargs)
        return TaskRecord(
            id="TASK_9999",
            project_slug="demo",
            title=kwargs["title"],
            status=kwargs["status"],
            priority=kwargs["priority"],
            task_type=kwargs["task_type"],
            tags=(),
            due_date=None,
            description=kwargs.get("description") or "",
            related_task_id=kwargs.get("related_task_id"),
            parent_id=None,
            root_id="TASK_9999",
            depth=0,
            lineage="9999",
            archived=False,
        )

    bind_calls: list[tuple[str, list[dict[str, str]], str]] = []

    async def fake_bind(task_arg, attachments, actor):
        bind_calls.append((task_arg.id, list(attachments), actor))
        return []

    logged_actions: list[dict[str, Any]] = []

    async def fake_log_action(task_id: str, **kwargs):
        logged_actions.append({"task_id": task_id, **kwargs})
        return None

    async def fake_collect(_message, _dir, *, processed):
        return [], "", processed

    async def fake_render_task_detail(task_id: str):
        assert task_id == "TASK_9999"
        return "DETAIL", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "create_root_task", fake_create_root_task)
    monkeypatch.setattr(bot, "_bind_serialized_attachments", fake_bind)
    monkeypatch.setattr(bot, "_log_task_action", fake_log_action)
    monkeypatch.setattr(bot, "_collect_generic_media_group", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render_task_detail)

    state = DummyState(
        data={
            "origin_task_id": origin.id,
            "reporter": "Tester",
            "title": "缺陷标题",
            "description": "缺陷描述",
            "pending_attachments": [{"path": "./data/log.txt", "display_name": "log.txt", "mime_type": "text/plain"}],
            "processed_media_groups": [],
        },
        state=bot.TaskDefectReportStates.waiting_confirm,
    )
    message = DummyMessage(text="✅ 确认创建")

    asyncio.run(bot.on_task_defect_report_confirm(message, state))

    assert state.state is None
    assert created_args["task_type"] == "defect"
    assert created_args["related_task_id"] == origin.id
    assert bind_calls and bind_calls[0][0] == "TASK_9999"
    assert logged_actions and logged_actions[0]["task_id"] == origin.id
    assert message.calls and any("缺陷任务详情" in call["text"] for call in message.calls)


def test_defect_report_confirm_allows_empty_description(monkeypatch, tmp_path: Path):
    """确认创建时允许描述为空。"""

    origin = _make_task(task_id="TASK_0001", title="触发任务", status="research")

    async def fake_get_task(task_id: str):
        assert task_id == origin.id
        return origin

    created_args: dict[str, Any] = {}

    async def fake_create_root_task(**kwargs):
        created_args.update(kwargs)
        return TaskRecord(
            id="TASK_9998",
            project_slug="demo",
            title=kwargs["title"],
            status=kwargs["status"],
            priority=kwargs["priority"],
            task_type=kwargs["task_type"],
            tags=(),
            due_date=None,
            description=kwargs.get("description") or "",
            related_task_id=kwargs.get("related_task_id"),
            parent_id=None,
            root_id="TASK_9998",
            depth=0,
            lineage="9998",
            archived=False,
        )

    async def fake_collect(_message, _dir, *, processed):
        return [], "", processed

    async def fake_log_action(*_args, **_kwargs):
        return None

    async def fake_render_task_detail(task_id: str):
        assert task_id == "TASK_9998"
        return "DETAIL", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "create_root_task", fake_create_root_task)
    monkeypatch.setattr(bot, "_log_task_action", fake_log_action)
    monkeypatch.setattr(bot, "_collect_generic_media_group", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render_task_detail)

    state = DummyState(
        data={
            "origin_task_id": origin.id,
            "reporter": "Tester",
            "title": "缺陷标题",
            "description": "",
            "pending_attachments": [],
            "processed_media_groups": [],
        },
        state=bot.TaskDefectReportStates.waiting_confirm,
    )
    message = DummyMessage(text="✅ 确认创建")

    asyncio.run(bot.on_task_defect_report_confirm(message, state))

    assert state.state is None
    assert created_args["description"] == ""
