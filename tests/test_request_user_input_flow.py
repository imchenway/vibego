from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

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

    def __init__(
        self,
        *,
        chat_id: int = 1,
        user_id: int = 1,
        text: str | None = None,
        caption: str | None = None,
    ) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester")
        self.message_id = 100
        self.text = text
        self.caption = caption
        self.calls: list[tuple[str, object, object, dict]] = []
        self.edited_reply_markup = 0

    async def answer(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id + len(self.calls), chat=self.chat)

    async def edit_reply_markup(self, reply_markup=None):
        self.edited_reply_markup += 1
        return None


def _make_saved_attachment(tmp_path: Path, name: str = "photo.jpg") -> bot.TelegramSavedAttachment:
    """构造 request_input 自定义决策可复用的附件对象。"""

    absolute_path = tmp_path / name
    absolute_path.write_bytes(b"fake-binary")
    return bot.TelegramSavedAttachment(
        kind="photo",
        display_name=name,
        mime_type="image/jpeg",
        absolute_path=absolute_path,
        relative_path=f"./data/telegram/demo/2026-03-09/{name}",
    )


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
    bot.PLAN_CONFIRM_SESSIONS.clear()
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS.clear()
    yield
    bot.REQUEST_INPUT_SESSIONS.clear()
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS.clear()
    bot.PLAN_CONFIRM_SESSIONS.clear()
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS.clear()


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
    assert not preview_calls, "提交后不应再发送中间推送代码块预览"
    assert ack_calls, "应发送 session ack"
    assert callback.message.calls, "提交成功后应回显决策摘要并恢复主菜单"
    summary_reply_markup = callback.message.calls[-1][2]
    assert isinstance(summary_reply_markup, ReplyKeyboardMarkup)
    assert callback.answers[-1] == ("已提交并推送到模型", False)
    assert session.token not in bot.REQUEST_INPUT_SESSIONS
    assert 10 not in bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS


def test_ask_user_submit_dispatches_schema_payload(monkeypatch, tmp_path: Path):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[
            bot.RequestInputOption(label="仅库存页", value="inventory"),
            bot.RequestInputOption(label="两页都改", value="all_pages"),
        ],
    )
    session = bot.RequestInputSession(
        token="token_submit_ask_user",
        chat_id=11,
        user_id=11,
        call_id="tool_ask_submit_1",
        tool_name="ask_user",
        session_key="session-key-ask-user",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        selected_option_indexes={"scope": 1},
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[11] = session.token

    dispatched: list[str] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        assert chat_id == 11
        dispatched.append(prompt)
        return True, tmp_path / "copilot-events.jsonl"

    async def fake_preview(*_args, **_kwargs):
        return None

    async def fake_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_SUBMIT),
        message=DummyMessage(chat_id=11, user_id=11),
        user_id=11,
    )

    asyncio.run(bot.on_request_user_input_callback(callback))

    assert dispatched, "提交后应推送到模型"
    prompt = dispatched[-1]
    assert "ask_user 工具结果" in prompt
    assert "call_id=tool_ask_submit_1" in prompt
    assert '{"scope":"all_pages"}' in prompt


def test_request_input_submit_dispatches_parallel_context(monkeypatch, tmp_path: Path):
    """并行会话里的 request_input 提交，应继续发回对应并行 CLI。"""

    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="库存页"), bot.RequestInputOption(label="两页")],
    )
    dispatch_context = bot.ParallelDispatchContext(
        task_id="TASK_0093",
        tmux_session="vibe-par-demo",
        pointer_file=tmp_path / "pointer.txt",
        workspace_root=tmp_path / "workspace",
    )
    session = bot.RequestInputSession(
        token="token_parallel_submit",
        chat_id=20,
        user_id=20,
        call_id="call_parallel_submit",
        session_key="parallel-session-key",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        selected_option_indexes={"scope": 1},
        parallel_task_id="TASK_0093",
        parallel_dispatch_context=dispatch_context,
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[20] = session.token

    captured_contexts: list[object] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, dispatch_context=None):
        assert chat_id == 20
        captured_contexts.append(dispatch_context)
        return True, tmp_path / "parallel.jsonl"

    async def fake_preview(*_args, **_kwargs):
        return None

    async def fake_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_SUBMIT),
        message=DummyMessage(chat_id=20, user_id=20),
        user_id=20,
    )

    asyncio.run(bot.on_request_user_input_callback(callback))

    assert captured_contexts == [dispatch_context]


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
    assert not message.calls, "未答完时仅提示，不额外发送跳题消息"


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


def test_request_input_keyboard_hides_submit_button():
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="A"), bot.RequestInputOption(label="B"), bot.RequestInputOption(label="C")],
    )
    session = bot.RequestInputSession(
        token="token_keyboard",
        chat_id=1,
        user_id=1,
        call_id="call_kb",
        session_key="s-kb",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )
    keyboard = bot._build_request_input_keyboard(session, question_index=0)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert any("D." in text for text in labels)
    assert not any("提交" in text for text in labels)


def test_request_input_option_auto_submits_when_all_answered(monkeypatch, tmp_path: Path):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_auto_submit",
        chat_id=88,
        user_id=88,
        call_id="call_auto_submit",
        session_key="s-auto",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[88] = session.token

    dispatched: list[str] = []
    preview_calls: list[str] = []
    ack_calls: list[str] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        assert chat_id == 88
        dispatched.append(prompt)
        return True, tmp_path / "auto_submit.jsonl"

    async def fake_preview(chat_id: int, preview_block: str, *, reply_to, parse_mode, reply_markup):
        preview_calls.append(preview_block)

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append(str(session_path))

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_OPTION, 1),
        message=DummyMessage(chat_id=88, user_id=88),
        user_id=88,
    )
    asyncio.run(bot.on_request_user_input_callback(callback))

    assert dispatched, "选项作答完成后应自动推送"
    assert '{"answers":{"scope":{"answers":["两页都改"]}}}' in dispatched[-1]
    assert callback.message.calls, "自动提交后应恢复主菜单"
    auto_summary_reply_markup = callback.message.calls[-1][2]
    assert isinstance(auto_summary_reply_markup, ReplyKeyboardMarkup)
    assert callback.answers[-1] == ("已自动推送到模型", False)
    assert not preview_calls, "自动提交后不应再发送中间推送代码块预览"
    assert ack_calls
    assert session.token not in bot.REQUEST_INPUT_SESSIONS


def test_request_input_custom_option_enters_text_mode():
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_custom_mode",
        chat_id=99,
        user_id=99,
        call_id="call_custom_mode",
        session_key="s-custom",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[99] = session.token
    message = DummyMessage(chat_id=99, user_id=99)
    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_CUSTOM),
        message=message,
        user_id=99,
    )

    asyncio.run(bot.on_request_user_input_callback(callback))

    assert session.input_mode_question_id == "scope"
    assert callback.answers[-1] == ("请发送自定义决策文本", False)
    assert message.calls
    assert "请发送第 1 题的自定义决策文本" in message.calls[-1][0]


def test_request_input_custom_option_claims_text_focus_token():
    """进入自定义输入模式后，应把文本输入焦点切到当前 token。"""

    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_custom_focus",
        chat_id=199,
        user_id=199,
        call_id="call_custom_focus",
        session_key="s-custom-focus",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    message = DummyMessage(chat_id=199, user_id=199)
    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_CUSTOM),
        message=message,
        user_id=199,
    )

    asyncio.run(bot.on_request_user_input_callback(callback))

    assert bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[199] == session.token


def test_request_input_old_callback_still_works_after_newer_session_created():
    """同 chat 下新问题出现后，旧问题按钮仍应可点，不应因“单活 token”提前失效。"""

    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    older = bot.RequestInputSession(
        token="token_older",
        chat_id=299,
        user_id=299,
        call_id="call_older",
        session_key="s-older",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )
    newer = bot.RequestInputSession(
        token="token_newer",
        chat_id=299,
        user_id=299,
        call_id="call_newer",
        session_key="s-newer",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )
    bot.REQUEST_INPUT_SESSIONS[older.token] = older
    bot.REQUEST_INPUT_SESSIONS[newer.token] = newer
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[299] = newer.token
    message = DummyMessage(chat_id=299, user_id=299)
    callback = DummyCallback(
        bot._build_request_input_callback_data(older.token, bot.REQUEST_INPUT_ACTION_CUSTOM),
        message=message,
        user_id=299,
    )

    asyncio.run(bot.on_request_user_input_callback(callback))

    assert callback.answers[-1] == ("请发送自定义决策文本", False)
    assert older.input_mode_question_id == "scope"
    assert bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[299] == older.token


def test_request_input_custom_text_auto_submits(monkeypatch, tmp_path: Path):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_custom_submit",
        chat_id=66,
        user_id=66,
        call_id="call_custom_submit",
        session_key="s-custom-submit",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        input_mode_question_id="scope",
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[66] = session.token

    dispatched: list[str] = []
    preview_calls: list[str] = []
    ack_calls: list[str] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        assert chat_id == 66
        dispatched.append(prompt)
        return True, tmp_path / "custom_submit.jsonl"

    async def fake_preview(chat_id: int, preview_block: str, *, reply_to, parse_mode, reply_markup):
        preview_calls.append(preview_block)

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append(str(session_path))

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    message = DummyMessage(chat_id=66, user_id=66, text="按现有逻辑先仅做归因")
    handled = asyncio.run(bot._handle_request_input_custom_text_message(message))

    assert handled is True
    assert dispatched, "输入自定义决策后应自动推送"
    assert '{"answers":{"scope":{"answers":["按现有逻辑先仅做归因"]}}}' in dispatched[-1]
    assert not preview_calls, "自定义文本自动提交后不应再发送中间推送代码块预览"
    assert ack_calls
    assert session.token not in bot.REQUEST_INPUT_SESSIONS
    assert message.calls, "自定义文本成功提交后应发送收口消息"
    assert isinstance(message.calls[-1][2], ReplyKeyboardMarkup)
    main_keyboard_rows = message.calls[-1][2].keyboard
    assert main_keyboard_rows
    assert main_keyboard_rows[0][0].text == bot.WORKER_MENU_BUTTON_TEXT


def test_request_input_custom_text_auto_submits_to_parallel_context(monkeypatch, tmp_path: Path):
    """并行会话里的自定义决策文本提交，应继续发回对应并行 CLI。"""

    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    dispatch_context = bot.ParallelDispatchContext(
        task_id="TASK_0093",
        tmux_session="vibe-par-demo",
        pointer_file=tmp_path / "pointer.txt",
        workspace_root=tmp_path / "workspace",
    )
    session = bot.RequestInputSession(
        token="token_parallel_custom_submit",
        chat_id=166,
        user_id=166,
        call_id="call_parallel_custom_submit",
        session_key="parallel-custom-submit",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        input_mode_question_id="scope",
        parallel_task_id="TASK_0093",
        parallel_dispatch_context=dispatch_context,
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[166] = session.token

    captured_contexts: list[object] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, dispatch_context=None):
        assert chat_id == 166
        captured_contexts.append(dispatch_context)
        return True, tmp_path / "parallel_custom.jsonl"

    async def fake_preview(*_args, **_kwargs):
        return None

    async def fake_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    message = DummyMessage(chat_id=166, user_id=166, text="按推荐，但保留例外")
    handled = asyncio.run(bot._handle_request_input_custom_text_message(message))

    assert handled is True
    assert captured_contexts == [dispatch_context]


def test_dispatch_prompt_to_model_does_not_drop_existing_request_input_session_for_same_chat(monkeypatch, tmp_path: Path):
    """同 chat 新一轮入模时，不应把尚未过期的 request_input 会话直接删掉。"""

    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_keep_alive",
        chat_id=399,
        user_id=399,
        call_id="call_keep_alive",
        session_key="s-keep-alive",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[399] = session.token

    pointer = tmp_path / "pointer.txt"
    new_session = tmp_path / "rollout-new.jsonl"
    new_session.write_text("", encoding="utf-8")
    pointer.write_text(str(new_session), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", pointer, raising=False)
    monkeypatch.setattr(bot, "_drop_chat_plan_confirm_session", lambda *_args, **_kwargs: None)
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
        ok, _session_path = await bot._dispatch_prompt_to_model(399, "继续处理", reply_to=None, ack_immediately=True)
        assert ok is True

    asyncio.run(_scenario())

    assert session.token in bot.REQUEST_INPUT_SESSIONS
    assert bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[399] == session.token

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def test_request_input_custom_media_message_auto_submits_with_attachment_prompt(monkeypatch, tmp_path: Path):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_custom_media_submit",
        chat_id=266,
        user_id=266,
        call_id="call_custom_media_submit",
        session_key="s-custom-media-submit",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        input_mode_question_id="scope",
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[266] = session.token

    dispatched: list[str] = []
    ack_calls: list[str] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        assert chat_id == 266
        dispatched.append(prompt)
        return True, tmp_path / "custom_media_submit.jsonl"

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append(str(session_path))

    async def fake_collect(*_args, **_kwargs):
        return [_make_saved_attachment(tmp_path)]

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)

    message = DummyMessage(chat_id=266, user_id=266, caption="请按图片里的方案调整")
    message.photo = [SimpleNamespace(file_id="p1")]
    handled = asyncio.run(bot._handle_request_input_custom_text_message(message))

    assert handled is True
    assert dispatched, "图文混合自定义决策应自动推送"
    prompt = dispatched[-1]
    assert "请按图片里的方案调整" in prompt
    assert "附件列表（文件位于项目工作目录" in prompt
    assert "./data/telegram/demo/2026-03-09/photo.jpg" in prompt
    assert ack_calls


def test_request_input_custom_media_only_auto_submits_with_attachment_prompt(monkeypatch, tmp_path: Path):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_custom_media_only_submit",
        chat_id=366,
        user_id=366,
        call_id="call_custom_media_only_submit",
        session_key="s-custom-media-only-submit",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        input_mode_question_id="scope",
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[366] = session.token

    dispatched: list[str] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        assert chat_id == 366
        dispatched.append(prompt)
        return True, None

    async def fake_collect(*_args, **_kwargs):
        return [_make_saved_attachment(tmp_path, "diagram.jpg")]

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_collect_saved_attachments", fake_collect)
    monkeypatch.setattr(bot, "_attachment_dir_for_message", lambda *_args, **_kwargs: tmp_path)

    message = DummyMessage(chat_id=366, user_id=366, caption=None)
    message.photo = [SimpleNamespace(file_id="p2")]
    handled = asyncio.run(bot._handle_request_input_custom_text_message(message))

    assert handled is True
    assert dispatched, "纯附件自定义决策应自动推送"
    prompt = dispatched[-1]
    assert "附件列表（文件位于项目工作目录" in prompt
    assert "./data/telegram/demo/2026-03-09/diagram.jpg" in prompt


def test_request_input_custom_text_submit_failure_restores_main_keyboard(monkeypatch):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_custom_submit_fail",
        chat_id=166,
        user_id=166,
        call_id="call_custom_submit_fail",
        session_key="s-custom-submit-fail",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        input_mode_question_id="scope",
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[166] = session.token

    async def fake_dispatch(*_args, **_kwargs):
        return False, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    message = DummyMessage(chat_id=166, user_id=166, text="继续按现有逻辑")
    handled = asyncio.run(bot._handle_request_input_custom_text_message(message))

    assert handled is True
    assert session.submission_state == "failed"
    assert message.calls, "失败后应提示重试并恢复主菜单"
    assert isinstance(message.calls[0][2], InlineKeyboardMarkup), "首条应保留重试提交按钮"
    assert isinstance(message.calls[-1][2], ReplyKeyboardMarkup), "最后一条应恢复主菜单"
    assert message.calls[-1][2].keyboard[0][0].text == bot.WORKER_MENU_BUTTON_TEXT


def test_request_input_submission_summary_keeps_full_custom_text():
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    custom_text = "这是一个很长的自定义决策说明，用于确认摘要不能再被六十字符裁切。" * 3
    session = bot.RequestInputSession(
        token="token_summary_full",
        chat_id=466,
        user_id=466,
        call_id="call_summary_full",
        session_key="s-summary-full",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        selected_option_indexes={"scope": bot.REQUEST_INPUT_CUSTOM_OPTION_INDEX},
        custom_answers={"scope": custom_text},
    )

    summary = bot._build_request_input_submission_summary(session)

    assert custom_text in summary
    assert "…" not in summary


def test_request_input_submit_falls_back_to_long_text_attachment_without_truncation(monkeypatch, tmp_path: Path):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    custom_text = "超长自定义说明" * 500
    session = bot.RequestInputSession(
        token="token_summary_attachment",
        chat_id=566,
        user_id=566,
        call_id="call_summary_attachment",
        session_key="s-summary-attachment",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        selected_option_indexes={"scope": bot.REQUEST_INPUT_CUSTOM_OPTION_INDEX},
        custom_answers={"scope": custom_text},
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[566] = session.token

    fallback_calls: list[tuple[str, object, object]] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True):
        assert chat_id == 566
        return True, tmp_path / "custom_summary_attachment.jsonl"

    async def fake_reply(*_args, **_kwargs):
        raise TelegramBadRequest(method="sendMessage", message="Bad Request: message is too long")

    async def fake_reply_large_text(chat_id: int, text: str, *, parse_mode=None, preformatted=False, reply_markup=None, attachment_reply_markup=None):
        fallback_calls.append((text, reply_markup, attachment_reply_markup))
        return text

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)
    monkeypatch.setattr(bot, "reply_large_text", fake_reply_large_text)
    monkeypatch.setattr(bot, "_send_session_ack", lambda *args, **kwargs: asyncio.sleep(0))

    success = asyncio.run(
        bot._submit_request_input_session(
            session,
            reply_to=DummyMessage(chat_id=566, user_id=566, text="超长说明"),
            actor_user_id=566,
            remove_reply_keyboard=True,
        )
    )

    assert success is True
    assert fallback_calls, "超长摘要应走 reply_large_text 降级，而不是裁切"
    assert custom_text in fallback_calls[-1][0]


def test_request_input_custom_text_cancel_returns_question(monkeypatch):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_custom_cancel",
        chat_id=77,
        user_id=77,
        call_id="call_custom_cancel",
        session_key="s-custom-cancel",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        input_mode_question_id="scope",
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[77] = session.token

    sent_questions: list[str] = []

    async def fake_send_question(current_session, *, reply_to):
        sent_questions.append(current_session.token)
        return True

    monkeypatch.setattr(bot, "_send_request_input_question", fake_send_question)

    message = DummyMessage(chat_id=77, user_id=77, text="取消")
    handled = asyncio.run(bot._handle_request_input_custom_text_message(message))

    assert handled is True
    assert session.input_mode_question_id is None
    assert sent_questions == [session.token]
    assert session.token in bot.REQUEST_INPUT_SESSIONS


def test_request_input_keyboard_includes_question_index_in_callback_data():
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="A"), bot.RequestInputOption(label="B")],
    )
    session = bot.RequestInputSession(
        token="token_cb_index",
        chat_id=1,
        user_id=1,
        call_id="call_cb_index",
        session_key="s-cb-index",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )

    keyboard = bot._build_request_input_keyboard(session, question_index=0)
    first_button = keyboard.inline_keyboard[0][0]
    custom_button = keyboard.inline_keyboard[-1][0]
    assert first_button.callback_data == f"{bot.REQUEST_INPUT_CALLBACK_PREFIX.rstrip(':')}:{session.token}:{bot.REQUEST_INPUT_ACTION_OPTION}:0:0"
    assert custom_button.callback_data == f"{bot.REQUEST_INPUT_CALLBACK_PREFIX.rstrip(':')}:{session.token}:{bot.REQUEST_INPUT_ACTION_CUSTOM}:0"


def test_request_input_repeated_click_same_question_is_locked(monkeypatch):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_locked",
        chat_id=188,
        user_id=188,
        call_id="call_locked",
        session_key="s-locked",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
        selected_option_indexes={"scope": 0},
        question_message_ids={"scope": 100},
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[188] = session.token

    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_OPTION, 0, 1),
        message=DummyMessage(chat_id=188, user_id=188),
        user_id=188,
    )

    asyncio.run(bot.on_request_user_input_callback(callback))
    assert callback.answers[-1] == ("第 1 题已锁定，不可修改。", True)
    assert session.selected_option_indexes["scope"] == 0


def test_request_input_auto_submit_failure_sends_retry_button(monkeypatch):
    question = bot.RequestInputQuestion(
        question_id="scope",
        question="请选择范围",
        options=[bot.RequestInputOption(label="仅库存页"), bot.RequestInputOption(label="两页都改")],
    )
    session = bot.RequestInputSession(
        token="token_retry",
        chat_id=208,
        user_id=208,
        call_id="call_retry",
        session_key="s-retry",
        questions=[question],
        current_index=0,
        created_at=time.monotonic(),
        expires_at=time.monotonic() + 600,
    )
    bot.REQUEST_INPUT_SESSIONS[session.token] = session
    bot.CHAT_ACTIVE_REQUEST_INPUT_TOKENS[208] = session.token

    async def fake_dispatch(*_args, **_kwargs):
        return False, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    message = DummyMessage(chat_id=208, user_id=208)
    callback = DummyCallback(
        bot._build_request_input_callback_data(session.token, bot.REQUEST_INPUT_ACTION_OPTION, 0, 1),
        message=message,
        user_id=208,
    )
    asyncio.run(bot.on_request_user_input_callback(callback))

    assert session.submission_state == "failed"
    assert session.submit_retry_count == 1
    assert callback.answers[-1] == ("自动提交失败，可点击“重试提交”继续。", True)
    assert message.calls, "应发送重试提交提示"
    retry_markup = message.calls[0][2]
    assert isinstance(retry_markup, InlineKeyboardMarkup)
    retry_button = retry_markup.inline_keyboard[0][0]
    assert retry_button.callback_data == f"{bot.REQUEST_INPUT_CALLBACK_PREFIX.rstrip(':')}:{session.token}:{bot.REQUEST_INPUT_ACTION_RETRY_SUBMIT}"
    assert isinstance(message.calls[-1][2], ReplyKeyboardMarkup), "失败兜底后应恢复主菜单"
