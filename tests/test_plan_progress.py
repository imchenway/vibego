import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional
from types import SimpleNamespace

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test-token")

import bot


class DummyBot:
    def __init__(self):
        self.sent_messages: list[tuple[int, str, Optional[str], bool]] = []
        self.edited_messages: list[tuple[int, int, str, Optional[str]]] = []
        self.deleted_messages: list[tuple[int, int]] = []
        self.delete_error: Optional[Exception] = None

    async def send_message(self, chat_id: int, text: str, parse_mode=None, disable_notification: bool = False):
        message_id = len(self.sent_messages) + 1
        mode_value = parse_mode if parse_mode is None else str(parse_mode)
        self.sent_messages.append((chat_id, text, mode_value, disable_notification))
        return SimpleNamespace(message_id=message_id)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, parse_mode=None):
        mode_value = parse_mode if parse_mode is None else str(parse_mode)
        self.edited_messages.append((chat_id, message_id, text, mode_value))

    async def delete_message(self, chat_id: int, message_id: int):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted_messages.append((chat_id, message_id))


class DummyDocumentBot(DummyBot):
    def __init__(self):
        super().__init__()
        self.sent_documents: list[tuple[int, BufferedInputFile, Optional[str], Optional[str]]] = []

    async def send_document(self, chat_id: int, document, caption=None, parse_mode=None):
        self.sent_documents.append((chat_id, document, caption, parse_mode))
        return SimpleNamespace(file_id="dummy")


@pytest.fixture()
def plan_test_env(monkeypatch, tmp_path):
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_SESSION_MAP.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_PLAN_MESSAGES.clear()
    bot.CHAT_PLAN_TEXT.clear()
    bot.CHAT_PLAN_COMPLETION.clear()
    dummy = DummyBot()
    bot._bot = dummy
    bot.ENABLE_PLAN_PROGRESS = True
    bot.PLAN_PROGRESS_PARSE_MODE = None
    replies: list[tuple[int, str]] = []

    async def fake_reply(
        chat_id: int,
        text: str,
        *,
        parse_mode=None,
        preformatted: bool = False,
        reply_markup=None,
        attachment_reply_markup=None,
    ):
        replies.append((chat_id, text))
        return text

    async def fake_notify(chat_id: int):
        return None

    monkeypatch.setattr(bot, "reply_large_text", fake_reply)
    monkeypatch.setattr(bot, "_notify_send_failure_message", fake_notify)

    session_file = tmp_path / "session.jsonl"
    session_file.write_text("", encoding="utf-8")

    def append_events(events: list[dict]):
        with session_file.open("a", encoding="utf-8") as handler:
            for event in events:
                handler.write(json.dumps(event) + "\n")

    return {
        "dummy_bot": dummy,
        "replies": replies,
        "session": session_file,
        "append_events": append_events,
    }


@pytest.fixture()
def reply_bot_env():
    original_bot = bot._bot
    dummy = DummyDocumentBot()
    bot._bot = dummy
    try:
        yield dummy
    finally:
        bot._bot = original_bot


def _plan_event(status: str, explanation: str = "同步进度") -> dict:
    arguments = json.dumps(
        {
            "explanation": explanation,
            "plan": [
                {
                    "step": "处理任务",
                    "status": status,
                }
            ],
        }
    )
    return {
        "timestamp": "2025-01-01T00:00:00Z",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "update_plan",
            "arguments": arguments,
        },
    }


def _final_event(message: str) -> dict:
    return {
        "timestamp": "2025-01-01T00:00:10Z",
        "type": "response_item",
        "payload": {
            "type": "assistant_message",
            "message": message,
        },
    }


def test_plan_incomplete_keeps_watcher(plan_test_env):
    env = plan_test_env
    chat_id = 101
    env["append_events"]([
        _plan_event("in_progress"),
        _final_event("处理完成"),
    ])
    bot.SESSION_OFFSETS[str(env["session"])] = 0

    result = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert result is False
    assert env["dummy_bot"].sent_messages  # plan消息已发送
    assert env["dummy_bot"].sent_messages[-1][3] is True
    assert env["replies"]  # 最终回复已返回
    assert bot.CHAT_PLAN_COMPLETION[chat_id] is False
    assert chat_id in bot.CHAT_PLAN_TEXT


def test_plan_complete_after_final_message(plan_test_env):
    env = plan_test_env
    chat_id = 202
    env["append_events"]([
        _plan_event("in_progress"),
        _final_event("初步完成"),
    ])
    bot.SESSION_OFFSETS[str(env["session"])] = 0
    first = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))
    assert first is False

    env["append_events"]([
        _plan_event("completed", "最终收尾"),
    ])

    second = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert second is True
    assert env["dummy_bot"].sent_messages  # 已发送过计划
    assert env["dummy_bot"].edited_messages  # 最终完成触发编辑
    assert chat_id not in bot.CHAT_PLAN_TEXT
    assert chat_id not in bot.CHAT_PLAN_COMPLETION


def test_plan_completed_without_final_response(plan_test_env):
    env = plan_test_env
    chat_id = 303
    env["append_events"]([
        _plan_event("completed"),
    ])
    bot.SESSION_OFFSETS[str(env["session"])] = 0

    result = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert result is False
    assert env["dummy_bot"].sent_messages  # 计划消息已发送
    assert not env["replies"]  # 尚未发送最终回复
    assert bot.CHAT_PLAN_COMPLETION[chat_id] is True


def test_plan_disabled_falls_back(monkeypatch, plan_test_env):
    env = plan_test_env
    chat_id = 404
    monkeypatch.setattr(bot, "ENABLE_PLAN_PROGRESS", False)
    env["append_events"]([
        _plan_event("in_progress"),
        _final_event("已关闭计划"),
    ])
    bot.SESSION_OFFSETS[str(env["session"])] = 0

    result = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert result is True
    assert not env["dummy_bot"].sent_messages  # 未启用计划时不会发送计划消息
    assert env["replies"]  # 正常返回最终响应


def test_plan_metadata_missing_treated_incomplete(plan_test_env):
    env = plan_test_env
    chat_id = 505
    event = _plan_event("in_progress")
    # 移除 status 字段模拟模型缺省
    event["payload"]["arguments"] = json.dumps({
        "plan": [
            {
                "step": "处理任务",
            }
        ]
    })
    env["append_events"]([event, _final_event("继续等待")])
    bot.SESSION_OFFSETS[str(env["session"])] = 0

    result = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert result is False
    assert bot.CHAT_PLAN_COMPLETION[chat_id] is False


def test_plan_edit_flow_keeps_waiting(plan_test_env):
    env = plan_test_env
    chat_id = 606
    env["append_events"]([_plan_event("in_progress")])
    bot.SESSION_OFFSETS[str(env["session"])] = 0
    first = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))
    assert first is False

    env["append_events"]([
        _plan_event("in_progress", "追加检查"),
    ])
    second = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert second is False
    assert env["dummy_bot"].sent_messages
    assert env["dummy_bot"].edited_messages  # 重复进度触发编辑
    assert chat_id in bot.CHAT_PLAN_TEXT


def test_plan_progress_uses_plain_text_by_default(plan_test_env):
    env = plan_test_env
    chat_id = 6161

    asyncio.run(bot._update_plan_progress(chat_id, "计划内容", plan_completed=False))

    assert env["dummy_bot"].sent_messages
    assert env["dummy_bot"].sent_messages[-1][2] is None


def test_plan_progress_edit_plain_text(plan_test_env):
    env = plan_test_env
    chat_id = 6262
    bot.CHAT_PLAN_MESSAGES[chat_id] = 1
    bot.CHAT_PLAN_TEXT[chat_id] = "旧文本"

    asyncio.run(bot._update_plan_progress(chat_id, "新文本", plan_completed=False))

    assert env["dummy_bot"].edited_messages
    assert env["dummy_bot"].edited_messages[-1][3] is None


def test_plan_progress_honors_custom_parse_mode(monkeypatch, plan_test_env):
    env = plan_test_env
    chat_id = 6363
    monkeypatch.setattr(bot, "PLAN_PROGRESS_PARSE_MODE", ParseMode.MARKDOWN)

    asyncio.run(bot._update_plan_progress(chat_id, "**计划内容**", plan_completed=False))

    assert env["dummy_bot"].sent_messages
    assert env["dummy_bot"].sent_messages[-1][2] == ParseMode.MARKDOWN.value


def test_plan_finalization_clears_state(plan_test_env):
    env = plan_test_env
    chat_id = 707
    env["append_events"]([
        _plan_event("in_progress"),
        _final_event("处理中"),
    ])
    bot.SESSION_OFFSETS[str(env["session"])] = 0
    asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    env["append_events"]([
        _plan_event("completed"),
    ])
    asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert chat_id not in bot.CHAT_PLAN_TEXT
    assert chat_id not in bot.CHAT_PLAN_MESSAGES
    assert chat_id not in bot.CHAT_PLAN_COMPLETION


def test_update_plan_progress_send_failure(monkeypatch, plan_test_env):
    from aiogram.exceptions import TelegramBadRequest

    class FailingBot(DummyBot):
        async def send_message(
            self,
            chat_id: int,
            text: str,
            parse_mode=None,
            disable_notification: bool = False,
        ):
            raise TelegramBadRequest(method="sendMessage", message="bad request")

    env = plan_test_env
    bot._bot = FailingBot()

    result = asyncio.run(bot._update_plan_progress(808, "计划内容", plan_completed=False))

    assert result is False
    assert 808 not in bot.CHAT_PLAN_TEXT


def test_update_plan_progress_edit_failure(plan_test_env):
    from aiogram.exceptions import TelegramBadRequest

    class EditFailBot(DummyBot):
        async def edit_message_text(self, chat_id: int, message_id: int, text: str, parse_mode=None):
            raise TelegramBadRequest(method="editMessageText", message="bad request")

    env = plan_test_env
    chat_id = 909
    bot.CHAT_PLAN_MESSAGES[chat_id] = 1
    bot.CHAT_PLAN_TEXT[chat_id] = "旧文本"
    bot.CHAT_PLAN_COMPLETION[chat_id] = False
    bot._bot = EditFailBot()

    result = asyncio.run(bot._update_plan_progress(chat_id, "新文本", plan_completed=False))

    assert result is False
    assert chat_id not in bot.CHAT_PLAN_TEXT
    assert chat_id not in bot.CHAT_PLAN_MESSAGES


def test_final_message_without_plan_returns_true(plan_test_env):
    env = plan_test_env
    chat_id = 1001
    env["append_events"]([
        _final_event("直接完成"),
    ])
    bot.SESSION_OFFSETS[str(env["session"])] = 0

    result = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert result is True
    assert not env["dummy_bot"].sent_messages
    assert env["replies"]


def test_final_message_same_text_new_session(plan_test_env):
    env = plan_test_env
    chat_id = 1111
    text = "重复文本"

    env["append_events"]([
        _final_event(text),
    ])
    first_session = env["session"]
    bot.SESSION_OFFSETS[str(first_session)] = 0

    first_result = asyncio.run(bot._deliver_pending_messages(chat_id, first_session))

    assert first_result is True
    assert env["replies"]
    assert env["replies"][-1][0] == chat_id
    assert env["replies"][-1][1].endswith(text)

    second_session = first_session.parent / "session2.jsonl"
    second_session.write_text(json.dumps(_final_event(text)) + "\n", encoding="utf-8")
    bot.SESSION_OFFSETS[str(second_session)] = 0

    before = len(env["replies"])
    second_result = asyncio.run(bot._deliver_pending_messages(chat_id, second_session))

    assert second_result is True
    assert len(env["replies"]) == before + 1
    assert env["replies"][-1][0] == chat_id
    assert env["replies"][-1][1].endswith(text)
def test_duplicate_messages_sent_once(plan_test_env):
    env = plan_test_env
    chat_id = 1404
    message_text = "重复测试"
    events = [_final_event(message_text) for _ in range(3)]
    env["append_events"](events)
    bot.SESSION_OFFSETS[str(env["session"])] = 0

    result = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert result is True
    assert len(env["replies"]) == 1
    assert env["replies"][0][1].endswith(message_text)


def test_clear_last_message_allows_new_delivery(plan_test_env):
    env = plan_test_env
    chat_id = 1505
    session_key = str(env["session"])
    bot.SESSION_OFFSETS[session_key] = 0
    bot.CHAT_LAST_MESSAGE.setdefault(chat_id, {})[session_key] = "旧消息"

    bot._clear_last_message(chat_id)

    result = asyncio.run(bot._deliver_pending_messages(chat_id, env["session"]))

    assert result is False
    assert bot._get_last_message(chat_id, session_key) is None


def test_reply_large_text_attachment(reply_bot_env):
    chat_id = 1901
    long_text = f"{bot.MODEL_COMPLETION_PREFIX}\n\n" + ("内容较长\n" * (bot.TELEGRAM_MESSAGE_LIMIT // 3 + 10))

    delivered = asyncio.run(bot.reply_large_text(chat_id, long_text))

    assert reply_bot_env.sent_messages, "应发送摘要提示"
    summary = reply_bot_env.sent_messages[-1][1]
    assert "附件" in summary
    assert delivered == summary

    assert reply_bot_env.sent_documents, "应发送附件"
    doc_chat_id, buffered_file, caption, parse_mode = reply_bot_env.sent_documents[-1]
    assert doc_chat_id == chat_id
    assert isinstance(buffered_file, BufferedInputFile)
    assert buffered_file.filename.endswith(".md")
    assert buffered_file.data.decode("utf-8") == long_text
    assert caption is None
    assert parse_mode is None


def test_reply_large_text_short_message(reply_bot_env):
    chat_id = 1902
    short_text = f"{bot.MODEL_COMPLETION_PREFIX}\n\n结果很短"

    delivered = asyncio.run(bot.reply_large_text(chat_id, short_text))

    assert len(reply_bot_env.sent_messages) == 1
    assert reply_bot_env.sent_messages[0][0] == chat_id
    assert delivered == reply_bot_env.sent_messages[0][1]
    assert not reply_bot_env.sent_documents


def test_session_ack_message_silent(monkeypatch, tmp_path):
    original_mode = bot.MODE
    original_model = bot.ACTIVE_MODEL
    original_plan_flag = bot.ENABLE_PLAN_PROGRESS
    original_bot = bot._bot

    bot.MODE = "B"
    bot.ACTIVE_MODEL = "codex"
    bot.ENABLE_PLAN_PROGRESS = False
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_SESSION_MAP.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_FAILURE_NOTICES.clear()

    session_file = tmp_path / "rollout-2025-10-10T09-50-13-0199cbcf-bfda-7fc3-8a65-630d360d2d06.jsonl"
    session_file.write_text("", encoding="utf-8")
    chat_id = 999
    bot.CHAT_SESSION_MAP[chat_id] = str(session_file)

    class DummyAiogram:
        def __init__(self):
            self.actions = []

        async def send_chat_action(self, chat_id: int, action: str):
            self.actions.append((chat_id, action))

    bot._bot = DummyAiogram()

    async def dummy_watch(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_watch_and_notify", dummy_watch)
    monkeypatch.setattr(bot, "tmux_send_line", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0)

    captured_answers: list[tuple[str, dict]] = []

    class DummyMessage:
        def __init__(self, text: str):
            self.text = text
            self.chat = SimpleNamespace(id=chat_id)
            self.from_user = SimpleNamespace(full_name="tester")

        async def answer(self, text: str, **kwargs):
            captured_answers.append((text, kwargs))
            return SimpleNamespace(message_id=len(captured_answers))

    msg = DummyMessage("测试指令")
    try:
        # 直接调用内部派发函数，避免依赖 router 注入的 FSMContext 参数。
        asyncio.run(bot._handle_prompt_dispatch(msg, msg.text))
    finally:
        bot.MODE = original_mode
        bot.ACTIVE_MODEL = original_model
        bot.ENABLE_PLAN_PROGRESS = original_plan_flag
        bot._bot = original_bot
        bot.SESSION_OFFSETS.clear()
        bot.CHAT_SESSION_MAP.clear()
        bot.CHAT_WATCHERS.clear()
        bot.CHAT_LAST_MESSAGE.clear()
        bot.CHAT_FAILURE_NOTICES.clear()

    assert captured_answers, "应返回会话确认信息"
    prompt_text, kwargs = captured_answers[-1]
    assert "思考中" in prompt_text
    assert kwargs.get("disable_notification") is True
