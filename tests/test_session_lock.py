import asyncio
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("BOT_TOKEN", "dummy")

import bot


@pytest.fixture
def restore_session_lock_flag():
    original = bot.SESSION_LOCK_REQUIRED
    yield
    bot.SESSION_LOCK_REQUIRED = original


def _reset_session_state() -> None:
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_SESSION_MAP.clear()
    bot.CHAT_WATCHERS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_FAILURE_NOTICES.clear()
    bot.CHAT_PLAN_MESSAGES.clear()
    bot.CHAT_PLAN_TEXT.clear()
    bot.CHAT_PLAN_COMPLETION.clear()
    bot._SESSION_LOCK_CACHE_MTIME = None
    bot._SESSION_LOCK_CACHE_VALUE = None


async def _noop(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_dispatch_prefers_lock_session(monkeypatch, tmp_path, restore_session_lock_flag):
    original_lock_path = bot.SESSION_LOCK_FILE_PATH
    original_pointer = bot.CODEX_SESSION_FILE_PATH
    original_workdir = bot.CODEX_WORKDIR
    try:
        lock_path = tmp_path / "session_lock.json"
        pointer_path = tmp_path / "current_session.txt"
        other_session = tmp_path / "rollout-other.jsonl"
        lock_session = tmp_path / "rollout-lock.jsonl"

        other_session.write_text(json.dumps({"payload": {"cwd": str(tmp_path)}}) + "\n", encoding="utf-8")
        pointer_path.write_text(str(other_session), encoding="utf-8")
        lock_session.write_text(json.dumps({"payload": {"cwd": str(tmp_path)}}) + "\n", encoding="utf-8")
        lock_payload = {
            "session_path": str(lock_session),
            "tmux_session": bot.TMUX_SESSION,
            "project": "demo",
            "workdir": str(tmp_path),
            "captured_at": "2025-01-01T00:00:00Z",
        }
        lock_path.write_text(json.dumps(lock_payload), encoding="utf-8")

        bot.SESSION_LOCK_FILE_PATH = str(lock_path)
        bot.CODEX_SESSION_FILE_PATH = str(pointer_path)
        bot.CODEX_WORKDIR = str(tmp_path)
        _reset_session_state()

        async def fake_reply(*_args, **_kwargs):
            return SimpleNamespace(message_id=1)

        async def fake_deliver(*_args, **_kwargs):
            return False

        monkeypatch.setattr(bot, "tmux_send_line", lambda *args, **kwargs: None)
        monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)
        monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
        monkeypatch.setattr(bot, "_interrupt_long_poll", _noop)
        monkeypatch.setattr(bot, "_watch_and_notify", _noop)

        success, bound_path = await bot._dispatch_prompt_to_model(
            chat_id=42,
            prompt="hello",
            reply_to=None,
        )

        assert success is True
        assert bound_path == lock_session
        assert bot.CHAT_SESSION_MAP[42] == str(lock_session)
        assert pointer_path.read_text(encoding="utf-8").strip() == str(lock_session)
    finally:
        bot.SESSION_LOCK_FILE_PATH = original_lock_path
        bot.CODEX_SESSION_FILE_PATH = original_pointer
        bot.CODEX_WORKDIR = original_workdir
        _reset_session_state()


@pytest.mark.asyncio
async def test_dispatch_requires_lock_when_missing(monkeypatch, tmp_path, restore_session_lock_flag):
    original_lock_path = bot.SESSION_LOCK_FILE_PATH
    original_pointer = bot.CODEX_SESSION_FILE_PATH
    original_workdir = bot.CODEX_WORKDIR
    try:
        lock_path = tmp_path / "session_lock.json"
        pointer_path = tmp_path / "current_session.txt"
        pointer_session = tmp_path / "rollout-pointer.jsonl"
        pointer_session.write_text(json.dumps({"payload": {"cwd": str(tmp_path)}}) + "\n", encoding="utf-8")
        pointer_path.write_text(str(pointer_session), encoding="utf-8")

        bot.SESSION_LOCK_FILE_PATH = str(lock_path)  # Lock file does not exist
        bot.CODEX_SESSION_FILE_PATH = str(pointer_path)
        bot.CODEX_WORKDIR = str(tmp_path)
        bot.SESSION_LOCK_REQUIRED = True
        _reset_session_state()

        replies: list[str] = []

        async def fake_reply(*_args, **_kwargs):
            replies.append(_args[1])
            return SimpleNamespace(message_id=1)

        async def fake_deliver(*_args, **_kwargs):
            return False

        monkeypatch.setattr(bot, "tmux_send_line", lambda *args, **kwargs: None)
        monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)
        monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
        monkeypatch.setattr(bot, "_interrupt_long_poll", _noop)
        monkeypatch.setattr(bot, "_watch_and_notify", _noop)

        success, bound_path = await bot._dispatch_prompt_to_model(
            chat_id=7,
            prompt="hello",
            reply_to=None,
        )

        assert success is False
        assert bound_path is None
        assert replies and "会话锁" in replies[0]
    finally:
        bot.SESSION_LOCK_FILE_PATH = original_lock_path
        bot.CODEX_SESSION_FILE_PATH = original_pointer
        bot.CODEX_WORKDIR = original_workdir
        _reset_session_state()


@pytest.mark.asyncio
async def test_dispatch_fallback_on_lock_mismatch_when_optional(monkeypatch, tmp_path, restore_session_lock_flag):
    original_lock_path = bot.SESSION_LOCK_FILE_PATH
    original_pointer = bot.CODEX_SESSION_FILE_PATH
    original_workdir = bot.CODEX_WORKDIR
    try:
        lock_path = tmp_path / "session_lock.json"
        pointer_path = tmp_path / "current_session.txt"
        pointer_session = tmp_path / "rollout-pointer.jsonl"
        pointer_session.write_text(json.dumps({"payload": {"cwd": str(tmp_path)}}) + "\n", encoding="utf-8")
        pointer_path.write_text(str(pointer_session), encoding="utf-8")

        lock_payload = {
            "session_path": str(tmp_path / "rollout-other.jsonl"),
            "tmux_session": "another-session",
            "project": "demo",
            "workdir": str(tmp_path),
            "captured_at": "2025-01-01T00:00:00Z",
        }
        lock_path.write_text(json.dumps(lock_payload), encoding="utf-8")

        bot.SESSION_LOCK_FILE_PATH = str(lock_path)
        bot.CODEX_SESSION_FILE_PATH = str(pointer_path)
        bot.CODEX_WORKDIR = str(tmp_path)
        bot.SESSION_LOCK_REQUIRED = False
        _reset_session_state()

        async def fake_reply(*_args, **_kwargs):
            return SimpleNamespace(message_id=1)

        async def fake_deliver(*_args, **_kwargs):
            return False

        monkeypatch.setattr(bot, "tmux_send_line", lambda *args, **kwargs: None)
        monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)
        monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
        monkeypatch.setattr(bot, "_interrupt_long_poll", _noop)
        monkeypatch.setattr(bot, "_watch_and_notify", _noop)

        success, bound_path = await bot._dispatch_prompt_to_model(
            chat_id=9,
            prompt="hello",
            reply_to=None,
        )

        assert success is True
        assert bound_path == pointer_session
        assert bot.CHAT_SESSION_MAP[9] == str(pointer_session)
    finally:
        bot.SESSION_LOCK_FILE_PATH = original_lock_path
        bot.CODEX_SESSION_FILE_PATH = original_pointer
        bot.CODEX_WORKDIR = original_workdir
        _reset_session_state()


@pytest.mark.asyncio
async def test_ensure_session_watcher_uses_lock(monkeypatch, tmp_path, restore_session_lock_flag):
    original_lock_path = bot.SESSION_LOCK_FILE_PATH
    original_pointer = bot.CODEX_SESSION_FILE_PATH
    original_workdir = bot.CODEX_WORKDIR
    try:
        lock_path = tmp_path / "session_lock.json"
        pointer_path = tmp_path / "current_session.txt"
        lock_session = tmp_path / "rollout-lock.jsonl"

        lock_session.write_text(json.dumps({"payload": {"cwd": str(tmp_path)}}) + "\n", encoding="utf-8")
        pointer_path.write_text("", encoding="utf-8")
        lock_payload = {
            "session_path": str(lock_session),
            "tmux_session": bot.TMUX_SESSION,
            "project": "demo",
            "workdir": str(tmp_path),
            "captured_at": "2025-01-01T00:00:00Z",
        }
        lock_path.write_text(json.dumps(lock_payload), encoding="utf-8")

        bot.SESSION_LOCK_FILE_PATH = str(lock_path)
        bot.CODEX_SESSION_FILE_PATH = str(pointer_path)
        bot.CODEX_WORKDIR = str(tmp_path)
        _reset_session_state()

        async def fake_deliver(*_args, **_kwargs):
            return False

        monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
        monkeypatch.setattr(bot, "_watch_and_notify", lambda *args, **kwargs: _noop())

        bound_path = await bot._ensure_session_watcher(chat_id=101)

        assert bound_path == lock_session
        assert bot.CHAT_SESSION_MAP[101] == str(lock_session)
        assert pointer_path.read_text(encoding="utf-8").strip() == str(lock_session)
    finally:
        bot.SESSION_LOCK_FILE_PATH = original_lock_path
        bot.CODEX_SESSION_FILE_PATH = original_pointer
        bot.CODEX_WORKDIR = original_workdir
        _reset_session_state()


@pytest.mark.asyncio
async def test_ensure_session_watcher_requires_lock(monkeypatch, tmp_path, restore_session_lock_flag):
    original_lock_path = bot.SESSION_LOCK_FILE_PATH
    original_pointer = bot.CODEX_SESSION_FILE_PATH
    original_workdir = bot.CODEX_WORKDIR
    try:
        lock_path = tmp_path / "session_lock.json"
        pointer_path = tmp_path / "current_session.txt"
        pointer_session = tmp_path / "rollout-pointer.jsonl"
        pointer_session.write_text("", encoding="utf-8")
        pointer_path.write_text(str(pointer_session), encoding="utf-8")

        bot.SESSION_LOCK_FILE_PATH = str(lock_path)
        bot.CODEX_SESSION_FILE_PATH = str(pointer_path)
        bot.CODEX_WORKDIR = str(tmp_path)
        bot.SESSION_LOCK_REQUIRED = True
        _reset_session_state()

        bound_path = await bot._ensure_session_watcher(chat_id=202)

        assert bound_path is None
        assert 202 not in bot.CHAT_SESSION_MAP
    finally:
        bot.SESSION_LOCK_FILE_PATH = original_lock_path
        bot.CODEX_SESSION_FILE_PATH = original_pointer
        bot.CODEX_WORKDIR = original_workdir
        _reset_session_state()
@pytest.mark.asyncio
async def test_dispatch_fallback_when_lock_optional(monkeypatch, tmp_path, restore_session_lock_flag):
    original_lock_path = bot.SESSION_LOCK_FILE_PATH
    original_pointer = bot.CODEX_SESSION_FILE_PATH
    original_workdir = bot.CODEX_WORKDIR
    try:
        lock_path = tmp_path / "session_lock.json"
        pointer_path = tmp_path / "current_session.txt"
        pointer_session = tmp_path / "rollout-pointer.jsonl"
        pointer_session.write_text(json.dumps({"payload": {"cwd": str(tmp_path)}}) + "\n", encoding="utf-8")
        pointer_path.write_text(str(pointer_session), encoding="utf-8")

        bot.SESSION_LOCK_FILE_PATH = str(lock_path)  # Lock file does not exist
        bot.CODEX_SESSION_FILE_PATH = str(pointer_path)
        bot.CODEX_WORKDIR = str(tmp_path)
        bot.SESSION_LOCK_REQUIRED = False
        _reset_session_state()

        async def fake_reply(*_args, **_kwargs):
            return SimpleNamespace(message_id=1)

        async def fake_deliver(*_args, **_kwargs):
            return False

        monkeypatch.setattr(bot, "tmux_send_line", lambda *args, **kwargs: None)
        monkeypatch.setattr(bot, "_reply_to_chat", fake_reply)
        monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
        monkeypatch.setattr(bot, "_interrupt_long_poll", _noop)
        monkeypatch.setattr(bot, "_watch_and_notify", _noop)

        success, bound_path = await bot._dispatch_prompt_to_model(
            chat_id=8,
            prompt="hello",
            reply_to=None,
        )

        assert success is True
        assert bound_path == pointer_session
        assert bot.CHAT_SESSION_MAP[8] == str(pointer_session)
    finally:
        bot.SESSION_LOCK_FILE_PATH = original_lock_path
        bot.CODEX_SESSION_FILE_PATH = original_pointer
        bot.CODEX_WORKDIR = original_workdir
        _reset_session_state()
