import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot
from tasks.fsm import TaskCreateStates

from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")


class DummyState:
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
    def __init__(self, text):
        self.text = text
        self.chat = SimpleNamespace(id=1)
        self.from_user = SimpleNamespace(id=1, full_name="Tester")
        self.calls = []
        self.edits = []
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

    async def answer(self, text, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(message_id=len(self.calls))

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        self.edits.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )


class DummyCallback:
    def __init__(self, message, data="task:create_confirm"):
        self.message = message
        self.data = data
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(
            {
                "text": text,
                "show_alert": show_alert,
            }
        )


def test_task_new_interactive_sets_default_priority_and_prompt():
    state = DummyState()
    message = DummyMessage("/task_new")
    asyncio.run(bot.on_task_new(message, state))

    assert state.state == TaskCreateStates.waiting_title
    assert state.data["priority"] == bot.DEFAULT_PRIORITY
    assert message.calls and message.calls[-1]["text"] == "请输入任务标题："


def test_task_new_command_rejects_priority_param():
    state = DummyState()
    message = DummyMessage("/task_new 修复登录 | priority=2 | type=需求")
    asyncio.run(bot.on_task_new(message, state))

    assert message.calls
    assert "priority 参数已取消" in message.calls[-1]["text"]


def test_task_create_title_moves_to_type_selection():
    state = DummyState(data={"priority": bot.DEFAULT_PRIORITY})
    message = DummyMessage("新任务标题")
    asyncio.run(bot.on_task_create_title(message, state))

    assert state.state == TaskCreateStates.waiting_type
    assert state.data["title"] == "新任务标题"
    assert message.calls
    assert isinstance(message.calls[-1]["reply_markup"], ReplyKeyboardMarkup)
    assert "请选择任务类型" in message.calls[-1]["text"]


def test_task_create_type_requirement_moves_to_description_prompt():
    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
        },
        state=TaskCreateStates.waiting_type,
    )
    message = DummyMessage(bot._format_task_type("requirement"))
    asyncio.run(bot.on_task_create_type(message, state))

    assert state.state == TaskCreateStates.waiting_description
    assert state.data["task_type"] == "requirement"
    assert message.calls
    prompt = message.calls[-1]["text"]
    assert prompt.startswith("请输入任务描述")
    markup = message.calls[-1]["reply_markup"]
    assert isinstance(markup, ReplyKeyboardMarkup)
    buttons = [button.text for row in markup.keyboard for button in row]
    assert any("跳过" in text for text in buttons)
    assert any("取消" in text for text in buttons)


def test_task_create_type_task_moves_to_current_effect_prompt():
    state = DummyState(
        data={
            "title": "优化任务",
            "priority": bot.DEFAULT_PRIORITY,
        },
        state=TaskCreateStates.waiting_type,
    )
    message = DummyMessage(bot._format_task_type("task"))
    asyncio.run(bot.on_task_create_type(message, state))

    assert state.state == TaskCreateStates.waiting_current_effect
    assert state.data["task_type"] == "task"
    assert message.calls
    prompt = message.calls[-1]["text"]
    assert prompt.startswith("请输入当前效果")
    assert isinstance(message.calls[-1]["reply_markup"], ReplyKeyboardMarkup)


def test_task_create_type_defect_moves_to_related_selection(monkeypatch):
    state = DummyState(
        data={
            "title": "缺陷任务",
            "priority": bot.DEFAULT_PRIORITY,
        },
        state=TaskCreateStates.waiting_type,
    )
    message = DummyMessage(bot._format_task_type("defect"))

    async def fake_view(*, page: int):
        assert page == 1
        return "请选择关联任务：", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot, "_build_related_task_select_view", fake_view)

    asyncio.run(bot.on_task_create_type(message, state))

    assert state.state == TaskCreateStates.waiting_related_task
    assert state.data["task_type"] == "defect"
    assert state.data["related_page"] == 1
    assert state.data["related_task_id"] is None
    assert message.calls
    assert isinstance(message.calls[-1]["reply_markup"], InlineKeyboardMarkup)


def test_task_create_related_task_text_accepts_number_skip(monkeypatch):
    """缺陷创建：关联任务阶段输入 1 应等价于“跳过”。"""

    async def fake_view(*, page: int):
        return "请选择关联任务：", InlineKeyboardMarkup(inline_keyboard=[])

    monkeypatch.setattr(bot, "_build_related_task_select_view", fake_view)

    state = DummyState(
        data={
            "title": "缺陷任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "defect",
            "related_page": 1,
        },
        state=TaskCreateStates.waiting_related_task,
    )
    message = DummyMessage("1")
    asyncio.run(bot.on_task_create_related_task_text(message, state))

    assert state.state == TaskCreateStates.waiting_precondition
    assert state.data.get("related_task_id") is None
    assert message.calls
    assert any("已跳过关联任务选择" in call["text"] for call in message.calls)
    assert any("请输入前置条件" in call["text"] for call in message.calls)


def test_task_create_related_task_text_accepts_number_cancel():
    """缺陷创建：关联任务阶段输入 2 应等价于“取消创建任务”。"""

    state = DummyState(
        data={
            "title": "缺陷任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "defect",
            "related_page": 1,
        },
        state=TaskCreateStates.waiting_related_task,
    )
    message = DummyMessage("2")
    asyncio.run(bot.on_task_create_related_task_text(message, state))

    assert state.state is None
    assert not state.data
    assert message.calls
    assert message.calls[-1]["text"] == "已取消创建任务。"


@pytest.mark.parametrize(
    "invalid_text",
    [
        "",
        " ",
        "无效类型",
        "priority=2",
        "task*",
        "需 求",
        "任务?",
        "---",
        "123",
        "🤖",
    ],
)
def test_task_create_type_invalid_reprompts(invalid_text):
    state = DummyState(
        data={
            "title": "测试任务",
            "priority": bot.DEFAULT_PRIORITY,
        },
        state=TaskCreateStates.waiting_type,
    )
    message = DummyMessage(invalid_text)
    asyncio.run(bot.on_task_create_type(message, state))

    assert state.state == TaskCreateStates.waiting_type
    assert message.calls
    assert message.calls[-1]["text"].startswith("任务类型无效")
    assert isinstance(message.calls[-1]["reply_markup"], ReplyKeyboardMarkup)


def test_task_create_description_skip_produces_summary():
    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
        },
        state=TaskCreateStates.waiting_description,
    )
    message = DummyMessage(bot.SKIP_TEXT)
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert state.data["description"] == ""
    assert len(message.calls) >= 2
    summary = message.calls[-2]["text"]
    assert "描述：暂无" in summary
    assert isinstance(message.calls[-1]["reply_markup"], ReplyKeyboardMarkup)


def test_task_create_description_accepts_text():
    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
        },
        state=TaskCreateStates.waiting_description,
    )
    description = "这是任务描述，包含背景与预期结果。"
    message = DummyMessage(description)
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert state.data["description"] == description
    summary = message.calls[-2]["text"]
    assert "描述：" in summary
    assert description in summary


def test_task_create_description_too_long_converts_to_attachment_and_continues():
    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
        },
        state=TaskCreateStates.waiting_description,
    )
    long_text = "a" * (bot.DESCRIPTION_MAX_LENGTH + 1)
    message = DummyMessage(long_text)
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert "已自动保存为附件" in state.data["description"]
    pending = state.data.get("pending_attachments")
    assert isinstance(pending, list) and pending, "超长描述应被转为附件并暂存"
    last = pending[-1]
    assert last.get("mime_type") == "text/plain"
    assert last.get("path")


def test_task_create_description_cancel_aborts():
    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
        },
        state=TaskCreateStates.waiting_description,
    )
    message = DummyMessage("取消")
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state is None
    assert message.calls
    assert message.calls[-1]["text"] == "已取消创建任务。"


def test_task_create_description_cancel_keyboard_aborts():
    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
        },
        state=TaskCreateStates.waiting_description,
    )
    message = DummyMessage("2. 取消")
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state is None
    assert message.calls
    assert message.calls[-1]["text"] == "已取消创建任务。"


def test_task_create_description_binds_attachments(monkeypatch, tmp_path):
    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
        },
        state=TaskCreateStates.waiting_description,
    )
    message = DummyMessage("任务描述")
    message.bot = SimpleNamespace(username="tester_bot")
    message.date = datetime.now(bot.UTC)

    saved = [
        bot.TelegramSavedAttachment(
            kind="document",
            display_name="log.txt",
            mime_type="text/plain",
            absolute_path=tmp_path / "log.txt",
            relative_path="./data/log.txt",
        )
    ]

    collect_queue = [saved, []]

    async def fake_collect(msg, target_dir):
        return collect_queue.pop(0)

    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)

    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert state.data.get("pending_attachments")
    assert state.data["pending_attachments"][0]["path"] == "./data/log.txt"
    assert message.calls
    summary = message.calls[-2]["text"]
    assert "附件列表：" in summary
    assert "log.txt（text/plain）→ ./data/log.txt" in summary

    created_task = bot.TaskRecord(
        id="TASK_1234",
        project_slug="demo",
        title="测试标题",
        status="research",
        priority=3,
        task_type="requirement",
        tags=(),
        due_date=None,
        description="任务描述",
        parent_id=None,
        root_id="TASK_1234",
        depth=0,
        lineage="0001",
        archived=False,
    )

    async def fake_create_root_task(**_kwargs):
        return created_task

    added_paths = []

    async def fake_add_attachment(task_id, display_name, mime_type, path, kind):
        added_paths.append(path)
        return bot.TaskAttachmentRecord(
            id=1,
            task_id=task_id,
            display_name=display_name,
            mime_type=mime_type,
            path=path,
            kind=kind,
        )

    async def fake_log_task_event(task_id, **_kwargs):
        return None

    async def fake_render(task_id: str):
        return "detail", ReplyKeyboardMarkup(keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "create_root_task", fake_create_root_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "add_attachment", fake_add_attachment)
    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_task_event)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)

    confirm_message = DummyMessage("✅ 确认创建")
    confirm_message.bot = message.bot
    confirm_message.date = message.date
    asyncio.run(bot.on_task_create_confirm(confirm_message, state))

    assert added_paths == ["./data/log.txt"]


def test_task_create_album_keeps_text_and_collects_followup_attachments(monkeypatch, tmp_path):
    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
        },
        state=TaskCreateStates.waiting_description,
    )
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
        [],
    ]

    async def fake_collect(msg, target_dir):
        return queue.pop(0)

    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)

    message = DummyMessage("描述文本")
    asyncio.run(bot.on_task_create_description(message, state))

    # 媒体组后续消息到达确认阶段时继续补充附件
    followup = DummyMessage("")
    asyncio.run(bot.on_task_create_confirm(followup, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert state.data.get("description") == "描述文本"
    assert len(state.data.get("pending_attachments", [])) == 2

    created_task = bot.TaskRecord(
        id="TASK_5678",
        project_slug="demo",
        title="测试标题",
        status="research",
        priority=3,
        task_type="requirement",
        tags=(),
        due_date=None,
        description="描述文本",
        parent_id=None,
        root_id="TASK_5678",
        depth=0,
        lineage="0001",
        archived=False,
    )

    async def fake_create_root_task(**_kwargs):
        return created_task

    added_paths = []

    async def fake_add_attachment(task_id, display_name, mime_type, path, kind):
        added_paths.append(path)
        return bot.TaskAttachmentRecord(
            id=len(added_paths),
            task_id=task_id,
            display_name=display_name,
            mime_type=mime_type,
            path=path,
            kind=kind,
        )

    async def fake_log_task_event(task_id, **_kwargs):
        return None

    async def fake_render(task_id: str):
        return "detail", ReplyKeyboardMarkup(keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "create_root_task", fake_create_root_task)
    monkeypatch.setattr(bot.TASK_SERVICE, "add_attachment", fake_add_attachment)
    monkeypatch.setattr(bot.TASK_SERVICE, "log_task_event", fake_log_task_event)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)

    confirm = DummyMessage("✅ 确认创建")
    confirm.bot = message.bot
    confirm.date = message.date
    asyncio.run(bot.on_task_create_confirm(confirm, state))

    assert added_paths == ["./data/a1.jpg", "./data/a2.jpg"]


def test_task_create_media_group_dedupes_attachments_and_advances_once(monkeypatch, tmp_path):
    """相册两张图只应推进一次创建流程，pending 附件不应重复。"""

    bot.GENERIC_MEDIA_GROUP_CONSUMED.clear()
    monkeypatch.setattr(bot, "MEDIA_GROUP_AGGREGATION_DELAY", 0.01)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)

    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
            "processed_media_groups": [],
        },
        state=TaskCreateStates.waiting_description,
    )

    msg1 = DummyMessage("")
    msg1.media_group_id = "task_album_1"
    msg1.caption = "相册描述"

    msg2 = DummyMessage("")
    msg2.media_group_id = "task_album_1"

    async def fake_collect(msg, target_dir):
        if msg is msg1:
            return [
                bot.TelegramSavedAttachment(
                    kind="photo",
                    display_name="a1.jpg",
                    mime_type="image/jpeg",
                    absolute_path=tmp_path / "a1.jpg",
                    relative_path="./data/a1.jpg",
                )
            ]
        if msg is msg2:
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

    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)

    async def run_album_flow():
        await asyncio.gather(
            bot.on_task_create_description(msg1, state),
            bot.on_task_create_description(msg2, state),
        )

    asyncio.run(run_album_flow())

    assert state.state == TaskCreateStates.waiting_confirm
    assert state.data.get("description") == "相册描述"
    pending = state.data.get("pending_attachments", [])
    assert len(pending) == 2
    assert {item.get("path") for item in pending} == {"./data/a1.jpg", "./data/a2.jpg"}
    # 只应有一条消息触发回复（两次 answer：信息汇总 + 确认提示）
    assert sorted([len(msg1.calls), len(msg2.calls)]) == [0, 2]


def test_task_create_confirm_media_group_appends_once(monkeypatch, tmp_path):
    """确认阶段相册补充附件只应记录一次。"""

    bot.GENERIC_MEDIA_GROUP_CONSUMED.clear()
    monkeypatch.setattr(bot, "MEDIA_GROUP_AGGREGATION_DELAY", 0.01)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)

    state = DummyState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
            "description": "已有描述",
            "pending_attachments": [],
            "processed_media_groups": [],
        },
        state=TaskCreateStates.waiting_confirm,
    )

    msg1 = DummyMessage("")
    msg1.media_group_id = "task_album_2"
    msg1.caption = "补充说明"

    msg2 = DummyMessage("")
    msg2.media_group_id = "task_album_2"

    async def fake_collect(msg, target_dir):
        if msg is msg1:
            return [
                bot.TelegramSavedAttachment(
                    kind="photo",
                    display_name="b1.jpg",
                    mime_type="image/jpeg",
                    absolute_path=tmp_path / "b1.jpg",
                    relative_path="./data/b1.jpg",
                )
            ]
        if msg is msg2:
            return [
                bot.TelegramSavedAttachment(
                    kind="photo",
                    display_name="b2.jpg",
                    mime_type="image/jpeg",
                    absolute_path=tmp_path / "b2.jpg",
                    relative_path="./data/b2.jpg",
                )
            ]
        return []

    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)

    async def run_album_flow():
        await asyncio.gather(
            bot.on_task_create_confirm(msg1, state),
            bot.on_task_create_confirm(msg2, state),
        )

    asyncio.run(run_album_flow())

    assert state.state == TaskCreateStates.waiting_confirm
    pending = state.data.get("pending_attachments", [])
    assert len(pending) == 2
    assert {item.get("path") for item in pending} == {"./data/b1.jpg", "./data/b2.jpg"}
    assert "已有描述" in (state.data.get("description") or "")
    # 只应有一条消息提示“已记录补充…”
    assert sorted([len(msg1.calls), len(msg2.calls)]) == [0, 1]


@pytest.mark.parametrize(
    ("attachments", "expected_paths"),
    [
        ([], []),
        ([{"path": "./data/a.jpg"}], ["./data/a.jpg"]),
        ([{"path": "./data/a.jpg"}, {"path": "./data/b.jpg"}], ["./data/a.jpg", "./data/b.jpg"]),
        ([{"path": "./data/a.jpg"}, {"path": "./data/a.jpg"}], ["./data/a.jpg"]),
        (
            [{"path": "./data/a.jpg"}, {"path": "./data/a.jpg"}, {"path": "./data/b.jpg"}],
            ["./data/a.jpg", "./data/b.jpg"],
        ),
        (
            [{"path": "./data/a.jpg"}, {"path": "./data/b.jpg"}, {"path": "./data/a.jpg"}],
            ["./data/a.jpg", "./data/b.jpg"],
        ),
        ([{"path": ""}, {"path": ""}], ["", ""]),
        ([{"path": "  ./data/a.jpg  "}, {"path": "./data/a.jpg"}], ["./data/a.jpg"]),
        ([{"path": None}, {"path": "./data/a.jpg"}, {"path": None}], ["", "./data/a.jpg", ""]),
        (
            [
                {"path": "./data/a.jpg"},
                {"path": "./data/a.jpg"},
                {"path": "./data/b.jpg"},
                {"path": "./data/c.jpg"},
                {"path": "./data/b.jpg"},
            ],
            ["./data/a.jpg", "./data/b.jpg", "./data/c.jpg"],
        ),
    ],
)
def test_bind_serialized_attachments_dedupes_by_path(monkeypatch, attachments, expected_paths):
    """按 path 去重，避免重复写库。"""

    task = bot.TaskRecord(
        id="TASK_9999",
        project_slug="demo",
        title="测试",
        status="research",
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description=None,
        parent_id=None,
        root_id="TASK_9999",
        depth=0,
        lineage="0001",
        archived=False,
    )

    added_paths: list[str] = []

    async def fake_add_attachment(task_id, display_name, mime_type, path, kind):
        added_paths.append(path)
        return bot.TaskAttachmentRecord(
            id=len(added_paths),
            task_id=task_id,
            display_name=display_name,
            mime_type=mime_type,
            path=path,
            kind=kind,
        )

    monkeypatch.setattr(bot.TASK_SERVICE, "add_attachment", fake_add_attachment)

    asyncio.run(bot._bind_serialized_attachments(task, attachments, actor="Tester"))

    assert added_paths == expected_paths


def test_task_create_confirm_uses_default_priority(monkeypatch):
    state = DummyState(
        data={
            "title": "测试任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
            "actor": "Tester#1",
            "description": "",
        },
        state=TaskCreateStates.waiting_confirm,
    )
    message = DummyMessage("1")
    calls = []

    async def fake_create_root_task(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="TASK_9999")

    async def fake_render_detail(task_id):
        return "详情文本", None

    monkeypatch.setattr(
        bot,
        "TASK_SERVICE",
        SimpleNamespace(create_root_task=fake_create_root_task),
    )
    monkeypatch.setattr(bot, "_render_task_detail", fake_render_detail)

    asyncio.run(bot.on_task_create_confirm(message, state))

    assert calls and calls[0]["priority"] == bot.DEFAULT_PRIORITY
    assert state.state is None
    assert message.calls
    assert isinstance(message.calls[-2]["reply_markup"], ReplyKeyboardMarkup)
    assert "任务已创建：" in message.calls[-1]["text"]


def test_task_create_confirm_invalid_prompts_again():
    state = DummyState(
        data={
            "title": "测试任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
            "description": "",
        },
        state=TaskCreateStates.waiting_confirm,
    )
    message = DummyMessage("随便输入")

    asyncio.run(bot.on_task_create_confirm(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert message.calls
    assert "已记录补充的描述/附件" in message.calls[-1]["text"]
    assert isinstance(message.calls[-1]["reply_markup"], ReplyKeyboardMarkup)


def test_task_create_confirm_cancel_via_number():
    state = DummyState(
        data={
            "title": "测试任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "requirement",
        },
        state=TaskCreateStates.waiting_confirm,
    )
    message = DummyMessage("2")

    asyncio.run(bot.on_task_create_confirm(message, state))

    assert state.state is None
    assert len(message.calls) >= 2
    assert isinstance(message.calls[-2]["reply_markup"], ReplyKeyboardRemove)
    assert message.calls[-2]["text"] == "已取消创建任务。"
    assert message.calls[-1]["text"] == "已返回主菜单。"


def test_task_create_precondition_accepts_text_and_moves_to_reproduction():
    state = DummyState(
        data={
            "title": "缺陷任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "defect",
            "related_task_id": None,
        },
        state=TaskCreateStates.waiting_precondition,
    )
    message = DummyMessage("已登录测试账号")

    asyncio.run(bot.on_task_create_precondition(message, state))

    assert state.state == TaskCreateStates.waiting_reproduction
    assert state.data["precondition"] == "已登录测试账号"
    assert message.calls and "请输入复现步骤" in message.calls[-1]["text"]


def test_task_create_reproduction_accepts_text_and_moves_to_expected_result():
    state = DummyState(
        data={
            "title": "缺陷任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "defect",
            "related_task_id": None,
            "precondition": "已登录测试账号",
        },
        state=TaskCreateStates.waiting_reproduction,
    )
    message = DummyMessage("1. 打开页面")

    asyncio.run(bot.on_task_create_reproduction(message, state))

    assert state.state == TaskCreateStates.waiting_expected_result
    assert state.data["reproduction"] == "1. 打开页面"
    assert message.calls and "请输入预期结果" in message.calls[-1]["text"]


def test_task_create_expected_result_skip_builds_defect_summary():
    state = DummyState(
        data={
            "title": "缺陷任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "defect",
            "related_task_id": None,
            "precondition": "已登录测试账号",
            "reproduction": "1. 打开页面",
        },
        state=TaskCreateStates.waiting_expected_result,
    )
    message = DummyMessage(bot.SKIP_TEXT)

    asyncio.run(bot.on_task_create_expected_result(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert state.data["description"] == "前置条件：\n已登录测试账号\n\n复现步骤：\n1. 打开页面\n\n预期结果：\n-"
    summary = message.calls[-2]["text"]
    assert "前置条件：" in summary
    assert "复现步骤：" in summary
    assert "预期结果：-" in summary


def test_task_create_current_effect_accepts_text_and_moves_to_expected_effect():
    state = DummyState(
        data={
            "title": "优化任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
        },
        state=TaskCreateStates.waiting_current_effect,
    )
    message = DummyMessage("当前按钮需要点击两次才能成功")

    asyncio.run(bot.on_task_create_current_effect(message, state))

    assert state.state == TaskCreateStates.waiting_expected_effect
    assert state.data["current_effect"] == "当前按钮需要点击两次才能成功"
    assert message.calls and "请输入期望效果" in message.calls[-1]["text"]


def test_task_create_expected_effect_accepts_text_and_builds_task_summary():
    state = DummyState(
        data={
            "title": "优化任务",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
            "current_effect": "当前按钮需要点击两次才能成功",
        },
        state=TaskCreateStates.waiting_expected_effect,
    )
    message = DummyMessage("点击一次即可成功提交")

    asyncio.run(bot.on_task_create_expected_effect(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert (
        state.data["description"]
        == "当前效果：\n当前按钮需要点击两次才能成功\n\n期望效果：\n点击一次即可成功提交"
    )
    summary = message.calls[-2]["text"]
    assert "当前效果：" in summary
    assert "期望效果：" in summary


def test_task_new_command_defect_accepts_structured_fields(monkeypatch):
    state = DummyState()
    message = DummyMessage(
        "/task_new 登录按钮无响应 | type=缺陷 | precondition=已登录测试账号 | reproduction=点击登录按钮 | expected_result=页面应成功进入首页"
    )

    created_calls = []

    async def fake_create_root_task(**kwargs):
        created_calls.append(kwargs)
        return SimpleNamespace(id="TASK_9001")

    async def fake_render(task_id: str):
        return "detail", None

    monkeypatch.setattr(bot.TASK_SERVICE, "create_root_task", fake_create_root_task)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)

    asyncio.run(bot.on_task_new(message, state))

    assert created_calls
    assert created_calls[0]["task_type"] == "defect"
    assert created_calls[0]["description"] == "前置条件：\n已登录测试账号\n\n复现步骤：\n点击登录按钮\n\n预期结果：\n页面应成功进入首页"


def test_task_new_command_task_accepts_structured_fields(monkeypatch):
    state = DummyState()
    message = DummyMessage(
        "/task_new 优化登录流程 | type=优化 | current_effect=当前需要重复点击两次 | expected_effect=点击一次即可完成提交"
    )

    created_calls = []

    async def fake_create_root_task(**kwargs):
        created_calls.append(kwargs)
        return SimpleNamespace(id="TASK_9002")

    async def fake_render(task_id: str):
        return "detail", None

    monkeypatch.setattr(bot.TASK_SERVICE, "create_root_task", fake_create_root_task)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)

    asyncio.run(bot.on_task_new(message, state))

    assert created_calls
    assert created_calls[0]["task_type"] == "task"
    assert created_calls[0]["description"] == "当前效果：\n当前需要重复点击两次\n\n期望效果：\n点击一次即可完成提交"


@pytest.mark.parametrize("task_type_text", ["缺陷", "优化"])
def test_task_new_command_keeps_legacy_description_param_compatible(monkeypatch, task_type_text):
    state = DummyState()
    message = DummyMessage(f"/task_new 兼容旧参数 | type={task_type_text} | description=旧描述正文")

    created_calls = []

    async def fake_create_root_task(**kwargs):
        created_calls.append(kwargs)
        return SimpleNamespace(id="TASK_9003")

    async def fake_render(task_id: str):
        return "detail", None

    monkeypatch.setattr(bot.TASK_SERVICE, "create_root_task", fake_create_root_task)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)

    asyncio.run(bot.on_task_new(message, state))

    assert created_calls
    assert created_calls[0]["description"] == "旧描述正文"


def test_task_child_command_reports_deprecation():
    state = DummyState(data={"stage": "child"}, state="waiting")
    message = DummyMessage("/task_child TASK_0001 新子任务")

    asyncio.run(bot.on_task_child(message, state))

    assert state.state is None
    assert not state.data
    assert message.calls
    assert "子任务功能已下线" in message.calls[-1]["text"]


def test_task_children_command_reports_deprecation():
    message = DummyMessage("/task_children TASK_0001")

    asyncio.run(bot.on_task_children(message))

    assert message.calls
    assert "子任务功能已下线" in message.calls[-1]["text"]


def test_task_add_child_callback_reports_deprecation():
    callback = DummyCallback(DummyMessage(""), "task:add_child:TASK_0001")
    state = DummyState(data={"stage": "child"}, state="waiting")

    asyncio.run(bot.on_add_child_callback(callback, state))

    assert state.state is None
    assert not state.data
    assert callback.answers
    assert "子任务功能已下线" in (callback.answers[-1]["text"] or "")
    assert callback.message.calls
    assert "子任务功能已下线" in callback.message.calls[-1]["text"]


def test_task_list_children_callback_reports_deprecation():
    callback = DummyCallback(DummyMessage(""), "task:list_children:TASK_0001")

    asyncio.run(bot.on_list_children_callback(callback))

    assert callback.answers
    assert "子任务功能已下线" in (callback.answers[-1]["text"] or "")
    assert callback.message.calls
    assert "子任务功能已下线" in callback.message.calls[-1]["text"]
