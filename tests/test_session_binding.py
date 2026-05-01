import asyncio
import json
from pathlib import Path
import sys

import pytest

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
        self.calls: list[str] = []

    async def answer(self, text: str, **_kwargs):
        self.calls.append(text)
        return None


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
