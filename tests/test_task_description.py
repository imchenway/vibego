from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import aiosqlite

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

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
        self.bot = SimpleNamespace(username="tester_bot")
        self.date = datetime.now(bot.UTC)
        self.photo = []
        self.document = None
        self.voice = None
        self.video = None
        self.audio = None
        self.animation = None
        self.video_note = None
        self.caption = None
        self.media_group_id = None
        self.text = None

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
    """构造测试用任务记录。"""

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


@pytest.mark.parametrize(
    "task, expected",
    [
        (
            _make_task(
                task_id="TASK_0001",
                title="调研任务",
                status="research",
                task_type="requirement",
            ),
            "- 调研任务",
        ),
        (
            _make_task(
                task_id="TASK_0002",
                title="",
                status="research",
                task_type="defect",
            ),
            "- -",
        ),
        (
            _make_task(
                task_id="TASK_0003",
                title="子任务",
                status="research",
                depth=1,
                task_type=None,
            ),
            "  - 子任务",
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
            title="测试任务",
            status="research",
            priority=3,
            task_type="task",
            tags=(),
            due_date=None,
            description="初始描述",
            actor="tester",
        )
        assert task.description == "初始描述"
        assert task.task_type == "task"

        updated = await svc.update_task(
            task.id,
            actor="tester",
            description="新的描述",
            task_type="defect",
        )
        assert updated.description == "新的描述"
        assert updated.task_type == "defect"

        fetched = await svc.get_task(task.id)
        assert fetched is not None
        assert fetched.description == "新的描述"
        assert fetched.task_type == "defect"

    asyncio.run(_scenario())


def test_format_local_time_conversion():
    assert bot._format_local_time("2025-01-01T00:00:00+08:00") == "2025-01-01 00:00"
    assert bot._format_local_time("invalid") == "invalid"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("requirement", "requirement"),
        ("需求", "requirement"),
        ("Req", "requirement"),
        ("feature", "requirement"),
        ("defect", "defect"),
        ("bug", "defect"),
        ("缺陷", "defect"),
        ("task", "task"),
        ("任务", "task"),
        ("risk", "risk"),
        ("风险", "risk"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_task_type_variants(raw, expected):
    assert bot._normalize_task_type(raw) == expected


def test_format_task_detail_without_history():
    task = _make_task(task_id="TASK_0100", title="测试任务", status="research", task_type="requirement")
    notes = (
        TaskNoteRecord(
            id=1,
            task_id=task.id,
            note_type="research",
            content="第一条备注",
            created_at="2025-01-01T00:00:00+08:00",
        ),
    )

    result = bot._format_task_detail(task, notes=notes)
    lines = result.splitlines()
    assert lines[0] == "📝 标题：" + bot._escape_markdown_text("测试任务")
    expected_meta = (
        f"🏷️ 任务编码：/TASK\\_0100"
        f" · 📂 类型：{bot._strip_task_type_emoji(bot._format_task_type('requirement'))}"
    )
    assert lines[1] == expected_meta
    assert any(line.startswith("🖊️ 描述：") for line in lines)
    assert any(line.startswith("📅 创建时间：") for line in lines)
    assert any(line.startswith("🔁 更新时间：") for line in lines)
    assert "💬 备注记录：" not in result
    assert "变更历史" not in result
    assert "第一条备注" not in result
    stripped_type = bot._strip_task_type_emoji(bot._format_task_type("requirement"))
    assert f"📂 类型：{stripped_type}" in result
    assert "📊 状态：" not in result


def test_format_task_detail_misc_note_without_label():
    task = _make_task(task_id="TASK_0110", title="无标签任务", status="research")
    notes = (
        TaskNoteRecord(
            id=1,
            task_id=task.id,
            note_type="misc",
            content="无需标签的备注内容",
            created_at="2025-02-02T12:00:00+08:00",
        ),
    )
    result = bot._format_task_detail(task, notes=notes)
    lines = result.splitlines()
    note_lines = [line for line in lines if line.startswith("- ")]
    assert not note_lines, "移除备注后不应再展示备注行"
    assert "备注" not in result


def test_task_note_flow_defaults_to_misc(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    state, _storage = make_state(message)
    service = TaskService(tmp_path / "tasks.db", "demo")
    monkeypatch.setattr(bot, "TASK_SERVICE", service)

    async def scenario() -> None:
        await service.initialize()
        task = await service.create_root_task(
            title="测试任务",
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
        assert message.calls, "应提示输入备注内容"
        assert message.calls[-1][0] == "请输入备注内容："

        content_message = DummyMessage()
        content_message.chat = message.chat
        content_message.from_user = message.from_user
        content_message.text = "这是新的备注内容"

        await bot.on_note_content(content_message, state)
        assert await state.get_state() is None

        notes = await service.list_notes(task.id)
        assert notes, "备注应已写入"
        assert notes[-1].note_type == "misc", "默认类型应为 misc"
        assert any("备注已添加" in call[0] for call in content_message.calls), "应输出成功提示"

    asyncio.run(scenario())


def test_task_history_callback(monkeypatch):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=123)
    callback = DummyCallback("task:history:TASK_0200", message)

    task = _make_task(task_id="TASK_0200", title="历史任务", status="test")

    async def fake_get_task(task_id: str):
        assert task_id == task.id
        return task

    history_records = [
        TaskHistoryRecord(
            id=1,
            task_id=task.id,
            field="title",
            old_value="旧标题",
            new_value="历史任务",
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

    assert not message.edits, "历史消息不应再编辑原消息"
    assert message.calls, "历史消息应通过新消息展示"
    sent_text, parse_mode_value, reply_markup, _kwargs = message.calls[-1]
    assert parse_mode_value is not None
    assert sent_text.startswith("```\n")
    assert "任务 TASK_0200 事件历史" in sent_text
    assert "标题：历史任务" in sent_text
    title_line_variants = ["- **更新标题** · 01-01 00:00", "- *更新标题* · 01-01 00:00"]
    assert any(fragment in sent_text for fragment in title_line_variants)
    assert "  - 标题：旧标题 -> 历史任务" in sent_text
    status_line_variants = ["- **更新状态** · 01-02 00:00", "- *更新状态* · 01-02 00:00"]
    assert any(fragment in sent_text for fragment in status_line_variants)
    assert "  - 状态：🔍 调研中 -> 🧪 测试中" in sent_text
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[-1][0].callback_data == f"{bot.TASK_HISTORY_BACK_CALLBACK}:{task.id}"
    assert callback.answers and callback.answers[-1][0] == "已展示历史记录"

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
        title="调研任务",
        status="research",
        priority=3,
        task_type="requirement",
        tags=(),
        due_date=None,
        description="需要调研的事项",
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
    async def fake_list_attachments(task_id: str):
        return []
    monkeypatch.setattr(bot.TASK_SERVICE, "list_attachments", fake_list_attachments)

    async def fake_list_attachments(task_id: str):
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "list_attachments", fake_list_attachments)

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
        assert await state.get_state() == bot.TaskPushStates.waiting_choice.state
        assert callback.answers and "请选择推送模式" in (callback.answers[0][0] or "")
        assert not recorded
        assert message.calls
        prompt_text, _, prompt_markup, _ = message.calls[0]
        assert prompt_text == bot._build_push_mode_prompt()
        assert prompt_markup is not None

        choice_message = DummyMessage()
        choice_message.text = bot.PUSH_MODE_PLAN
        await bot.on_task_push_model_choice(choice_message, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_supplement.state
        assert choice_message.calls
        choice_text, _, choice_markup, _ = choice_message.calls[0]
        assert f"已选择 {bot.PUSH_MODE_PLAN} 模式" in choice_text
        assert bot._build_push_supplement_prompt() in choice_text
        assert choice_markup is not None

        skip_message = DummyMessage()
        skip_message.text = bot.SKIP_TEXT
        await bot.on_task_push_model_supplement(skip_message, state)

        assert recorded
        chat_id, payload, reply_to = recorded[0]
        assert chat_id == message.chat.id
        assert reply_to is message
        lines = payload.splitlines()
        assert lines[0].startswith(f"进入 {bot.PUSH_MODE_PLAN} 模式")
        assert "进入vibe阶段" not in lines[0]
        assert "进入测试阶段" not in lines[0]
        assert "任务标题：调研任务" in payload
        assert "任务编码：/TASK_0001" in payload
        assert "\\_" not in payload
        assert "任务描述：需要调研的事项" in payload
        assert "任务备注：" not in payload
        assert "补充任务描述：-" in payload
        assert payload.endswith("以下为任务执行记录，用于辅助回溯任务处理记录： -")
        assert await state.get_state() is None
        final_text, _, final_markup, _ = message.calls[-1]
        expected_block, _ = bot._wrap_text_in_code_block(payload)
        assert final_text == f"已推送到模型：\n{expected_block}"
        assert isinstance(final_markup, ReplyKeyboardMarkup)
        final_buttons = [button.text for row in final_markup.keyboard for button in row]
        assert bot.WORKER_MENU_BUTTON_TEXT in final_buttons
        assert bot.WORKER_COMMANDS_BUTTON_TEXT in final_buttons
        assert ack_calls and ack_calls[0][2] is message
        assert not logged_events

    asyncio.run(_scenario())


def test_push_model_supplement_uses_caption(monkeypatch, tmp_path: Path):
    """推送补充阶段：图片/文件消息常用 caption 承载文字，应写入补充任务描述。"""

    message = DummyMessage()
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1, full_name="Tester")
    message.text = None
    message.caption = "补充描述中包含图片地址：https://example.com/image.jpg"
    state, _storage = make_state(message)
    asyncio.run(state.set_state(bot.TaskPushStates.waiting_supplement))
    asyncio.run(
        state.update_data(
            task_id="TASK_0001",
            actor="Tester",
            chat_id=message.chat.id,
            origin_message=None,
            push_mode=bot.PUSH_MODE_PLAN,
            processed_media_groups=[],
        )
    )

    task = _make_task(
        task_id="TASK_0001",
        title="调研任务",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == task.id
        return task

    async def fake_collect(msg, target_dir):
        return []

    push_calls: list[dict] = []

    async def fake_push(task_arg, *, chat_id, reply_to, supplement, actor, is_bug_report=False, push_mode=None):
        push_calls.append(
            {
                "task_id": task_arg.id,
                "chat_id": chat_id,
                "supplement": supplement,
                "actor": actor,
            }
        )
        return True, "PROMPT", None

    async def fake_preview(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)

    asyncio.run(bot.on_task_push_model_supplement(message, state))

    assert push_calls, "应触发推送"
    assert push_calls[0]["supplement"] == message.caption


def test_push_model_supplement_falls_back_to_attachment_names(monkeypatch, tmp_path: Path):
    """推送补充阶段：仅附件无文字时，补充描述应生成“见附件：文件名列表”。"""

    message = DummyMessage()
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1, full_name="Tester")
    message.text = None
    message.caption = None
    state, _storage = make_state(message)
    asyncio.run(state.set_state(bot.TaskPushStates.waiting_supplement))
    asyncio.run(
        state.update_data(
            task_id="TASK_0001",
            actor="Tester",
            chat_id=message.chat.id,
            origin_message=None,
            push_mode=bot.PUSH_MODE_PLAN,
            processed_media_groups=[],
        )
    )

    task = _make_task(
        task_id="TASK_0001",
        title="调研任务",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == task.id
        return task

    saved = [
        bot.TelegramSavedAttachment(
            kind="photo",
            display_name="photo.jpg",
            mime_type="image/jpeg",
            absolute_path=tmp_path / "photo.jpg",
            relative_path="./data/photo.jpg",
        )
    ]

    async def fake_collect(msg, target_dir):
        return saved

    bound_calls: list[tuple[str, list[dict], str]] = []

    async def fake_bind(task_arg, attachments, actor):
        bound_calls.append((task_arg.id, list(attachments), actor))
        return []

    push_calls: list[dict] = []

    async def fake_push(task_arg, *, chat_id, reply_to, supplement, actor, is_bug_report=False, push_mode=None):
        push_calls.append(
            {
                "task_id": task_arg.id,
                "chat_id": chat_id,
                "supplement": supplement,
                "actor": actor,
            }
        )
        return True, "PROMPT", None

    async def fake_preview(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_bind_serialized_attachments", fake_bind)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)

    asyncio.run(bot.on_task_push_model_supplement(message, state))

    assert bound_calls, "应绑定附件"
    assert push_calls, "应触发推送"
    assert push_calls[0]["supplement"] == "见附件：photo.jpg"


def test_push_model_supplement_binds_attachments(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1, full_name="Tester")
    message.text = "补充描述"
    message.bot = SimpleNamespace(username="tester_bot")
    message.date = datetime.now(bot.UTC)
    state, _storage = make_state(message)
    asyncio.run(state.set_state(bot.TaskPushStates.waiting_supplement))
    asyncio.run(
        state.update_data(
            task_id="TASK_0001",
            actor="Tester",
            chat_id=message.chat.id,
            origin_message=None,
            push_mode=bot.PUSH_MODE_PLAN,
        )
    )

    task = _make_task(
        task_id="TASK_0001",
        title="调研任务",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == task.id
        return task

    saved = [
        bot.TelegramSavedAttachment(
            kind="document",
            display_name="log.txt",
            mime_type="text/plain",
            absolute_path=tmp_path / "log.txt",
            relative_path="./data/log.txt",
        )
    ]

    async def fake_collect(msg, target_dir):
        return saved

    bound_calls: list[tuple[str, list[dict], str]] = []

    async def fake_bind(task_arg, attachments, actor):
        bound_calls.append((task_arg.id, list(attachments), actor))
        return []

    async def fake_push(task_arg, *, chat_id, reply_to, supplement, actor, is_bug_report=False, push_mode=None):
        return True, "PROMPT", None

    async def fake_reply_to_chat(chat_id, text, reply_to=None, parse_mode=None, reply_markup=None):
        return None

    async def fake_send_session_ack(chat_id, session_path, *, reply_to):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_bind_serialized_attachments", fake_bind)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push)
    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply_to_chat)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_session_ack)

    asyncio.run(bot.on_task_push_model_supplement(message, state))

    assert bound_calls
    task_id, attachments, actor = bound_calls[0]
    assert task_id == task.id
    assert attachments and attachments[0]["path"] == "./data/log.txt"
    assert actor == "Tester"


def test_push_model_preview_fallback_on_too_long(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0001", message)
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1)
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_0001",
        title="调研任务",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def fake_list_history(task_id: str):
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        return True, tmp_path / "session.jsonl"

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)

    async def fake_collect(msg, target_dir):
        return []

    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)

    reply_calls: list[tuple[str, Optional[str], Optional[ReplyKeyboardMarkup]]] = []

    async def fake_reply_to_chat(chat_id, text, reply_to=None, parse_mode=None, reply_markup=None):
        reply_calls.append((text, parse_mode, reply_markup))
        if len(reply_calls) == 1:
            raise TelegramBadRequest(method="sendMessage", message="Bad Request: message is too long")
        return None

    fallback_calls: list[tuple[int, str, Optional[str], bool]] = []

    async def fake_reply_large_text(
        chat_id,
        text,
        *,
        parse_mode=None,
        preformatted=False,
        reply_markup=None,
        attachment_reply_markup=None,
    ):
        fallback_calls.append((chat_id, text, parse_mode, preformatted))
        return text

    async def fake_send_session_ack(chat_id: int, session_path: Path, *, reply_to):
        return None

    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply_to_chat)
    monkeypatch.setattr(bot, "reply_large_text", fake_reply_large_text)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_session_ack)

    async def fake_push(task_arg, *, chat_id, reply_to, supplement, actor, is_bug_report=False, push_mode=None):
        long_prompt = "A" * (bot.TELEGRAM_MESSAGE_LIMIT + 100)
        return True, long_prompt, tmp_path / "session.jsonl"

    monkeypatch.setattr(bot, "_push_task_to_model", fake_push)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        choice_message = DummyMessage()
        choice_message.text = bot.PUSH_MODE_YOLO
        await bot.on_task_push_model_choice(choice_message, state)
        skip_message = DummyMessage()
        skip_message.text = "补充"
        await bot.on_task_push_model_supplement(skip_message, state)

    asyncio.run(_scenario())

    assert fallback_calls
    sent_text = fallback_calls[0][1]
    assert sent_text.startswith("已推送到模型：")
    assert "附件形式发送" in reply_calls[-1][0]


def test_push_model_test_push(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0002", message)
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1)
    state, _storage = make_state(message)

    task = TaskRecord(
        id="TASK_0002",
        project_slug="demo",
        title="测试任务",
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
    async def fake_list_attachments(task_id: str):
        return []
    monkeypatch.setattr(bot.TASK_SERVICE, "list_attachments", fake_list_attachments)

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
        assert await state.get_state() == bot.TaskPushStates.waiting_choice.state
        assert callback.answers and "请选择推送模式" in (callback.answers[0][0] or "")
        assert message.calls
        prompt_text, _, prompt_markup, _ = message.calls[0]
        assert prompt_text == bot._build_push_mode_prompt()
        assert prompt_markup is not None

        choice_message = DummyMessage()
        choice_message.text = bot.PUSH_MODE_YOLO
        await bot.on_task_push_model_choice(choice_message, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_supplement.state

        input_message = DummyMessage()
        input_message.text = "补充说明内容"
        await bot.on_task_push_model_supplement(input_message, state)

        assert recorded
        chat_id, payload, reply_to = recorded[0]
        assert chat_id == message.chat.id
        assert reply_to is message
        lines = payload.splitlines()
        assert lines[0].startswith(f"{bot.PUSH_MODE_YOLO} ")
        assert "进入vibe阶段" not in lines[0]
        assert "进入测试阶段" not in lines[0]
        assert "任务标题：测试任务" in payload
        assert "任务备注：" not in payload
        assert "补充任务描述：补充说明内容" in payload
        assert "以下为任务执行记录，用于辅助回溯任务处理记录： -" in payload
        assert "测试阶段补充说明：" not in payload
        assert await state.get_state() is None
        final_text, _, final_markup, _ = message.calls[-1]
        expected_block, _ = bot._wrap_text_in_code_block(payload)
        assert final_text == f"已推送到模型：\n{expected_block}"
        assert isinstance(final_markup, ReplyKeyboardMarkup)
        final_buttons = [button.text for row in final_markup.keyboard for button in row]
        assert bot.WORKER_MENU_BUTTON_TEXT in final_buttons
        assert bot.WORKER_COMMANDS_BUTTON_TEXT in final_buttons
        assert ack_calls and ack_calls[0][2] is message
        assert message.calls and "已推送到模型" in message.calls[-1][0]
        assert not logged_events

    asyncio.run(_scenario())


def test_push_model_test_push_includes_related_task_context(monkeypatch, tmp_path: Path):
    """推送到模型：当任务存在关联任务时，仅包含关联任务编码（不再展开关联任务详情）。"""

    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0002", message)
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1)
    state, _storage = make_state(message)

    task = TaskRecord(
        id="TASK_0002",
        project_slug="demo",
        title="测试任务",
        status="test",
        priority=2,
        task_type="defect",
        tags=(),
        due_date=None,
        description="主任务描述",
        related_task_id="TASK_0001",
        parent_id=None,
        root_id="TASK_0002",
        depth=0,
        lineage="0002",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )
    related = TaskRecord(
        id="TASK_0001",
        project_slug="demo",
        title="关联任务标题",
        status="research",
        priority=3,
        task_type="requirement",
        tags=(),
        due_date=None,
        description="关联任务描述",
        parent_id=None,
        root_id="TASK_0001",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    async def fake_get_task(task_id: str):
        if task_id == "TASK_0002":
            return task
        if task_id == "TASK_0001":
            return related
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def fake_list_history(task_id: str):
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)

    async def fake_list_notes(task_id: str):
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "list_notes", fake_list_notes)

    async def fake_list_attachments(task_id: str):
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "list_attachments", fake_list_attachments)

    recorded: list[tuple[int, str, DummyMessage]] = []

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
    ):
        assert not ack_immediately
        recorded.append((chat_id, prompt, reply_to))
        return True, tmp_path / "session.jsonl"

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        return None

    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        choice_message = DummyMessage()
        choice_message.text = bot.PUSH_MODE_YOLO
        await bot.on_task_push_model_choice(choice_message, state)
        input_message = DummyMessage()
        input_message.text = "补充说明内容"
        await bot.on_task_push_model_supplement(input_message, state)

    asyncio.run(_scenario())

    assert recorded
    _chat_id, payload, _reply_to = recorded[0]
    assert "任务标题：测试任务" in payload
    assert "关联任务编码：/TASK_0001" in payload
    assert "关联任务信息：" not in payload
    assert "任务标题：关联任务标题" not in payload
    assert "任务编码：/TASK_0001" not in [line.strip() for line in payload.splitlines()]
    assert "任务描述：关联任务描述" not in payload


def test_push_model_done_push(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0004", message)
    message.chat = SimpleNamespace(id=1)
    message.from_user = SimpleNamespace(id=1)
    state, _storage = make_state(message)

    task = TaskRecord(
        id="TASK_0004",
        project_slug="demo",
        title="完成任务",
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
        assert recorded, "完成阶段应发送 /compact"
        _, payload, reply_to = recorded[0]
        assert reply_to is message
        assert payload.endswith("/compact")
        assert callback.answers and callback.answers[0][0] == "已推送到模型"
        assert message.calls
        preview_text, preview_mode, _, _ = message.calls[0]
        expected_block, expected_mode = bot._wrap_text_in_code_block(payload)
        assert preview_text == f"已推送到模型：\n{expected_block}"
        assert preview_mode == expected_mode
        assert ack_calls and ack_calls[0][2] is message
        assert await state.get_state() is None
        assert not logged_events

    asyncio.run(_scenario())


def test_history_context_respects_limits(monkeypatch):
    history_items = [
        TaskHistoryRecord(
            id=index + 1,
            task_id="TASK_1000",
            field="title",
            old_value=f"旧值{index}",
            new_value=f"新值{index}",
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
    assert "旧值0" not in context
    assert "新值59" in context


def test_push_model_missing_task(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:UNKNOWN", message)
    state, _storage = make_state(message)

    async def fake_get_task(task_id: str):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    asyncio.run(bot.on_task_push_model(callback, state))

    assert callback.answers and callback.answers[0][0] == "任务不存在"
    assert not message.calls


def test_build_bug_report_intro_plain_task_id():
    task = _make_task(task_id="TASK_0055", title="编辑描述任务", status="test")
    intro = bot._build_bug_report_intro(task)
    assert "/TASK_0055" in intro
    assert "\\_" not in intro


def test_bug_report_description_binds_attachments(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=321)
    message.from_user = SimpleNamespace(id=321, full_name="Reporter")
    message.text = "缺陷描述文本"
    message.bot = SimpleNamespace(username="tester_bot")
    message.date = datetime.now(bot.UTC)
    state, _storage = make_state(message)
    asyncio.run(state.set_state(bot.TaskBugReportStates.waiting_description))
    asyncio.run(state.update_data(task_id="TASK_0001", reporter="Reporter"))

    task = _make_task(
        task_id="TASK_0001",
        title="缺陷任务",
        status="research",
        task_type="defect",
    )

    async def fake_get_task(task_id: str):
        return task

    saved = [
        bot.TelegramSavedAttachment(
            kind="document",
            display_name="log.txt",
            mime_type="text/plain",
            absolute_path=tmp_path / "log.txt",
            relative_path="./data/log.txt",
        )
    ]

    async def fake_collect(msg, target_dir):
        return saved

    bound_calls: list[tuple[str, list[dict], str]] = []

    async def fake_bind(task_arg, attachments, actor):
        bound_calls.append((task_arg.id, list(attachments), actor))
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_bind_serialized_attachments", fake_bind)

    asyncio.run(bot.on_task_bug_description(message, state))

    assert bound_calls
    task_id, attachments, actor = bound_calls[0]
    assert task_id == task.id
    assert attachments and attachments[0]["path"] == "./data/log.txt"
    assert actor == "Reporter"
    data_after = asyncio.run(state.get_data())
    assert "description" in data_after and "./data/log.txt" in data_after["description"]
    assert asyncio.run(state.get_state()) == bot.TaskBugReportStates.waiting_reproduction.state


def test_build_bug_preview_plain_task_id():
    task = _make_task(task_id="TASK_0055", title="编辑描述任务", status="test")
    preview = bot._build_bug_preview_text(
        task=task,
        description="缺陷描述",
        reproduction="步骤",
        logs="日志",
        reporter="Tester#007",
    )
    assert "任务编码：/TASK_0055" in preview
    assert "\\_" not in preview


def test_bug_report_logs_binds_attachments(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=321)
    message.from_user = SimpleNamespace(id=321, full_name="Reporter")
    message.text = "日志内容"
    message.bot = SimpleNamespace(username="tester_bot")
    message.date = datetime.now(bot.UTC)
    state, _storage = make_state(message)
    asyncio.run(state.set_state(bot.TaskBugReportStates.waiting_logs))
    asyncio.run(
        state.update_data(
            task_id="TASK_0001",
            description="缺陷描述",
            reproduction="步骤",
            reporter="Reporter",
        )
    )

    task = _make_task(
        task_id="TASK_0001",
        title="缺陷任务",
        status="research",
        task_type="defect",
    )

    async def fake_get_task(task_id: str):
        return task

    saved = [
        bot.TelegramSavedAttachment(
            kind="photo",
            display_name="photo.jpg",
            mime_type="image/jpeg",
            absolute_path=tmp_path / "photo.jpg",
            relative_path="./data/photo.jpg",
        )
    ]

    async def fake_collect(msg, target_dir):
        return saved

    bound_calls: list[tuple[str, list[dict], str]] = []

    async def fake_bind(task_arg, attachments, actor):
        bound_calls.append((task_arg.id, list(attachments), actor))
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_bind_serialized_attachments", fake_bind)

    asyncio.run(bot.on_task_bug_logs(message, state))

    assert bound_calls
    task_id, attachments, actor = bound_calls[0]
    assert task_id == task.id
    assert attachments and attachments[0]["path"] == "./data/photo.jpg"
    assert actor == "Reporter"
    data_after = asyncio.run(state.get_data())
    assert "logs" in data_after and "./data/photo.jpg" in data_after["logs"]
    assert asyncio.run(state.get_state()) == bot.TaskBugReportStates.waiting_confirm.state


def test_bug_report_reproduction_binds_attachments(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=321)
    message.from_user = SimpleNamespace(id=321, full_name="Reporter")
    message.text = "复现步骤"
    message.bot = SimpleNamespace(username="tester_bot")
    message.date = datetime.now(bot.UTC)
    state, _storage = make_state(message)
    asyncio.run(state.set_state(bot.TaskBugReportStates.waiting_reproduction))
    asyncio.run(
        state.update_data(
            task_id="TASK_0002",
            description="缺陷描述",
            reporter="Reporter",
        )
    )

    task = _make_task(
        task_id="TASK_0002",
        title="缺陷任务",
        status="research",
        task_type="defect",
    )

    async def fake_get_task(task_id: str):
        return task

    saved = [
        bot.TelegramSavedAttachment(
            kind="photo",
            display_name="photo.jpg",
            mime_type="image/jpeg",
            absolute_path=tmp_path / "photo.jpg",
            relative_path="./data/photo.jpg",
        )
    ]

    async def fake_collect(msg, target_dir):
        return saved

    bound_calls: list[tuple[str, list[dict], str]] = []

    async def fake_bind(task_arg, attachments, actor):
        bound_calls.append((task_arg.id, list(attachments), actor))
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_bind_serialized_attachments", fake_bind)

    asyncio.run(bot.on_task_bug_reproduction(message, state))

    assert bound_calls
    task_id, attachments, actor = bound_calls[0]
    assert task_id == task.id
    assert attachments and attachments[0]["path"] == "./data/photo.jpg"
    assert actor == "Reporter"
    data_after = asyncio.run(state.get_data())
    assert "reproduction" in data_after and "./data/photo.jpg" in data_after["reproduction"]
    assert asyncio.run(state.get_state()) == bot.TaskBugReportStates.waiting_logs.state


def test_bug_report_auto_push_success(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=321)
    message.from_user = SimpleNamespace(id=321, full_name="Tester")
    message.text = "✅ 确认提交"
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_AUTO",
        title="自动推送任务",
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
        return "任务详情：- \n- 示例", ReplyKeyboardMarkup(keyboard=[])

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
            description="缺陷描述",
            reproduction="步骤",
            logs="日志",
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
    assert payload["description"] == "缺陷描述"
    assert payload["reproduction"] == "步骤"
    assert payload["logs"] == "日志"
    assert payload["reporter"] == "Tester#001"
    assert payload["has_reproduction"] is True
    assert payload["has_logs"] is True

    assert len(message.calls) == 1
    push_text, push_mode, push_markup, push_kwargs = message.calls[0]
    expected_block, expected_mode = bot._wrap_text_in_code_block("AUTO_PROMPT")
    assert push_text == f"已推送到模型：\n{expected_block}"
    assert push_mode == expected_mode
    assert isinstance(push_markup, ReplyKeyboardMarkup)
    assert push_kwargs.get("disable_notification") is False


def test_bug_report_auto_push_preview_fallback_on_too_long(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=321)
    message.from_user = SimpleNamespace(id=321, full_name="Tester")
    message.text = "✅ 确认提交"
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_AUTO_LONG",
        title="自动推送长预览任务",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == task.id
        return task

    async def fake_log_event(task_id: str, **kwargs):
        return None

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
        long_prompt = "A" * (bot.TELEGRAM_MESSAGE_LIMIT + 100)
        return True, long_prompt, tmp_path / "session.jsonl"

    reply_calls: list[tuple[str, Optional[str], Optional[ReplyKeyboardMarkup]]] = []

    async def fake_reply_to_chat(chat_id, text, reply_to=None, parse_mode=None, reply_markup=None):
        reply_calls.append((text, parse_mode, reply_markup))
        if len(reply_calls) == 1:
            raise TelegramBadRequest(method="sendMessage", message="Bad Request: message is too long")
        return None

    fallback_calls: list[tuple[int, str, Optional[str], bool]] = []

    async def fake_reply_large_text(
        chat_id,
        text,
        *,
        parse_mode=None,
        preformatted=False,
        reply_markup=None,
        attachment_reply_markup=None,
    ):
        fallback_calls.append((chat_id, text, parse_mode, preformatted))
        return text

    async def fake_send_session_ack(chat_id: int, session_path: Path, *, reply_to):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_event)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push)
    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply_to_chat)
    monkeypatch.setattr(bot, "reply_large_text", fake_reply_large_text)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_session_ack)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)

    async def _scenario() -> None:
        await state.set_state(bot.TaskBugReportStates.waiting_confirm)
        await state.update_data(
            task_id=task.id,
            description="缺陷描述",
            reproduction="步骤",
            logs="日志",
            reporter="Tester#001",
        )
        await bot.on_task_bug_confirm(message, state)

    asyncio.run(_scenario())

    assert fallback_calls
    sent_text = fallback_calls[0][1]
    assert sent_text.startswith("已推送到模型：")
    assert any("附件形式发送" in text for text, _mode, _markup in reply_calls)


def test_bug_report_confirm_accepts_extra_attachments(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=321)
    message.from_user = SimpleNamespace(id=321, full_name="Reporter")
    message.text = ""
    message.bot = SimpleNamespace(username="tester_bot")
    message.date = datetime.now(bot.UTC)
    state, _storage = make_state(message)
    asyncio.run(state.set_state(bot.TaskBugReportStates.waiting_confirm))
    asyncio.run(
        state.update_data(
            task_id="TASK_CONFIRM",
            description="缺陷描述",
            reproduction="步骤",
            logs="日志",
            reporter="Reporter",
        )
    )

    task = _make_task(
        task_id="TASK_CONFIRM",
        title="缺陷任务",
        status="research",
        task_type="defect",
    )

    async def fake_get_task(task_id: str):
        return task

    queue = [
        [
            bot.TelegramSavedAttachment(
                kind="photo",
                display_name="photo.jpg",
                mime_type="image/jpeg",
                absolute_path=tmp_path / "photo.jpg",
                relative_path="./data/photo.jpg",
            )
        ],
        [],
    ]

    async def fake_collect(msg, target_dir):
        if queue:
            return queue.pop(0)
        return []

    bound_calls: list[tuple[str, list[dict], str]] = []

    async def fake_bind(task_arg, attachments, actor):
        bound_calls.append((task_arg.id, list(attachments), actor))
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_bind_serialized_attachments", fake_bind)

    asyncio.run(bot.on_task_bug_confirm(message, state))

    assert bound_calls and bound_calls[0][0] == task.id
    assert bound_calls[0][1][0]["path"] == "./data/photo.jpg"
    # 仍处于确认阶段，等待用户最终确认
    assert asyncio.run(state.get_state()) == bot.TaskBugReportStates.waiting_confirm.state
    assert message.calls and "已记录补充的附件/日志" in message.calls[-1][0]


def test_bug_report_album_aggregates_attachments_once(monkeypatch, tmp_path: Path):
    """相册三张图应聚合一次绑定并写入描述。"""

    message1 = DummyMessage()
    message1.media_group_id = "album1"
    message1.caption = "缺陷描述"
    message1.bot = SimpleNamespace(username="tester_bot")
    message1.date = datetime.now(bot.UTC)

    message2 = DummyMessage()
    message2.media_group_id = "album1"
    message2.bot = message1.bot
    message2.date = message1.date

    message3 = DummyMessage()
    message3.media_group_id = "album1"
    message3.bot = message1.bot
    message3.date = message1.date

    state, _storage = make_state(message1)
    asyncio.run(state.set_state(bot.TaskBugReportStates.waiting_description))
    asyncio.run(state.update_data(task_id="TASK_0001", reporter="Reporter"))

    task = _make_task(
        task_id="TASK_0001",
        title="缺陷任务",
        status="research",
        task_type="defect",
    )

    async def fake_get_task(task_id: str):
        return task

    queue = [
        [
            bot.TelegramSavedAttachment(
                kind="photo",
                display_name="a1.jpg",
                mime_type="image/jpeg",
                absolute_path=tmp_path / "a1.jpg",
                relative_path="./data/a1.jpg",
            )
        ],
        [
            bot.TelegramSavedAttachment(
                kind="photo",
                display_name="a2.jpg",
                mime_type="image/jpeg",
                absolute_path=tmp_path / "a2.jpg",
                relative_path="./data/a2.jpg",
            )
        ],
        [
            bot.TelegramSavedAttachment(
                kind="photo",
                display_name="a3.jpg",
                mime_type="image/jpeg",
                absolute_path=tmp_path / "a3.jpg",
                relative_path="./data/a3.jpg",
            )
        ],
    ]

    async def fake_collect(msg, target_dir):
        if queue:
            return queue.pop(0)
        return []

    bound_calls: list[tuple[str, list[dict], str]] = []

    async def fake_bind(task_arg, attachments, actor):
        bound_calls.append((task_arg.id, list(attachments), actor))
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_bind_serialized_attachments", fake_bind)
    monkeypatch.setattr(bot, "MEDIA_GROUP_AGGREGATION_DELAY", 0.01)

    async def run_album_flow():
        await asyncio.gather(
            bot.on_task_bug_description(message1, state),
            bot.on_task_bug_description(message2, state),
            bot.on_task_bug_description(message3, state),
        )

    asyncio.run(run_album_flow())

    # 仅绑定一次，三张图全部被收录
    assert bound_calls and len(bound_calls) == 1
    assert len(bound_calls[0][1]) == 3
    assert asyncio.run(state.get_state()) == bot.TaskBugReportStates.waiting_reproduction.state
    data_after = asyncio.run(state.get_data())
    description = data_after.get("description", "")
    assert description.count("[附件:") == 3

    # 再次确认应成功通过，无额外附件
    confirm_msg = DummyMessage()
    confirm_msg.text = "✅ 确认提交"
    confirm_msg.chat = message1.chat
    confirm_msg.from_user = message1.from_user
    confirm_msg.bot = message1.bot
    confirm_msg.date = message1.date

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
        push_calls.append((chat_id, supplement, actor))
        return True, "PROMPT", tmp_path / "session.jsonl"

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        return None

    async def fake_render_detail(task_id: str):
        return "任务详情", ReplyKeyboardMarkup(keyboard=[])

    async def fake_log_task_action(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_push_task_to_model", fake_push)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render_detail)
    monkeypatch.setattr(bot, "_log_task_action", fake_log_task_action)

    asyncio.run(bot.on_task_bug_confirm(confirm_msg, state))

    assert push_calls and push_calls[0][0] == message1.chat.id
    assert push_calls[0][2] == "Reporter"


def test_bug_report_auto_push_skipped_when_status_not_supported(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.chat = SimpleNamespace(id=654)
    message.from_user = SimpleNamespace(id=654, full_name="Tester")
    message.text = "✅ 确认提交"
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_SKIP",
        title="不支持任务",
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
        return "详情：-", ReplyKeyboardMarkup(keyboard=[])

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
            description="描述",
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
    assert "当前状态暂不支持自动推送到模型" in warning_text
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
            content="普通回复 /TASK_0001",
        )

    asyncio.run(scenario())
    bot.PENDING_SUMMARIES.clear()
    assert not calls, "普通模型回复不应写入历史"


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
            content=f"SUMMARY_REQUEST_ID::{request_id}\n摘要内容",
        )

    asyncio.run(scenario())
    assert bot.PENDING_SUMMARIES.get(session_key) is None
    assert logged, "摘要应写入历史"
    payload = logged[0]
    assert payload["event_type"] == "model_summary"
    assert payload["task_id"] == "TASK_0001"
    assert not logged_replies, "摘要流程不应触发 model_reply 落库"
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
        buffer="前置 SUMMARY\\_REQUEST\\_ID::other",
    )

    async def scenario() -> None:
        await bot._handle_model_response(
            chat_id=1,
            session_key=session_key,
            session_path=session_path,
            event_offset=77,
            content=f"SUMMARY\\_REQUEST\\_ID::{request_id}\n摘要内容含\\_下划线",
        )

    asyncio.run(scenario())
    assert bot.PENDING_SUMMARIES.get(session_key) is None
    assert logged, "摘要应写入历史"
    payload = logged[0]
    assert payload["event_type"] == "model_summary"
    stored_payload = payload["payload"] or {}
    assert "SUMMARY_REQUEST_ID" in stored_payload.get("content", "")
    assert "\\_" not in stored_payload.get("content", ""), "摘要内容应去除转义"
    bot.PENDING_SUMMARIES.clear()


def test_task_summary_command_triggers_request(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.text = "/task_summary_request_TASK_0200"
    message.chat = SimpleNamespace(id=200)
    message.from_user = SimpleNamespace(id=200, full_name="Tester")

    base_task = TaskRecord(
        id="TASK_0200",
        project_slug="demo",
        title="摘要任务",
        status="research",
        priority=2,
        description="说明",
        parent_id=None,
        root_id="TASK_0200",
        depth=0,
        lineage="0200",
        archived=False,
    )
    updated_task = TaskRecord(
        id="TASK_0200",
        project_slug="demo",
        title="摘要任务",
        status="test",
        priority=2,
        description="说明",
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
        return ("历史记录：\n- 项目条目", 1)

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

    assert updates, "应更新任务状态为测试"
    assert dispatch_calls, "应向模型推送摘要请求"
    prompt_text = dispatch_calls[0][1]
    assert prompt_text.startswith(
        "进入摘要阶段...\n任务编码：/TASK_0200\nSUMMARY_REQUEST_ID::"
    )
    assert message.calls, "应向用户提示处理结果"
    reply_text, _, _, _ = message.calls[-1]
    assert "任务状态已自动更新为“测试”" in reply_text
    assert bot.PENDING_SUMMARIES, "应记录待落库的摘要上下文"
    assert not log_calls, "生成模型摘要的触发动作不应写入任务历史"
    bot.PENDING_SUMMARIES.clear()


def test_task_summary_command_skips_status_when_already_test(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    message.text = "/task_summary_request_TASK_0300"
    message.chat = SimpleNamespace(id=300)
    message.from_user = SimpleNamespace(id=300, full_name="Tester")

    task = TaskRecord(
        id="TASK_0300",
        project_slug="demo",
        title="已有测试任务",
        status="test",
        priority=2,
        description="说明",
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
        raise AssertionError("不应在状态已为 test 时调用 update_task")

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
    assert "任务状态已自动更新为“测试”" not in reply_text
    bot.PENDING_SUMMARIES.clear()


def test_model_quick_reply_keyboard_includes_task_to_test_button():
    markup = bot._build_model_quick_reply_keyboard(task_id="TASK_0001")
    assert isinstance(markup, InlineKeyboardMarkup)
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]
    assert any(value == f"{bot.MODEL_TASK_TO_TEST_PREFIX}TASK_0001" for value in callbacks)


def test_model_task_to_test_callback_updates_status(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("model:task_to_test:TASK_0600", message)

    base_task = TaskRecord(
        id="TASK_0600",
        project_slug="demo",
        title="准备测试",
        status="research",
        priority=2,
        description="说明",
        parent_id=None,
        root_id="TASK_0600",
        depth=0,
        lineage="0600",
        archived=False,
    )
    updated_task = TaskRecord(
        id="TASK_0600",
        project_slug="demo",
        title="准备测试",
        status="test",
        priority=2,
        description="说明",
        parent_id=None,
        root_id="TASK_0600",
        depth=0,
        lineage="0600",
        archived=False,
    )

    updates: list[tuple] = []

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0600"
        return base_task

    async def fake_update_task(task_id: str, *, actor, status=None, **kwargs):
        updates.append((task_id, actor, status))
        assert status == "test"
        return updated_task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)

    async def scenario() -> None:
        await bot.on_model_task_to_test(callback)

    asyncio.run(scenario())
    assert updates, "应更新任务状态为测试"
    assert callback.answers[-1] == ("已切换到测试", False)
    assert message.calls, "应发送状态更新提示消息"
    reply_text, _, reply_markup, _ = message.calls[-1]
    assert "状态已更新为“测试”" in reply_text
    assert isinstance(reply_markup, ReplyKeyboardMarkup)
    assert not message.edits, "不应编辑原消息内容"


def test_model_task_to_test_callback_skips_when_already_test(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("model:task_to_test:TASK_0601", message)

    task = TaskRecord(
        id="TASK_0601",
        project_slug="demo",
        title="已在测试",
        status="test",
        priority=2,
        description="说明",
        parent_id=None,
        root_id="TASK_0601",
        depth=0,
        lineage="0601",
        archived=False,
    )

    async def fake_get_task(task_id: str):
        return task

    async def fake_update_task(*args, **kwargs):
        raise AssertionError("不应在状态已为 test 时调用 update_task")

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)

    async def scenario() -> None:
        await bot.on_model_task_to_test(callback)

    asyncio.run(scenario())
    assert callback.answers[-1] == ("任务已处于“测试”状态", False)
    assert not message.calls


def test_model_task_to_test_callback_handles_missing_task(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("model:task_to_test:TASK_0602", message)

    async def fake_get_task(task_id: str):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> None:
        await bot.on_model_task_to_test(callback)

    asyncio.run(scenario())
    assert callback.answers[-1] == ("任务不存在", True)


def test_model_task_to_test_callback_rejects_invalid_task_id():
    message = DummyMessage()
    callback = DummyCallback("model:task_to_test:BAD_TASK_ID", message)

    async def scenario() -> None:
        await bot.on_model_task_to_test(callback)

    asyncio.run(scenario())
    assert callback.answers[-1] == ("任务 ID 无效", True)


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
    assert reply_text == "任务不存在"


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
    assert reply_text == "任务不存在"


def test_task_summary_command_alias_requires_task_id():
    message = DummyMessage()
    message.text = "/tasksummaryrequest"
    message.chat = SimpleNamespace(id=501)
    message.from_user = SimpleNamespace(id=501, full_name="Tester")

    async def scenario() -> None:
        await bot.on_task_summary_command(message)

    asyncio.run(scenario())
    reply_text, _, _, _ = message.calls[-1]
    assert reply_text == "请提供任务 ID，例如：/task_summary_request_TASK_0001"


def test_ensure_session_watcher_rebinds_pointer(monkeypatch, tmp_path: Path):
    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")

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

    # 清理全局状态，避免影响其他用例
    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_COMPACT_STATE.clear()
    bot.CHAT_REPLY_COUNT.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()


def test_dispatch_prompt_rebinds_when_pointer_updates(monkeypatch, tmp_path: Path):
    pointer = tmp_path / "pointer.txt"
    old_session = tmp_path / "old.jsonl"
    new_session = tmp_path / "new.jsonl"
    old_session.write_text("", encoding="utf-8")
    new_session.write_text("", encoding="utf-8")
    pointer.write_text(str(old_session), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)

    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_COMPACT_STATE.clear()
    bot.CHAT_REPLY_COUNT.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()

    chat_id = 555
    bot.CHAT_SESSION_MAP[chat_id] = str(old_session)
    pointer.write_text(str(new_session), encoding="utf-8")

    ack_records: list[str] = []

    async def fake_reply(chat_id: int, text: str, **kwargs):
        ack_records.append(text)
        class Dummy:
            message_id = 1
        return Dummy()

    async def fake_deliver(chat_id: int, session_path: Path) -> bool:
        return False

    async def fake_watch(*args, **kwargs) -> Optional[Path]:
        return None

    def fake_tmux_send_line(_session: str, _prompt: str) -> None:
        return

    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)
    monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
    monkeypatch.setattr(bot, "_await_session_path", fake_watch)
    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)

    created_tasks: list = []

    class DummyTask:
        def __init__(self):
            self._done = False
        def done(self) -> bool:
            return self._done
        def cancel(self) -> None:
            self._done = True

    def fake_create_task(coro):
        created_tasks.append(coro)
        return DummyTask()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    async def scenario() -> None:
        await bot._dispatch_prompt_to_model(chat_id, "pwd", reply_to=None, ack_immediately=True)

    asyncio.run(scenario())
    assert bot.CHAT_SESSION_MAP[chat_id] == str(new_session)
    assert ack_records, "应发送新的 sessionId 提示"
    assert new_session.stem in ack_records[-1]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass

    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_COMPACT_STATE.clear()
    bot.CHAT_REPLY_COUNT.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()


def test_dispatch_prompt_injects_enforced_agents_notice(monkeypatch, tmp_path: Path):
    """普通 prompt 推送到 tmux 前应自动追加强制规约提示语。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)

    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_COMPACT_STATE.clear()
    bot.CHAT_REPLY_COUNT.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()

    sent: dict[str, str] = {}

    def fake_tmux_send_line(_session: str, line: str) -> None:
        sent["line"] = line

    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)

    async def fake_interrupt(_chat_id: int) -> None:
        return

    monkeypatch.setattr(bot, "_interrupt_long_poll", fake_interrupt)

    created_tasks: list = []

    class DummyTask:
        def __init__(self):
            self._done = False

        def done(self) -> bool:
            return self._done

        def cancel(self) -> None:
            self._done = True

    def fake_create_task(coro):
        created_tasks.append(coro)
        return DummyTask()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    async def scenario() -> None:
        ok, path = await bot._dispatch_prompt_to_model(777, "pwd", reply_to=None, ack_immediately=False)
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert sent.get("line") == f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass

    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_COMPACT_STATE.clear()
    bot.CHAT_REPLY_COUNT.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()


def test_dispatch_prompt_skips_enforced_agents_notice_for_slash_command(monkeypatch, tmp_path: Path):
    """命令类 prompt（以 / 开头）必须跳过强制提示语，避免破坏语义。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)

    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_COMPACT_STATE.clear()
    bot.CHAT_REPLY_COUNT.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()

    sent: dict[str, str] = {}

    def fake_tmux_send_line(_session: str, line: str) -> None:
        sent["line"] = line

    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)

    async def fake_interrupt(_chat_id: int) -> None:
        return

    monkeypatch.setattr(bot, "_interrupt_long_poll", fake_interrupt)

    created_tasks: list = []

    class DummyTask:
        def __init__(self):
            self._done = False

        def done(self) -> bool:
            return self._done

        def cancel(self) -> None:
            self._done = True

    def fake_create_task(coro):
        created_tasks.append(coro)
        return DummyTask()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    async def scenario() -> None:
        ok, path = await bot._dispatch_prompt_to_model(778, "/compact", reply_to=None, ack_immediately=False)
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert sent.get("line") == "/compact"

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass

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
    "raw_prompt,expected",
    [
        ("pwd", f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"),
        ("pwd\n", f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd\n"),
        ("\npwd", f"{bot.ENFORCED_AGENTS_NOTICE}\n\n\npwd"),
        ("  pwd", f"{bot.ENFORCED_AGENTS_NOTICE}\n\n  pwd"),
        ("/compact", "/compact"),
        (" /compact", " /compact"),
        ("", ""),
        ("\n", "\n"),
        (f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd", f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"),
        (f"  {bot.ENFORCED_AGENTS_NOTICE}\nabc", f"  {bot.ENFORCED_AGENTS_NOTICE}\nabc"),
    ],
)
def test_prepend_enforced_agents_notice_cases(raw_prompt: str, expected: str):
    """验证强制规约提示语在多种输入下的拼接与跳过逻辑（覆盖 ≥10 条输入）。"""

    assert bot._prepend_enforced_agents_notice(raw_prompt) == expected


@pytest.mark.parametrize(
    "status,description,expected_checks",
    [
        (
            "research",
            "描述A",
            (
                ("startswith", f"{bot.VIBE_PHASE_PROMPT}\n任务标题：案例任务"),
                ("contains", "任务描述：描述A"),
                ("not_contains", "任务备注："),
                ("endswith", "以下为任务执行记录，用于辅助回溯任务处理记录： -"),
            ),
        ),
        (
            "research",
            None,
            (
                ("startswith", f"{bot.VIBE_PHASE_PROMPT}\n任务标题：案例任务"),
                ("contains", "任务描述：-"),
                ("not_contains", "任务备注："),
                ("endswith", "以下为任务执行记录，用于辅助回溯任务处理记录： -"),
            ),
        ),
        (
            "test",
            "测试说明",
            (
                ("startswith", f"{bot.VIBE_PHASE_PROMPT}\n任务标题：案例任务"),
                ("contains", "任务描述：测试说明"),
                ("not_contains", "任务备注："),
                ("endswith", "以下为任务执行记录，用于辅助回溯任务处理记录： -"),
            ),
        ),
        (
            "test",
            " ",
            (
                ("startswith", f"{bot.VIBE_PHASE_PROMPT}\n任务标题：案例任务"),
                ("contains", "任务描述：-"),
                ("not_contains", "任务备注："),
                ("endswith", "以下为任务执行记录，用于辅助回溯任务处理记录： -"),
            ),
        ),
        (
            "done",
            "",
            (("equals", "/compact"),),
        ),
        (
            "done",
            "已完成",
            (("equals", "/compact"),),
        ),
    ],
)
def test_build_model_push_payload_cases(status, description, expected_checks):
    task = TaskRecord(
        id="TASK_CHECK",
        project_slug="demo",
        title="案例任务",
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
        elif kind == "not_contains":
            assert expected not in payload
        else:
            raise AssertionError(f"未知断言类型 {kind}")


def test_build_model_push_payload_with_supplement():
    task = TaskRecord(
        id="TASK_CHECK_SUP",
        project_slug="demo",
        title="补充示例",
        status="test",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="原始描述",
        parent_id=None,
        root_id="TASK_CHECK_SUP",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    history = "2025-01-01T10:00:00+08:00 | 推送到模型（结果=success）\n补充任务描述：旧补充"

    payload = bot._build_model_push_payload(task, supplement="补充内容", history=history)
    lines = payload.splitlines()
    assert lines[0] == bot.VIBE_PHASE_PROMPT
    assert "任务描述：原始描述" in payload
    assert "任务编码：/TASK_CHECK_SUP" in payload
    assert "\\_" not in payload
    assert "任务备注：" not in payload
    assert "补充任务描述：补充内容" in payload
    assert "以下为任务执行记录，用于辅助回溯任务处理记录：" in payload
    assert "2025-01-01T10:00:00+08:00 | 推送到模型（结果=success）" in payload
    assert "补充任务描述：旧补充" in payload
    history_intro_index = payload.index("以下为任务执行记录，用于辅助回溯任务处理记录：")
    assert payload.index("补充任务描述：补充内容") < history_intro_index
    assert payload.endswith("补充任务描述：旧补充")
    assert "## 测试阶段" not in payload
    assert "测试阶段补充说明：" not in payload


def test_build_model_push_payload_without_history_formatting():
    task = TaskRecord(
        id="TASK_NO_HISTORY",
        project_slug="demo",
        title="无历史任务",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="描述B",
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
    assert "任务备注：" not in payload
    assert "以下为任务执行记录，用于辅助回溯任务处理记录： -" in payload
    assert payload.endswith("以下为任务执行记录，用于辅助回溯任务处理记录： -")
    assert "需求调研问题分析阶段" not in payload


def test_build_model_push_payload_with_notes():
    task = TaskRecord(
        id="TASK_CHECK_NOTES",
        project_slug="demo",
        title="备注任务",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="描述B",
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
            content="第一条备注",
            created_at="2025-01-01T00:00:00+08:00",
        ),
        TaskNoteRecord(
            id=2,
            task_id=task.id,
            note_type="research",
            content="第二条备注\n包含换行",
            created_at="2025-01-02T00:00:00+08:00",
        ),
    ]

    payload = bot._build_model_push_payload(task, notes=notes)
    assert "第一条备注" not in payload
    assert "第二条备注" not in payload
    assert "任务备注：" not in payload
    assert payload.startswith(bot.VIBE_PHASE_PROMPT)


def test_build_model_push_payload_skips_bug_notes():
    task = TaskRecord(
        id="TASK_SKIP_BUG",
        project_slug="demo",
        title="缺陷备注忽略",
        status="test",
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description="描述C",
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
            content="缺陷详情\n需要修复",
            created_at="2025-01-03T00:00:00+08:00",
        ),
        TaskNoteRecord(
            id=2,
            task_id=task.id,
            note_type="misc",
            content="仍需跟进",
            created_at="2025-01-04T00:00:00+08:00",
        ),
    ]

    payload = bot._build_model_push_payload(task, notes=notes)
    assert "缺陷详情" not in payload
    assert "需要修复" not in payload
    assert "仍需跟进" not in payload
    assert "任务备注：" not in payload
    assert "缺陷记录（最近 3 条）" not in payload
    assert payload.startswith(bot.VIBE_PHASE_PROMPT)


def test_build_model_push_payload_removes_legacy_bug_header():
    task = _make_task(task_id="TASK_LEGACY", title="兼容旧标题", status="test")
    legacy_history = "缺陷记录（最近 3 条）：\n2025-01-02 10:00 | 已同步历史记录"

    payload = bot._build_model_push_payload(task, history=legacy_history)

    assert "缺陷记录（最近 3 条）" not in payload
    assert "2025-01-02 10:00 | 已同步历史记录" in payload
    assert "以下为任务执行记录，用于辅助回溯任务处理记录：" in payload


# --- 任务描述编辑交互 ---


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

    task = _make_task(task_id="TASK_EDIT", title="示例任务", status="research")
    task.description = "原始描述"

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
    assert data.get("current_description") == "原始描述"
    assert callback.answers and callback.answers[-1] == (None, False)
    assert len(message.calls) >= 3, "应先展示菜单与原描述再提示输入"
    first_text, _parse_mode, first_markup, _ = message.calls[0]
    assert "当前描述" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)
    labels = _extract_reply_labels(first_markup)
    assert any(bot.TASK_DESC_CLEAR_TEXT in label for label in labels)
    assert any(bot.TASK_DESC_CANCEL_TEXT in label for label in labels)
    assert any(bot.TASK_DESC_REPROMPT_TEXT in label for label in labels)
    third_text, _, third_markup, _ = message.calls[2]
    assert "请直接发送新的任务描述" in third_text
    assert third_markup is None


def test_task_edit_description_redirects_to_fsm(monkeypatch):
    message = DummyMessage()
    state, _storage = make_state(message)
    task = _make_task(task_id="TASK_EDIT", title="示例任务", status="research")
    task.description = "原始描述"

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_EDIT"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", actor="Tester#1")
        await state.set_state(bot.TaskEditStates.waiting_field_choice)
        message.text = "描述"
        await bot.on_edit_field_choice(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("task_id") == "TASK_EDIT"
    assert data.get("current_description") == "原始描述"
    assert len(message.calls) >= 3
    first_text, _, first_markup, _ = message.calls[0]
    assert "当前描述" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)


def test_task_desc_reprompt_menu_replays_prompt():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", current_description="旧描述")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        message.text = f"1. {bot.TASK_DESC_REPROMPT_TEXT}"
        await bot.on_task_desc_input(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("current_description") == "旧描述"
    assert len(message.calls) >= 3
    first_text, _, first_markup, _ = message.calls[-3]
    assert "当前描述" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)


def test_task_desc_input_clear_menu_enters_confirm():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", actor="Tester#1", current_description="旧描述")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        message.text = bot.TASK_DESC_CLEAR_TEXT
        await bot.on_task_desc_input(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert data.get("new_description") == ""
    assert message.calls, "应发送确认提示"
    confirm_text, _, confirm_markup, _ = message.calls[-1]
    assert "请确认新的任务描述" in confirm_text
    assert isinstance(confirm_markup, ReplyKeyboardMarkup)
    labels = _extract_reply_labels(confirm_markup)
    assert any(bot.TASK_DESC_CONFIRM_TEXT in label for label in labels)
    assert any(bot.TASK_DESC_RETRY_TEXT in label for label in labels)


def test_task_desc_input_moves_to_confirm():
    message = DummyMessage()
    message.text = "新的描述"
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", actor="Tester#1", current_description="旧描述")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_input(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert data.get("new_description") == "新的描述"
    assert message.calls, "应发送确认提示"
    confirm_text, _, confirm_markup, _ = message.calls[-1]
    assert "请确认新的任务描述" in confirm_text
    assert isinstance(confirm_markup, ReplyKeyboardMarkup)


def test_task_desc_input_cancel_text():
    message = DummyMessage()
    message.text = "取消"
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        await state.update_data(task_id="TASK_EDIT", current_description="旧描述")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_input(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and message.calls[-1][0] == "已取消编辑任务描述。"


def test_task_desc_input_cancel_menu_button():
    message = DummyMessage()
    message.text = f"1. {bot.TASK_DESC_CANCEL_TEXT}"
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        await state.update_data(task_id="TASK_EDIT", current_description="旧描述")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_input(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and message.calls[-1][0] == "已取消编辑任务描述。"


def test_task_desc_input_rejects_too_long():
    message = DummyMessage()
    message.text = "x" * (bot.DESCRIPTION_MAX_LENGTH + 1)
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        await state.update_data(task_id="TASK_EDIT", current_description="旧描述")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_input(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert len(message.calls) >= 4, "超长后需要重新提示输入"
    warn_text, _, warn_markup, _ = message.calls[0]
    assert "不可超过" in warn_text
    assert isinstance(warn_markup, ReplyKeyboardMarkup)
    tail_text, _, tail_markup, _ = message.calls[-1]
    assert "请直接发送新的任务描述" in tail_text
    assert tail_markup is None


def test_task_desc_confirm_updates_description(monkeypatch):
    message = DummyMessage()
    state, _storage = make_state(message)

    updated_task = _make_task(task_id="TASK_EDIT", title="描述任务", status="research")
    update_calls: list[tuple[str, str, str]] = []

    async def fake_update(task_id: str, *, actor: str, description: str):
        update_calls.append((task_id, actor, description))
        updated_task.description = description
        return updated_task

    async def fake_render(task_id: str):
        assert task_id == "TASK_EDIT"
        return "任务详情：示例", ReplyKeyboardMarkup(keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)

    async def scenario() -> str | None:
        message.text = bot.TASK_DESC_CONFIRM_TEXT
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="最终描述",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert update_calls == [("TASK_EDIT", "Tester#1", "最终描述")]
    assert message.calls and "任务描述已更新" in message.calls[0][0]
    assert any("任务描述已更新：" in text for text, *_ in message.calls)


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
    assert message.calls and "会话已失效" in message.calls[0][0]


def test_task_desc_retry_returns_to_input(monkeypatch):
    message = DummyMessage()
    state, _storage = make_state(message)

    task = _make_task(task_id="TASK_EDIT", title="描述任务", status="research")
    task.description = "原始描述"

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_EDIT"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> tuple[str | None, dict]:
        message.text = bot.TASK_DESC_RETRY_TEXT
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="草稿描述",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("new_description") is None
    assert len(message.calls) >= 4
    first_text, _, first_markup, _ = message.calls[0]
    assert "已回到描述输入阶段" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)
    assert any("当前描述" in text for text, *_ in message.calls)


def test_task_desc_confirm_missing_description_reprompts():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        message.text = bot.TASK_DESC_CONFIRM_TEXT
        await state.update_data(
            task_id="TASK_EDIT",
            current_description="仍为旧描述",
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
    assert "描述内容已失效" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)
    assert any("仍为旧描述" in text for text, *_ in message.calls)


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
            new_description="草稿描述",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and "任务不存在" in message.calls[0][0]


def test_task_desc_confirm_update_failure(monkeypatch):
    message = DummyMessage()
    state, _storage = make_state(message)

    async def fake_update(task_id: str, *, actor: str, description: str):
        raise ValueError("无法更新描述")

    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update)

    async def scenario() -> str | None:
        message.text = bot.TASK_DESC_CONFIRM_TEXT
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="异常描述",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and message.calls[0][0] == "无法更新描述"


def test_task_desc_confirm_unknown_message_prompts_menu():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = "随便输入"
        await state.update_data(task_id="TASK_EDIT", new_description="草稿", actor="Tester#1")
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert message.calls and ("请使用菜单中的按钮" in message.calls[-1][0] or "当前处于确认阶段" in message.calls[-1][0])
    assert isinstance(message.calls[-1][2], ReplyKeyboardMarkup)


def test_task_desc_confirm_cancel_menu_exits():
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = bot.TASK_DESC_CANCEL_TEXT
        await state.update_data(task_id="TASK_EDIT", new_description="草稿")
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None
    assert message.calls and message.calls[-1][0] == "已取消编辑任务描述。"


def test_task_desc_legacy_callback_reprompts_input():
    message = DummyMessage()
    callback = DummyCallback(f"{bot.TASK_DESC_INPUT_CALLBACK}:TASK_EDIT", message)
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", current_description="旧描述")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_legacy_callback(callback, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state
    assert data.get("current_description") == "旧描述"
    assert callback.answers and callback.answers[-1] == ("任务描述编辑的按钮已移动到菜单栏，请使用菜单操作。", True)
    assert len(message.calls) >= 3
    first_text, _, first_markup, _ = message.calls[0]
    assert "当前描述" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)


def test_task_desc_legacy_callback_replays_confirm():
    message = DummyMessage()
    callback = DummyCallback(f"{bot.TASK_DESC_CONFIRM_CALLBACK}:TASK_EDIT", message)
    state, _storage = make_state(message)

    async def scenario() -> tuple[str | None, dict]:
        await state.update_data(task_id="TASK_EDIT", new_description="草稿描述")
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_legacy_callback(callback, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert data.get("new_description") == "草稿描述"
    assert callback.answers and callback.answers[-1] == ("任务描述编辑的按钮已移动到菜单栏，请使用菜单操作。", True)
    assert message.calls and "请确认新的任务描述" in message.calls[-1][0]
    assert isinstance(message.calls[-1][2], ReplyKeyboardMarkup)


def test_format_history_description_push_model_includes_supplement():
    record = TaskHistoryRecord(
        id=1,
        task_id="TASK_001",
        field="",
        old_value=None,
        new_value="旧补充",
        actor="tester",
        event_type=bot.HISTORY_EVENT_TASK_ACTION,
        payload=json.dumps(
            {
                "action": "push_model",
                "result": "success",
                "model": "codex",
                "supplement": "最新补充描述",
            }
        ),
        created_at="2025-01-01T00:00:00+08:00",
    )

    text = bot._format_history_description(record)
    assert "结果：success" in text
    assert "模型：codex" in text
    assert "补充描述：最新补充描述" in text


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
    assert not bot._is_cancel_message("继续编辑")


def test_on_text_handles_quick_task_lookup(monkeypatch):
    message = DummyMessage()
    message.text = "/TASK_0007"
    state, _storage = make_state(message)
    calls: list[tuple[DummyMessage, str]] = []

    async def fake_reply(detail_message: DummyMessage, task_id: str) -> None:
        calls.append((detail_message, task_id))

    monkeypatch.setattr(bot, "_reply_task_detail_message", fake_reply)

    asyncio.run(bot.on_text(message, state))

    assert calls == [(message, "TASK_0007")]


def test_on_text_ignores_regular_commands(monkeypatch):
    message = DummyMessage()
    message.text = "/task_show"
    state, _storage = make_state(message)

    async def fake_reply(detail_message: DummyMessage, task_id: str) -> None:  # pragma: no cover
        raise AssertionError("不应触发任务详情回复")

    monkeypatch.setattr(bot, "_reply_task_detail_message", fake_reply)

    asyncio.run(bot.on_text(message, state))


def test_on_text_aggregates_near_limit_messages_into_single_attachment_prompt(monkeypatch, tmp_path: Path):
    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 10)
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.01)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_cleanup_attachment_storage", lambda: None)

    recorded: list[str] = []

    async def fake_handle(_message: DummyMessage, prompt: str) -> None:
        recorded.append(prompt)

    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_handle)

    message1 = DummyMessage()
    message1.text = "A" * 10  # 触发阈值，开启聚合
    message2 = DummyMessage()
    message2.message_id = message1.message_id + 1
    message2.text = "B"
    message3 = DummyMessage()
    message3.message_id = message1.message_id + 2
    message3.text = "C"

    state, _storage = make_state(message1)

    async def _scenario() -> None:
        await bot.on_text(message1, state)
        await bot.on_text(message2, state)
        await bot.on_text(message3, state)
        await asyncio.sleep(0.05)

    asyncio.run(_scenario())

    assert len(recorded) == 1
    payload = recorded[0]
    assert "附件列表" in payload
    file_lines = [line for line in payload.splitlines() if "→" in line]
    assert len(file_lines) == 1
    path_str = file_lines[0].split("→", 1)[1].strip()
    file_path = Path(path_str)
    assert file_path.exists()
    assert file_path.read_text(encoding="utf-8") == ("A" * 10 + "B" + "C")


def test_on_text_aggregates_prefix_and_near_limit_messages_into_single_attachment_prompt(monkeypatch, tmp_path: Path):
    """短前缀 + 长日志：应合并为一次推送，并在 prompt 中同时包含前缀与附件列表。"""

    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 10)
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.01)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_MAX_CHARS", 50)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS", 200)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_cleanup_attachment_storage", lambda: None)

    recorded: list[str] = []

    async def fake_handle(_message: DummyMessage, prompt: str) -> None:
        recorded.append(prompt)

    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_handle)

    prefix = DummyMessage()
    prefix.text = "1 见如下日志："
    part1 = DummyMessage()
    part1.message_id = prefix.message_id + 1
    part1.text = "A" * 10  # 触发阈值，进入聚合并落盘
    part2 = DummyMessage()
    part2.message_id = prefix.message_id + 2
    part2.text = "B"

    state, _storage = make_state(prefix)

    async def _scenario() -> None:
        await bot.on_text(prefix, state)
        await bot.on_text(part1, state)
        await bot.on_text(part2, state)
        await asyncio.sleep(0.05)

    asyncio.run(_scenario())

    assert len(recorded) == 1
    payload = recorded[0]
    assert "1 见如下日志：" in payload
    assert "附件列表" in payload
    file_lines = [line for line in payload.splitlines() if "→" in line]
    assert len(file_lines) == 1
    path_str = file_lines[0].split("→", 1)[1].strip()
    file_path = Path(path_str)
    assert file_path.exists()
    assert file_path.read_text(encoding="utf-8") == ("A" * 10 + "B")


def test_on_text_prefix_only_falls_back_to_normal_dispatch_after_delay(monkeypatch):
    """仅发送短前缀且窗口内无后续日志：应在延迟后回退为普通推送，避免吞消息。"""

    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 10)
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.01)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_MAX_CHARS", 50)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS", 200)

    recorded: list[str] = []

    async def fake_handle(_message: DummyMessage, prompt: str) -> None:
        recorded.append(prompt)

    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_handle)

    prefix = DummyMessage()
    prefix.text = "1 见如下日志："
    state, _storage = make_state(prefix)

    async def _scenario() -> None:
        await bot.on_text(prefix, state)
        await asyncio.sleep(0.05)

    asyncio.run(_scenario())

    assert recorded == ["1 见如下日志："]


def test_on_text_prefix_followed_by_short_message_flushes_prefix(monkeypatch):
    """短前缀后面跟的消息仍很短：应立即回退推送前缀，避免误把普通对话转附件。"""

    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 10)
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.5)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_MAX_CHARS", 50)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS", 200)

    recorded: list[str] = []

    async def fake_handle(_message: DummyMessage, prompt: str) -> None:
        recorded.append(prompt)

    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_handle)

    prefix = DummyMessage()
    prefix.text = "1 见如下日志："
    followup = DummyMessage()
    followup.message_id = prefix.message_id + 1
    followup.text = "ok"
    state, _storage = make_state(prefix)

    async def _scenario() -> None:
        await bot.on_text(prefix, state)
        await bot.on_text(followup, state)

    asyncio.run(_scenario())

    assert recorded == ["1 见如下日志：", "ok"]
    assert all("附件列表" not in item for item in recorded)


def test_on_text_prefix_captures_short_log_fragment_before_near_limit_chunk(monkeypatch, tmp_path: Path):
    """短前缀触发窗口后，首段日志可能小于 near-limit：仍应纳入聚合，避免附件内容缺头。"""

    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 20)
    # 留出足够的时间窗口，避免“短前缀 finalize”在后续分片到达前提前触发。
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.2)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_MAX_CHARS", 50)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS", 200)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_cleanup_attachment_storage", lambda: None)

    recorded: list[str] = []

    async def fake_handle(_message: DummyMessage, prompt: str) -> None:
        recorded.append(prompt)

    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_handle)

    prefix = DummyMessage()
    prefix.text = "1 见如下日志："
    part1 = DummyMessage()
    part1.message_id = prefix.message_id + 1
    part1.text = "2026-01-27 17:12:13"  # 小于阈值，但符合日志前缀特征
    part2 = DummyMessage()
    part2.message_id = prefix.message_id + 2
    part2.text = "X" * 20  # 达到阈值，确保落盘

    state, _storage = make_state(prefix)

    async def _scenario() -> None:
        await bot.on_text(prefix, state)
        await bot.on_text(part1, state)
        await bot.on_text(part2, state)
        await asyncio.sleep(0.25)

    asyncio.run(_scenario())

    assert len(recorded) == 1
    payload = recorded[0]
    assert "1 见如下日志：" in payload
    assert "附件列表" in payload
    file_lines = [line for line in payload.splitlines() if "→" in line]
    assert len(file_lines) == 1
    path_str = file_lines[0].split("→", 1)[1].strip()
    file_path = Path(path_str)
    assert file_path.exists()
    assert file_path.read_text(encoding="utf-8") == ("2026-01-27 17:12:13" + "X" * 20)


def test_on_text_skips_text_paste_aggregation_for_short_messages(monkeypatch):
    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 10)
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.01)

    recorded: list[str] = []

    async def fake_handle(_message: DummyMessage, prompt: str) -> None:
        recorded.append(prompt)

    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_handle)

    message = DummyMessage()
    message.text = "short"
    state, _storage = make_state(message)

    asyncio.run(bot.on_text(message, state))

    assert recorded == ["short"]


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
                "根任务",
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
                "子任务",
                "test",
                2,
                "task",
                "[]",
                None,
                "子任务描述",
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
                "第二个根任务",
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
            ("TASK-0001", "misc", "备注内容", created),
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
            title="列表示例",
            status="research",
            priority=3,
            task_type="task",
            tags=(),
            due_date=None,
            description="描述A",
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
    assert message.calls, "应生成列表消息"
    text, parse_mode, markup, _ = message.calls[0]
    lines = text.splitlines()
    assert lines[:2] == [
        "*任务列表*",
        "筛选状态：全部 · 页码 1/1 · 每页 10 条 · 总数 1",
    ]
    assert "- 🛠️ 列表示例" not in text
    assert "- ⚪ 列表示例" not in text
    assert f"[{task_id}]" not in text
    assert markup is not None
    status_rows: list[list] = []
    for row in markup.inline_keyboard:
        if any(btn.callback_data.startswith("task:detail") for btn in row):
            break
        status_rows.append(row)
    assert status_rows, "应存在状态筛选按钮行"
    first_row = status_rows[0]
    assert first_row[0].text == "✔️ ⭐ 全部"
    assert all(not btn.text.lstrip().startswith(tuple("0123456789")) for row in status_rows for btn in row)
    options_count = len(bot.STATUS_FILTER_OPTIONS)
    if options_count <= 4:
        assert len(status_rows) == 1
        assert len(status_rows[0]) == options_count
    else:
        assert all(len(row) <= 3 for row in status_rows), "状态按钮每行不应超过三个"
    assert any(
        btn.callback_data == "task:list_page:-:1:10"
        for row in status_rows
        for btn in row
    ), "应包含筛选全部的按钮"
    detail_texts = [
        btn.text
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data == f"task:detail:{task_id}"
    ]
    assert detail_texts, "应包含跳转详情的按钮"
    assert detail_texts[0].startswith("🔍 "), "详情按钮文本应展示状态图标"
    assert all(icon not in detail_texts[0] for icon in bot.TASK_TYPE_EMOJIS.values()), "详情按钮文本不应展示类型图标"
    assert "⚪" not in detail_texts[0], "详情按钮文本不应展示默认类型图标"


def test_task_desc_confirm_numeric_input_1_confirms(monkeypatch):
    """测试输入数字"1"应触发确认更新操作"""
    message = DummyMessage()
    state, _storage = make_state(message)

    update_calls = []

    async def fake_update_task(task_id: str, *, actor: str, **kwargs) -> TaskRecord:
        update_calls.append((task_id, actor, kwargs.get("description")))
        return _make_task(task_id=task_id, title="任务", status="research")

    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)

    async def fake_render_task_detail(task_id: str):
        return "任务详情", None

    monkeypatch.setattr(bot, "_render_task_detail", fake_render_task_detail)

    async def scenario() -> str | None:
        message.text = "1"  # 输入数字1，应该对应第一个选项"确认更新"
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="新的描述内容",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None, "确认后应清空状态"
    assert update_calls == [("TASK_EDIT", "Tester#1", "新的描述内容")], "应调用更新任务"
    assert message.calls and "任务描述已更新" in message.calls[0][0]


def test_task_desc_confirm_numeric_input_2_retries(monkeypatch):
    """测试输入数字"2"应触发重新输入操作"""
    message = DummyMessage()
    state, _storage = make_state(message)

    task = _make_task(task_id="TASK_EDIT", title="描述任务", status="research")
    task.description = "原始描述"

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_EDIT"
        return task

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> tuple[str | None, dict]:
        message.text = "2"  # 输入数字2，应该对应第二个选项"重新输入"
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="草稿描述",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state(), await state.get_data()

    state_value, data = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_content.state, "应回到输入状态"
    assert data.get("new_description") is None, "应清空草稿描述"
    assert len(message.calls) >= 4
    first_text, _, first_markup, _ = message.calls[0]
    assert "已回到描述输入阶段" in first_text
    assert isinstance(first_markup, ReplyKeyboardMarkup)


def test_task_desc_confirm_numeric_input_3_cancels():
    """测试输入数字"3"应触发取消操作"""
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = "3"  # 输入数字3，应该对应第三个选项"取消"
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="草稿描述",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None, "取消后应清空状态"
    assert message.calls and "已取消编辑任务描述" in message.calls[0][0]
    _, _, markup, _ = message.calls[0]
    assert isinstance(markup, ReplyKeyboardMarkup), "应显示主菜单键盘"


def test_task_desc_confirm_numeric_input_with_prefix():
    """测试输入带前缀的按钮文本（如"1. ✅ 确认更新"）也能正确识别"""
    message = DummyMessage()
    state, _storage = make_state(message)

    update_calls = []

    async def fake_update_task(task_id: str, *, actor: str, **kwargs) -> TaskRecord:
        update_calls.append((task_id, actor, kwargs.get("description")))
        return _make_task(task_id=task_id, title="任务", status="research")

    def monkeypatch_update():
        import bot as bot_module
        original_update = bot_module.TASK_SERVICE.update_task
        bot_module.TASK_SERVICE.update_task = fake_update_task
        return original_update

    async def fake_render_task_detail(task_id: str):
        return "任务详情", None

    def monkeypatch_render():
        import bot as bot_module
        original_render = bot_module._render_task_detail
        bot_module._render_task_detail = fake_render_task_detail
        return original_render

    async def scenario() -> str | None:
        message.text = "1. ✅ 确认更新"  # 带序号和emoji的完整按钮文本
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="新的描述内容",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)

        # 临时替换函数
        original_update = monkeypatch_update()
        original_render = monkeypatch_render()

        try:
            await bot.on_task_desc_confirm_stage_text(message, state)
            return await state.get_state()
        finally:
            # 恢复原函数
            bot.TASK_SERVICE.update_task = original_update
            bot._render_task_detail = original_render

    state_value = asyncio.run(scenario())

    assert state_value is None, "确认后应清空状态"
    assert update_calls == [("TASK_EDIT", "Tester#1", "新的描述内容")], "应调用更新任务"
    assert message.calls and "任务描述已更新" in message.calls[0][0]


def test_task_desc_confirm_text_input_still_works():
    """测试直接输入文本（如"确认"、"取消"）仍然有效"""
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = "取消"  # 直接输入文本
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="草稿描述",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value is None, "取消后应清空状态"
    assert message.calls and "已取消编辑任务描述" in message.calls[0][0]


def test_task_desc_confirm_invalid_numeric_input():
    """测试输入无效数字（如"0"、"99"）应提示重新选择"""
    message = DummyMessage()
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        message.text = "99"  # 超出范围的数字
        await state.update_data(
            task_id="TASK_EDIT",
            new_description="草稿描述",
            actor="Tester#1",
            current_description="旧描述",
        )
        await state.set_state(bot.TaskDescriptionStates.waiting_confirm)
        await bot.on_task_desc_confirm_stage_text(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    # 应该保持在确认状态，并提示用户
    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    assert message.calls
    assert "当前处于确认阶段" in message.calls[0][0] or "请选择" in message.calls[0][0]
