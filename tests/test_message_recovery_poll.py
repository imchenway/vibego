from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest
from aiogram.types import Chat, Message, User

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot


def _utc_now() -> datetime:
    try:
        from datetime import UTC  # type: ignore

        return datetime.now(UTC)
    except Exception:  # pragma: no cover - Python <3.11 兜底
        return datetime.now(timezone.utc)


def _build_message(*, chat_id: int, message_id: int, text: Optional[str] = "hello") -> Message:
    chat = Chat.model_construct(id=chat_id, type="private")
    user = User.model_construct(id=123, is_bot=False, first_name="Tester")
    return Message.model_construct(
        message_id=message_id,
        date=_utc_now(),
        chat=chat,
        text=text,
        from_user=user,
    )


@pytest.fixture(autouse=True)
def _reset_global_state(monkeypatch):
    monkeypatch.setenv("MODEL_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BINDER_TOKEN_FILE", "")
    bot.CHAT_MESSAGE_RECOVERY_POLL_TASKS.clear()
    bot.CHAT_RECENT_MODEL_DISPATCH_PROOFS.clear()
    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_WATCHERS.clear()
    bot.TEXT_PASTE_SYNTHETIC_GUARD.clear()
    yield
    bot.CHAT_MESSAGE_RECOVERY_POLL_TASKS.clear()
    bot.CHAT_RECENT_MODEL_DISPATCH_PROOFS.clear()
    bot.CHAT_SESSION_MAP.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    bot.CHAT_WATCHERS.clear()
    bot.TEXT_PASTE_SYNTHETIC_GUARD.clear()


@pytest.mark.asyncio
async def test_schedule_message_recovery_poll_stops_after_first_hit(monkeypatch):
    """命中新消息后，应提前结束后续轮次。"""

    chat_id = 2001
    message = _build_message(chat_id=chat_id, message_id=11, text="ping")
    monkeypatch.setattr(bot, "MESSAGE_RECOVERY_POLL_DELAYS_SECONDS", (0.01, 0.01, 0.01))

    rounds: list[int] = []

    async def fake_probe(*_args, round_index: int, **_kwargs) -> bool:
        rounds.append(round_index)
        return round_index == 1

    monkeypatch.setattr(bot, "_probe_new_model_message_once", fake_probe)

    await bot._schedule_message_recovery_poll(message, source="test")
    await asyncio.sleep(0.08)

    assert rounds == [1]
    assert chat_id not in bot.CHAT_MESSAGE_RECOVERY_POLL_TASKS


@pytest.mark.asyncio
async def test_schedule_message_recovery_poll_overrides_previous_task(monkeypatch):
    """同一 chat 新消息应覆盖旧补偿任务。"""

    chat_id = 2002
    msg1 = _build_message(chat_id=chat_id, message_id=21, text="first")
    msg2 = _build_message(chat_id=chat_id, message_id=22, text="second")
    monkeypatch.setattr(bot, "MESSAGE_RECOVERY_POLL_DELAYS_SECONDS", (0.05,))

    trigger_ids: list[int] = []

    async def fake_probe(*_args, trigger_message_id: int, **_kwargs) -> bool:
        trigger_ids.append(trigger_message_id)
        return False

    monkeypatch.setattr(bot, "_probe_new_model_message_once", fake_probe)

    await bot._schedule_message_recovery_poll(msg1, source="test")
    first_task = bot.CHAT_MESSAGE_RECOVERY_POLL_TASKS[chat_id]
    await asyncio.sleep(0.005)

    await bot._schedule_message_recovery_poll(msg2, source="test")
    second_task = bot.CHAT_MESSAGE_RECOVERY_POLL_TASKS[chat_id]

    assert first_task is not second_task
    assert first_task.done()

    await asyncio.sleep(0.08)
    assert trigger_ids == [22]
    assert chat_id not in bot.CHAT_MESSAGE_RECOVERY_POLL_TASKS


@pytest.mark.asyncio
async def test_schedule_message_recovery_poll_skips_internal_synthetic_message():
    """内部合成消息不应触发补偿轮询。"""

    chat_id = 2003
    message = _build_message(chat_id=chat_id, message_id=31, text="/task_new")
    bot._mark_text_paste_synthetic_message(chat_id, message.message_id)

    await bot._schedule_message_recovery_poll(message, source="test")

    assert chat_id not in bot.CHAT_MESSAGE_RECOVERY_POLL_TASKS


@pytest.mark.asyncio
async def test_probe_message_recovery_rebinds_to_new_pointer_and_delivers_once(monkeypatch, tmp_path: Path):
    """补偿轮询发现 pointer 漂移时，应改读新会话且同一 final 只投递一次。"""

    chat_id = 2004
    pointer = tmp_path / "current_session.txt"
    old_session = tmp_path / "old.jsonl"
    new_session = tmp_path / "new.jsonl"
    old_session.write_text("", encoding="utf-8")
    historical_final = {
        "timestamp": "2026-07-11T00:00:00Z",
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "content": []},
    }
    proof_prompt = "current request"
    current_user = {
        "timestamp": "2026-07-11T00:01:00Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": bot._prepend_enforced_agents_notice(proof_prompt),
                }
            ],
        },
    }
    current_final = {
        "timestamp": "2026-07-11T00:01:01Z",
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "content": []},
    }
    historical_line = json.dumps(historical_final) + "\n"
    new_session.write_text(
        historical_line + json.dumps(current_user) + "\n" + json.dumps(current_final) + "\n",
        encoding="utf-8",
    )
    expected_recovery_offset = len(historical_line.encode("utf-8"))
    triggered_at_epoch = datetime(2026, 7, 11, 0, 0, 30, tzinfo=timezone.utc).timestamp()
    dispatch_epoch = datetime(2026, 7, 11, 0, 0, 45, tzinfo=timezone.utc).timestamp()
    pointer.write_text(str(new_session), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setenv("MODEL_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BINDER_TOKEN_FILE", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    bot.CHAT_SESSION_MAP[chat_id] = str(old_session)
    bot.CHAT_RECENT_MODEL_DISPATCH_PROOFS[chat_id] = (proof_prompt, dispatch_epoch)

    original_find_prompt_offset = bot._find_user_prompt_event_offset
    offset_scan_threads: list[threading.Thread] = []

    def tracked_find_prompt_offset(*args, **kwargs) -> Optional[int]:
        offset_scan_threads.append(threading.current_thread())
        return original_find_prompt_offset(*args, **kwargs)

    monkeypatch.setattr(bot, "_find_user_prompt_event_offset", tracked_find_prompt_offset)

    delivered_paths: list[Path] = []
    delivered_count = 0

    async def fake_deliver(target_chat_id: int, session_path: Path, **_kwargs) -> bool:
        nonlocal delivered_count
        assert target_chat_id == chat_id
        delivered_paths.append(session_path)
        session_key = str(session_path)
        if session_path == new_session and delivered_count == 0:
            assert bot.SESSION_OFFSETS[session_key] == expected_recovery_offset
            delivered_count += 1
            bot._set_last_message(chat_id, session_key, "new final")
            bot._get_delivered_offsets(chat_id, session_key).add(42)
            return True
        return False

    async def fake_interrupt(_chat_id: int) -> None:
        return None

    watched_paths: list[Path] = []

    def fake_watch(_chat_id: int, session_path: Path, **_kwargs):
        watched_paths.append(session_path)

        async def _noop() -> None:
            return None

        return _noop()

    created_coroutines: list = []

    class DummyTask:
        def __init__(self) -> None:
            self.cancelled = False

        def done(self) -> bool:
            return self.cancelled

        def cancel(self) -> None:
            self.cancelled = True

    def fake_create_task(coro):
        created_coroutines.append(coro)
        return DummyTask()

    monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
    monkeypatch.setattr(bot, "_interrupt_long_poll", fake_interrupt)
    monkeypatch.setattr(bot, "_watch_and_notify", fake_watch)
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    old_watcher = DummyTask()
    bot.CHAT_WATCHERS[chat_id] = old_watcher

    first_hit = await bot._probe_new_model_message_once(
        chat_id,
        trigger_message_id=41,
        round_index=1,
        source="test",
        triggered_at_epoch=triggered_at_epoch,
    )
    second_hit = await bot._probe_new_model_message_once(
        chat_id,
        trigger_message_id=41,
        round_index=2,
        source="test",
        triggered_at_epoch=triggered_at_epoch,
    )

    assert first_hit is True
    assert second_hit is False
    assert delivered_count == 1
    assert delivered_paths and all(path == new_session for path in delivered_paths)
    assert bot.CHAT_SESSION_MAP[chat_id] == str(new_session)
    assert old_watcher.cancelled is True
    assert watched_paths == [new_session]
    assert offset_scan_threads
    assert all(thread is not threading.main_thread() for thread in offset_scan_threads)

    for coro in created_coroutines:
        coro.close()


@pytest.mark.asyncio
async def test_probe_message_recovery_restarts_watcher_when_rebound_delivery_fails(
    monkeypatch,
    tmp_path: Path,
):
    """漂移后即时投递异常也必须补建新 watcher，不能永久失去监听。"""

    chat_id = 2005
    pointer = tmp_path / "current_session.txt"
    old_session = tmp_path / "old.jsonl"
    new_session = tmp_path / "new.jsonl"
    old_session.write_text("", encoding="utf-8")
    proof_prompt = "failure recovery request"
    new_session.write_text(
        json.dumps(
            {
                "timestamp": "2026-07-11T00:01:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": bot._prepend_enforced_agents_notice(proof_prompt),
                        }
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    pointer.write_text(str(new_session), encoding="utf-8")
    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    bot.CHAT_SESSION_MAP[chat_id] = str(old_session)
    dispatch_epoch = datetime(2026, 7, 11, 0, 0, 45, tzinfo=timezone.utc).timestamp()
    bot.CHAT_RECENT_MODEL_DISPATCH_PROOFS[chat_id] = (proof_prompt, dispatch_epoch)

    class DummyTask:
        def __init__(self) -> None:
            self.cancelled = False

        def done(self) -> bool:
            return self.cancelled

        def cancel(self) -> None:
            self.cancelled = True

    old_watcher = DummyTask()
    bot.CHAT_WATCHERS[chat_id] = old_watcher

    async def failing_deliver(*_args, **_kwargs) -> bool:
        raise RuntimeError("synthetic delivery failure")

    async def fake_interrupt(_chat_id: int) -> None:
        return None

    watched_paths: list[Path] = []

    def fake_watch(_chat_id: int, session_path: Path, **_kwargs):
        watched_paths.append(session_path)

        async def _noop() -> None:
            return None

        return _noop()

    created_coroutines: list = []

    def fake_create_task(coro):
        created_coroutines.append(coro)
        return DummyTask()

    monkeypatch.setattr(bot, "_deliver_pending_messages", failing_deliver)
    monkeypatch.setattr(bot, "_interrupt_long_poll", fake_interrupt)
    monkeypatch.setattr(bot, "_watch_and_notify", fake_watch)
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    hit = await bot._probe_new_model_message_once(
        chat_id,
        trigger_message_id=51,
        round_index=1,
        source="test",
        triggered_at_epoch=dispatch_epoch - 1,
    )

    assert hit is False
    assert bot.CHAT_SESSION_MAP[chat_id] == str(new_session)
    assert old_watcher.cancelled is True
    assert watched_paths == [new_session]
    for coro in created_coroutines:
        coro.close()


@pytest.mark.asyncio
async def test_probe_message_recovery_rejects_pointer_without_current_dispatch_prompt(
    monkeypatch,
    tmp_path: Path,
):
    """同 marker/CWD 的新 pointer 若不含本轮 prompt，recovery 必须保持旧绑定。"""

    chat_id = 2006
    pointer = tmp_path / "current_session.txt"
    old_session = tmp_path / "old.jsonl"
    new_session = tmp_path / "new.jsonl"
    old_session.write_text("", encoding="utf-8")
    unrelated_user = {
        "timestamp": "2026-07-11T00:01:00Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "other prompt"}],
        },
    }
    unrelated_final = {
        "timestamp": "2026-07-11T00:01:01Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "other final"}],
        },
    }
    new_session.write_text(
        json.dumps(unrelated_user) + "\n" + json.dumps(unrelated_final) + "\n",
        encoding="utf-8",
    )
    pointer.write_text(str(new_session), encoding="utf-8")
    dispatch_epoch = datetime(2026, 7, 11, 0, 0, 30, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(
        bot,
        "CHAT_RECENT_MODEL_DISPATCH_PROOFS",
        {chat_id: ("expected prompt", dispatch_epoch)},
        raising=False,
    )
    bot.CHAT_SESSION_MAP[chat_id] = str(old_session)

    delivered_paths: list[Path] = []

    async def fake_deliver(_chat_id: int, session_path: Path, **_kwargs) -> bool:
        delivered_paths.append(session_path)
        return False

    monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)

    hit = await bot._probe_new_model_message_once(
        chat_id,
        trigger_message_id=61,
        round_index=1,
        source="test",
        triggered_at_epoch=dispatch_epoch - 1,
    )

    assert hit is False
    assert bot.CHAT_SESSION_MAP[chat_id] == str(old_session)
    assert delivered_paths == [old_session]


def test_dispatch_prompt_keeps_watcher_after_quick_delivery(monkeypatch, tmp_path: Path):
    """即时轮询已命中输出时，仍应补建 watcher 监听后续消息。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)
    monkeypatch.setattr(bot, "SESSION_POLL_TIMEOUT", 0.2)

    def fake_tmux_send_line(_session: str, _line: str) -> None:
        return

    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

    delivered_calls = {"count": 0}

    async def fake_deliver(_chat_id: int, _path: Path, **_kwargs) -> bool:
        delivered_calls["count"] += 1
        return delivered_calls["count"] == 1

    monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)

    watch_start_flags: list[bool] = []

    def fake_watch(_chat_id: int, _path: Path, max_wait: float, interval: float, *, start_in_long_poll: bool = False):
        watch_start_flags.append(start_in_long_poll)

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(bot, "_watch_and_notify", fake_watch)

    created_coroutines: list = []

    class DummyTask:
        def __init__(self) -> None:
            self._done = False

        def done(self) -> bool:
            return self._done

        def cancel(self) -> None:
            self._done = True

    def fake_create_task(coro):
        created_coroutines.append(coro)
        return DummyTask()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    async def _scenario() -> None:
        ok, path = await bot._dispatch_prompt_to_model(3001, "pwd", reply_to=None, ack_immediately=False)
        assert ok
        assert path == session_file

    asyncio.run(_scenario())

    assert watch_start_flags == [True]
    for coro in created_coroutines:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - 最佳努力清理
            pass


def test_ensure_session_watcher_keeps_watcher_after_quick_delivery(monkeypatch, tmp_path: Path):
    """ensure_session_watcher 即时命中 pending 后，仍应继续补建 watcher。"""

    pointer = tmp_path / "pointer.txt"
    session_file = tmp_path / "rollout.jsonl"
    session_file.write_text("", encoding="utf-8")
    pointer.write_text(str(session_file), encoding="utf-8")

    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setattr(bot, "CODEX_WORKDIR", "")
    monkeypatch.setattr(bot, "SESSION_BIND_STRICT", True)

    async def fake_deliver(_chat_id: int, _path: Path, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
    monkeypatch.setattr(bot, "_interrupt_long_poll", lambda _chat_id: asyncio.sleep(0))

    watch_start_flags: list[bool] = []

    def fake_watch(_chat_id: int, _path: Path, max_wait: float, interval: float, *, start_in_long_poll: bool = False):
        watch_start_flags.append(start_in_long_poll)

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(bot, "_watch_and_notify", fake_watch)

    created_coroutines: list = []

    class DummyTask:
        def __init__(self) -> None:
            self._done = False

        def done(self) -> bool:
            return self._done

        def cancel(self) -> None:
            self._done = True

    def fake_create_task(coro):
        created_coroutines.append(coro)
        return DummyTask()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    result = asyncio.run(bot._ensure_session_watcher(3002))

    assert result == session_file
    assert watch_start_flags == [True]
    assert isinstance(bot.CHAT_WATCHERS[3002], DummyTask)

    for coro in created_coroutines:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - 最佳努力清理
            pass
