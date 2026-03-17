from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

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
        self.reply_markup_edits = []
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester")
        self.message_id = 100
        self.date = datetime.now(bot.UTC)
        self.text = None
        self.caption = None
        self.reply_markup = None

    async def answer(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id + len(self.calls), chat=self.chat)

    async def edit_text(self, text: str, parse_mode=None, reply_markup=None, **kwargs):
        self.edits.append((text, parse_mode, reply_markup, kwargs))
        return SimpleNamespace(message_id=self.message_id, chat=self.chat)

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.reply_markup = reply_markup
        self.reply_markup_edits.append((reply_markup, kwargs))
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


def _callback_data_list(markup) -> list[str]:
    if markup is None:
        return []
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]


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


def test_existing_cli_target_with_multiple_sessions_opens_session_picker(monkeypatch):
    """现有 CLI 会话处理：当主会话外还存在活动并行会话时，应先让用户选择目标会话。"""

    message = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0001", message)
    state, _storage = make_state(message)

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0001"
        return _task()

    async def fake_list_project_live_sessions():
        return [
            bot.SessionLiveEntry(key="main", label="💻 主会话（vibe）", tmux_session="vibe", kind="main"),
            bot.SessionLiveEntry(
                key="parallel:TASK_0115",
                label="/TASK_0115 并行会话",
                tmux_session="vibe-par-demo-115",
                kind="parallel",
                task_id="TASK_0115",
            ),
        ]

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_list_project_live_sessions", fake_list_project_live_sessions)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)

        dispatch_target_message = DummyMessage()
        dispatch_target_message.text = bot.PUSH_TARGET_CURRENT
        await bot.on_task_push_model_dispatch_target(dispatch_target_message, state)

        assert await state.get_state() == bot.TaskPushStates.waiting_existing_session.state
        assert dispatch_target_message.calls, "应展示现有会话选择页"
        prompt_text, _, reply_markup, _ = dispatch_target_message.calls[-1]
        assert prompt_text == bot._build_push_existing_session_prompt(session_count=2)
        assert isinstance(reply_markup, InlineKeyboardMarkup)
        callback_data = [
            button.callback_data
            for row in reply_markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        assert bot.PUSH_EXISTING_SESSION_MAIN_CALLBACK in callback_data
        assert f"{bot.PUSH_EXISTING_SESSION_PARALLEL_PREFIX}TASK_0115" in callback_data
        assert bot.PUSH_EXISTING_SESSION_REFRESH_CALLBACK in callback_data
        assert bot.PUSH_EXISTING_SESSION_CANCEL_CALLBACK in callback_data

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
    origin.reply_markup = bot._build_model_quick_reply_keyboard(
        task_id="TASK_0001",
        parallel_task_title="调研任务",
        enable_parallel_actions=True,
        parallel_callback_payload="TASK_0001",
    )

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
        assert origin.reply_markup_edits, "回复模式进入成功后应移除已点击的回复按钮"
        callback_data = _callback_data_list(origin.reply_markup_edits[-1][0])
        assert f"{bot.PARALLEL_REPLY_CALLBACK_PREFIX}TASK_0001" not in callback_data
        assert f"{bot.PARALLEL_COMMIT_CALLBACK_PREFIX}TASK_0001" in callback_data
        assert f"{bot.MODEL_TASK_TO_TEST_PREFIX}TASK_0001" in callback_data
        assert "task:detail:TASK_0001" in callback_data

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


def test_format_parallel_operation_lines_groups_failures_success_and_skipped():
    """结果消息应先给总览，再按失败/成功/跳过分组展示，并缩进多行错误。"""

    text = bot._format_parallel_operation_lines(
        "并行分支提交结果：",
        [
            bot.RepoOperationResult("repo-failed", "repo-failed", False, "failed", "第一行失败\r\n第二行失败"),
            bot.RepoOperationResult("repo-pushed", "repo-pushed", True, "pushed", "提交并推送成功"),
            bot.RepoOperationResult("repo-skipped", "repo-skipped", True, "skipped", "无改动，已跳过"),
            bot.RepoOperationResult("repo-local", "repo-local", True, "committed", "已本地提交，未配置远端，已跳过推送"),
        ],
    )

    assert text.splitlines()[0] == "并行分支提交结果"
    assert "总览：4 个仓库｜失败 1｜成功 2｜跳过 1" in text
    assert text.index("❌ 失败（1）") < text.index("✅ 成功（2）") < text.index("⏭️ 已跳过（1）")
    assert "- repo-failed" in text
    assert "  第一行失败" in text
    assert "  第二行失败" in text
    assert "- repo-pushed" in text
    assert "- repo-local" in text
    assert "- repo-skipped" in text


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


def test_parallel_common_branch_select_redirects_to_first_unselected_repo_when_ignored_root_exists():
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
                "__root__",
                Path("/tmp/workspace-root"),
                ".",
                [BranchRef(name="main", source="local", is_current=True)],
            ),
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
        current_branch_labels={"__root__": "main", "backend-java": "develop", "frontend-admin": "develop"},
        common_branch_options=[CommonBranchRef(name="develop", source="local", current_count=2, total_repos=2)],
        common_branch_repo_keys={"backend-java", "frontend-admin"},
        common_branch_scope_repo_count=2,
        common_branch_ignored_repos=["."],
        selection_mode="bulk",
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    callback = DummyCallback(f"{bot.PARALLEL_COMMON_BRANCH_SELECT_PREFIX}demo:0", message)
    asyncio.run(bot.on_parallel_common_branch_select_callback(callback))

    assert callback.answers[-1] == ("已批量应用共同分支 develop，请继续补选剩余仓库。", False)
    assert session.selection_mode == "individual"
    assert set(session.selections) == {"backend-java", "frontend-admin"}
    assert message.edits, "存在待补选仓库时，应直接进入逐个补选页"
    assert "当前仓库：." in message.edits[-1][0]


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
    assert session.selections == {"backend-java": BranchRef(name="develop", source="local")}
    assert message.edits, "全部已选时，应直接回到摘要确认页"
    assert "以下仓库将进入并行处理" in message.edits[-1][0]


def test_parallel_branch_individual_callback_keeps_bulk_selections_and_starts_from_first_unselected_repo():
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
                "__root__",
                Path("/tmp/workspace-root"),
                ".",
                [BranchRef(name="main", source="local", is_current=True)],
            ),
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [BranchRef(name="develop", source="local", is_current=True)],
            ),
        ],
        selections={"backend-java": BranchRef(name="develop", source="local", is_current=True)},
        current_branch_labels={"__root__": "main", "backend-java": "develop"},
        common_branch_options=[CommonBranchRef(name="develop", source="local", current_count=1, total_repos=1)],
        common_branch_scope_repo_count=1,
        common_branch_ignored_repos=["."],
        selection_mode="bulk",
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    callback = DummyCallback(f"{bot.PARALLEL_BRANCH_INDIVIDUAL_PREFIX}demo", message)
    asyncio.run(bot.on_parallel_branch_individual_callback(callback))

    assert session.selection_mode == "individual"
    assert set(session.selections) == {"backend-java"}
    assert message.edits, "应从首个未选择仓库开始补选"
    assert "当前仓库：." in message.edits[-1][0]


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


def test_parallel_branch_select_callback_skips_already_selected_repos_and_finishes_with_summary():
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
                "__root__",
                Path("/tmp/workspace-root"),
                ".",
                [BranchRef(name="main", source="local", is_current=True)],
            ),
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [BranchRef(name="develop", source="local", is_current=True)],
            ),
            (
                "frontend-admin",
                Path("/tmp/frontend-admin"),
                "frontend-admin",
                [BranchRef(name="develop", source="local", is_current=True)],
            ),
        ],
        selections={
            "backend-java": BranchRef(name="develop", source="local", is_current=True),
            "frontend-admin": BranchRef(name="develop", source="local", is_current=True),
        },
        current_branch_labels={"__root__": "main", "backend-java": "develop", "frontend-admin": "develop"},
        selection_mode="individual",
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    callback = DummyCallback("parallel:branch_select:demo:0:0", message)
    asyncio.run(bot.on_parallel_branch_select_callback(callback))

    assert session.selections["__root__"].name == "main"
    assert message.edits, "补选完成后应直接进入摘要页"
    assert "以下仓库将进入并行处理" in message.edits[-1][0]
    assert "当前仓库：" not in message.edits[-1][0]


def test_parallel_branch_confirm_callback_keeps_session_when_selection_incomplete():
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
                "__root__",
                Path("/tmp/workspace-root"),
                ".",
                [BranchRef(name="main", source="local", is_current=True)],
            ),
            (
                "backend-java",
                Path("/tmp/backend-java"),
                "backend-java",
                [BranchRef(name="develop", source="local", is_current=True)],
            ),
        ],
        selections={"backend-java": BranchRef(name="develop", source="local", is_current=True)},
        current_branch_labels={"__root__": "main", "backend-java": "develop"},
        selection_mode="individual",
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    callback = DummyCallback("parallel:branch_confirm:demo", message)
    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))

    assert callback.answers[-1] == ("仍有仓库未选择基线分支", True)
    assert "demo" in bot.PARALLEL_LAUNCH_SESSIONS
    assert not message.edits


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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
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
    assert callback.answers == [(None, False)]
    assert message.edits[0][0] == "正在准备并行副本，请稍候……"


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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
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

    assert len(message.edits) >= 3, "成功后应继续覆盖同一条消息为阶段提示与摘要"
    assert message.edits[0][0] == "正在准备并行副本，请稍候……"
    assert any(text == "正在启动并行 CLI，请稍候……" for text, *_rest in message.edits)
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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
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


def test_parallel_branch_confirm_callback_prompts_for_prefix_when_missing():
    """仓库分支全部选完后，首次确认应先进入分支前缀输入，而不是直接创建并行目录。"""

    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=message.chat.id,
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

    callback = DummyCallback("parallel:branch_confirm:demo", message)
    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))

    assert callback.answers[-1] == ("请输入分支前缀；发送取消将使用默认前缀", False)
    assert bot.CHAT_PARALLEL_BRANCH_PREFIX_INPUTS[message.chat.id] == "demo"
    assert message.calls, "应发送新消息进入前缀输入页并展示底部菜单按钮"
    prompt_text, _, reply_markup, _ = message.calls[-1]
    assert "请输入本次并行任务的分支前缀（例如：Sprint001）。" in prompt_text
    assert "示例（自定义）：Sprint001/TASK\\_0001-调研任务" in prompt_text
    assert "示例（默认）：vibego/TASK\\_0001-调研任务" in prompt_text
    labels = [button.text for row in reply_markup.keyboard for button in row]
    assert labels == ["取消"]


def test_parallel_branch_prefix_cancel_uses_default_prefix(monkeypatch, tmp_path: Path):
    """前缀输入页点击取消时，应回退默认前缀并继续创建并行目录。"""

    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=message.chat.id,
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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session
    bot.CHAT_PARALLEL_BRANCH_PREFIX_INPUTS[message.chat.id] = "demo"

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

    callback = DummyCallback(f"{bot.PARALLEL_BRANCH_PREFIX_CANCEL_PREFIX}demo", message)
    asyncio.run(bot.on_parallel_branch_prefix_cancel_callback(callback))

    assert captured["branch_prefix"] == bot.DEFAULT_PARALLEL_BRANCH_PREFIX
    assert session.branch_prefix == bot.DEFAULT_PARALLEL_BRANCH_PREFIX
    assert message.chat.id not in bot.CHAT_PARALLEL_BRANCH_PREFIX_INPUTS


def test_parallel_branch_prefix_text_uses_custom_prefix_and_continues(monkeypatch, tmp_path: Path):
    """输入自定义前缀后，应以 prefix/TASK_... 创建任务分支。"""

    origin = DummyMessage(chat_id=9, user_id=9)
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=origin.chat.id,
        actor=None,
        origin_message=origin,
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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session
    bot.CHAT_PARALLEL_BRANCH_PREFIX_INPUTS[origin.chat.id] = "demo"

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

    message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
    message.text = "TRADE114"
    state, _storage = make_state(message)

    asyncio.run(bot.on_text(message, state))

    assert captured["branch_prefix"] == "TRADE114"
    assert session.branch_prefix == "TRADE114"
    assert origin.chat.id not in bot.CHAT_PARALLEL_BRANCH_PREFIX_INPUTS


def test_parallel_dispatch_target_clears_fsm_before_branch_prefix_input(monkeypatch, tmp_path: Path):
    """选择并行 CLI 后，应先退出 dispatch_target 状态，避免分支前缀输入被旧处理器吞掉。"""

    origin = DummyMessage()
    callback = DummyCallback("task:push_model:TASK_0001", origin)
    state, _storage = make_state(origin)

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0001"
        return _task()

    async def fake_begin_parallel_launch(
        *,
        task,
        chat_id: int,
        origin_message,
        actor,
        push_mode,
        send_mode,
        supplement,
    ) -> None:
        bot.PARALLEL_LAUNCH_SESSIONS["demo"] = bot.ParallelLaunchSession(
            token="demo",
            task=task,
            chat_id=chat_id,
            actor=actor,
            origin_message=origin_message,
            push_mode=push_mode,
            send_mode=send_mode,
            supplement=supplement,
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

    captured: dict[str, object] = {}

    def fake_prepare_parallel_workspace(**kwargs):
        captured.update(kwargs)
        return []

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

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_begin_parallel_launch", fake_begin_parallel_launch)
    monkeypatch.setattr(bot, "prepare_parallel_workspace", fake_prepare_parallel_workspace)
    monkeypatch.setattr(bot, "_start_parallel_tmux_session", fake_start_parallel_tmux_session)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "upsert_session", fake_upsert_session)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task_to_model)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_send_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_ack)

    async def _scenario() -> None:
        await bot.on_task_push_model(callback, state)
        assert await state.get_state() == bot.TaskPushStates.waiting_dispatch_target.state

        dispatch_target_message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
        dispatch_target_message.text = bot.PUSH_TARGET_PARALLEL
        await bot.on_task_push_model_dispatch_target(dispatch_target_message, state)

        assert await state.get_state() is None
        assert "demo" in bot.PARALLEL_LAUNCH_SESSIONS

        confirm_callback = DummyCallback(f"{bot.PARALLEL_BRANCH_CONFIRM_PREFIX}demo", origin)
        await bot.on_parallel_branch_confirm_callback(confirm_callback)

        prefix_message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
        prefix_message.text = "TRADE114"
        await bot.on_text(prefix_message, state)

    asyncio.run(_scenario())

    assert captured["branch_prefix"] == "TRADE114"
    assert origin.chat.id not in bot.CHAT_PARALLEL_BRANCH_PREFIX_INPUTS


def test_parallel_branch_confirm_callback_acknowledges_callback_before_prepare(monkeypatch, tmp_path: Path):
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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    callback = DummyCallback("parallel:branch_confirm:demo", message)

    def fake_prepare_parallel_workspace(**_kwargs):
        assert callback.answers == [(None, False)], "进入重操作前应先确认 callback，避免超时失效"
        return []

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

    monkeypatch.setattr(bot, "prepare_parallel_workspace", fake_prepare_parallel_workspace)
    monkeypatch.setattr(bot, "_start_parallel_tmux_session", fake_start_parallel_tmux_session)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "upsert_session", fake_upsert_session)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task_to_model)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_send_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_ack)

    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))


def test_parallel_branch_confirm_callback_falls_back_to_chat_when_failure_callback_expires(monkeypatch):
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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    async def flaky_answer(text: str | None = None, show_alert: bool = False):
        if text == "并行创建失败":
            raise TelegramBadRequest(method="answerCallbackQuery", message="Bad Request: query is too old")
        callback.answers.append((text, show_alert))

    monkeypatch.setattr(
        bot,
        "prepare_parallel_workspace",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("ssh: connect to host gitlab.cckggroup.com port 10002: Connection reset by peer")),
    )

    callback = DummyCallback("parallel:branch_confirm:demo", message)
    monkeypatch.setattr(callback, "answer", flaky_answer)

    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))

    assert callback.answers == [(None, False)]
    assert message.calls, "callback 失效后仍应回退为普通聊天消息"
    assert "并行创建失败：" in message.calls[-1][0]
    assert "Connection reset by peer" in message.calls[-1][0]


def test_parallel_branch_confirm_callback_reports_prepare_timeout(monkeypatch):
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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    def slow_prepare_parallel_workspace(**_kwargs):
        time.sleep(0.05)
        return []

    async def should_not_start_tmux(*_args, **_kwargs):
        raise AssertionError("准备阶段超时后不应继续启动并行 CLI")

    monkeypatch.setattr(bot, "prepare_parallel_workspace", slow_prepare_parallel_workspace)
    monkeypatch.setattr(bot, "PARALLEL_WORKSPACE_PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(bot, "_start_parallel_tmux_session", should_not_start_tmux)

    callback = DummyCallback("parallel:branch_confirm:demo", message)
    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))

    assert callback.answers == [(None, False)]
    assert message.calls, "准备超时后应向聊天明确回报失败"
    assert "并行副本准备超时" in message.calls[-1][0]


def test_parallel_branch_confirm_callback_stops_when_push_task_to_model_fails(monkeypatch, tmp_path: Path):
    """并行 tmux 已启动但首次推送失败时，不得继续展示成功摘要或预览。"""

    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=message,
        push_mode=bot.PUSH_MODE_PLAN,
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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    monkeypatch.setattr(bot, "prepare_parallel_workspace", lambda **_kwargs: [])

    async def fake_start_parallel_tmux_session(task, workspace_root):
        return "vibe-par-demo", tmp_path / "pointer.txt"

    async def fake_upsert_session(**_kwargs):
        return None

    updates: list[tuple[str, dict]] = []

    async def fake_update_status(task_id: str, **kwargs):
        updates.append((task_id, kwargs))

    async def fake_push_task_to_model(*_args, **_kwargs):
        return False, "PROMPT", None

    preview_calls: list[int] = []
    ack_calls: list[int] = []

    async def fake_send_preview(*_args, **_kwargs):
        preview_calls.append(1)
        return None

    async def fake_send_ack(*_args, **_kwargs):
        ack_calls.append(1)
        return None

    monkeypatch.setattr(bot, "_start_parallel_tmux_session", fake_start_parallel_tmux_session)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "upsert_session", fake_upsert_session)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "update_status", fake_update_status)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task_to_model)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_send_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_ack)

    callback = DummyCallback("parallel:branch_confirm:demo", message)
    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))

    assert callback.answers == [(None, False)]
    assert message.calls, "首次推送失败后应向聊天明确回报失败"
    assert "并行 CLI 未启动成功" in message.calls[-1][0]
    assert preview_calls == []
    assert ack_calls == []
    assert not any("已创建并行开发副本（原目录未改动）：" in text for text, *_rest in message.edits)
    assert updates and updates[-1][1]["status"] == "closed"


def test_parallel_branch_confirm_callback_ensures_workspace_trust_before_starting_tmux(monkeypatch, tmp_path: Path):
    """启动并行 tmux 前应先确保 workspace 已写入 Codex trusted 配置。"""

    message = DummyMessage()
    session = bot.ParallelLaunchSession(
        token="demo",
        task=_task(),
        chat_id=1,
        actor=None,
        origin_message=message,
        push_mode=bot.PUSH_MODE_PLAN,
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
        branch_prefix=bot.DEFAULT_PARALLEL_BRANCH_PREFIX,
    )
    bot.PARALLEL_LAUNCH_SESSIONS["demo"] = session

    monkeypatch.setattr(bot, "prepare_parallel_workspace", lambda **_kwargs: [])

    call_order: list[tuple[str, str]] = []

    async def fake_ensure_trusted(path: Path, *, scope: str, owner_key: str):
        call_order.append(("trust", f"{path}|{scope}|{owner_key}"))

    async def fake_start_parallel_tmux_session(task, workspace_root):
        call_order.append(("tmux", str(workspace_root)))
        return "vibe-par-demo", tmp_path / "pointer.txt"

    async def fake_upsert_session(**_kwargs):
        return None

    async def fake_push_task_to_model(*_args, **_kwargs):
        return True, "PROMPT", tmp_path / "session.jsonl"

    async def fake_send_preview(*_args, **_kwargs):
        return None

    async def fake_send_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_ensure_codex_trusted_project_path", fake_ensure_trusted)
    monkeypatch.setattr(bot, "_start_parallel_tmux_session", fake_start_parallel_tmux_session)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "upsert_session", fake_upsert_session)
    monkeypatch.setattr(bot, "_push_task_to_model", fake_push_task_to_model)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_send_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_send_ack)

    callback = DummyCallback("parallel:branch_confirm:demo", message)
    asyncio.run(bot.on_parallel_branch_confirm_callback(callback))

    assert call_order[0][0] == "trust"
    assert call_order[1][0] == "tmux"


def test_start_parallel_tmux_session_requires_ready_file(monkeypatch, tmp_path: Path):
    """并行 CLI 启动脚本即使返回成功，也必须产出 ready 回执文件。"""

    task = SimpleNamespace(id="TASK_9005")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(bot, "CONFIG_ROOT_PATH", tmp_path)
    monkeypatch.setattr(bot, "PROJECT_SLUG", "demo")

    captured_env: dict[str, str] = {}

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured_env.update({key: str(value) for key, value in kwargs["env"].items()})
        return DummyProcess()

    monkeypatch.setattr(bot.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="ready 回执"):
            await bot._start_parallel_tmux_session(task, workspace_root)

    asyncio.run(scenario())

    assert captured_env["SESSION_READY_FILE"].endswith("tmux_ready")


def test_start_parallel_tmux_session_returns_after_ready_file_written(monkeypatch, tmp_path: Path):
    """并行 CLI 启动脚本返回成功且 ready 回执存在时，应允许继续后续派发。"""

    task = SimpleNamespace(id="TASK_9006")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(bot, "CONFIG_ROOT_PATH", tmp_path)
    monkeypatch.setattr(bot, "PROJECT_SLUG", "demo")

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        ready_file = Path(str(kwargs["env"]["SESSION_READY_FILE"]))
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        ready_file.write_text("ready", encoding="utf-8")
        return DummyProcess()

    monkeypatch.setattr(bot.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    async def scenario() -> None:
        tmux_session, pointer_file = await bot._start_parallel_tmux_session(task, workspace_root)
        assert tmux_session == "vibe-par-demo-task_9006"
        assert pointer_file.name == "current_session.txt"

    asyncio.run(scenario())


def test_parallel_commit_callback_reports_runtime_failure(monkeypatch):
    """提交并行分支异常时，应在 Telegram 明确回执失败原因。"""

    message = DummyMessage()
    callback = DummyCallback(f"{bot.PARALLEL_COMMIT_CALLBACK_PREFIX}TASK_0001", message)
    message.reply_markup = bot._build_model_quick_reply_keyboard(
        task_id="TASK_0001",
        parallel_task_title="调研任务",
        enable_parallel_actions=True,
        parallel_callback_payload="TASK_0001",
    )

    async def fake_active_parallel_session(_task_id: str):
        return SimpleNamespace(task_id="TASK_0001")

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0001"
        return _task()

    async def fake_get_repos(_task_id: str):
        return [SimpleNamespace(repo_key="repo")]

    async def fake_update_status(*_args, **_kwargs):
        return None

    async def fake_to_thread(*_args, **_kwargs):
        raise RuntimeError("git 输出解码失败")

    monkeypatch.setattr(bot, "_get_active_parallel_session_for_task", fake_active_parallel_session)
    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_get_parallel_session_repos", fake_get_repos)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "update_status", fake_update_status)
    monkeypatch.setattr(bot.asyncio, "to_thread", fake_to_thread)

    asyncio.run(bot.on_parallel_commit_callback(callback))

    assert callback.answers[0] == ("正在提交并行分支…", False)
    assert message.calls, "异常时应向聊天发送失败消息"
    text, _, reply_markup, _ = message.calls[-1]
    assert "提交失败" in text
    assert "git 输出解码失败" in text
    assert isinstance(reply_markup, ReplyKeyboardMarkup)
    assert not message.reply_markup_edits, "提交失败时不应移除按钮"


def test_parallel_commit_callback_formats_grouped_result(monkeypatch):
    """并行提交成功后，应输出失败置顶的分组摘要。"""

    message = DummyMessage()
    callback = DummyCallback(f"{bot.PARALLEL_COMMIT_CALLBACK_PREFIX}TASK_0001", message)
    message.reply_markup = bot._build_model_quick_reply_keyboard(
        task_id="TASK_0001",
        parallel_task_title="并行任务标题",
        enable_parallel_actions=True,
        parallel_callback_payload="TASK_0001",
    )

    async def fake_active_parallel_session(_task_id: str):
        return SimpleNamespace(task_id="TASK_0001")

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0001"
        return _task()

    async def fake_get_repos(_task_id: str):
        return [SimpleNamespace(repo_key="repo-failed"), SimpleNamespace(repo_key="repo-pushed"), SimpleNamespace(repo_key="repo-skipped")]

    async def fake_update_status(*_args, **_kwargs):
        return None

    async def fake_update_repo_status(*_args, **_kwargs):
        return None

    async def fake_to_thread(*_args, **_kwargs):
        return SimpleNamespace(
            failed=True,
            results=[
                bot.RepoOperationResult("repo-failed", "repo-failed", False, "failed", "推送失败\r\n请检查权限"),
                bot.RepoOperationResult("repo-pushed", "repo-pushed", True, "pushed", "提交并推送成功"),
                bot.RepoOperationResult("repo-skipped", "repo-skipped", True, "skipped", "无改动，已跳过"),
            ],
        )

    monkeypatch.setattr(bot, "_get_active_parallel_session_for_task", fake_active_parallel_session)
    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_get_parallel_session_repos", fake_get_repos)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "update_status", fake_update_status)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "update_repo_status", fake_update_repo_status)
    monkeypatch.setattr(bot.asyncio, "to_thread", fake_to_thread)

    asyncio.run(bot.on_parallel_commit_callback(callback))

    assert message.calls, "成功回执应输出结构化摘要"
    text, _parse_mode, _markup, _kwargs = message.calls[-1]
    assert text.splitlines()[0] == "并行分支提交结果"
    assert "总览：3 个仓库｜失败 1｜成功 1｜跳过 1" in text
    assert text.index("❌ 失败（1）") < text.index("✅ 成功（1）") < text.index("⏭️ 已跳过（1）")
    assert "  请检查权限" in text
    assert message.reply_markup_edits, "提交成功后应移除已点击的并行提交按钮"
    callback_data = _callback_data_list(message.reply_markup_edits[-1][0])
    assert f"{bot.PARALLEL_COMMIT_CALLBACK_PREFIX}TASK_0001" not in callback_data
    assert f"{bot.MODEL_TASK_TO_TEST_PREFIX}TASK_0001" in callback_data
    assert f"{bot.PARALLEL_REPLY_CALLBACK_PREFIX}TASK_0001" in callback_data
    assert "task:detail:TASK_0001" in callback_data


def test_parallel_merge_callback_reports_runtime_failure(monkeypatch):
    """自动合并异常时，应在 Telegram 明确回执失败原因。"""

    message = DummyMessage()
    callback = DummyCallback(f"{bot.PARALLEL_MERGE_CALLBACK_PREFIX}TASK_0001", message)

    async def fake_active_parallel_session(_task_id: str):
        return SimpleNamespace(task_id="TASK_0001")

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0001"
        return _task()

    async def fake_get_repos(_task_id: str):
        return [SimpleNamespace(repo_key="repo")]

    async def fake_update_status(*_args, **_kwargs):
        return None

    async def fake_to_thread(*_args, **_kwargs):
        raise RuntimeError("远端服务异常")

    monkeypatch.setattr(bot, "_get_active_parallel_session_for_task", fake_active_parallel_session)
    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_get_parallel_session_repos", fake_get_repos)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "update_status", fake_update_status)
    monkeypatch.setattr(bot.asyncio, "to_thread", fake_to_thread)

    asyncio.run(bot.on_parallel_merge_callback(callback))

    assert callback.answers[0] == ("正在尝试自动合并…", False)
    assert message.calls, "异常时应向聊天发送失败消息"
    text, _, reply_markup, _ = message.calls[-1]
    assert "自动合并失败" in text
    assert "远端服务异常" in text
    assert isinstance(reply_markup, ReplyKeyboardMarkup)


def test_parallel_merge_callback_formats_grouped_success_result(monkeypatch):
    """自动合并成功后，应输出成功/跳过分组摘要。"""

    message = DummyMessage()
    callback = DummyCallback(f"{bot.PARALLEL_MERGE_CALLBACK_PREFIX}TASK_0001", message)

    async def fake_active_parallel_session(_task_id: str):
        return SimpleNamespace(task_id="TASK_0001")

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0001"
        return _task()

    async def fake_get_repos(_task_id: str):
        return [SimpleNamespace(repo_key="repo-merged"), SimpleNamespace(repo_key="repo-skipped")]

    async def fake_update_status(*_args, **_kwargs):
        return None

    async def fake_update_repo_status(*_args, **_kwargs):
        return None

    async def fake_to_thread(*_args, **_kwargs):
        return SimpleNamespace(
            failed=False,
            results=[
                bot.RepoOperationResult("repo-merged", "repo-merged", True, "merged", "已自动合并到 develop"),
                bot.RepoOperationResult("repo-skipped", "repo-skipped", True, "skipped", "未配置远端，已跳过自动合并"),
            ],
        )

    monkeypatch.setattr(bot, "_get_active_parallel_session_for_task", fake_active_parallel_session)
    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(bot, "_get_parallel_session_repos", fake_get_repos)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "update_status", fake_update_status)
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "update_repo_status", fake_update_repo_status)
    monkeypatch.setattr(bot.asyncio, "to_thread", fake_to_thread)

    asyncio.run(bot.on_parallel_merge_callback(callback))

    assert message.calls, "成功回执应输出结构化摘要"
    text, _parse_mode, _markup, _kwargs = message.calls[-1]
    assert text.splitlines()[0] == "自动合并成功"
    assert "总览：2 个仓库｜失败 0｜成功 1｜跳过 1" in text
    assert "❌ 失败" not in text
    assert "✅ 成功（1）" in text
    assert "⏭️ 已跳过（1）" in text
