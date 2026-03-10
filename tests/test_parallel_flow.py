from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot  # noqa: E402
from parallel_runtime import BranchRef, build_parallel_commit_message  # noqa: E402
from tasks import TaskRecord  # noqa: E402


class DummyMessage:
    def __init__(self, *, chat_id: int = 1, user_id: int = 1):
        self.calls = []
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester")
        self.message_id = 100
        self.date = datetime.now(bot.UTC)
        self.text = None
        self.caption = None

    async def answer(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id + len(self.calls), chat=self.chat)


class DummyCallback:
    def __init__(self, data: str, message: DummyMessage):
        self.data = data
        self.message = message
        self.answers = []
        self.from_user = SimpleNamespace(id=1, full_name="Tester")

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


def make_state(message: DummyMessage) -> tuple[FSMContext, MemoryStorage]:
    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=999, chat_id=message.chat.id, user_id=message.from_user.id),
    )
    return state, storage


def _task() -> TaskRecord:
    return TaskRecord(
        id="TASK_0001",
        project_slug="demo",
        title="调研任务",
        status="research",
        priority=3,
        task_type="requirement",
        tags=(),
        due_date=None,
        description="需要调研的事项",
        parent_id=None,
        root_id="TASK_0001",
        depth=0,
        lineage="0001",
        created_at="2025-01-01T00:00:00+08:00",
        updated_at="2025-01-01T00:00:00+08:00",
        archived=False,
    )


def test_push_model_starts_with_dispatch_target_choice(monkeypatch):
    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0001", message)
    state, _storage = make_state(message)

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0001"
        return _task()

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_dispatch_target.state
        assert callback.answers and "请选择处理方式" in (callback.answers[-1][0] or "")
        assert message.calls, "应提示用户选择当前 CLI 或并行 CLI"
        prompt_text, _, reply_markup, _ = message.calls[-1]
        assert prompt_text == bot._build_push_dispatch_target_prompt()
        assert isinstance(reply_markup, ReplyKeyboardMarkup)

    asyncio.run(_scenario())


def test_model_quick_reply_keyboard_includes_parallel_actions():
    markup = bot._build_model_quick_reply_keyboard(
        task_id="TASK_0001",
        parallel_task_title="调研任务",
        enable_parallel_actions=True,
    )
    callback_data = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert "task:detail:TASK_0001" in callback_data
    assert f"{bot.PARALLEL_REPLY_CALLBACK_PREFIX}TASK_0001" in callback_data
    assert f"{bot.PARALLEL_COMMIT_CALLBACK_PREFIX}TASK_0001" in callback_data


def test_parallel_reply_mode_auto_prefixes_next_message(monkeypatch):
    origin = DummyMessage()
    callback = DummyCallback(f"{bot.PARALLEL_REPLY_CALLBACK_PREFIX}TASK_0001", origin)

    recorded: list[tuple[str, object]] = []

    async def fake_handle_request_input_custom_text_message(_message):
        return False

    async def fake_handle_command_trigger_message(_message, _prompt, _state):
        return False

    async def fake_handle_prompt_dispatch(_message, prompt: str, **kwargs):
        recorded.append((prompt, kwargs.get("dispatch_context")))

    monkeypatch.setattr(bot, "_handle_request_input_custom_text_message", fake_handle_request_input_custom_text_message)
    monkeypatch.setattr(bot, "_handle_command_trigger_message", fake_handle_command_trigger_message)
    monkeypatch.setattr(bot, "_handle_prompt_dispatch", fake_handle_prompt_dispatch)

    dispatch_context = bot.ParallelDispatchContext(
        task_id="TASK_0001",
        tmux_session="vibe-par-demo",
        pointer_file=Path("/tmp/demo-pointer.txt"),
        workspace_root=Path("/tmp/demo-workspace"),
    )

    async def fake_active_parallel_session(task_id: str):
        return {
            "task_id": task_id,
            "tmux_session": dispatch_context.tmux_session,
            "pointer_file": str(dispatch_context.pointer_file),
            "workspace_root": str(dispatch_context.workspace_root),
        }

    monkeypatch.setattr(bot, "_get_active_parallel_session_for_task", fake_active_parallel_session)

    async def _scenario() -> None:
        await bot.on_parallel_reply_callback(callback)
        assert origin.calls and origin.calls[-1][0] == "已进入 /TASK_0001 回复模式。"
        reply_keyboard = origin.calls[-1][2]
        labels = [button.text for row in reply_keyboard.keyboard for button in row]
        assert labels == ["取消"]

        message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
        message.text = "继续完善方案"
        state, _storage = make_state(message)
        await bot.on_text(message, state)

        assert recorded == [("继续完善方案", dispatch_context)]
        assert origin.chat.id not in bot.CHAT_PARALLEL_REPLY_TARGETS

    asyncio.run(_scenario())


def test_parallel_commit_message_uses_task_type_prefix():
    subject, body = build_parallel_commit_message(_task(), repo_name="web-base")
    assert subject == "feat(TASK_0001): 调研任务"
    assert "任务编码: /TASK_0001" in body
    assert "任务标题: 调研任务" in body
    assert "仓库: web-base" in body


def test_parallel_branch_title_shows_current_branch():
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=None,
        push_mode=None,
        supplement=None,
        repo_options=[
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [
                    BranchRef(name="develop", source="local", is_current=True),
                    BranchRef(name="feature/demo", source="local"),
                    BranchRef(name="origin/develop", source="remote", remote="origin"),
                ],
            )
        ],
        selections={},
        current_branch_labels={"backend-java": "develop"},
    )

    title = bot._build_parallel_branch_title(session, 0)

    assert "当前仓库：backend-java" in title
    assert "当前分支：develop" in title


def test_parallel_branch_keyboard_marks_current_branch_and_keeps_it_first():
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=None,
        push_mode=None,
        supplement=None,
        repo_options=[
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [
                    BranchRef(name="develop", source="local", is_current=True),
                    BranchRef(name="feature/demo", source="local"),
                    BranchRef(name="origin/develop", source="remote", remote="origin"),
                ],
            )
        ],
        selections={},
        current_branch_labels={"backend-java": "develop"},
    )

    markup = bot._build_parallel_branch_keyboard(session, repo_index=0, page=0)
    branch_rows = [row for row in markup.inline_keyboard if row and row[0].callback_data and row[0].callback_data.startswith(bot.PARALLEL_BRANCH_SELECT_PREFIX)]

    assert branch_rows[0][0].text.startswith("📍 develop")
    assert "（当前）" in branch_rows[0][0].text
