from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot  # noqa: E402


class DummyMessage:
    """模拟最小可用的 Telegram Message。"""

    def __init__(self, *, chat_id: int = 1, user_id: int = 1, text: str | None = None):
        self.calls: list[tuple[str, object, object, dict]] = []
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester")
        self.message_id = 100
        self.text = text
        self.caption = None

    async def answer(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id + len(self.calls), chat=self.chat)


class DummyCallback:
    """模拟最小可用的 Telegram CallbackQuery。"""

    def __init__(self, data: str, message: DummyMessage):
        self.data = data
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []
        self.from_user = SimpleNamespace(id=message.from_user.id, full_name="Tester")

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


def _make_state(message: DummyMessage) -> tuple[FSMContext, MemoryStorage]:
    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=999, chat_id=message.chat.id, user_id=message.from_user.id),
    )
    return state, storage


def _parallel_context(task_id: str = "TASK_0093") -> bot.ParallelDispatchContext:
    return bot.ParallelDispatchContext(
        task_id=task_id,
        tmux_session="vibe-par-demo",
        pointer_file=Path("/tmp/demo-pointer.txt"),
        workspace_root=Path("/tmp/demo-workspace"),
    )


def test_parallel_keyboard_uses_session_scoped_callback_payload():
    """并行消息底部按钮应携带会话级 payload，避免仅靠 task_id 串到原生会话。"""

    markup = bot._build_model_quick_reply_keyboard(
        task_id="TASK_0093",
        parallel_task_title="并行任务",
        enable_parallel_actions=True,
        parallel_callback_payload="TASK_0093:deadbeef",
    )
    callback_data = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert f"{bot.MODEL_QUICK_REPLY_ALL_TASK_PREFIX}TASK_0093:deadbeef" in callback_data
    assert f"{bot.MODEL_QUICK_REPLY_PARTIAL_TASK_PREFIX}TASK_0093:deadbeef" in callback_data
    assert f"{bot.PARALLEL_REPLY_CALLBACK_PREFIX}TASK_0093:deadbeef" in callback_data


def test_quick_reply_all_uses_parallel_binding_dispatch_context(monkeypatch, tmp_path: Path):
    """并行消息上的“全部按推荐”应严格命中并行上下文，不能回落到原生会话。"""

    origin = DummyMessage(chat_id=9, user_id=9)
    callback = DummyCallback(f"{bot.MODEL_QUICK_REPLY_ALL_TASK_PREFIX}TASK_0093:deadbeef", origin)
    binding = SimpleNamespace(
        token="deadbeef",
        task_id="TASK_0093",
        dispatch_context=_parallel_context(),
    )
    bot.PARALLEL_CALLBACK_BINDINGS = {"deadbeef": binding}

    recorded: list[object] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, dispatch_context=None, **_kwargs):
        recorded.append(dispatch_context)
        return True, tmp_path / "parallel.jsonl"

    async def fake_preview(*_args, **_kwargs):
        return None

    async def fake_ack(*_args, **_kwargs):
        return None

    async def fake_active_parallel_session(_task_id: str):
        return None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)
    monkeypatch.setattr(bot, "_get_active_parallel_session_for_task", fake_active_parallel_session)

    asyncio.run(bot.on_model_quick_reply_all(callback))

    assert recorded and recorded[-1] is binding.dispatch_context


def test_quick_reply_all_parallel_payload_fails_closed_when_binding_missing(monkeypatch):
    """并行按钮若已失效，必须直接报错，不能静默回落到原生会话。"""

    origin = DummyMessage(chat_id=10, user_id=10)
    callback = DummyCallback(f"{bot.MODEL_QUICK_REPLY_ALL_TASK_PREFIX}TASK_0093:deadbeef", origin)
    bot.PARALLEL_CALLBACK_BINDINGS = {}

    async def fake_dispatch(*_args, **_kwargs):
        raise AssertionError("并行会话失效时不应继续回落到默认会话")

    async def fake_active_parallel_session(_task_id: str):
        return None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_get_active_parallel_session_for_task", fake_active_parallel_session)

    asyncio.run(bot.on_model_quick_reply_all(callback))

    assert callback.answers and callback.answers[-1] == ("并行会话已失效，请在最新并行消息中重试。", True)
    assert origin.calls and "并行会话已失效" in origin.calls[-1][0]


def test_parallel_reply_callback_and_followup_use_bound_dispatch_context(monkeypatch):
    """回复按钮应优先使用按钮绑定的并行上下文，而不是再次依赖活动会话查库。"""

    origin = DummyMessage(chat_id=12, user_id=12)
    callback = DummyCallback(f"{bot.PARALLEL_REPLY_CALLBACK_PREFIX}TASK_0093:deadbeef", origin)
    binding = SimpleNamespace(
        token="deadbeef",
        task_id="TASK_0093",
        dispatch_context=_parallel_context(),
    )
    bot.PARALLEL_CALLBACK_BINDINGS = {"deadbeef": binding}

    async def fake_active_parallel_session(_task_id: str):
        return None

    recorded: list[tuple[str, object]] = []

    async def fake_handle_request_input_custom_text_message(_message):
        return False

    async def fake_handle_command_trigger_message(_message, _prompt, _state):
        return False

    async def fake_handle_prompt_dispatch(_message, prompt: str, *, dispatch_context=None):
        recorded.append((prompt, dispatch_context))

    monkeypatch.setattr(bot, "_get_active_parallel_session_for_task", fake_active_parallel_session)
    monkeypatch.setattr(bot, "_handle_request_input_custom_text_message", fake_handle_request_input_custom_text_message)
    monkeypatch.setattr(bot, "_handle_command_trigger_message", fake_handle_command_trigger_message)
    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_handle_prompt_dispatch)

    async def _scenario() -> None:
        await bot.on_parallel_reply_callback(callback)
        message = DummyMessage(chat_id=12, user_id=12, text="继续补充")
        state, _ = _make_state(message)
        await bot.on_text(message, state)

    asyncio.run(_scenario())

    assert recorded == [("/TASK_0093 继续补充", binding.dispatch_context)]


def test_parallel_dispatch_does_not_override_primary_session_binding(monkeypatch, tmp_path: Path):
    """向并行会话发消息时，不应覆盖 chat 当前原生会话，也不应取消原生 watcher。"""

    primary_session = tmp_path / "primary.jsonl"
    parallel_session = tmp_path / "parallel.jsonl"
    pointer = tmp_path / "parallel-pointer.txt"
    primary_session.write_text("", encoding="utf-8")
    parallel_session.write_text("", encoding="utf-8")
    pointer.write_text(str(parallel_session), encoding="utf-8")

    chat_id = 77
    bot.CHAT_SESSION_MAP[chat_id] = str(primary_session)

    class ActiveTask:
        def __init__(self) -> None:
            self.cancelled = False

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancelled = True

    active_watcher = ActiveTask()
    bot.CHAT_WATCHERS[chat_id] = active_watcher

    def fake_tmux_send_line(_session: str, _prompt: str) -> None:
        return None

    async def fake_deliver(_chat_id: int, _session_path: Path, **_kwargs) -> bool:
        return False

    async def fake_interrupt_long_poll(_chat_id: int) -> None:
        return None

    async def fake_parallel_watch(*_args, **_kwargs):
        return None

    created_tasks: list[object] = []

    class DummyTask:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

    def fake_create_task(coro):
        created_tasks.append(coro)
        return DummyTask()

    monkeypatch.setattr(bot, "tmux_send_line", fake_tmux_send_line)
    monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
    monkeypatch.setattr(bot, "_interrupt_long_poll", fake_interrupt_long_poll)
    monkeypatch.setattr(bot, "_watch_parallel_and_notify", fake_parallel_watch)
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    async def _scenario() -> None:
        ok, session_path = await bot._dispatch_prompt_to_model(
            chat_id,
            "待决策项全部按模型推荐",
            reply_to=None,
            ack_immediately=False,
            dispatch_context=bot.ParallelDispatchContext(
                task_id="TASK_0093",
                tmux_session="vibe-par-demo",
                pointer_file=pointer,
                workspace_root=tmp_path / "workspace",
            ),
        )
        assert ok is True
        assert session_path == parallel_session

    asyncio.run(_scenario())

    assert bot.CHAT_SESSION_MAP[chat_id] == str(primary_session)
    assert active_watcher.cancelled is False
    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass
