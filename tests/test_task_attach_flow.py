import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot
from tasks.fsm import TaskAttachmentStates

from aiogram.types import ReplyKeyboardMarkup


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
    def __init__(self, text=""):
        self.text = text
        self.chat = SimpleNamespace(id=1)
        self.from_user = SimpleNamespace(id=1, full_name="Tester")
        self.calls = []
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


def test_attach_media_group_binds_once(monkeypatch, tmp_path):
    """相册两张图在 /attach 流程中只应绑定一次（避免重复附件）。"""

    bot.GENERIC_MEDIA_GROUP_CONSUMED.clear()
    monkeypatch.setattr(bot, "MEDIA_GROUP_AGGREGATION_DELAY", 0.01)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)

    task = bot.TaskRecord(
        id="TASK_0001",
        project_slug="demo",
        title="测试",
        status="research",
        priority=3,
        task_type="task",
        tags=(),
        due_date=None,
        description=None,
        parent_id=None,
        root_id="TASK_0001",
        depth=0,
        lineage="0001",
        archived=False,
    )

    async def fake_get_task(task_id: str):
        return task if task_id == "TASK_0001" else None

    async def fake_render(task_id: str):
        return "detail", ReplyKeyboardMarkup(keyboard=[])

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_render_task_detail", fake_render)

    msg1 = DummyMessage("")
    msg1.media_group_id = "attach_album_1"
    msg2 = DummyMessage("")
    msg2.media_group_id = "attach_album_1"

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

    state = DummyState(
        data={
            "task_id": "TASK_0001",
            "processed_media_groups": [],
        },
        state=TaskAttachmentStates.waiting_files,
    )

    async def run_album_flow():
        await asyncio.gather(
            bot.on_task_attach_files(msg1, state),
            bot.on_task_attach_files(msg2, state),
        )

    asyncio.run(run_album_flow())

    assert added_paths == ["./data/a1.jpg", "./data/a2.jpg"]
    assert sorted([len(msg1.calls), len(msg2.calls)]) == [0, 1]
