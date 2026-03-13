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
        self.reply_markup_edits = []
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

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.reply_markup_edits.append((reply_markup, kwargs))
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


def test_format_task_detail_defect_uses_reproduction_and_expected_result():
    """缺陷任务详情应优先展示复现步骤与期望结果。"""

    task = _make_task(task_id="TASK_0111", title="缺陷任务", status="research", task_type="defect")
    task.description = "复现步骤：\n1. 打开页面\n\n期望结果：\n页面应正常显示"

    result = bot._format_task_detail(task, notes=())

    assert "🧪 复现步骤：" in result
    assert "打开页面" in result
    assert "🎯 期望结果：页面应正常显示" in result
    assert "🖊️ 描述：" not in result


def test_format_task_detail_defect_falls_back_to_generic_description_when_unstructured():
    """历史缺陷任务若仍为旧描述结构，应回退到通用描述展示。"""

    task = _make_task(task_id="TASK_0112", title="历史缺陷", status="research", task_type="defect")
    task.description = "旧版自由描述"

    result = bot._format_task_detail(task, notes=())

    assert "🖊️ 描述：旧版自由描述" in result
    assert "🧪 复现步骤：" not in result
    assert "🎯 期望结果：" not in result


def test_format_task_detail_task_uses_current_and_expected_effect():
    """优化任务详情应优先展示当前效果与期望效果。"""

    task = _make_task(task_id="TASK_0113", title="优化任务", status="research", task_type="task")
    task.description = "当前效果：\n需要点击两次\n\n期望效果：\n点击一次即可提交"

    result = bot._format_task_detail(task, notes=())

    assert "当前效果：" in result
    assert "点击两次" in result
    assert "期望效果：点击一次即可提交" in result
    assert "🖊️ 描述：" not in result


def test_format_task_detail_task_falls_back_to_generic_description_when_unstructured():
    """历史优化任务若仍为旧描述结构，应回退到通用描述展示。"""

    task = _make_task(task_id="TASK_0114", title="历史优化", status="research", task_type="task")
    task.description = "旧版自由描述"

    result = bot._format_task_detail(task, notes=())

    assert "🖊️ 描述：旧版自由描述" in result
    assert "当前效果：" not in result
    assert "期望效果：" not in result


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

    async def fake_list_project_live_sessions():
        return [bot.SessionLiveEntry(key="main", label="💻 主会话（vibe）", tmux_session="vibe", kind="main")]

    monkeypatch.setattr(bot, "_list_project_live_sessions", fake_list_project_live_sessions)

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
        **_kwargs,
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
        assert await state.get_state() == bot.TaskPushStates.waiting_dispatch_target.state
        assert callback.answers and "请选择处理方式" in (callback.answers[0][0] or "")
        assert not recorded
        assert message.calls
        prompt_text, _, prompt_markup, _ = message.calls[0]
        assert prompt_text == bot._build_push_dispatch_target_prompt()
        assert prompt_markup is not None

        dispatch_target_message = DummyMessage()
        dispatch_target_message.text = bot.PUSH_TARGET_CURRENT
        await bot.on_task_push_model_dispatch_target(dispatch_target_message, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_choice.state
        assert dispatch_target_message.calls
        target_prompt_text, _, target_prompt_markup, _ = dispatch_target_message.calls[0]
        assert target_prompt_text == bot._build_push_mode_prompt()
        assert target_prompt_markup is not None

        choice_message = DummyMessage()
        choice_message.text = bot.PUSH_MODE_PLAN
        await bot.on_task_push_model_choice(choice_message, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_send_mode.state
        assert choice_message.calls
        choice_text, _, choice_markup, _ = choice_message.calls[0]
        assert f"已选择 {bot.PUSH_MODE_PLAN} 模式" in choice_text
        assert bot._build_push_send_mode_prompt() in choice_text
        assert choice_markup is not None

        send_mode_message = DummyMessage()
        send_mode_message.text = bot.PUSH_SEND_MODE_IMMEDIATE_LABEL
        await bot.on_task_push_model_send_mode(send_mode_message, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_supplement.state
        assert send_mode_message.calls
        send_mode_text, _, send_mode_markup, _ = send_mode_message.calls[0]
        assert bot._build_push_supplement_prompt() in send_mode_text
        assert bot.PUSH_SEND_MODE_IMMEDIATE_LABEL in send_mode_text
        assert send_mode_markup is not None

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
        assert "任务描述：\n~~~\n需要调研的事项\n~~~" in payload
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
            send_mode=bot.PUSH_SEND_MODE_IMMEDIATE,
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

    async def fake_push(task_arg, *, chat_id, reply_to, supplement, actor, is_bug_report=False, push_mode=None, send_mode=None, dispatch_context=None):
        push_calls.append(
            {
                "task_id": task_arg.id,
                "chat_id": chat_id,
                "supplement": supplement,
                "actor": actor,
                "send_mode": send_mode,
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
    assert push_calls[0]["send_mode"] == bot.PUSH_SEND_MODE_IMMEDIATE


def test_push_model_skip_keeps_selected_push_mode(monkeypatch, tmp_path: Path):
    """点击“跳过补充”回调时，应透传已选的 PLAN/YOLO 模式与发送方式。"""

    message = DummyMessage()
    callback = DummyCallback("task:push_model_skip:TASK_0099", message)
    state, _storage = make_state(message)
    asyncio.run(
        state.update_data(
            task_id="TASK_0099",
            chat_id=message.chat.id,
            origin_message=message,
            actor="Tester#1",
            push_mode=bot.PUSH_MODE_PLAN,
            send_mode=bot.PUSH_SEND_MODE_QUEUED,
        )
    )

    task = _make_task(
        task_id="TASK_0099",
        title="调研任务",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0099"
        return task

    recorded_modes: list[str | None] = []
    recorded_send_modes: list[str | None] = []

    async def fake_push_task(
        task_arg,
        *,
        chat_id,
        reply_to,
        supplement,
        actor,
        is_bug_report=False,
        push_mode=None,
        send_mode=None,
        dispatch_context=None,
    ):
        recorded_modes.append(push_mode)
        recorded_send_modes.append(send_mode)
        return True, "PROMPT", tmp_path / "session.jsonl"

    async def fake_preview(*_args, **_kwargs):
        return None

    async def fake_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    asyncio.run(bot.on_task_push_model_skip(callback, state))

    assert recorded_modes == [bot.PUSH_MODE_PLAN]
    assert recorded_send_modes == [bot.PUSH_SEND_MODE_QUEUED]


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
            send_mode=bot.PUSH_SEND_MODE_IMMEDIATE,
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

    async def fake_push(task_arg, *, chat_id, reply_to, supplement, actor, is_bug_report=False, push_mode=None, send_mode=None, dispatch_context=None):
        push_calls.append(
            {
                "task_id": task_arg.id,
                "chat_id": chat_id,
                "supplement": supplement,
                "actor": actor,
                "send_mode": send_mode,
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
    assert push_calls[0]["send_mode"] == bot.PUSH_SEND_MODE_IMMEDIATE


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
            send_mode=bot.PUSH_SEND_MODE_IMMEDIATE,
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

    async def fake_push(task_arg, *, chat_id, reply_to, supplement, actor, is_bug_report=False, push_mode=None, send_mode=None, dispatch_context=None):
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

    async def fake_list_project_live_sessions():
        return [bot.SessionLiveEntry(key="main", label="💻 主会话（vibe）", tmux_session="vibe", kind="main")]

    monkeypatch.setattr(bot, "_list_project_live_sessions", fake_list_project_live_sessions)

    async def fake_list_history(task_id: str):
        return []

    monkeypatch.setattr(bot.TASK_SERVICE, "list_history", fake_list_history)

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, **_kwargs):
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

    async def fake_push(task_arg, *, chat_id, reply_to, supplement, actor, is_bug_report=False, push_mode=None, send_mode=None, dispatch_context=None):
        long_prompt = "A" * (bot.TELEGRAM_MESSAGE_LIMIT + 100)
        return True, long_prompt, tmp_path / "session.jsonl"

    monkeypatch.setattr(bot, "_push_task_to_model", fake_push)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        choice_message = DummyMessage()
        choice_message.text = bot.PUSH_MODE_YOLO
        await bot.on_task_push_model_choice(choice_message, state)
        send_mode_message = DummyMessage()
        send_mode_message.text = bot.PUSH_SEND_MODE_IMMEDIATE_LABEL
        await bot.on_task_push_model_send_mode(send_mode_message, state)
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
        **_kwargs,
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
        assert await state.get_state() == bot.TaskPushStates.waiting_dispatch_target.state
        assert callback.answers and "请选择处理方式" in (callback.answers[0][0] or "")
        assert message.calls
        prompt_text, _, prompt_markup, _ = message.calls[0]
        assert prompt_text == bot._build_push_dispatch_target_prompt()
        assert prompt_markup is not None

        dispatch_target_message = DummyMessage()
        dispatch_target_message.text = bot.PUSH_TARGET_CURRENT
        await bot.on_task_push_model_dispatch_target(dispatch_target_message, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_choice.state
        assert dispatch_target_message.calls
        prompt_text, _, prompt_markup, _ = dispatch_target_message.calls[0]
        assert prompt_text == bot._build_push_mode_prompt()
        assert prompt_markup is not None

        choice_message = DummyMessage()
        choice_message.text = bot.PUSH_MODE_YOLO
        await bot.on_task_push_model_choice(choice_message, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_send_mode.state

        send_mode_message = DummyMessage()
        send_mode_message.text = bot.PUSH_SEND_MODE_IMMEDIATE_LABEL
        await bot.on_task_push_model_send_mode(send_mode_message, state)
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
        assert "补充任务描述：\n~~~\n补充说明内容\n~~~" in payload
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


def test_push_model_choice_for_non_codex_skips_send_mode(monkeypatch):
    """非 Codex 模型选择 PLAN/YOLO 后，应直接进入补充阶段。"""

    message = DummyMessage()
    state, _storage = make_state(message)
    asyncio.run(
        state.update_data(
            task_id="TASK_0001",
            chat_id=message.chat.id,
        )
    )
    asyncio.run(state.set_state(bot.TaskPushStates.waiting_choice))

    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "claudecode")

    choice_message = DummyMessage()
    choice_message.text = bot.PUSH_MODE_PLAN

    asyncio.run(bot.on_task_push_model_choice(choice_message, state))

    assert asyncio.run(state.get_state()) == bot.TaskPushStates.waiting_supplement.state
    assert choice_message.calls
    choice_text, _, _, _ = choice_message.calls[0]
    assert bot._build_push_supplement_prompt() in choice_text


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

    async def fake_list_project_live_sessions():
        return [bot.SessionLiveEntry(key="main", label="💻 主会话（vibe）", tmux_session="vibe", kind="main")]

    monkeypatch.setattr(bot, "_list_project_live_sessions", fake_list_project_live_sessions)

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
        **_kwargs,
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
        dispatch_target_message = DummyMessage()
        dispatch_target_message.text = bot.PUSH_TARGET_CURRENT
        await bot.on_task_push_model_dispatch_target(dispatch_target_message, state)
        choice_message = DummyMessage()
        choice_message.text = bot.PUSH_MODE_YOLO
        await bot.on_task_push_model_choice(choice_message, state)
        send_mode_message = DummyMessage()
        send_mode_message.text = bot.PUSH_SEND_MODE_IMMEDIATE_LABEL
        await bot.on_task_push_model_send_mode(send_mode_message, state)
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

    async def fake_list_project_live_sessions():
        return [bot.SessionLiveEntry(key="main", label="💻 主会话（vibe）", tmux_session="vibe", kind="main")]

    monkeypatch.setattr(bot, "_list_project_live_sessions", fake_list_project_live_sessions)

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
        **_kwargs,
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
        assert not recorded
        dispatch_target_message = DummyMessage()
        dispatch_target_message.text = bot.PUSH_TARGET_CURRENT
        await bot.on_task_push_model_dispatch_target(dispatch_target_message, state)
        assert recorded, "完成阶段应在选择当前 CLI 后发送 /compact"
        _, payload, reply_to = recorded[0]
        assert reply_to is message
        assert payload.endswith("/compact")
        assert callback.answers and callback.answers[0][0] == "请选择处理方式"
        assert message.calls
        preview_text, preview_mode, _, _ = message.calls[-1]
        expected_block, expected_mode = bot._wrap_text_in_code_block(payload)
        assert preview_text == f"已推送到模型：\n{expected_block}"
        assert preview_mode == expected_mode
        assert ack_calls and ack_calls[0][2] is message
        assert await state.get_state() is None
        assert not logged_events

    asyncio.run(_scenario())


def test_push_model_selected_existing_parallel_session_routes_to_parallel_context(monkeypatch, tmp_path: Path):
    """现有 CLI 会话处理：选择并行会话后，应继续发到所选并行会话。"""

    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0001", message)
    state, _storage = make_state(message)

    task = _make_task(
        task_id="TASK_0001",
        title="调研任务",
        status="research",
        task_type="requirement",
    )

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0001"
        return task

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

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    pointer_file = tmp_path / "pointer.txt"
    pointer_file.write_text("", encoding="utf-8")

    async def fake_get_active_parallel_session_for_task(task_id: str):
        assert task_id == "TASK_0115"
        return SimpleNamespace(
            task_id="TASK_0115",
            title_snapshot="并行会话",
            tmux_session="vibe-par-demo-115",
            pointer_file=str(pointer_file),
            workspace_root=str(workspace_root),
        )

    push_calls: list[dict] = []
    preview_calls: list[dict] = []
    ack_calls: list[tuple[int, Path | None, object]] = []

    async def fake_push_task(
        task_arg,
        *,
        chat_id,
        reply_to,
        supplement,
        actor,
        is_bug_report=False,
        push_mode=None,
        send_mode=None,
        dispatch_context=None,
    ):
        push_calls.append(
            {
                "task_id": task_arg.id,
                "chat_id": chat_id,
                "reply_to": reply_to,
                "supplement": supplement,
                "actor": actor,
                "push_mode": push_mode,
                "send_mode": send_mode,
                "dispatch_context": dispatch_context,
            }
        )
        return True, "PROMPT", tmp_path / "session.jsonl"

    async def fake_preview(chat_id, preview_block, *, reply_to, parse_mode=None, reply_markup=None):
        preview_calls.append(
            {
                "chat_id": chat_id,
                "preview_block": preview_block,
                "reply_to": reply_to,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return None

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append((chat_id, session_path, reply_to))

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_list_project_live_sessions", fake_list_project_live_sessions)
    monkeypatch.setattr(bot, "_get_active_parallel_session_for_task", fake_get_active_parallel_session_for_task)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "claudecode")

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)

        dispatch_target_message = DummyMessage()
        dispatch_target_message.text = bot.PUSH_TARGET_CURRENT
        await bot.on_task_push_model_dispatch_target(dispatch_target_message, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_existing_session.state

        picker_message = DummyMessage()
        picker_callback = DummyCallback(
            f"{bot.PUSH_EXISTING_SESSION_PARALLEL_PREFIX}TASK_0115",
            picker_message,
        )
        await bot.on_push_existing_session_parallel_callback(picker_callback, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_choice.state
        assert picker_callback.answers[-1] == ("已选择并行会话", False)

        choice_message = DummyMessage()
        choice_message.text = bot.PUSH_MODE_YOLO
        await bot.on_task_push_model_choice(choice_message, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_supplement.state

        supplement_message = DummyMessage()
        supplement_message.text = bot.SKIP_TEXT
        await bot.on_task_push_model_supplement(supplement_message, state)
        assert await state.get_state() is None

    asyncio.run(_scenario())

    assert push_calls, "应将任务推送到选中的并行会话"
    dispatch_context = push_calls[0]["dispatch_context"]
    assert dispatch_context is not None
    assert dispatch_context.task_id == "TASK_0115"
    assert dispatch_context.tmux_session == "vibe-par-demo-115"
    assert push_calls[0]["reply_to"] is message
    assert push_calls[0]["push_mode"] == bot.PUSH_MODE_YOLO
    assert preview_calls and preview_calls[0]["reply_to"] is message
    assert ack_calls and ack_calls[0][2] is message


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


def test_on_media_message_album_with_small_gap_dispatches_once(monkeypatch, tmp_path: Path):
    """普通聊天相册第二张稍晚到达时，仍应聚合为一次 prompt。"""

    bot.MEDIA_GROUP_STATE.clear()

    message1 = DummyMessage()
    message1.media_group_id = "chat_album_gap"
    message1.caption = "图文说明"
    message1.message_id = 200
    message1.bot = SimpleNamespace(username="tester_bot")
    message1.date = datetime.now(bot.UTC)

    message2 = DummyMessage()
    message2.media_group_id = "chat_album_gap"
    message2.message_id = 201
    message2.bot = message1.bot
    message2.date = message1.date

    async def fake_collect(msg, _target_dir):
        if msg is message1:
            return [
                bot.TelegramSavedAttachment(
                    kind="photo",
                    display_name="a1.jpg",
                    mime_type="image/jpeg",
                    absolute_path=tmp_path / "a1.jpg",
                    relative_path="./data/a1.jpg",
                )
            ]
        if msg is message2:
            return [
                bot.TelegramSavedAttachment(
                    kind="photo",
                    display_name="a2.jpg",
                    mime_type="image/jpeg",
                    absolute_path=tmp_path / "a2.jpg",
                    relative_path="./data/a2.jpg",
                )
            ]
        return []

    dispatched_prompts: list[str] = []

    async def fake_handle(_message, prompt: str) -> None:
        dispatched_prompts.append(prompt)

    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_handle)

    async def scenario() -> None:
        await bot.on_media_message(message1)
        await asyncio.sleep(0.85)
        await bot.on_media_message(message2)
        await asyncio.sleep(bot.MEDIA_GROUP_AGGREGATION_DELAY + 0.2)

    asyncio.run(scenario())

    assert len(dispatched_prompts) == 1
    assert "图文说明" in dispatched_prompts[0]
    assert "a1.jpg" in dispatched_prompts[0]
    assert "a2.jpg" in dispatched_prompts[0]
    assert not bot.MEDIA_GROUP_STATE


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

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool, **_kwargs):
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

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool, **_kwargs):
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
    message.reply_markup = bot._build_model_quick_reply_keyboard(task_id="TASK_0600")

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

    async def fake_paginate(*args, **kwargs):
        return [], 1

    async def fake_count_tasks(*args, **kwargs):
        return 0

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "paginate", fake_paginate)
    monkeypatch.setattr(bot.TASK_SERVICE, "count_tasks", fake_count_tasks)

    async def scenario() -> None:
        await bot.on_model_task_to_test(callback)

    asyncio.run(scenario())
    assert updates, "应更新任务状态为测试"
    assert callback.answers[-1] == ("已切换到测试", False)
    assert len(message.calls) >= 2, "应发送状态更新提示与任务列表消息"

    status_text, _, status_markup, _ = message.calls[0]
    assert "状态已更新为“测试”" in status_text
    assert isinstance(status_markup, ReplyKeyboardMarkup)

    list_text, _, list_markup, _ = message.calls[1]
    assert "任务列表" in list_text
    assert isinstance(list_markup, InlineKeyboardMarkup)
    assert not message.edits, "不应编辑原消息内容"
    assert message.reply_markup_edits, "成功更新后应移除已点击的测试按钮"
    updated_markup, _kwargs = message.reply_markup_edits[-1]
    callback_data = [
        button.callback_data
        for row in updated_markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]
    assert f"{bot.MODEL_TASK_TO_TEST_PREFIX}TASK_0600" not in callback_data
    bot.TASK_VIEW_STACK.clear()


def test_model_task_to_test_callback_still_shows_task_list_when_already_test(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("model:task_to_test:TASK_0601", message)
    message.reply_markup = bot._build_model_quick_reply_keyboard(task_id="TASK_0601")

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

    async def fake_paginate(*args, **kwargs):
        return [], 1

    async def fake_count_tasks(*args, **kwargs):
        return 0

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "paginate", fake_paginate)
    monkeypatch.setattr(bot.TASK_SERVICE, "count_tasks", fake_count_tasks)

    async def scenario() -> None:
        await bot.on_model_task_to_test(callback)

    asyncio.run(scenario())
    assert callback.answers[-1] == ("任务已处于“测试”状态", False)
    assert len(message.calls) >= 1
    list_text, _, list_markup, _ = message.calls[0]
    assert "任务列表" in list_text
    assert isinstance(list_markup, InlineKeyboardMarkup)
    assert message.reply_markup_edits, "已是测试状态且流程成功时也应移除测试按钮"
    updated_markup, _kwargs = message.reply_markup_edits[-1]
    callback_data = [
        button.callback_data
        for row in updated_markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]
    assert f"{bot.MODEL_TASK_TO_TEST_PREFIX}TASK_0601" not in callback_data
    bot.TASK_VIEW_STACK.clear()


def test_model_task_to_test_callback_handles_missing_task(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("model:task_to_test:TASK_0602", message)
    message.reply_markup = bot._build_model_quick_reply_keyboard(task_id="TASK_0602")

    async def fake_get_task(task_id: str):
        return None

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def scenario() -> None:
        await bot.on_model_task_to_test(callback)

    asyncio.run(scenario())
    assert callback.answers[-1] == ("任务不存在", True)
    assert not message.reply_markup_edits, "失败时不应移除按钮"


def test_model_task_to_test_callback_rejects_invalid_task_id():
    message = DummyMessage()
    callback = DummyCallback("model:task_to_test:BAD_TASK_ID", message)
    message.reply_markup = bot._build_model_quick_reply_keyboard(task_id="TASK_0603")

    async def scenario() -> None:
        await bot.on_model_task_to_test(callback)

    asyncio.run(scenario())
    assert callback.answers[-1] == ("任务 ID 无效", True)
    assert not message.reply_markup_edits, "参数无效时不应移除按钮"


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


def test_dispatch_prompt_strict_fallback_locates_latest_session_when_pointer_empty(monkeypatch, tmp_path: Path):
    """strict 绑定模式下，当 pointer 长时间为空时，应兜底扫描会话目录定位最新 session。"""

    pointer = tmp_path / "current_session.txt"
    pointer.write_text("", encoding="utf-8")

    target_cwd = str(tmp_path / "workdir")
    monkeypatch.setenv("MODEL_WORKDIR", target_cwd)

    session_file = tmp_path / "rollout-2026-02-05T00-00-00-test.jsonl"
    session_file.write_text(json.dumps({"payload": {"cwd": target_cwd}}) + "\n", encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "MODEL_SESSION_ROOT", str(tmp_path))
    monkeypatch.setattr(bot, "CODEX_SESSIONS_ROOT", "")
    monkeypatch.setattr(bot, "MODEL_SESSION_GLOB", "rollout-*.jsonl")
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

    def fake_tmux_send_line(_session: str, _prompt: str) -> None:
        return

    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)

    async def fake_await_session_path(*_args, **_kwargs) -> Optional[Path]:
        return None

    monkeypatch.setattr(bot, "_await_session_path", fake_await_session_path)

    async def fake_interrupt(_chat_id: int) -> None:
        return

    monkeypatch.setattr(bot, "_interrupt_long_poll", fake_interrupt)

    acked: list[Path] = []

    async def fake_send_ack(_chat_id: int, path: Path, *, reply_to) -> None:
        acked.append(path)

    monkeypatch.setattr(bot, "_send_session_ack", fake_send_ack)

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
        ok, path = await bot._dispatch_prompt_to_model(900, "pwd", reply_to=None, ack_immediately=True)
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert pointer.read_text(encoding="utf-8").strip() == str(session_file)
    assert acked == [session_file]

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


def test_dispatch_prompt_strict_fallback_errors_with_details_when_no_session_found(monkeypatch, tmp_path: Path):
    """strict 绑定模式下，当 pointer 为空且扫描也找不到 session 时，应返回带细节的错误提示。"""

    pointer = tmp_path / "current_session.txt"
    pointer.write_text("", encoding="utf-8")

    target_cwd = str(tmp_path / "workdir")
    monkeypatch.setenv("MODEL_WORKDIR", target_cwd)

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "MODEL_SESSION_ROOT", str(tmp_path))
    monkeypatch.setattr(bot, "CODEX_SESSIONS_ROOT", "")
    monkeypatch.setattr(bot, "MODEL_SESSION_GLOB", "rollout-*.jsonl")
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

    def fake_tmux_send_line(_session: str, _prompt: str) -> None:
        return

    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)

    async def fake_await_session_path(*_args, **_kwargs) -> Optional[Path]:
        return None

    monkeypatch.setattr(bot, "_await_session_path", fake_await_session_path)

    async def fake_interrupt(_chat_id: int) -> None:
        return

    monkeypatch.setattr(bot, "_interrupt_long_poll", fake_interrupt)

    replies: list[str] = []

    async def fake_reply(_chat_id: int, text: str, **_kwargs):
        replies.append(text)
        return None

    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)

    async def scenario() -> None:
        ok, path = await bot._dispatch_prompt_to_model(901, "pwd", reply_to=None, ack_immediately=True)
        assert not ok
        assert path is None

    asyncio.run(scenario())

    assert replies, "应返回错误提示"
    assert "pointer=" in replies[-1]
    assert str(pointer) in replies[-1]
    assert "cwd=" in replies[-1]
    assert target_cwd in replies[-1]

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
    "plan_prompt",
    [
        bot.PLAN_IMPLEMENT_PROMPT,
        bot.PLAN_IMPLEMENT_EXEC_PROMPT,
        bot.PLAN_RECOVERY_DEVELOP_PROMPT,
    ],
)
def test_dispatch_prompt_skips_enforced_agents_notice_for_plan_implement_prompt(
    monkeypatch, tmp_path: Path, plan_prompt: str
):
    """Plan 收口确认与恢复提示必须原样透传，不能注入强制提示语。"""

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
        ok, path = await bot._dispatch_prompt_to_model(
            779, plan_prompt, reply_to=None, ack_immediately=False
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert sent.get("line") == plan_prompt

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


def test_dispatch_prompt_plan_mode_sends_plan_switch_for_codex(monkeypatch, tmp_path: Path):
    """Codex + PLAN 模式：应先发 /plan，再发正文。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_BIND_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(bot, "SESSION_BIND_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")

    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()

    sent_lines: list[str] = []

    def fake_tmux_send_line(_session: str, line: str) -> None:
        sent_lines.append(line)

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
        ok, path = await bot._dispatch_prompt_to_model(
            701,
            "pwd",
            reply_to=None,
            ack_immediately=False,
            intended_mode=bot.PUSH_MODE_PLAN,
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert sent_lines[0] == bot.PLAN_MODE_SWITCH_COMMAND
    assert sent_lines[1] == f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_dispatch_prompt_plan_mode_queued_skips_plan_switch_for_codex(monkeypatch, tmp_path: Path):
    """Codex + 排队发送：不应先发 /plan，而应直接用 Tab 排队正文。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_BIND_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(bot, "SESSION_BIND_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")

    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()

    sent_lines: list[str] = []
    queued_lines: list[str] = []

    monkeypatch.setattr(bot, "tmux_send_line", lambda _session, line: sent_lines.append(line))
    monkeypatch.setattr(bot, "tmux_queue_line", lambda _session, line: queued_lines.append(line))

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
        ok, path = await bot._dispatch_prompt_to_model(
            1701,
            "pwd",
            reply_to=None,
            ack_immediately=False,
            intended_mode=bot.PUSH_MODE_PLAN,
            send_mode=bot.PUSH_SEND_MODE_QUEUED,
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert sent_lines == []
    assert queued_lines == [f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_push_send_mode_prompt_uses_generic_queue_label():
    """排队发送文案不应再显式绑定 Codex 名称。"""

    assert bot.PUSH_SEND_MODE_QUEUED_LABEL == "排队发送"
    assert "Codex" not in bot._build_push_send_mode_prompt()


def test_dispatch_prompt_yolo_mode_skips_plan_switch(monkeypatch, tmp_path: Path):
    """YOLO 模式：不应发送 /plan。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_BIND_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(bot, "SESSION_BIND_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")

    sent_lines: list[str] = []

    def fake_tmux_send_line(_session: str, line: str) -> None:
        sent_lines.append(line)

    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

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
        ok, path = await bot._dispatch_prompt_to_model(
            702,
            "pwd",
            reply_to=None,
            ack_immediately=False,
            intended_mode=bot.PUSH_MODE_YOLO,
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert sent_lines == [f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_dispatch_prompt_plan_mode_skips_switch_for_non_codex(monkeypatch, tmp_path: Path):
    """非 Codex 模型即使 PLAN，也不发送 /plan。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "claudecode")

    sent_lines: list[str] = []

    def fake_tmux_send_line(_session: str, line: str) -> None:
        sent_lines.append(line)

    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

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
        ok, path = await bot._dispatch_prompt_to_model(
            703,
            "pwd",
            reply_to=None,
            ack_immediately=False,
            intended_mode=bot.PUSH_MODE_PLAN,
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert sent_lines == [f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_dispatch_prompt_plan_mode_waits_for_parallel_tmux_ready(monkeypatch, tmp_path: Path):
    """并行 CLI 首次推送 PLAN 时，应先等待新 tmux 会话 ready，再发送 /plan。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")
    monkeypatch.setattr(bot, "PLAN_MODE_SWITCH_DELAY_SECONDS", 0.0)

    wait_calls: list[str] = []

    async def fake_wait(tmux_session: str | None) -> bool:
        wait_calls.append(tmux_session or "")
        return True

    sent_lines: list[str] = []

    def fake_tmux_send_line(_session: str, line: str) -> None:
        sent_lines.append(line)

    monkeypatch.setattr(bot, "_wait_tmux_session_ready_for_plan_switch", fake_wait)
    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

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

    dispatch_context = bot.ParallelDispatchContext(
        task_id="TASK_0115",
        tmux_session="vibe-par-hyphamall-task_0115",
        pointer_file=pointer,
        workspace_root=tmp_path / "workspace",
    )

    async def scenario() -> None:
        ok, path = await bot._dispatch_prompt_to_model(
            704,
            "pwd",
            reply_to=None,
            ack_immediately=False,
            intended_mode=bot.PUSH_MODE_PLAN,
            dispatch_context=dispatch_context,
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert wait_calls == [dispatch_context.tmux_session]
    assert sent_lines[0] == bot.PLAN_MODE_SWITCH_COMMAND
    assert sent_lines[1] == f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_dispatch_prompt_parallel_first_dispatch_fails_closed_when_tmux_still_shell(monkeypatch, tmp_path: Path):
    """并行首次派发时，若 pane 当前仍是 shell，则必须 fail-closed，且不能把正文打进 shell。"""

    pointer = tmp_path / "pointer.txt"
    pointer.write_text("", encoding="utf-8")
    old_session = tmp_path / "old-rollout.jsonl"
    old_session.write_text("", encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")
    monkeypatch.setattr(bot, "TMUX_SESSION", "vibe-primary")
    monkeypatch.setattr(bot, "PLAN_MODE_SWITCH_DELAY_SECONDS", 0.0)
    bot.PARALLEL_TASK_WATCHERS.clear()
    bot.PARALLEL_TASK_SESSION_MAP.clear()
    bot.PARALLEL_SESSION_CONTEXTS.clear()

    monkeypatch.setattr(bot, "_get_tmux_pane_current_command", lambda _session: "zsh", raising=False)

    async def fake_wait(tmux_session: str | None) -> bool:
        return True

    sent_lines: list[tuple[str, str]] = []

    def fake_tmux_send_line(session: str, line: str) -> None:
        sent_lines.append((session, line))

    fallback_calls: list[tuple[Path, Path | None]] = []

    def fake_fallback(pointer_path: Path, target_cwd: Path | None):
        fallback_calls.append((pointer_path, target_cwd))
        return old_session

    notices: list[str] = []

    async def fake_reply_to_chat(chat_id: int, text: str, *, reply_to=None, disable_notification: bool = False, parse_mode=None, reply_markup=None):
        notices.append(text)
        return None

    monkeypatch.setattr(bot, "_wait_tmux_session_ready_for_plan_switch", fake_wait)
    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)
    monkeypatch.setattr(bot, "_fallback_locate_latest_session", fake_fallback)
    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply_to_chat)
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

    dispatch_context = bot.ParallelDispatchContext(
        task_id="TASK_0115",
        tmux_session="vibe-par-demo",
        pointer_file=pointer,
        workspace_root=tmp_path / "workspace",
    )

    async def scenario() -> None:
        ok, path = await bot._dispatch_prompt_to_model(
            704,
            "pwd",
            reply_to=None,
            ack_immediately=False,
            intended_mode=bot.PUSH_MODE_PLAN,
            dispatch_context=dispatch_context,
        )
        assert ok is False
        assert path is None

    asyncio.run(scenario())

    assert sent_lines == []
    assert fallback_calls == []
    assert notices and "并行 CLI 未启动成功" in notices[-1]


def test_dispatch_prompt_parallel_first_dispatch_does_not_fallback_to_old_session(monkeypatch, tmp_path: Path):
    """并行首次派发若未绑定 fresh session，必须失败，不能 strict fallback 到旧 rollout。"""

    pointer = tmp_path / "pointer.txt"
    pointer.write_text("", encoding="utf-8")
    old_session = tmp_path / "old-rollout.jsonl"
    old_session.write_text("", encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_BIND_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(bot, "SESSION_BIND_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")
    monkeypatch.setattr(bot, "TMUX_SESSION", "vibe-primary")
    monkeypatch.setattr(bot, "PLAN_MODE_SWITCH_DELAY_SECONDS", 0.0)
    bot.PARALLEL_TASK_WATCHERS.clear()
    bot.PARALLEL_TASK_SESSION_MAP.clear()
    bot.PARALLEL_SESSION_CONTEXTS.clear()

    monkeypatch.setattr(bot, "_get_tmux_pane_current_command", lambda _session: "codex-aarch64-a", raising=False)

    async def fake_wait(tmux_session: str | None) -> bool:
        return True

    sent_lines: list[tuple[str, str]] = []

    def fake_tmux_send_line(session: str, line: str) -> None:
        sent_lines.append((session, line))

    fallback_calls: list[tuple[Path, Path | None]] = []

    def fake_fallback(pointer_path: Path, target_cwd: Path | None):
        fallback_calls.append((pointer_path, target_cwd))
        return old_session

    notices: list[str] = []

    async def fake_reply_to_chat(chat_id: int, text: str, *, reply_to=None, disable_notification: bool = False, parse_mode=None, reply_markup=None):
        notices.append(text)
        return None

    monkeypatch.setattr(bot, "_wait_tmux_session_ready_for_plan_switch", fake_wait)
    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)
    monkeypatch.setattr(bot, "_fallback_locate_latest_session", fake_fallback)
    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply_to_chat)
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

    dispatch_context = bot.ParallelDispatchContext(
        task_id="TASK_0115",
        tmux_session="vibe-par-demo",
        pointer_file=pointer,
        workspace_root=tmp_path / "workspace",
    )

    async def scenario() -> None:
        ok, path = await bot._dispatch_prompt_to_model(
            705,
            "pwd",
            reply_to=None,
            ack_immediately=False,
            intended_mode=bot.PUSH_MODE_PLAN,
            dispatch_context=dispatch_context,
        )
        assert ok is False
        assert path is None

    asyncio.run(scenario())

    assert sent_lines, "fresh session 未绑定前，当前实现仍会先尝试发入模正文"
    assert fallback_calls == []
    assert notices and "未生成新的会话日志" in notices[-1]


def test_dispatch_prompt_force_exit_plan_ui_sends_key_sequence_before_prompt(monkeypatch, tmp_path: Path):
    """Implement 链路启用 force_exit_plan_ui 时，应先发送 Escape+BTab 序列再发送提示词。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_RETRY_GAP_SECONDS", 0.0)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_MAX_ROUNDS", 1)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_ESC_FIRST", True)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_RETRY_KEYS", ("BTab", "BTab"))

    operations: list[tuple[str, str]] = []

    def fake_tmux_send_key(_session: str, key: str) -> None:
        operations.append(("key", key))

    def fake_tmux_send_line(_session: str, line: str) -> None:
        operations.append(("line", line))

    monkeypatch.setattr(bot, "tmux_send_key", fake_tmux_send_key)
    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)
    monkeypatch.setattr(bot, "_probe_terminal_collaboration_mode", lambda _tmux_session=None: "plan")
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

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
        ok, path = await bot._dispatch_prompt_to_model(
            704,
            bot.PLAN_IMPLEMENT_EXEC_PROMPT,
            reply_to=None,
            ack_immediately=False,
            force_exit_plan_ui=True,
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert operations == [
        ("key", "Escape"),
        ("key", "BTab"),
        ("key", "BTab"),
        ("line", bot.PLAN_IMPLEMENT_EXEC_PROMPT),
    ]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_dispatch_prompt_force_exit_plan_ui_skips_btab_when_not_plan(monkeypatch, tmp_path: Path):
    """终端已非 Plan 模式时，force_exit_plan_ui 不应重复发送 BTab。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")
    monkeypatch.setattr(bot, "_probe_terminal_collaboration_mode", lambda _tmux_session=None: "non_plan")
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

    sent_keys: list[str] = []
    sent_lines: list[str] = []

    def fake_tmux_send_key(_session: str, key: str) -> None:
        sent_keys.append(key)

    def fake_tmux_send_line(_session: str, line: str) -> None:
        sent_lines.append(line)

    monkeypatch.setattr(bot, "tmux_send_key", fake_tmux_send_key)
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
        ok, path = await bot._dispatch_prompt_to_model(
            705,
            bot.PLAN_IMPLEMENT_EXEC_PROMPT,
            reply_to=None,
            ack_immediately=False,
            force_exit_plan_ui=True,
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert sent_keys == []
    assert sent_lines == [bot.PLAN_IMPLEMENT_EXEC_PROMPT]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_dispatch_prompt_force_exit_plan_ui_uses_parallel_tmux_session(monkeypatch, tmp_path: Path):
    """并行会话触发 Implement 时，退出 Plan 的探测与按键必须命中并行 tmux。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")
    monkeypatch.setattr(bot, "TMUX_SESSION", "vibe-primary")
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_RETRY_GAP_SECONDS", 0.0)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_MAX_ROUNDS", 1)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_ESC_FIRST", True)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_RETRY_KEYS", ("BTab",))
    bot.PARALLEL_TASK_WATCHERS.clear()
    bot.PARALLEL_TASK_SESSION_MAP.clear()
    bot.PARALLEL_SESSION_CONTEXTS.clear()

    operations: list[tuple[str, str, str]] = []
    probe_calls: list[str | None] = []

    async def fake_probe(tmux_session: str | None = None):
        probe_calls.append(tmux_session)
        return "plan" if len(probe_calls) == 1 else "non_plan"

    def fake_tmux_send_key(session: str, key: str) -> None:
        operations.append(("key", session, key))

    def fake_tmux_send_line(session: str, line: str) -> None:
        operations.append(("line", session, line))

    monkeypatch.setattr(bot, "_probe_plan_execution_terminal_mode", fake_probe)
    monkeypatch.setattr(bot, "tmux_send_key", fake_tmux_send_key)
    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)
    async def fake_send_session_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_send_session_ack", fake_send_session_ack)
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

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

    dispatch_context = bot.ParallelDispatchContext(
        task_id="TASK_0115",
        tmux_session="vibe-par-demo",
        pointer_file=pointer,
        workspace_root=tmp_path / "workspace",
    )

    async def scenario() -> None:
        ok, path = await bot._dispatch_prompt_to_model(
            706,
            bot.PLAN_IMPLEMENT_PROMPT,
            reply_to=None,
            ack_immediately=False,
            force_exit_plan_ui=True,
            dispatch_context=dispatch_context,
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert probe_calls == [dispatch_context.tmux_session, dispatch_context.tmux_session]
    assert operations == [
        ("key", dispatch_context.tmux_session, "Escape"),
        ("key", dispatch_context.tmux_session, "BTab"),
        ("line", dispatch_context.tmux_session, bot.PLAN_IMPLEMENT_PROMPT),
    ]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_handle_prompt_dispatch_uses_manual_mode_control(monkeypatch):
    """直接 Telegram 文本消息：不再自动按 PLAN 推送。"""

    message = DummyMessage()
    message.text = "hello"
    monkeypatch.setattr(bot, "ENV_ISSUES", [])
    monkeypatch.setattr(bot, "MODE", "B")
    monkeypatch.setattr(bot, "ENABLE_AUTO_PLAN_FOR_DIRECT_MESSAGE", True)

    class DummyAiogram:
        async def send_chat_action(self, chat_id: int, action: str):
            return None

    bot._bot = DummyAiogram()
    captured: list[Optional[str]] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, intended_mode: Optional[str] = None, **_kwargs):
        captured.append(intended_mode)
        return True, Path("/tmp/fake-session.jsonl")

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    asyncio.run(bot._handle_prompt_dispatch(message, "hello"))

    assert captured == [None]


@pytest.mark.parametrize(
    "raw_prompt,expected",
    [
        ("pwd", f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"),
        ("pwd\n", f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd\n"),
        ("\npwd", f"{bot.ENFORCED_AGENTS_NOTICE}\n\n\npwd"),
        ("  pwd", f"{bot.ENFORCED_AGENTS_NOTICE}\n\n  pwd"),
        ("/compact", "/compact"),
        (" /compact", " /compact"),
        (bot.PLAN_IMPLEMENT_PROMPT, bot.PLAN_IMPLEMENT_PROMPT),
        (bot.PLAN_IMPLEMENT_EXEC_PROMPT, bot.PLAN_IMPLEMENT_EXEC_PROMPT),
        (bot.PLAN_RECOVERY_DEVELOP_PROMPT, bot.PLAN_RECOVERY_DEVELOP_PROMPT),
        ("", ""),
        ("\n", "\n"),
        (f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd", f"{bot.ENFORCED_AGENTS_NOTICE}\n\npwd"),
        (f"  {bot.ENFORCED_AGENTS_NOTICE}\nabc", f"  {bot.ENFORCED_AGENTS_NOTICE}\nabc"),
    ],
)
def test_prepend_enforced_agents_notice_cases(raw_prompt: str, expected: str):
    """验证强制规约提示语在多种输入下的拼接与跳过逻辑（覆盖 ≥10 条输入）。"""

    assert bot._prepend_enforced_agents_notice(raw_prompt) == expected


def test_dispatch_prompt_force_exit_plan_ui_retries_multiple_rounds(monkeypatch, tmp_path: Path):
    """终端持续处于 Plan 时，应按轮次重复发送退出按键序列。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_RETRY_GAP_SECONDS", 0.0)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_MAX_ROUNDS", 2)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_ESC_FIRST", True)
    monkeypatch.setattr(bot, "PLAN_EXECUTION_EXIT_PLAN_RETRY_KEYS", ("BTab", "BTab"))
    monkeypatch.setattr(bot, "_probe_terminal_collaboration_mode", lambda _tmux_session=None: "plan")
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

    sent_keys: list[str] = []
    sent_lines: list[str] = []

    def fake_tmux_send_key(_session: str, key: str) -> None:
        sent_keys.append(key)

    def fake_tmux_send_line(_session: str, line: str) -> None:
        sent_lines.append(line)

    monkeypatch.setattr(bot, "tmux_send_key", fake_tmux_send_key)
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
        ok, path = await bot._dispatch_prompt_to_model(
            706,
            bot.PLAN_IMPLEMENT_EXEC_PROMPT,
            reply_to=None,
            ack_immediately=False,
            force_exit_plan_ui=True,
        )
        assert ok
        assert path == session_file

    asyncio.run(scenario())

    assert sent_keys == ["Escape", "BTab", "BTab", "Escape", "BTab", "BTab"]
    assert sent_lines == [bot.PLAN_IMPLEMENT_EXEC_PROMPT]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_dispatch_prompt_to_model_does_not_drop_other_session_plan_confirm_for_same_chat(monkeypatch, tmp_path: Path):
    """同 chat 派发到另一条会话时，不应把无关 session 的 PlanConfirm 直接删掉。"""

    plan_session = bot.PlanConfirmSession(
        token="token_plan_keep",
        chat_id=499,
        session_key="session-plan-old",
        user_id=499,
        created_at=time.monotonic(),
    )
    bot.PLAN_CONFIRM_SESSIONS[plan_session.token] = plan_session
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[499] = plan_session.token

    pointer = tmp_path / "pointer.txt"
    new_session = tmp_path / "rollout-new.jsonl"
    new_session.write_text("", encoding="utf-8")
    pointer.write_text(str(new_session), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", pointer, raising=False)
    monkeypatch.setattr(bot, "_reply_to_chat", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(bot, "_deliver_pending_messages", lambda *args, **kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(bot, "_await_session_path", lambda *args, **kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(bot, "tmux_send_line", lambda *args, **kwargs: None)

    created_tasks: list = []

    class DummyTask:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

    def fake_create_task(coro):
        created_tasks.append(coro)
        return DummyTask()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    async def _scenario() -> None:
        ok, _session_path = await bot._dispatch_prompt_to_model(499, "继续处理", reply_to=None, ack_immediately=True)
        assert ok is True

    asyncio.run(_scenario())

    assert plan_session.token in bot.PLAN_CONFIRM_SESSIONS
    assert bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[499] == plan_session.token

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


@pytest.mark.parametrize(
    "raw_output,expected",
    [
        ("Plan mode", "plan"),
        ("Plan mode (shift+tab to cycle)", "plan"),
        ("DEFAULT mode", "default"),
        ("Default mode (shift+tab to cycle)", "default"),
        ("\x1b[32mPLAN mode (shift+tab to cycle)\x1b[0m", "plan"),
        ("no mode marker", None),
    ],
)
def test_extract_terminal_collaboration_mode(raw_output: str, expected: Optional[str]):
    """终端模式解析：应兼容大小写与 ANSI。"""

    assert bot._extract_terminal_collaboration_mode(raw_output) == expected


@pytest.mark.parametrize(
    "status,description,expected_checks",
    [
        (
            "research",
            "描述A",
            (
                ("startswith", f"{bot.VIBE_PHASE_PROMPT}\n任务标题：案例任务"),
                ("contains", "任务描述：\n~~~\n描述A\n~~~"),
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
                ("contains", "任务描述：\n~~~\n测试说明\n~~~"),
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
    assert "任务描述：\n~~~\n原始描述\n~~~" in payload
    assert "任务编码：/TASK_CHECK_SUP" in payload
    assert "\\_" not in payload
    assert "任务备注：" not in payload
    assert "补充任务描述：\n~~~\n补充内容\n~~~" in payload
    assert "以下为任务执行记录，用于辅助回溯任务处理记录：" in payload
    assert "2025-01-01T10:00:00+08:00 | 推送到模型（结果=success）" in payload
    assert "补充任务描述：旧补充" in payload
    history_intro_index = payload.index("以下为任务执行记录，用于辅助回溯任务处理记录：")
    assert payload.index("补充任务描述：\n~~~\n补充内容\n~~~") < history_intro_index
    assert payload.endswith("补充任务描述：旧补充")
    assert "## 测试阶段" not in payload
    assert "测试阶段补充说明：" not in payload


def test_build_model_push_payload_defect_uses_reproduction_and_expected_result():
    """缺陷任务推送到模型时应展示复现步骤与期望结果。"""

    task = TaskRecord(
        id="TASK_DEFECT_PUSH",
        project_slug="demo",
        title="缺陷推送",
        status="research",
        priority=2,
        task_type="defect",
        tags=(),
        due_date=None,
        description="复现步骤：\n1. 点击按钮\n\n期望结果：\n应弹出成功提示",
        parent_id=None,
        root_id="TASK_DEFECT_PUSH",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    payload = bot._build_model_push_payload(task, supplement="补充内容")

    assert "复现步骤：\n~~~\n1. 点击按钮\n~~~" in payload
    assert "期望结果：\n~~~\n应弹出成功提示\n~~~" in payload
    assert "补充任务描述：\n~~~\n补充内容\n~~~" in payload
    assert "任务描述：\n~~~\n复现步骤：" not in payload


def test_build_task_context_block_for_model_defect_uses_reproduction_and_expected_result():
    """任务上下文块在缺陷任务下也应输出双字段结构。"""

    task = TaskRecord(
        id="TASK_DEFECT_CTX",
        project_slug="demo",
        title="缺陷上下文",
        status="test",
        priority=2,
        task_type="defect",
        tags=(),
        due_date=None,
        description="复现步骤：\n打开控制台\n\n期望结果：\n不应报错",
        parent_id=None,
        root_id="TASK_DEFECT_CTX",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    block = bot._build_task_context_block_for_model(
        task,
        supplement="补充说明",
        history="",
        attachments=(),
    )

    assert "复现步骤：\n~~~\n打开控制台\n~~~" in block
    assert "期望结果：\n~~~\n不应报错\n~~~" in block
    assert "补充任务描述：\n~~~\n补充说明\n~~~" in block
    assert "任务描述：\n~~~\n复现步骤：" not in block


def test_build_model_push_payload_task_uses_current_and_expected_effect():
    """优化任务推送到模型时应展示当前效果与期望效果。"""

    task = TaskRecord(
        id="TASK_TASK_PUSH",
        project_slug="demo",
        title="优化推送",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="当前效果：\n需要点击两次\n\n期望效果：\n点击一次即可提交",
        parent_id=None,
        root_id="TASK_TASK_PUSH",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    payload = bot._build_model_push_payload(task, supplement="补充内容")

    assert "当前效果：\n~~~\n需要点击两次\n~~~" in payload
    assert "期望效果：\n~~~\n点击一次即可提交\n~~~" in payload
    assert "补充任务描述：\n~~~\n补充内容\n~~~" in payload
    assert "任务描述：\n~~~\n当前效果：" not in payload


def test_build_task_context_block_for_model_task_uses_current_and_expected_effect():
    """优化任务上下文块也应输出当前效果与期望效果。"""

    task = TaskRecord(
        id="TASK_TASK_CTX",
        project_slug="demo",
        title="优化上下文",
        status="test",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="当前效果：\n需要点击两次\n\n期望效果：\n点击一次即可提交",
        parent_id=None,
        root_id="TASK_TASK_CTX",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )

    block = bot._build_task_context_block_for_model(
        task,
        supplement="补充说明",
        history="",
        attachments=(),
    )

    assert "当前效果：\n~~~\n需要点击两次\n~~~" in block
    assert "期望效果：\n~~~\n点击一次即可提交\n~~~" in block
    assert "补充任务描述：\n~~~\n补充说明\n~~~" in block
    assert "任务描述：\n~~~\n当前效果：" not in block


def test_push_task_to_model_converts_overlong_prompt_to_attachment(monkeypatch, tmp_path: Path):
    """推送到模型：任务上下文超长时应自动转为本地附件提示词，避免直接注入超长文本。"""

    task = TaskRecord(
        id="TASK_PUSH_LONG",
        project_slug="demo",
        title="超长推送任务",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="X" * (bot.TELEGRAM_MESSAGE_LIMIT + 300),
        parent_id=None,
        root_id="TASK_PUSH_LONG",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )
    message = DummyMessage()
    message.chat = SimpleNamespace(id=2468)

    async def fake_history(task_id: str):
        assert task_id == task.id
        return "", 0

    async def fake_notes(task_id: str):
        assert task_id == task.id
        return []

    async def fake_attachments(task_id: str):
        assert task_id == task.id
        return []

    captured: dict[str, object] = {}

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
        **_kwargs,
    ):
        captured["chat_id"] = chat_id
        captured["prompt"] = prompt
        captured["reply_to"] = reply_to
        captured["ack_immediately"] = ack_immediately
        return True, tmp_path / "session.jsonl"

    def fake_persist(msg: DummyMessage, text: str) -> bot.TelegramSavedAttachment:
        assert msg is message
        assert len(text) > bot.TELEGRAM_MESSAGE_LIMIT
        path = tmp_path / "20260206_000000000-long-prompt.txt"
        path.write_text(text, encoding="utf-8")
        return bot.TelegramSavedAttachment(
            kind="document",
            display_name=path.name,
            mime_type="text/plain",
            absolute_path=path,
            relative_path=bot._format_relative_path(path),
        )

    monkeypatch.setattr(bot, "_build_history_context_for_model", fake_history)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_notes", fake_notes)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_attachments", fake_attachments)
    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_persist_text_paste_as_attachment", fake_persist)

    async def _scenario() -> tuple[bool, str, Optional[Path]]:
        return await bot._push_task_to_model(
            task,
            chat_id=message.chat.id,
            reply_to=message,
            supplement=None,
            actor="Tester",
        )

    success, prompt, session_path = asyncio.run(_scenario())

    assert success is True
    assert session_path == tmp_path / "session.jsonl"
    assert captured["chat_id"] == message.chat.id
    assert captured["reply_to"] is message
    assert captured["ack_immediately"] is False
    assert isinstance(captured.get("prompt"), str)
    dispatched_prompt = captured["prompt"]  # type: ignore[assignment]
    assert prompt == dispatched_prompt
    assert "当前任务推送内容较长，已自动保存为附件（文本）" in dispatched_prompt
    assert "附件列表（文件位于项目工作目录" in dispatched_prompt
    assert "20260206_000000000-long-prompt.txt" in dispatched_prompt
    assert "X" * 120 not in dispatched_prompt


@pytest.mark.parametrize("push_mode", [bot.PUSH_MODE_PLAN, bot.PUSH_MODE_YOLO])
def test_push_task_to_model_forwards_push_mode_as_intended_mode(monkeypatch, tmp_path: Path, push_mode: str):
    """任务推送选择的 PLAN/YOLO 模式应透传到底层分发链路。"""

    task = TaskRecord(
        id="TASK_PUSH_MODE",
        project_slug="demo",
        title="模式透传任务",
        status="research",
        priority=2,
        task_type="task",
        tags=(),
        due_date=None,
        description="需要验证模式透传",
        parent_id=None,
        root_id="TASK_PUSH_MODE",
        depth=0,
        lineage="0000",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )
    message = DummyMessage()
    message.chat = SimpleNamespace(id=1357)

    async def fake_history(task_id: str):
        assert task_id == task.id
        return "", 0

    async def fake_notes(task_id: str):
        assert task_id == task.id
        return []

    async def fake_attachments(task_id: str):
        assert task_id == task.id
        return []

    captured: list[Optional[str]] = []

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
        intended_mode: Optional[str] = None,
        **_kwargs,
    ):
        captured.append(intended_mode)
        return True, tmp_path / "session.jsonl"

    monkeypatch.setattr(bot, "_build_history_context_for_model", fake_history)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_notes", fake_notes)
    monkeypatch.setattr(bot.TASK_SERVICE, "list_attachments", fake_attachments)
    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    async def _scenario() -> tuple[bool, str, Optional[Path]]:
        return await bot._push_task_to_model(
            task,
            chat_id=message.chat.id,
            reply_to=message,
            supplement=None,
            actor="Tester",
            push_mode=push_mode,
        )

    success, _prompt, session_path = asyncio.run(_scenario())

    assert success is True
    assert session_path == tmp_path / "session.jsonl"
    assert captured == [push_mode]


def test_on_status_callback_done_schedules_parallel_cleanup(monkeypatch):
    """任务切到 done 后，应立即返回，并把详情刷新与清理都放到后台。"""

    message = DummyMessage()
    callback = DummyCallback("task:status:TASK_0115:done", message)
    updated = _make_task(
        task_id="TASK_0115",
        title="并行任务",
        status="done",
        task_type="task",
    )

    async def fake_update_task(task_id: str, *, actor, status: str):
        assert task_id == updated.id
        assert status == "done"
        return updated

    async def fake_render(task_id: str):
        raise AssertionError(f"done 快路径不应同步渲染详情: {task_id}")

    async def fake_try_edit_message(_message, _text, reply_markup=None):
        raise AssertionError("done 快路径不应同步编辑详情文本")

    cleanup_calls: list[str] = []

    async def fake_delete_parallel_session_workspace(task_id: str):
        cleanup_calls.append(task_id)

    created_coroutines: list = []

    class DummyTask:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

    def fake_create_task(coro):
        created_coroutines.append(coro)
        return DummyTask()

    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)
    monkeypatch.setattr(bot, "_try_edit_message", fake_try_edit_message)
    monkeypatch.setattr(bot, "_delete_parallel_session_workspace", fake_delete_parallel_session_workspace)
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    asyncio.run(bot.on_status_callback(callback))

    assert callback.answers[-1] == ("状态已更新", False)
    assert len(created_coroutines) == 1, "done 快路径应只创建一个后台收尾任务"
    assert cleanup_calls == []
    assert not message.reply_markup_edits
    asyncio.run(created_coroutines[0])
    assert cleanup_calls == [updated.id]
    assert message.reply_markup_edits, "后台应刷新详情按钮"
    refreshed_markup, _kwargs = message.reply_markup_edits[-1]
    button_texts = [
        button.text
        for row in refreshed_markup.inline_keyboard
        for button in row
    ]
    assert "✅ 已完成 (当前)" in button_texts


def test_on_status_callback_non_done_does_not_schedule_parallel_cleanup(monkeypatch):
    """任务切到非 done 状态时，不应触发并行运行态清理。"""

    message = DummyMessage()
    callback = DummyCallback("task:status:TASK_0115:test", message)
    updated = _make_task(
        task_id="TASK_0115",
        title="并行任务",
        status="test",
        task_type="task",
    )

    async def fake_update_task(task_id: str, *, actor, status: str):
        assert task_id == updated.id
        assert status == "test"
        return updated

    async def fake_render(task_id: str):
        assert task_id == updated.id
        return "详情", InlineKeyboardMarkup(inline_keyboard=[])

    async def fake_try_edit_message(_message, _text, reply_markup=None):
        return True

    async def fake_delete_parallel_session_workspace(_task_id: str):
        raise AssertionError("非 done 状态不应触发并行清理")

    created_coroutines: list = []

    class DummyTask:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

    def fake_create_task(coro):
        created_coroutines.append(coro)
        return DummyTask()

    monkeypatch.setattr(bot.TASK_SERVICE, "update_task", fake_update_task)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)
    monkeypatch.setattr(bot, "_try_edit_message", fake_try_edit_message)
    monkeypatch.setattr(bot, "_delete_parallel_session_workspace", fake_delete_parallel_session_workspace)
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    asyncio.run(bot.on_status_callback(callback))

    assert callback.answers[-1] == ("状态已更新", False)
    assert not created_coroutines


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


def test_task_desc_input_too_long_converts_to_attachment_and_enters_confirm():
    message = DummyMessage()
    message.text = "x" * (bot.DESCRIPTION_MAX_LENGTH + 1)
    state, _storage = make_state(message)

    async def scenario() -> str | None:
        await state.update_data(task_id="TASK_EDIT", current_description="旧描述")
        await state.set_state(bot.TaskDescriptionStates.waiting_content)
        await bot.on_task_desc_input(message, state)
        return await state.get_state()

    state_value = asyncio.run(scenario())

    assert state_value == bot.TaskDescriptionStates.waiting_confirm.state
    data = asyncio.run(state.get_data())
    assert "已自动保存为附件" in (data.get("new_description") or "")
    pending = data.get("pending_attachments")
    assert isinstance(pending, list) and pending
    text, _parse_mode, markup, _kwargs = message.calls[-1]
    assert "请确认新的任务描述" in text
    assert isinstance(markup, ReplyKeyboardMarkup)


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


def test_text_paste_aggregation_injects_single_combined_message(monkeypatch):
    """长文本粘贴聚合：应只注入一次合成消息，内容为全部分片拼接结果。"""

    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 10)
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.01)

    recorded: list[str] = []

    async def fake_feed(_message: DummyMessage, *, text: str) -> None:
        recorded.append(text)

    monkeypatch.setattr(bot, "_feed_synthetic_text_update", fake_feed)

    message1 = DummyMessage()
    message1.text = "A" * 10  # 触发阈值，开启聚合
    message2 = DummyMessage()
    message2.message_id = message1.message_id + 1
    message2.text = "B"
    message3 = DummyMessage()
    message3.message_id = message1.message_id + 2
    message3.text = "C"

    async def _scenario() -> None:
        assert await bot._maybe_enqueue_text_paste_message(message1, message1.text) is True
        assert await bot._maybe_enqueue_text_paste_message(message2, message2.text) is True
        assert await bot._maybe_enqueue_text_paste_message(message3, message3.text) is True
        await asyncio.sleep(0.05)

    asyncio.run(_scenario())

    assert recorded == ["A" * 10 + "B" + "C"]


def test_text_paste_aggregation_merges_prefix_and_log_parts(monkeypatch):
    """短前缀 + 长日志：应合并为一次合成消息，且保留前缀 + 换行 + 日志内容。"""

    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 10)
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.01)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_MAX_CHARS", 50)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS", 200)

    recorded: list[str] = []

    async def fake_feed(_message: DummyMessage, *, text: str) -> None:
        recorded.append(text)

    monkeypatch.setattr(bot, "_feed_synthetic_text_update", fake_feed)

    prefix = DummyMessage()
    prefix.text = "1 见如下日志："
    part1 = DummyMessage()
    part1.message_id = prefix.message_id + 1
    part1.text = "A" * 10  # 触发阈值，进入聚合
    part2 = DummyMessage()
    part2.message_id = prefix.message_id + 2
    part2.text = "B"

    async def _scenario() -> None:
        assert await bot._maybe_enqueue_text_paste_message(prefix, prefix.text) is True
        assert await bot._maybe_enqueue_text_paste_message(part1, part1.text) is True
        assert await bot._maybe_enqueue_text_paste_message(part2, part2.text) is True
        await asyncio.sleep(0.05)

    asyncio.run(_scenario())

    assert recorded == ["1 见如下日志：\n" + "A" * 10 + "B"]


def test_text_paste_prefix_only_falls_back_to_injection_after_delay(monkeypatch):
    """仅发送短前缀且窗口内无后续日志：应在延迟后注入该前缀，避免吞消息。"""

    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 10)
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.01)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_MAX_CHARS", 50)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS", 200)

    recorded: list[str] = []

    async def fake_feed(_message: DummyMessage, *, text: str) -> None:
        recorded.append(text)

    monkeypatch.setattr(bot, "_feed_synthetic_text_update", fake_feed)

    prefix = DummyMessage()
    prefix.text = "1 见如下日志："

    async def _scenario() -> None:
        assert await bot._maybe_enqueue_text_paste_message(prefix, prefix.text) is True
        await asyncio.sleep(0.05)

    asyncio.run(_scenario())

    assert recorded == ["1 见如下日志："]


def test_text_paste_prefix_followed_by_short_message_flushes_prefix(monkeypatch):
    """短前缀后面跟的消息仍很短：应立即注入前缀，并让后续消息走正常流程（聚合函数返回 False）。"""

    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 10)
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.5)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_MAX_CHARS", 50)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS", 200)

    recorded: list[str] = []

    async def fake_feed(_message: DummyMessage, *, text: str) -> None:
        recorded.append(text)

    monkeypatch.setattr(bot, "_feed_synthetic_text_update", fake_feed)

    prefix = DummyMessage()
    prefix.text = "1 见如下日志："
    followup = DummyMessage()
    followup.message_id = prefix.message_id + 1
    followup.text = "ok"

    async def _scenario() -> None:
        assert await bot._maybe_enqueue_text_paste_message(prefix, prefix.text) is True
        flushed = await bot._maybe_enqueue_text_paste_message(followup, followup.text)
        assert flushed is False

    asyncio.run(_scenario())

    assert recorded == ["1 见如下日志："]
    assert not bot.TEXT_PASTE_STATE, "前缀应被及时清空，避免误合并"


def test_text_paste_prefix_captures_short_log_fragment_before_near_limit_chunk(monkeypatch):
    """短前缀触发窗口后，首段日志可能小于 near-limit：仍应纳入聚合，避免合成内容缺头。"""

    bot.TEXT_PASTE_STATE.clear()
    monkeypatch.setattr(bot, "ENABLE_TEXT_PASTE_AGGREGATION", True)
    monkeypatch.setattr(bot, "TEXT_PASTE_NEAR_LIMIT_THRESHOLD", 20)
    # 留出足够的时间窗口，避免“短前缀 finalize”在后续分片到达前提前触发。
    monkeypatch.setattr(bot, "TEXT_PASTE_AGGREGATION_DELAY", 0.2)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_MAX_CHARS", 50)
    monkeypatch.setattr(bot, "TEXT_PASTE_PREFIX_FOLLOWUP_MIN_CHARS", 200)

    recorded: list[str] = []

    async def fake_feed(_message: DummyMessage, *, text: str) -> None:
        recorded.append(text)

    monkeypatch.setattr(bot, "_feed_synthetic_text_update", fake_feed)

    prefix = DummyMessage()
    prefix.text = "1 见如下日志："
    part1 = DummyMessage()
    part1.message_id = prefix.message_id + 1
    part1.text = "2026-01-27 17:12:13"  # 小于阈值，但符合日志前缀特征
    part2 = DummyMessage()
    part2.message_id = prefix.message_id + 2
    part2.text = "X" * 20  # 达到阈值

    async def _scenario() -> None:
        assert await bot._maybe_enqueue_text_paste_message(prefix, prefix.text) is True
        assert await bot._maybe_enqueue_text_paste_message(part1, part1.text) is True
        assert await bot._maybe_enqueue_text_paste_message(part2, part2.text) is True
        await asyncio.sleep(0.25)

    asyncio.run(_scenario())

    assert recorded == ["1 见如下日志：\n" + "2026-01-27 17:12:13" + "X" * 20]


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
