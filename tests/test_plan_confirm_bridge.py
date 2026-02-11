from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ACTIVE_MODEL", "codex")

import bot  # noqa: E402


class DummyMessage:
    """模拟 callback.message。"""

    def __init__(self, *, chat_id: int = 1) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.edited_reply_markup = False

    async def edit_reply_markup(self, reply_markup=None):
        self.edited_reply_markup = True


class DummyCallback:
    """模拟 callback query。"""

    def __init__(self, data: str, *, message: DummyMessage, user_id: int = 1) -> None:
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(id=user_id, full_name=f"U{user_id}")
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


@pytest.fixture(autouse=True)
def _reset_runtime():
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_SESSION_MAP.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_PLAN_MESSAGES.clear()
    bot.CHAT_PLAN_TEXT.clear()
    bot.CHAT_PLAN_COMPLETION.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()
    bot.CHAT_REPLY_COUNT.clear()
    bot.CHAT_COMPACT_STATE.clear()
    bot.CHAT_ACTIVE_USERS.clear()
    bot.REQUEST_INPUT_SESSIONS.clear()
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS.clear()
    bot.PLAN_CONFIRM_SESSIONS.clear()
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS.clear()
    yield
    bot.PLAN_CONFIRM_SESSIONS.clear()
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS.clear()


def _build_assistant_message_event(text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def test_deliver_pending_messages_triggers_plan_confirm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    chat_id = 88
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        json.dumps(_build_assistant_message_event("<proposed_plan>\nhello\n</proposed_plan>"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    bot.SESSION_OFFSETS[str(session_file)] = 0

    async def fake_reply_large_text(chat_id: int, text: str, **kwargs):
        return text

    async def fake_handle_model_response(**kwargs):
        return None

    async def fake_post_delivery(*args, **kwargs):
        return None

    confirm_calls: list[tuple[int, str]] = []

    async def fake_plan_confirm(chat_id: int, session_key: str):
        confirm_calls.append((chat_id, session_key))
        return True

    monkeypatch.setattr(bot, "reply_large_text", fake_reply_large_text)
    monkeypatch.setattr(bot, "_handle_model_response", fake_handle_model_response)
    monkeypatch.setattr(bot, "_post_delivery_compact_checks", fake_post_delivery)
    monkeypatch.setattr(bot, "_maybe_send_plan_confirm_prompt", fake_plan_confirm)

    delivered = asyncio.run(bot._deliver_pending_messages(chat_id, session_file))

    assert delivered is True
    assert confirm_calls == [(chat_id, str(session_file))]


def test_deliver_pending_messages_skips_plan_confirm_for_normal_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    chat_id = 99
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        json.dumps(_build_assistant_message_event("普通输出，不含计划块"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    bot.SESSION_OFFSETS[str(session_file)] = 0

    async def fake_reply_large_text(chat_id: int, text: str, **kwargs):
        return text

    async def fake_handle_model_response(**kwargs):
        return None

    async def fake_post_delivery(*args, **kwargs):
        return None

    confirm_calls: list[tuple[int, str]] = []

    async def fake_plan_confirm(chat_id: int, session_key: str):
        confirm_calls.append((chat_id, session_key))
        return True

    monkeypatch.setattr(bot, "reply_large_text", fake_reply_large_text)
    monkeypatch.setattr(bot, "_handle_model_response", fake_handle_model_response)
    monkeypatch.setattr(bot, "_post_delivery_compact_checks", fake_post_delivery)
    monkeypatch.setattr(bot, "_maybe_send_plan_confirm_prompt", fake_plan_confirm)

    delivered = asyncio.run(bot._deliver_pending_messages(chat_id, session_file))

    assert delivered is True
    assert not confirm_calls


def test_plan_confirm_yes_dispatches_implement_prompt(monkeypatch: pytest.MonkeyPatch):
    chat_id = 123
    token = "tokyes"
    session = bot.PlanConfirmSession(
        token=token,
        chat_id=chat_id,
        session_key="session-key",
        user_id=9,
        created_at=time.monotonic(),
    )
    bot.PLAN_CONFIRM_SESSIONS[token] = session
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] = token

    dispatched: list[tuple[int, str]] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, intended_mode=None):
        dispatched.append((chat_id, prompt))
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    callback = DummyCallback(
        bot._build_plan_confirm_callback_data(token, bot.PLAN_CONFIRM_ACTION_YES),
        message=DummyMessage(chat_id=chat_id),
        user_id=9,
    )
    asyncio.run(bot.on_plan_confirm_callback(callback))

    assert dispatched == [(chat_id, bot.PLAN_IMPLEMENT_PROMPT)]
    assert token not in bot.PLAN_CONFIRM_SESSIONS
    assert chat_id not in bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS
    assert callback.answers[-1] == ("已确认并推送到模型", False)


def test_plan_confirm_no_keeps_plan_mode(monkeypatch: pytest.MonkeyPatch):
    chat_id = 124
    token = "tokno"
    session = bot.PlanConfirmSession(
        token=token,
        chat_id=chat_id,
        session_key="session-key",
        user_id=11,
        created_at=time.monotonic(),
    )
    bot.PLAN_CONFIRM_SESSIONS[token] = session
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] = token

    dispatched: list[tuple[int, str]] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, intended_mode=None):
        dispatched.append((chat_id, prompt))
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    callback = DummyCallback(
        bot._build_plan_confirm_callback_data(token, bot.PLAN_CONFIRM_ACTION_NO),
        message=DummyMessage(chat_id=chat_id),
        user_id=11,
    )
    asyncio.run(bot.on_plan_confirm_callback(callback))

    assert not dispatched
    assert token not in bot.PLAN_CONFIRM_SESSIONS
    assert chat_id not in bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS
    assert callback.answers[-1] == ("已保持 Plan 模式", False)
