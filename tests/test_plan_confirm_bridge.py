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
        self.answers: list[tuple[str, dict]] = []

    async def edit_reply_markup(self, reply_markup=None):
        self.edited_reply_markup = True

    async def answer(self, text: str, **kwargs):
        self.answers.append((text, kwargs))


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
    bot.PLAN_CONFIRM_PROCESSING_TOKENS.clear()
    bot.WORKER_PLAN_MODE_STATE_CACHE.clear()
    bot.PARALLEL_SESSION_TASK_BINDINGS.clear()
    bot.PARALLEL_SESSION_CONTEXTS.clear()
    bot.PARALLEL_TASK_SESSION_MAP.clear()
    bot.SESSION_QUICK_REPLY_CALLBACK_BINDINGS.clear()
    yield
    bot.PLAN_CONFIRM_SESSIONS.clear()
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS.clear()
    bot.PLAN_CONFIRM_PROCESSING_TOKENS.clear()
    bot.WORKER_PLAN_MODE_STATE_CACHE.clear()
    bot.PARALLEL_SESSION_TASK_BINDINGS.clear()
    bot.PARALLEL_SESSION_CONTEXTS.clear()
    bot.PARALLEL_TASK_SESSION_MAP.clear()
    bot.SESSION_QUICK_REPLY_CALLBACK_BINDINGS.clear()


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

    dispatched: list[tuple[int, str, bool, tuple[str, ...] | None, int | None]] = []

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
        intended_mode=None,
        force_exit_plan_ui: bool = False,
        force_exit_plan_ui_key_sequence=None,
        force_exit_plan_ui_max_rounds=None,
    ):
        dispatched.append(
            (
                chat_id,
                prompt,
                force_exit_plan_ui,
                tuple(force_exit_plan_ui_key_sequence) if force_exit_plan_ui_key_sequence is not None else None,
                force_exit_plan_ui_max_rounds,
            )
        )
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_refresh_worker_plan_mode_state_cache", lambda *, force_probe=True: "off")

    callback = DummyCallback(
        bot._build_plan_confirm_callback_data(token, bot.PLAN_CONFIRM_ACTION_YES),
        message=DummyMessage(chat_id=chat_id),
        user_id=9,
    )
    asyncio.run(bot.on_plan_confirm_callback(callback))

    assert dispatched == [
        (
            chat_id,
            bot.PLAN_IMPLEMENT_PROMPT,
            True,
            bot._build_plan_develop_retry_exit_plan_key_sequence(),
            bot.PLAN_DEVELOP_RETRY_EXIT_PLAN_MAX_ROUNDS,
        )
    ]
    assert token not in bot.PLAN_CONFIRM_SESSIONS
    assert chat_id not in bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS
    assert callback.answers[-1] == ("已确认并推送到模型", False)
    assert callback.message.answers == []


def test_parallel_plan_confirm_yes_dispatches_bound_parallel_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """并行 CLI 发出的 Implement the plan，点 Yes 后必须继续发回对应并行会话。"""

    class DummyBot:
        def __init__(self) -> None:
            self.sent_messages: list[dict] = []

        async def send_message(self, chat_id: int, text: str, parse_mode=None, reply_markup=None):
            self.sent_messages.append(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "reply_markup": reply_markup,
                }
            )
            return SimpleNamespace(message_id=len(self.sent_messages), chat=SimpleNamespace(id=chat_id))

    chat_id = 188
    session_key = "parallel-plan-session"
    dispatch_context = bot.ParallelDispatchContext(
        task_id="TASK_0093",
        tmux_session="vibe-par-demo",
        pointer_file=tmp_path / "pointer.txt",
        workspace_root=tmp_path / "workspace",
    )
    bot.PARALLEL_SESSION_TASK_BINDINGS[session_key] = "TASK_0093"
    bot.PARALLEL_SESSION_CONTEXTS[session_key] = dispatch_context
    bot.PARALLEL_TASK_SESSION_MAP["TASK_0093"] = session_key
    bot.CHAT_ACTIVE_USERS[chat_id] = 9

    async def fake_send_with_retry(coro_factory, *, attempts=bot.SEND_RETRY_ATTEMPTS):
        await coro_factory()

    monkeypatch.setattr(bot, "_bot", DummyBot())
    monkeypatch.setattr(bot, "_send_with_retry", fake_send_with_retry)

    created = asyncio.run(bot._maybe_send_plan_confirm_prompt(chat_id, session_key))

    assert created is True
    token = bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id]

    captured_contexts: list[object] = []

    async def fake_dispatch(
        _chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
        intended_mode=None,
        force_exit_plan_ui: bool = False,
        force_exit_plan_ui_key_sequence=None,
        force_exit_plan_ui_max_rounds=None,
        dispatch_context=None,
    ):
        assert prompt == bot.PLAN_IMPLEMENT_PROMPT
        captured_contexts.append(dispatch_context)
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_refresh_worker_plan_mode_state_cache", lambda *, force_probe=True: "off")

    callback = DummyCallback(
        bot._build_plan_confirm_callback_data(token, bot.PLAN_CONFIRM_ACTION_YES),
        message=DummyMessage(chat_id=chat_id),
        user_id=9,
    )

    asyncio.run(bot.on_plan_confirm_callback(callback))

    assert captured_contexts == [dispatch_context]
    assert callback.answers[-1] == ("已确认并推送到模型", False)


def test_parallel_plan_confirm_yes_fails_closed_when_context_stale(monkeypatch: pytest.MonkeyPatch):
    """并行 Plan Confirm 若上下文已失效，必须 fail-closed，不能回落到原生 CLI。"""

    chat_id = 189
    token = "tok-par-stale"
    bot.PLAN_CONFIRM_SESSIONS[token] = SimpleNamespace(
        token=token,
        chat_id=chat_id,
        session_key="parallel-plan-session",
        user_id=9,
        created_at=time.monotonic(),
        parallel_task_id="TASK_0093",
        parallel_dispatch_context=None,
    )
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] = token

    dispatched: list[tuple[int, str]] = []

    async def fake_dispatch(*args, **kwargs):
        dispatched.append((args[0], args[1]))
        return True, None

    async def fake_resolve_parallel_dispatch_context(task_id: str | None, token: str | None):
        assert task_id == "TASK_0093"
        assert token is None
        return "TASK_0093", None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_resolve_parallel_dispatch_context", fake_resolve_parallel_dispatch_context)
    monkeypatch.setattr(bot, "_refresh_worker_plan_mode_state_cache", lambda *, force_probe=True: "off")

    callback = DummyCallback(
        bot._build_plan_confirm_callback_data(token, bot.PLAN_CONFIRM_ACTION_YES),
        message=DummyMessage(chat_id=chat_id),
        user_id=9,
    )

    asyncio.run(bot.on_plan_confirm_callback(callback))

    assert not dispatched
    assert callback.answers[-1] == ("并行会话已失效，请在最新并行消息中重试。", True)
    assert callback.message.answers
    assert "并行会话已失效" in callback.message.answers[-1][0]


def test_plan_confirm_old_callback_still_works_after_newer_session_created(monkeypatch: pytest.MonkeyPatch):
    """同 chat 出现更新的 PlanConfirm 后，旧按钮仍应可点击，不应被“单活 token”提前判失效。"""

    chat_id = 190
    older = bot.PlanConfirmSession(
        token="tok-older",
        chat_id=chat_id,
        session_key="session-older",
        user_id=9,
        created_at=time.monotonic(),
    )
    newer = bot.PlanConfirmSession(
        token="tok-newer",
        chat_id=chat_id,
        session_key="session-newer",
        user_id=9,
        created_at=time.monotonic(),
    )
    bot.PLAN_CONFIRM_SESSIONS[older.token] = older
    bot.PLAN_CONFIRM_SESSIONS[newer.token] = newer
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] = newer.token

    dispatched: list[tuple[int, str]] = []

    async def fake_dispatch(
        _chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
        intended_mode=None,
        force_exit_plan_ui: bool = False,
        force_exit_plan_ui_key_sequence=None,
        force_exit_plan_ui_max_rounds=None,
        dispatch_context=None,
    ):
        dispatched.append((_chat_id, prompt))
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_refresh_worker_plan_mode_state_cache", lambda *, force_probe=True: "off")

    callback = DummyCallback(
        bot._build_plan_confirm_callback_data(older.token, bot.PLAN_CONFIRM_ACTION_YES),
        message=DummyMessage(chat_id=chat_id),
        user_id=9,
    )

    asyncio.run(bot.on_plan_confirm_callback(callback))

    assert dispatched == [(chat_id, bot.PLAN_IMPLEMENT_PROMPT)]
    assert older.token not in bot.PLAN_CONFIRM_SESSIONS
    assert newer.token in bot.PLAN_CONFIRM_SESSIONS
    assert bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] == newer.token
    assert callback.answers[-1] == ("已确认并推送到模型", False)


def test_old_native_quick_reply_fail_closed_does_not_drop_other_session_plan_confirm(
    monkeypatch: pytest.MonkeyPatch,
):
    """旧原生会话 quick reply fail-closed 后，不应误删另一个活动会话的 PlanConfirm。"""

    chat_id = 260
    plan_token = "plan-keep"
    plan_session = bot.PlanConfirmSession(
        token=plan_token,
        chat_id=chat_id,
        session_key="session-current",
        user_id=9,
        created_at=time.monotonic(),
    )
    bot.PLAN_CONFIRM_SESSIONS[plan_token] = plan_session
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] = plan_token
    bot.CHAT_SESSION_MAP[chat_id] = "session-current"
    bot.SESSION_QUICK_REPLY_CALLBACK_BINDINGS["deadbeef"] = bot.SessionQuickReplyBinding(
        token="deadbeef",
        task_id="TASK_0200",
        session_key="session-old",
    )

    async def should_not_dispatch_quick_reply(*_args, **_kwargs):
        raise AssertionError("旧原生 quick reply fail-closed 时不应派发")

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", should_not_dispatch_quick_reply)

    stale_callback = DummyCallback(
        f"{bot.MODEL_QUICK_REPLY_ALL_SESSION_PREFIX}TASK_0200:deadbeef",
        message=DummyMessage(chat_id=chat_id),
        user_id=9,
    )

    asyncio.run(bot.on_model_quick_reply_all(stale_callback))

    assert stale_callback.answers[-1] == ("该消息所属会话已失效，请在最新会话中重试。", True)
    assert plan_token in bot.PLAN_CONFIRM_SESSIONS
    assert bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] == plan_token

    dispatched: list[tuple[int, str]] = []

    async def fake_dispatch_yes(
        _chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
        intended_mode=None,
        force_exit_plan_ui: bool = False,
        force_exit_plan_ui_key_sequence=None,
        force_exit_plan_ui_max_rounds=None,
        dispatch_context=None,
    ):
        dispatched.append((_chat_id, prompt))
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch_yes)
    monkeypatch.setattr(bot, "_refresh_worker_plan_mode_state_cache", lambda *, force_probe=True: "off")

    yes_callback = DummyCallback(
        bot._build_plan_confirm_callback_data(plan_token, bot.PLAN_CONFIRM_ACTION_YES),
        message=DummyMessage(chat_id=chat_id),
        user_id=9,
    )

    asyncio.run(bot.on_plan_confirm_callback(yes_callback))

    assert dispatched == [(chat_id, bot.PLAN_IMPLEMENT_PROMPT)]
    assert yes_callback.answers[-1] == ("已确认并推送到模型", False)


def test_plan_confirm_yes_is_idempotent_under_concurrent_clicks(monkeypatch: pytest.MonkeyPatch):
    """同一个 Plan Yes token 并发点击时，最多只允许一次实际派发。"""

    chat_id = 127
    token = "tok-concurrent"
    session = bot.PlanConfirmSession(
        token=token,
        chat_id=chat_id,
        session_key="session-key",
        user_id=21,
        created_at=time.monotonic(),
    )
    bot.PLAN_CONFIRM_SESSIONS[token] = session
    bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] = token

    dispatched: list[tuple[int, str]] = []
    second_dispatch_seen = asyncio.Event()

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
        intended_mode=None,
        force_exit_plan_ui: bool = False,
        force_exit_plan_ui_key_sequence=None,
        force_exit_plan_ui_max_rounds=None,
    ):
        dispatched.append((chat_id, prompt))
        if len(dispatched) == 1:
            try:
                await asyncio.wait_for(second_dispatch_seen.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass
        else:
            second_dispatch_seen.set()
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_refresh_worker_plan_mode_state_cache", lambda *, force_probe=True: "off")

    callback1 = DummyCallback(
        bot._build_plan_confirm_callback_data(token, bot.PLAN_CONFIRM_ACTION_YES),
        message=DummyMessage(chat_id=chat_id),
        user_id=21,
    )
    callback2 = DummyCallback(
        bot._build_plan_confirm_callback_data(token, bot.PLAN_CONFIRM_ACTION_YES),
        message=DummyMessage(chat_id=chat_id),
        user_id=21,
    )

    async def _run() -> None:
        await asyncio.gather(
            bot.on_plan_confirm_callback(callback1),
            bot.on_plan_confirm_callback(callback2),
        )

    asyncio.run(_run())

    # 期望：并发点击只能派发一次（当前缺陷实现下会触发两次）。
    assert dispatched == [(chat_id, bot.PLAN_IMPLEMENT_PROMPT)]
    all_answers = callback1.answers + callback2.answers
    assert ("已确认并推送到模型", False) in all_answers
    assert ("正在处理中，请勿重复点击。", False) in all_answers
    assert token not in bot.PLAN_CONFIRM_SESSIONS
    assert chat_id not in bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS


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

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
        intended_mode=None,
        force_exit_plan_ui: bool = False,
    ):
        dispatched.append((chat_id, prompt))
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_refresh_worker_plan_mode_state_cache", lambda *, force_probe=True: "on")

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
    assert callback.message.answers == []


def test_maybe_send_plan_confirm_prompt_keeps_older_session_for_same_chat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """同 chat 出现新的 PlanConfirm 时，不应直接删掉旧 session。"""

    class DummyBot:
        def __init__(self) -> None:
            self.sent_messages: list[dict] = []

        async def send_message(self, chat_id: int, text: str, parse_mode=None, reply_markup=None):
            self.sent_messages.append(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "reply_markup": reply_markup,
                }
            )
            return SimpleNamespace(message_id=len(self.sent_messages), chat=SimpleNamespace(id=chat_id))

    chat_id = 191

    async def fake_send_with_retry(coro_factory, *, attempts=bot.SEND_RETRY_ATTEMPTS):
        await coro_factory()

    async def fake_resolve_parallel(_session_key: str):
        return None, None

    monkeypatch.setattr(bot, "_bot", DummyBot())
    monkeypatch.setattr(bot, "_send_with_retry", fake_send_with_retry)
    monkeypatch.setattr(bot, "_resolve_parallel_plan_confirm_context", fake_resolve_parallel)

    created_1 = asyncio.run(bot._maybe_send_plan_confirm_prompt(chat_id, str(tmp_path / "session-1.jsonl")))
    older_token = bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id]
    created_2 = asyncio.run(bot._maybe_send_plan_confirm_prompt(chat_id, str(tmp_path / "session-2.jsonl")))
    newer_token = bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id]

    assert created_1 is True
    assert created_2 is True
    assert older_token != newer_token
    assert older_token in bot.PLAN_CONFIRM_SESSIONS
    assert newer_token in bot.PLAN_CONFIRM_SESSIONS
    assert bot.CHAT_ACTIVE_PLAN_CONFIRM_TOKENS[chat_id] == newer_token


def test_plan_develop_retry_callback_dispatches_implement_prompt_without_session(
    monkeypatch: pytest.MonkeyPatch,
):
    """兼容旧按钮：即便没有重试会话，也应再次推送 Implement。"""

    chat_id = 225
    token = "retrytok"
    dispatched: list[tuple[int, str, bool, tuple[str, ...] | None, int | None]] = []

    async def fake_dispatch(
        chat_id: int,
        prompt: str,
        *,
        reply_to,
        ack_immediately: bool = True,
        intended_mode=None,
        force_exit_plan_ui: bool = False,
        force_exit_plan_ui_key_sequence=None,
        force_exit_plan_ui_max_rounds=None,
    ):
        dispatched.append(
            (
                chat_id,
                prompt,
                force_exit_plan_ui,
                tuple(force_exit_plan_ui_key_sequence) if force_exit_plan_ui_key_sequence is not None else None,
                force_exit_plan_ui_max_rounds,
            )
        )
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    callback = DummyCallback(
        bot._build_plan_develop_retry_callback_data(token, bot.PLAN_DEVELOP_RETRY_ACTION_RETRY),
        message=DummyMessage(chat_id=chat_id),
        user_id=12,
    )
    asyncio.run(bot.on_plan_develop_retry_callback(callback))

    assert dispatched == [
        (
            chat_id,
            bot.PLAN_IMPLEMENT_PROMPT,
            True,
            bot._build_plan_develop_retry_exit_plan_key_sequence(),
            bot.PLAN_DEVELOP_RETRY_EXIT_PLAN_MAX_ROUNDS,
        )
    ]
    assert callback.answers[-1] == ("已重试并推送到模型", False)


def test_plan_develop_retry_callback_invalid_action(monkeypatch: pytest.MonkeyPatch):
    chat_id = 226
    dispatched: list[str] = []

    async def fake_dispatch(*args, **kwargs):  # pragma: no cover - 不应触发
        dispatched.append("called")
        return True, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    callback = DummyCallback(
        bot._build_plan_develop_retry_callback_data("legacy", "noop"),
        message=DummyMessage(chat_id=chat_id),
        user_id=7,
    )
    asyncio.run(bot.on_plan_develop_retry_callback(callback))

    assert not dispatched
    assert callback.answers[-1] == ("暂不支持该操作。", True)
