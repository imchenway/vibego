from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardMarkup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ACTIVE_MODEL", "codex")

import bot  # noqa: E402


class DummyBot:
    """用于捕获 send_message 调用。"""

    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, chat_id: int, text: str, parse_mode=None, disable_notification: bool = False, reply_markup=None):
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
                "reply_markup": reply_markup,
            }
        )
        return SimpleNamespace(message_id=len(self.sent_messages), chat=SimpleNamespace(id=chat_id))


class DummyMessage:
    """模拟 callback.message，支持 answer。"""

    def __init__(self, *, chat_id: int = 1, user_id: int = 1) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester")
        self.message_id = 100
        self.calls: list[tuple[str, object, object, dict]] = []

    async def answer(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id + len(self.calls), chat=self.chat)


class DummyCallback:
    """模拟 callback query。"""

    def __init__(self, data: str, *, message: DummyMessage, user_id: int = 1) -> None:
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(id=user_id, full_name=f"U{user_id}")
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


def _build_request_input_event(*, call_id: str = "call_123") -> dict:
    args = {
        "questions": [
            {
                "id": "scope",
                "header": "定位范围",
                "question": "请选择范围",
                "options": [
                    {"label": "仅库存页", "description": "只改库存页"},
                    {"label": "两页都改", "description": "统一改造"},
                ],
            }
        ]
    }
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "request_user_input",
            "call_id": call_id,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


@pytest.fixture(autouse=True)
def _reset_runtime(monkeypatch):
    monkeypatch.setattr(bot, "ACTIVE_MODEL", "codex")
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "codex")
    monkeypatch.setattr(bot, "ENABLE_REQUEST_USER_INPUT_UI", True)
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
    yield
    bot.REQUEST_INPUT_SESSIONS.clear()
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS.clear()


def test_extract_request_user_input_payload():
    event = _build_request_input_event()
    result = bot._extract_deliverable_payload(event, event_timestamp=None)
    assert result is not None
    kind, text, metadata = result
    assert kind == bot.DELIVERABLE_KIND_REQUEST_INPUT
    assert "模型请求你补充决策" in text
    assert metadata is not None
    assert metadata["call_id"] == "call_123"
    assert metadata["questions"][0]["id"] == "scope"


def test_deliver_pending_messages_request_input_sends_keyboard(tmp_path: Path):
    chat_id = 42
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(json.dumps(_build_request_input_event(), ensure_ascii=False) + "\n", encoding="utf-8")
    bot.SESSION_OFFSETS[str(session_file)] = 0
    bot.CHAT_ACTIVE_USERS[chat_id] = 9527
    dummy_bot = DummyBot()
    bot._bot = dummy_bot

    delivered = asyncio.run(bot._deliver_pending_messages(chat_id, session_file))

    assert delivered is True
    assert dummy_bot.sent_messages, "应向 Telegram 发送 request_user_input 交互题目"
    payload = dummy_bot.sent_messages[-1]
    assert "模型请求补充决策" in payload["text"]
    assert isinstance(payload["reply_markup"], InlineKeyboardMarkup)
    first_button = payload["reply_markup"].inline_keyboard[0][0]
    assert first_button.callback_data.startswith(bot.REQUEST_INPUT_CALLBACK_PREFIX)
    assert bot.REQUEST_INPUT_SESSIONS
    session = next(iter(bot.REQUEST_INPUT_SESSIONS.values()))
    assert session.user_id == 9527


def test_request_input_callback_rejects_non_owner():
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="A"), bot.RequestInputOption(label="B")],
    )
    session = bot.RequestInputSession(
        token="token123",
        chat_id=1,
        user_id=100,
        call_id="call_x",
        session_key="s1",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[1] = session.token
    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_OPTION, 0),
        message=DummyMessage(chat_id=1, user_id=100),
        user_id=200,
    )

    asyncio.run(bot.on_request_user_input_callback(callback))

    assert callback.answers[-1] == ("仅会话发起人可操作该按钮。", True)
    assert session.token in bot.REQUEST_INPUT_SESSIONS


def test_request_input_submit_dispatches_structured_payload(monkeypatch, tmp_path: Path):
    questions = [
        bot.RequestInputQuestion(
            question_id="scope",
            question="请选择范围",
            options=[bot.RequestInputOption(label="库存页"), bot.RequestInputOption(label="两页")],
        ),
        bot.RequestInputQuestion(
            question_id="style",
            question="请选择风格",
            options=[bot.RequestInputOption(label="胶囊"), bot.RequestInputOption(label="下拉")],
        ),
    ]
    session = bot.RequestInputSession(
        token="token_submit",
        chat_id=10,
        user_id=10,
        call_id="call_submit_1",
        session_key="session-key",
        questions=questions,
        current_index=1,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        selected_option_indexes={"scope": 1, "style": 0},
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[10] = session.token

    dispatched: list[str] = []
    preview_calls: list[str] = []
    ack_calls: list[str] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        assert chat_id == 10
        dispatched.append(prompt)
        return True, tmp_path / "rollout.jsonl"

    async def fake_preview(chat_id: int, preview_block: str, *, reply_to, parse_mode, reply_markup):
        preview_calls.append(preview_block)

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append(str(session_path))

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_SUBMIT),
        message=DummyMessage(chat_id=10, user_id=10),
        user_id=10,
    )

    asyncio.run(bot.on_request_user_input_callback(callback))

    assert dispatched, "提交后应推送到模型"
    prompt = dispatched[-1]
    assert "call_id=call_submit_1" in prompt
    assert '{"answers":{"scope":{"answers":["两页"]},"style":{"answers":["胶囊"]}}}' in prompt
    assert preview_calls, "应回显推送预览"
    assert ack_calls, "应发送 session ack"
    assert callback.answers[-1] == ("已提交并推送到模型", False)
    assert session.token not in bot.REQUEST_INPUT_SESSIONS
    assert 10 not in bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS


def test_request_input_submit_requires_all_answers(monkeypatch):
    question_scope = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="库存页"), bot.RequestInputOption(label="两页")],
    )
    question_style = bot.RequestInputQuestion(
        question_id="style",
        question="请选择风格",
        options=[bot.RequestInputOption(label="胶囊"), bot.RequestInputOption(label="下拉")],
    )
    session = bot.RequestInputSession(
        token="token_need_all",
        chat_id=33,
        user_id=33,
        call_id="call_need_all",
        session_key="s",
        questions=[question_scope, question_style],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        selected_option_indexes={"scope": 0},
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[33] = session.token

    async def fake_dispatch(*_args, **_kwargs):
        raise AssertionError("未答完时不应触发推送")

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    message = DummyMessage(chat_id=33, user_id=33)
    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_SUBMIT),
        message=message,
        user_id=33,
    )
    asyncio.run(bot.on_request_user_input_callback(callback))

    assert callback.answers[-1] == ("请先完成全部题目后再提交。", True)
    assert message.calls, "应引导到未完成题目"
    assert "请选择风格" in message.calls[-1][0]


def test_request_input_callback_expired_session():
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="A"), bot.RequestInputOption(label="B")],
    )
    session = bot.RequestInputSession(
        token="token_expired",
        chat_id=77,
        user_id=77,
        call_id="call_x",
        session_key="s1",
        questions=[question],
        current_index=0,
        created_at=time.monotonic() - 1000,
        expires_at=time.monotonic() - 1,
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[77] = session.token
    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_OPTION, 0),
        message=DummyMessage(chat_id=77, user_id=77),
        user_id=77,
    )

    asyncio.run(bot.on_request_user_input_callback(callback))

    assert callback.answers[-1] == ("该交互已失效，请重新触发。", True)
    assert session.token not in bot.REQUEST_INPUT_SESSIONS
