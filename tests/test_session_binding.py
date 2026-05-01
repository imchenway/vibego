import asyncio
import json
from pathlib import Path
import sys

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot


class DummyChat:
    def __init__(self, chat_id: int = 123) -> None:
        self.id = chat_id


class DummyMessage:
    def __init__(self, text: str, chat_id: int = 123) -> None:
        self.text = text
        self.chat = DummyChat(chat_id)
        self.from_user = type("DummyUser", (), {"id": chat_id})()
        self.calls: list[str] = []
        self.answer_kwargs: list[dict] = []

    async def answer(self, text: str, **_kwargs):
        self.calls.append(text)
        self.answer_kwargs.append(_kwargs)
        return None


class DummyCallback:
    def __init__(self, data: str, message: DummyMessage, user_id: int | None = None) -> None:
        self.data = data
        self.message = message
        self.from_user = type("DummyUser", (), {"id": user_id or message.chat.id})()
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


def make_state(message: DummyMessage) -> FSMContext:
    storage = MemoryStorage()
    return FSMContext(
        storage=storage,
        key=StorageKey(bot_id=999, chat_id=message.chat.id, user_id=message.from_user.id),
    )


def _write_codex_session(path: Path, *, session_uuid: str, cwd: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": session_uuid,
                    "cwd": str(cwd),
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_await_session_path_strict_waits(tmp_path):
    """严格模式下应等待 pointer 写入后再返回。"""
    pointer = tmp_path / "current_session.txt"
    pointer.write_text("", encoding="utf-8")
    session_file = tmp_path / "sessions" / "rollout-new.jsonl"
    session_file.parent.mkdir()
    session_file.write_text("{}", encoding="utf-8")

    async def _delayed_bind() -> None:
        await asyncio.sleep(0.05)
        pointer.write_text(str(session_file), encoding="utf-8")

    task = asyncio.create_task(_delayed_bind())
    result = await bot._await_session_path(
        pointer,
        target_cwd=None,
        poll=0.01,
        strict=True,
        max_wait=1.0,
    )
    await task
    assert result == session_file


@pytest.mark.asyncio
async def test_await_session_path_strict_timeout(tmp_path):
    """超过超时仍未绑定时需返回 None 供上层提示用户重试。"""
    pointer = tmp_path / "current_session.txt"
    pointer.write_text("", encoding="utf-8")
    result = await bot._await_session_path(
        pointer,
        target_cwd=None,
        poll=0.01,
        strict=True,
        max_wait=0.05,
    )
    assert result is None


def test_resolve_main_session_binding_target_accepts_uuid_and_file_stem(monkeypatch, tmp_path: Path) -> None:
    """绑定主会话：应同时支持 Codex UUID 与 Telegram 展示的文件 stem。"""

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    session_uuid = "019d0f8d-fd9d-7000-a111-123456789abc"
    session_file = tmp_path / "sessions" / f"rollout-2026-05-01T00-00-00-{session_uuid}.jsonl"
    _write_codex_session(session_file, session_uuid=session_uuid, cwd=workdir)

    monkeypatch.setattr(bot, "MODEL_SESSION_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(bot, "CODEX_SESSIONS_ROOT", "")
    monkeypatch.setattr(bot, "MODEL_SESSION_GLOB", "rollout-*.jsonl")

    by_uuid, error_by_uuid = bot._resolve_main_session_binding_target(session_uuid, str(workdir))
    by_stem, error_by_stem = bot._resolve_main_session_binding_target(session_file.stem, str(workdir))

    assert error_by_uuid is None
    assert error_by_stem is None
    assert by_uuid is not None
    assert by_stem is not None
    assert by_uuid.session_path == session_file
    assert by_stem.session_path == session_file
    assert by_uuid.resume_session_id == session_uuid
    assert by_stem.resume_session_id == session_uuid


def test_resolve_main_session_binding_target_rejects_other_workdir(monkeypatch, tmp_path: Path) -> None:
    """绑定主会话：跨工作目录 session 必须 fail-closed，避免串项目。"""

    workdir = tmp_path / "workdir"
    other_workdir = tmp_path / "other"
    workdir.mkdir()
    other_workdir.mkdir()
    session_uuid = "019d0f8d-fd9d-7000-a111-123456789abd"
    session_file = tmp_path / "sessions" / f"rollout-2026-05-01T00-00-00-{session_uuid}.jsonl"
    _write_codex_session(session_file, session_uuid=session_uuid, cwd=other_workdir)

    monkeypatch.setattr(bot, "MODEL_SESSION_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(bot, "CODEX_SESSIONS_ROOT", "")
    monkeypatch.setattr(bot, "MODEL_SESSION_GLOB", "rollout-*.jsonl")

    target, error = bot._resolve_main_session_binding_target(session_uuid, str(workdir))

    assert target is None
    assert error is not None
    assert "不属于当前项目" in error


def test_bind_session_command_restarts_main_tmux_and_binds_watcher(monkeypatch, tmp_path: Path) -> None:
    """绑定主会话：成功后应恢复主 tmux、更新 pointer，并把 watcher 绑定到目标会话。"""

    pointer = tmp_path / "current_session.txt"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    session_uuid = "019d0f8d-fd9d-7000-a111-123456789abe"
    session_file = tmp_path / "sessions" / f"rollout-2026-05-01T00-00-00-{session_uuid}.jsonl"
    _write_codex_session(session_file, session_uuid=session_uuid, cwd=workdir)
    session_file.write_text(session_file.read_text(encoding="utf-8") + "historical output\n", encoding="utf-8")

    monkeypatch.setattr(bot, "ACTIVE_MODEL", "codex")
    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setenv("MODEL_WORKDIR", str(workdir))
    monkeypatch.setattr(bot, "MODEL_SESSION_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(bot, "CODEX_SESSIONS_ROOT", "")
    monkeypatch.setattr(bot, "MODEL_SESSION_GLOB", "rollout-*.jsonl")

    restarted: list[str] = []

    async def fake_restart(resume_session_id: str) -> tuple[bool, str]:
        restarted.append(resume_session_id)
        return True, ""

    delivered_calls: list[tuple[int, Path]] = []

    async def fake_deliver(chat_id: int, session_path: Path) -> bool:
        delivered_calls.append((chat_id, session_path))
        return False

    async def fake_interrupt(_chat_id: int) -> None:
        return None

    monkeypatch.setattr(bot, "_restart_main_tmux_with_resume_session", fake_restart)
    monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
    monkeypatch.setattr(bot, "_interrupt_long_poll", fake_interrupt)

    class DummyTask:
        def __init__(self):
            self._done = False

        def done(self) -> bool:
            return self._done

        def cancel(self) -> None:
            self._done = True

    created_tasks: list = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        return DummyTask()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    bot.CHAT_SESSION_MAP.clear()
    bot.CHAT_WATCHERS.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    message = DummyMessage(f"/bind_session {session_file.stem}", chat_id=909)

    asyncio.run(bot.on_bind_session_command(message))

    assert restarted == [session_uuid]
    assert pointer.read_text(encoding="utf-8").strip() == str(session_file)
    assert bot.CHAT_SESSION_MAP[909] == str(session_file)
    assert bot.SESSION_OFFSETS[str(session_file)] == session_file.stat().st_size
    assert delivered_calls == [(909, session_file)]
    assert str(session_file.stem) in message.calls[-1]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass

    bot.CHAT_SESSION_MAP.clear()
    bot.CHAT_WATCHERS.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()


def test_bind_session_command_rejects_non_codex_model(monkeypatch, tmp_path: Path) -> None:
    """绑定主会话：非 Codex 模型缺少可证恢复契约，应直接拒绝。"""

    monkeypatch.setattr(bot, "ACTIVE_MODEL", "gemini")
    monkeypatch.setenv("MODEL_WORKDIR", str(tmp_path))
    message = DummyMessage("/bind_session whatever")

    asyncio.run(bot.on_bind_session_command(message))

    assert message.calls
    assert "暂仅支持 Codex" in message.calls[-1]


def test_session_live_resume_callback_prompts_for_session_id(monkeypatch) -> None:
    """会话实况 Resume：点击入口后应进入 sessionId 输入态。"""

    monkeypatch.setattr(bot, "ACTIVE_MODEL", "codex")
    message = DummyMessage("", chat_id=818)
    callback = DummyCallback(bot.SESSION_LIVE_RESUME_CALLBACK, message)
    state = make_state(message)

    asyncio.run(bot.on_session_live_resume_callback(callback, state))

    assert asyncio.run(state.get_state()) == bot.SessionResumeStates.waiting_session_id.state
    assert callback.answers[-1] == ("请输入 sessionId", False)
    assert "请输入要 resume 的 sessionId" in message.calls[-1]


def test_session_live_resume_callback_rejects_non_codex_model(monkeypatch) -> None:
    """会话实况 Resume：非 Codex 模型缺少可证恢复契约，应直接拒绝。"""

    monkeypatch.setattr(bot, "ACTIVE_MODEL", "gemini")
    message = DummyMessage("", chat_id=819)
    callback = DummyCallback(bot.SESSION_LIVE_RESUME_CALLBACK, message)
    state = make_state(message)

    asyncio.run(bot.on_session_live_resume_callback(callback, state))

    assert asyncio.run(state.get_state()) is None
    assert callback.answers[-1] == ("暂仅支持 Codex 会话 resume。", True)
    assert not message.calls


def test_session_resume_input_restarts_main_tmux_and_binds_watcher(monkeypatch, tmp_path: Path) -> None:
    """会话实况 Resume：输入 sessionId 后应复用主会话恢复逻辑。"""

    pointer = tmp_path / "current_session.txt"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    session_uuid = "019d0f8d-fd9d-7000-a111-123456789abf"
    session_file = tmp_path / "sessions" / f"rollout-2026-05-01T00-00-00-{session_uuid}.jsonl"
    _write_codex_session(session_file, session_uuid=session_uuid, cwd=workdir)
    session_file.write_text(session_file.read_text(encoding="utf-8") + "historical output\n", encoding="utf-8")

    monkeypatch.setattr(bot, "ACTIVE_MODEL", "codex")
    monkeypatch.setattr(bot, "CODEX_SESSION_FILE_PATH", str(pointer))
    monkeypatch.setenv("MODEL_WORKDIR", str(workdir))
    monkeypatch.setattr(bot, "MODEL_SESSION_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(bot, "CODEX_SESSIONS_ROOT", "")
    monkeypatch.setattr(bot, "MODEL_SESSION_GLOB", "rollout-*.jsonl")

    restarted: list[str] = []

    async def fake_restart(resume_session_id: str) -> tuple[bool, str]:
        restarted.append(resume_session_id)
        return True, ""

    delivered_calls: list[tuple[int, Path]] = []

    async def fake_deliver(chat_id: int, session_path: Path) -> bool:
        delivered_calls.append((chat_id, session_path))
        return False

    async def fake_interrupt(_chat_id: int) -> None:
        return None

    monkeypatch.setattr(bot, "_restart_main_tmux_with_resume_session", fake_restart)
    monkeypatch.setattr(bot, "_deliver_pending_messages", fake_deliver)
    monkeypatch.setattr(bot, "_interrupt_long_poll", fake_interrupt)

    class DummyTask:
        def __init__(self):
            self._done = False

        def done(self) -> bool:
            return self._done

        def cancel(self) -> None:
            self._done = True

    created_tasks: list = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        return DummyTask()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    bot.CHAT_SESSION_MAP.clear()
    bot.CHAT_WATCHERS.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()
    message = DummyMessage(f"sessionId : {session_file.stem}", chat_id=909)
    state = make_state(message)
    asyncio.run(state.set_state(bot.SessionResumeStates.waiting_session_id))

    asyncio.run(bot.on_session_resume_session_id_input(message, state))

    assert restarted == [session_uuid]
    assert asyncio.run(state.get_state()) is None
    assert pointer.read_text(encoding="utf-8").strip() == str(session_file)
    assert bot.CHAT_SESSION_MAP[909] == str(session_file)
    assert bot.SESSION_OFFSETS[str(session_file)] == session_file.stat().st_size
    assert delivered_calls == [(909, session_file)]
    assert "已恢复并绑定为主会话" in message.calls[-1]
    assert str(session_file.stem) in message.calls[-1]

    for coro in created_tasks:
        try:
            coro.close()  # type: ignore[attr-defined]
        except Exception:
            pass

    bot.CHAT_SESSION_MAP.clear()
    bot.CHAT_WATCHERS.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_LAST_MESSAGE.clear()


def test_session_resume_input_cancel_clears_state(monkeypatch) -> None:
    """会话实况 Resume：用户输入取消时不应执行任何 tmux 恢复。"""

    async def fake_restart(_resume_session_id: str) -> tuple[bool, str]:
        raise AssertionError("取消输入不应重启 tmux")

    monkeypatch.setattr(bot, "_restart_main_tmux_with_resume_session", fake_restart)
    message = DummyMessage("取消", chat_id=910)
    state = make_state(message)
    asyncio.run(state.set_state(bot.SessionResumeStates.waiting_session_id))

    asyncio.run(bot.on_session_resume_session_id_input(message, state))

    assert asyncio.run(state.get_state()) is None
    assert "已取消 resume 会话" in message.calls[-1]
