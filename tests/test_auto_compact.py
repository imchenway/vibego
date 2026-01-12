import asyncio
import json
import os
import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test-token")

import bot


class DummyBot:
    def __init__(self):
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, parse_mode=None):
        self.sent_messages.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent_messages))


@pytest.fixture()
def auto_compact_env(monkeypatch, tmp_path):
    """构造自动压缩测试环境，隔离全局状态。"""

    session_file = tmp_path / "session.jsonl"
    session_file.write_text("", encoding="utf-8")

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

    dummy = DummyBot()
    original_bot = bot._bot
    original_threshold = bot.AUTO_COMPACT_THRESHOLD
    bot._bot = dummy
    bot.AUTO_COMPACT_THRESHOLD = 2

    responses: list[tuple[int, str]] = []
    tmux_calls: list[tuple[str, str]] = []

    async def fake_reply(
        chat_id: int,
        text: str,
        *,
        parse_mode=None,
        preformatted: bool = False,
        reply_markup=None,
        attachment_reply_markup=None,
    ) -> str:
        responses.append((chat_id, text))
        return text

    def tmux_stub(session: str, line: str) -> None:
        tmux_calls.append((session, line))
        if line != "/compact":
            raise AssertionError(f"意外的命令: {line}")

    monkeypatch.setattr(bot, "reply_large_text", fake_reply)
    monkeypatch.setattr(bot, "tmux_send_line", tmux_stub)

    def append_events(events: list[dict]) -> None:
        with session_file.open("a", encoding="utf-8") as handler:
            for event in events:
                handler.write(json.dumps(event) + "\n")

    yield {
        "dummy_bot": dummy,
        "responses": responses,
        "tmux_calls": tmux_calls,
        "session": session_file,
        "append": append_events,
        "restore": (original_bot, original_threshold),
    }

    bot._bot = original_bot
    bot.AUTO_COMPACT_THRESHOLD = original_threshold


def _message_event(text: str) -> dict:
    return {
        "timestamp": "2025-01-01T00:00:00Z",
        "type": "response_item",
        "payload": {
            "type": "assistant_message",
            "message": text,
        },
    }


def _session_key(path: Path) -> str:
    return str(path)


def test_auto_compact_triggers_after_threshold(auto_compact_env):
    env = auto_compact_env
    chat_id = 8801
    session = env["session"]
    key = _session_key(session)
    bot.SESSION_OFFSETS[key] = 0

    env["append"]([
        _message_event("第一次回复"),
    ])
    first = asyncio.run(bot._deliver_pending_messages(chat_id, session))
    assert first is True
    assert env["responses"] == [(chat_id, "✅模型执行完成，响应结果如下：\n\n第一次回复")]
    assert env["tmux_calls"] == []
    assert env["dummy_bot"].sent_messages == []

    env["append"]([
        _message_event("第二次回复"),
    ])
    second = asyncio.run(bot._deliver_pending_messages(chat_id, session))

    assert second is True
    assert env["tmux_calls"] == [(bot.TMUX_SESSION, "/compact")]
    notices = [msg for _, msg in env["dummy_bot"].sent_messages]
    assert "准备自动执行 /compact" in notices[0]
    assert "等待整理结果" in notices[1]
    assert bot._is_compact_pending(chat_id, key) is True
    assert bot.CHAT_REPLY_COUNT[chat_id][key] == 0


def test_auto_compact_completion_notice(auto_compact_env):
    env = auto_compact_env
    chat_id = 8802
    session = env["session"]
    key = _session_key(session)
    bot.SESSION_OFFSETS[key] = 0

    env["append"]([
        _message_event("回复 A"),
        _message_event("回复 B"),
    ])
    asyncio.run(bot._deliver_pending_messages(chat_id, session))
    asyncio.run(bot._deliver_pending_messages(chat_id, session))
    assert bot._is_compact_pending(chat_id, key) is True

    env["append"]([
        _message_event("/compact 执行结果"),
    ])
    asyncio.run(bot._deliver_pending_messages(chat_id, session))

    completion_msgs = [msg for _, msg in env["dummy_bot"].sent_messages if "已完成" in msg]
    assert completion_msgs, "应通知压缩完成"
    assert bot._is_compact_pending(chat_id, key) is False
    assert bot.CHAT_REPLY_COUNT[chat_id][key] == 1


def test_auto_compact_tmux_failure(monkeypatch, auto_compact_env):
    env = auto_compact_env
    chat_id = 8803
    session = env["session"]
    key = _session_key(session)
    bot.SESSION_OFFSETS[key] = 0

    def failing_tmux(session_name: str, line: str):
        raise subprocess.CalledProcessError(1, ["tmux", "-u", "send-keys"], "failure")

    monkeypatch.setattr(bot, "tmux_send_line", failing_tmux)

    env["append"]([
        _message_event("响应一"),
        _message_event("响应二"),
    ])
    asyncio.run(bot._deliver_pending_messages(chat_id, session))
    asyncio.run(bot._deliver_pending_messages(chat_id, session))

    failure_msgs = [msg for _, msg in env["dummy_bot"].sent_messages if "失败" in msg]
    assert failure_msgs, "应提示压缩失败"
    assert bot._is_compact_pending(chat_id, key) is False
    assert bot.CHAT_REPLY_COUNT[chat_id][key] == bot.AUTO_COMPACT_THRESHOLD - 1


def test_auto_compact_disabled_threshold(monkeypatch, auto_compact_env):
    env = auto_compact_env
    original = bot.AUTO_COMPACT_THRESHOLD
    bot.AUTO_COMPACT_THRESHOLD = 0
    chat_id = 8804
    session = env["session"]
    bot.SESSION_OFFSETS[_session_key(session)] = 0

    env["append"]([
        _message_event("回复-1"),
        _message_event("回复-2"),
        _message_event("回复-3"),
    ])

    asyncio.run(bot._deliver_pending_messages(chat_id, session))
    asyncio.run(bot._deliver_pending_messages(chat_id, session))
    asyncio.run(bot._deliver_pending_messages(chat_id, session))

    assert env["dummy_bot"].sent_messages == []
    assert env["tmux_calls"] == []
    bot.AUTO_COMPACT_THRESHOLD = original
