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
from parallel_runtime import BranchRef, CommonBranchRef, build_parallel_commit_message  # noqa: E402
from tasks import TaskRecord  # noqa: E402


class DummyMessage:
    def __init__(self, *, chat_id: int = 1, user_id: int = 1):
        self.calls = []
        self.edits = []
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester")
        self.message_id = 100
        self.date = datetime.now(bot.UTC)
        self.text = None
        self.caption = None

    async def answer(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id + len(self.calls), chat=self.chat)

    async def edit_text(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.edits.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id, chat=self.chat)


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


def test_parallel_common_branch_keyboard_marks_current_and_has_individual_entry():
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=None,
        push_mode=None,
        supplement=None,
        repo_options=[],
        selections={},
        current_branch_labels={},
        common_branch_options=[
            CommonBranchRef(name="develop", source="local", current_count=2, total_repos=2),
            CommonBranchRef(name="origin/develop", source="remote", remote="origin", current_count=0, total_repos=2),
        ],
        selection_mode="bulk",
    )

    markup = bot._build_parallel_common_branch_keyboard(session, page=0)
    labels = [button.text for row in markup.inline_keyboard for button in row if button.text]

    assert any(label.startswith("📍 develop") and "（当前）" in label for label in labels)
    assert "🧩 逐个选择" in labels


def test_begin_parallel_launch_prefers_common_branch_selector(monkeypatch):
    message = DummyMessage()

    monkeypatch.setattr(
        bot,
        "discover_git_repos",
        lambda _base_dir, include_nested=False: [
            ("backend-java", Path("/tmp/backend-java"), "backend-java"),
            ("frontend-admin", Path("/tmp/frontend-admin"), "frontend-admin"),
        ],
    )
    monkeypatch.setattr(bot, "get_current_branch_state", lambda _repo_path: ("develop", "develop"))
    monkeypatch.setattr(
        bot,
        "list_branch_refs",
        lambda _repo_path, current_local_branch=None: [
            BranchRef(name="develop", source="local", is_current=True),
            BranchRef(name="origin/develop", source="remote", remote="origin"),
        ],
    )

    asyncio.run(
        bot._begin_parallel_launch(
            task=_task(),
            chat_id=message.chat.id,
            origin_message=message,
            actor=None,
            push_mode=None,
            supplement=None,
        )
    )

    assert message.calls, "首屏应展示共同分支批量选择"
    assert "共用的基线分支" in message.calls[-1][0]


def test_begin_parallel_launch_ignores_local_only_root_repo_in_common_branch_scope(monkeypatch):
    message = DummyMessage()

    root_path = Path("/tmp/workspace-root")
    backend_path = Path("/tmp/backend-java")
    frontend_path = Path("/tmp/web-base")

    monkeypatch.setattr(
        bot,
        "discover_git_repos",
        lambda _base_dir, include_nested=False: [
            ("__root__", root_path, "."),
            ("backend-java", backend_path, "backend-java"),
            ("web-base", frontend_path, "web-base"),
        ],
    )

    def fake_current_branch(repo_path: Path):
        if repo_path == root_path:
            return ("main", "main")
        return ("master", "master")

    def fake_list_branch_refs(repo_path: Path, current_local_branch=None):
        if repo_path == root_path:
            return [BranchRef(name="main", source="local", is_current=True)]
        return [
            BranchRef(name="master", source="local", is_current=True),
            BranchRef(name="origin/develop", source="remote", remote="origin"),
            BranchRef(name="origin/master", source="remote", remote="origin"),
        ]

    monkeypatch.setattr(bot, "get_current_branch_state", fake_current_branch)
    monkeypatch.setattr(bot, "list_branch_refs", fake_list_branch_refs)

    asyncio.run(
        bot._begin_parallel_launch(
            task=_task(),
            chat_id=message.chat.id,
            origin_message=message,
            actor=None,
            push_mode=None,
            supplement=None,
        )
    )

    assert message.calls, "忽略本地根仓库后，应进入共同分支批量选择"
    prompt_text = message.calls[-1][0]
    assert "共用的基线分支" in prompt_text
    assert "共同分支计算范围：2/3 个仓库" in prompt_text
    assert "已忽略仓库：.（根仓库无远端分支）" in prompt_text


def test_parallel_common_branch_select_populates_all_repo_selections(monkeypatch):
    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=message,
        push_mode=None,
        supplement=None,
        repo_options=[
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [
                    BranchRef(name="develop", source="local", is_current=True),
                    BranchRef(name="origin/develop", source="remote", remote="origin"),
                ],
            ),
            (
                "frontend-admin",
                Path("/tmp/frontend-admin"),
                "frontend-admin",
                [
                    BranchRef(name="develop", source="local", is_current=True),
                    BranchRef(name="origin/develop", source="remote", remote="origin"),
                ],
            ),
        ],
        selections={},
        current_branch_labels={"backend-java": "develop", "frontend-admin": "develop"},
        common_branch_options=[CommonBranchRef(name="develop", source="local", current_count=2, total_repos=2)],
        selection_mode="bulk",
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    callback = DummyCallback(f"{bot.PARALLEL_COMMON_BRANCH_SELECT_PREFIX}demo:0", message)
    asyncio.run(bot.on_parallel_common_branch_select_callback(callback))

    assert session.selection_mode == "bulk"
    assert set(session.selections) == {"backend-java", "frontend-admin"}
    assert all(branch.name == "develop" for branch in session.selections.values())
    assert message.edits, "批量选择后应直接进入摘要确认页"
    assert "以下仓库将进入并行处理" in message.edits[-1][0]


def test_parallel_branch_individual_callback_edits_first_repo():
    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=message,
        push_mode=None,
        supplement=None,
        repo_options=[
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [BranchRef(name="develop", source="local")],
            )
        ],
        selections={"backend-java": BranchRef(name="develop", source="local")},
        current_branch_labels={"backend-java": "develop"},
        common_branch_options=[CommonBranchRef(name="develop", source="local", current_count=1, total_repos=1)],
        selection_mode="bulk",
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    callback = DummyCallback(f"{bot.PARALLEL_BRANCH_INDIVIDUAL_PREFIX}demo", message)
    asyncio.run(bot.on_parallel_branch_individual_callback(callback))

    assert session.selection_mode == "individual"
    assert session.selections == {}
    assert message.edits, "切到逐个选择后应编辑为首个仓库的分支页"
    assert "当前仓库：backend-java" in message.edits[-1][0]


def test_parallel_branch_page_callback_edits_same_message():
    message = DummyMessage()
    callback = DummyCallback("parallel:branch_page:demo:0:1", message)
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=message,
        push_mode=None,
        supplement=None,
        repo_options=[
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [
                    BranchRef(name=f"feature/{idx}", source="local")
                    for idx in range(12)
                ],
            )
        ],
        selections={},
        current_branch_labels={"backend-java": "feature/0"},
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    asyncio.run(bot.on_parallel_branch_page_callback(callback))

    assert message.edits, "翻页应编辑同一条消息"
    assert not message.calls, "翻页不应新增消息"


def test_parallel_branch_select_callback_edits_same_message_for_next_repo_and_summary():
    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=message,
        push_mode=None,
        supplement=None,
        repo_options=[
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [BranchRef(name="develop", source="local")],
            ),
            (
                "frontend-admin",
                Path("/tmp/frontend-admin"),
                "frontend-admin",
                [BranchRef(name="main", source="local")],
            ),
        ],
        selections={},
        current_branch_labels={"backend-java": "develop", "frontend-admin": "main"},
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    callback_first = DummyCallback("parallel:branch_select:demo:0:0", message)
    asyncio.run(bot.on_parallel_branch_select_callback(callback_first))
    assert message.edits, "选择后进入下一仓库应编辑消息"
    assert not message.calls, "进入下一仓库不应新增消息"

    callback_second = DummyCallback("parallel:branch_select:demo:1:0", message)
    asyncio.run(bot.on_parallel_branch_select_callback(callback_second))
    assert len(message.edits) >= 2, "最后展示摘要也应继续编辑同一条消息"
    assert not message.calls, "展示开始处理按钮不应新增消息"


def test_parallel_branch_confirm_callback_edits_same_message_to_processing(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=message,
        push_mode=None,
        supplement=None,
        repo_options=[
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [BranchRef(name="develop", source="local")],
            )
        ],
        selections={"backend-java": BranchRef(name="develop", source="local")},
        current_branch_labels={"backend-java": "develop"},
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    monkeypatch.setattr(
        bot,
        "prepare_parallel_workspace",
        lambda **_kwargs: [],
    )

    async def fake_start_parallel_tmux_session(task, workspace_root):
        return "vibe-par-demo", tmp_path / "pointer.txt"

    async def fake_upsert_session(**_kwargs):
        return None

    async def fake_push_task_to_model(*_args, **_kwargs):
        return True, "PROMPT", tmp_path / "session.jsonl"

    async def fake_send_preview(*_args, **_kwargs):
        return None

    async def fake_send_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_start_parallel_tmux_session", fake_start_parallel_tmux_session)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "upsert_session", fake_upsert_session)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task_to_model)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_send_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_ack)

    callback = DummyCallback("parallel:branch_confirm:demo", message)
    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))

    assert message.edits, "点击开始并行处理应先覆盖原消息为处理中提示"
    assert message.edits[0][0] == "正在创建并行副本并启动并行 CLI，请稍候……"


def test_parallel_branch_confirm_callback_replaces_processing_message_with_summary(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=message,
        push_mode=None,
        supplement=None,
        repo_options=[
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [BranchRef(name="develop", source="local")],
            )
        ],
        selections={"backend-java": BranchRef(name="develop", source="local")},
        current_branch_labels={"backend-java": "develop"},
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    monkeypatch.setattr(bot, "prepare_parallel_workspace", lambda **_kwargs: [])

    async def fake_start_parallel_tmux_session(task, workspace_root):
        return "vibe-par-demo", tmp_path / "pointer.txt"

    async def fake_upsert_session(**_kwargs):
        return None

    async def fake_push_task_to_model(*_args, **_kwargs):
        return True, "PROMPT", tmp_path / "session.jsonl"

    async def fake_send_preview(*_args, **_kwargs):
        return None

    async def fake_send_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_start_parallel_tmux_session", fake_start_parallel_tmux_session)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "upsert_session", fake_upsert_session)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task_to_model)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_send_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_ack)

    callback = DummyCallback("parallel:branch_confirm:demo", message)
    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))

    assert len(message.edits) >= 2, "成功后应继续覆盖同一条消息为摘要"
    assert message.edits[0][0] == "正在创建并行副本并启动并行 CLI，请稍候……"
    assert "已创建并行开发副本（原目录未改动）：" in message.edits[-1][0]
    assert not message.calls, "成功收口不应再额外新增摘要消息"


def test_parallel_branch_confirm_callback_passes_source_root_for_full_copy(monkeypatch, tmp_path: Path):
    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=message,
        push_mode=None,
        supplement=None,
        repo_options=[
            (
                "backend-java",
                Path("/tmp/project-root/backend-java"),
                "backend-java",
                [BranchRef(name="develop", source="local")],
            )
        ],
        selections={"backend-java": BranchRef(name="develop", source="local")},
        current_branch_labels={"backend-java": "develop"},
        base_dir=Path("/tmp/project-root"),
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    captured: dict[str, object] = {}

    def fake_prepare_parallel_workspace(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(bot, "prepare_parallel_workspace", fake_prepare_parallel_workspace)

    async def fake_start_parallel_tmux_session(task, workspace_root):
        return "vibe-par-demo", tmp_path / "pointer.txt"

    async def fake_upsert_session(**_kwargs):
        return None

    async def fake_push_task_to_model(*_args, **_kwargs):
        return True, "PROMPT", tmp_path / "session.jsonl"

    async def fake_send_preview(*_args, **_kwargs):
        return None

    async def fake_send_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_start_parallel_tmux_session", fake_start_parallel_tmux_session)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "upsert_session", fake_upsert_session)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task_to_model)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_send_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_ack)

    callback = DummyCallback("parallel:branch_confirm:demo", message)
    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))

    assert captured["source_root"] == Path("/tmp/project-root")
