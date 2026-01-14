from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot  # noqa: E402


class DummyMessage:
    """模拟 aiogram Message，覆盖本用例所需的最小接口。"""

    def __init__(self, *, chat_id: int = 1, user_id: int = 1):
        self.calls = []
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester")
        self.message_id = 100
        self.date = datetime.now(bot.UTC)
        self.text = None
        self.caption = None

    async def answer(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id + len(self.calls), chat=self.chat)


class DummyCallback:
    """模拟 aiogram CallbackQuery，覆盖本用例所需的最小接口。"""

    def __init__(self, data: str, message: DummyMessage):
        self.data = data
        self.message = message
        self.answers = []
        self.from_user = SimpleNamespace(id=1, full_name="Tester")

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


def make_state(message: DummyMessage) -> tuple[FSMContext, MemoryStorage]:
    """构造测试用 FSMContext（MemoryStorage）。"""

    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=999, chat_id=message.chat.id, user_id=message.from_user.id),
    )
    return state, storage


def test_quick_reply_partial_enters_supplement_state():
    """点击“部分按推荐（需补充）”应进入补充输入状态，不应立即推送到模型。"""

    message = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_PARTIAL_CALLBACK, message)
    state, _ = make_state(message)

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        assert callback.answers and callback.answers[-1][0] == "请发送补充说明，或点击跳过/取消"
        assert await state.get_state() == bot.ModelQuickReplyStates.waiting_partial_supplement.state
        assert message.calls, "应提示用户输入补充说明"
        prompt_text, _, reply_markup, _ = message.calls[-1]
        assert "请发送需要补充的说明" in prompt_text
        assert isinstance(reply_markup, ReplyKeyboardMarkup)

    asyncio.run(_scenario())


def test_quick_reply_partial_supplement_dispatches_prompt(monkeypatch, tmp_path: Path):
    """补充阶段输入文案后，应推送“未提及按推荐 + 用户补充说明”到模型。"""

    origin = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_PARTIAL_CALLBACK, origin)
    state, _ = make_state(origin)

    recorded: list[tuple[int, str, object, bool]] = []
    previews: list[tuple[int, str, object, object]] = []
    ack_calls: list[tuple[int, Path, object]] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        recorded.append((chat_id, prompt, reply_to, ack_immediately))
        return True, tmp_path / "session.jsonl"

    async def fake_preview(chat_id: int, preview_block: str, *, reply_to, parse_mode, reply_markup):
        previews.append((chat_id, preview_block, parse_mode, reply_markup))

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append((chat_id, session_path, reply_to))

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    supplement_message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
    supplement_message.text = "我需要补充：只有第 3 个选项不按推荐，其余都按推荐。"

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        await bot.on_model_quick_reply_partial_supplement(supplement_message, state)
        assert recorded, "应推送到模型"
        chat_id, prompt, reply_to, ack_immediately = recorded[-1]
        assert chat_id == origin.chat.id
        assert reply_to is origin
        assert not ack_immediately
        assert "未提及的决策项全部按推荐。" in prompt
        assert "用户补充说明：" in prompt
        assert supplement_message.text in prompt
        assert await state.get_state() is None
        assert previews, "应回显推送预览"
        assert ack_calls, "应回显 session ack"

    asyncio.run(_scenario())


@pytest.mark.parametrize("input_text", ["跳过", "", None])
def test_quick_reply_partial_skip_sends_all_recommended(monkeypatch, tmp_path: Path, input_text):
    """补充阶段发送跳过/空消息时，应等价“全部按推荐”。"""

    origin = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_PARTIAL_CALLBACK, origin)
    state, _ = make_state(origin)

    recorded: list[str] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        recorded.append(prompt)
        return True, tmp_path / "session.jsonl"

    async def fake_preview(*_args, **_kwargs):
        return None

    async def fake_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    supplement_message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
    supplement_message.text = input_text

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        await bot.on_model_quick_reply_partial_supplement(supplement_message, state)
        assert recorded and recorded[-1] == "待决策项全部按模型推荐"
        assert await state.get_state() is None

    asyncio.run(_scenario())


def test_quick_reply_partial_cancel(monkeypatch):
    """补充阶段发送“取消”应退出流程且不推送到模型。"""

    origin = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_PARTIAL_CALLBACK, origin)
    state, _ = make_state(origin)

    async def fake_dispatch(*_args, **_kwargs):
        raise AssertionError("取消时不应触发推送")

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    supplement_message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
    supplement_message.text = "取消"

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        await bot.on_model_quick_reply_partial_supplement(supplement_message, state)
        assert await state.get_state() is None
        assert supplement_message.calls
        text, _, reply_markup, _ = supplement_message.calls[-1]
        assert "已取消" in text
        assert isinstance(reply_markup, ReplyKeyboardMarkup)

    asyncio.run(_scenario())
