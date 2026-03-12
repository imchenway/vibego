from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_quick_reply_runtime():
    bot.SESSION_QUICK_REPLY_CALLBACK_BINDINGS.clear()
    bot.CHAT_SESSION_MAP.clear()
    yield
    bot.SESSION_QUICK_REPLY_CALLBACK_BINDINGS.clear()
    bot.CHAT_SESSION_MAP.clear()


class DummyMessage:
    """模拟 aiogram Message，覆盖本用例所需的最小接口。"""

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
    """模拟 aiogram CallbackQuery，覆盖本用例所需的最小接口。"""

    def __init__(self, data: str, message: DummyMessage):
        self.data = data
        self.message = message
        self.answers = []
        self.from_user = SimpleNamespace(id=1, full_name="Tester")

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


def make_state(message: DummyMessage) -> tuple[FSMContext, MemoryStorage]:
    """构造测试用 FSMContext（MemoryStorage）。"""

    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=999, chat_id=message.chat.id, user_id=message.from_user.id),
    )
    return state, storage


def _assistant_event(text: str, *, cwd: str) -> dict:
    """构造最小 assistant_message 事件，携带 cwd 供会话路由测试复用。"""

    return {
        "timestamp": "2025-01-01T00:00:00Z",
        "type": "response_item",
        "payload": {
            "type": "assistant_message",
            "message": text,
            "cwd": cwd,
        },
    }


def test_quick_reply_partial_enters_supplement_state():
    """点击“部分按推荐（需补充）”应进入补充输入状态，不应立即推送到模型。"""

    message = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_PARTIAL_CALLBACK, message)
    state, _ = make_state(message)

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        assert callback.answers and callback.answers[-1][0] == "请发送补充说明，或点击跳过/取消"
        assert await state.get_state() == bot.ModelQuickReplyStates.waiting_partial_supplement.state
        assert message.calls, "应提示用户输入补充说明"
        prompt_text, _, reply_markup, _ = message.calls[-1]
        assert "请发送需要补充的说明" in prompt_text
        assert isinstance(reply_markup, ReplyKeyboardMarkup)

    asyncio.run(_scenario())


def test_deliver_pending_messages_for_bound_native_session_includes_commit_button(monkeypatch, tmp_path: Path):
    """原生 Codex 会话若已绑定任务，应在模型答案底部展示会话级“提交分支”按钮。"""

    session_file = tmp_path / "session.jsonl"
    workspace_root = tmp_path / "native-workspace"
    session_file.write_text(
        json.dumps(_assistant_event("原生会话返回结果", cwd=str(workspace_root)), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    bot.SESSION_OFFSETS[str(session_file)] = 0
    bot.SESSION_TASK_BINDINGS[str(session_file)] = "TASK_0200"

    captured_markups: list[object] = []

    async def fake_reply(
        _chat_id: int,
        _text: str,
        *,
        parse_mode=None,
        preformatted: bool = False,
        reply_markup=None,
        attachment_reply_markup=None,
    ):
        captured_markups.append(reply_markup)
        return "ok"

    monkeypatch.setattr(bot, "reply_large_text", fake_reply)

    delivered = asyncio.run(bot._deliver_pending_messages(42, session_file))

    assert delivered is True
    assert captured_markups, "应给模型答案消息附加快捷按钮"
    callback_data = [
        button.callback_data
        for row in captured_markups[-1].inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]
    assert any(data.startswith(bot.MODEL_QUICK_REPLY_ALL_SESSION_PREFIX) for data in callback_data)
    assert any(data.startswith(bot.MODEL_QUICK_REPLY_PARTIAL_SESSION_PREFIX) for data in callback_data)
    assert any(data.startswith(bot.SESSION_COMMIT_CALLBACK_PREFIX) for data in callback_data)


def test_native_session_commit_callback_uses_bound_workspace_root(monkeypatch, tmp_path: Path):
    """原生会话的提交按钮应按会话绑定目录收敛提交范围，避免串到其它会话目录。"""

    message = DummyMessage()
    callback = DummyCallback(f"{bot.SESSION_COMMIT_CALLBACK_PREFIX}TASK_0200:deadbeef", message)
    workspace_root = tmp_path / "native-workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    binding = SimpleNamespace(
        token="deadbeef",
        task_id="TASK_0200",
        session_key=str(tmp_path / "session.jsonl"),
        workspace_root=workspace_root,
    )
    bot.SESSION_COMMIT_CALLBACK_BINDINGS = {"deadbeef": binding}

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0200"
        return SimpleNamespace(id="TASK_0200", title="原生提交", task_type="task")

    discovered_roots: list[Path] = []
    committed_repo_roots: list[Path] = []

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(
        bot,
        "discover_git_repos",
        lambda root, include_nested=True: discovered_roots.append(Path(root)) or [
            ("__root__", workspace_root, "."),
            ("service", workspace_root / "service", "service"),
        ],
    )
    monkeypatch.setattr(bot, "get_current_branch_state", lambda _repo: ("feature/TASK_0200", "feature/TASK_0200"))

    def fake_commit_parallel_repos(*, task, repos):
        committed_repo_roots.extend(Path(item.workspace_repo_path) for item in repos)
        return SimpleNamespace(
            results=[
                bot.RepoOperationResult("service", "service", True, "pushed", "提交并推送成功"),
            ]
        )

    monkeypatch.setattr(bot, "commit_parallel_repos", fake_commit_parallel_repos)

    asyncio.run(bot.on_session_commit_callback(callback))

    assert discovered_roots == [workspace_root]
    assert committed_repo_roots == [workspace_root, workspace_root / "service"]
    assert message.calls, "成功时应向聊天发送结构化结果消息"
    text, _parse_mode, _markup, _kwargs = message.calls[-1]
    assert "分支提交结果" in text
    assert "总览：1 个仓库｜失败 0｜成功 1｜跳过 0" in text
    assert "✅ 成功（1）" in text
    assert "- service" in text
    assert "提交并推送成功" in text


def test_native_session_commit_callback_reports_runtime_failure(monkeypatch, tmp_path: Path):
    """原生会话提交分支异常时，应在 Telegram 明确回执失败原因。"""

    message = DummyMessage()
    callback = DummyCallback(f"{bot.SESSION_COMMIT_CALLBACK_PREFIX}TASK_0200:deadbeef", message)
    workspace_root = tmp_path / "native-workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    binding = SimpleNamespace(
        token="deadbeef",
        task_id="TASK_0200",
        session_key=str(tmp_path / "session.jsonl"),
        workspace_root=workspace_root,
    )
    bot.SESSION_COMMIT_CALLBACK_BINDINGS = {"deadbeef": binding}

    async def fake_get_task(task_id: str):
        assert task_id == "TASK_0200"
        return SimpleNamespace(id="TASK_0200", title="原生提交", task_type="task")

    monkeypatch.setattr(bot.TASK_SERVICE, "get_task", fake_get_task)
    monkeypatch.setattr(
        bot,
        "discover_git_repos",
        lambda root, include_nested=True: [("__root__", workspace_root, ".")],
    )
    monkeypatch.setattr(bot, "get_current_branch_state", lambda _repo: ("feature/TASK_0200", "feature/TASK_0200"))

    def fake_commit_parallel_repos(*, task, repos):
        raise RuntimeError("git push 失败")

    monkeypatch.setattr(bot, "commit_parallel_repos", fake_commit_parallel_repos)

    asyncio.run(bot.on_session_commit_callback(callback))

    assert callback.answers[0] == ("正在提交当前会话分支…", False)
    assert message.calls, "异常时应向聊天发送失败消息"
    text, _, reply_markup, _ = message.calls[-1]
    assert "提交失败" in text
    assert "git push 失败" in text
    assert isinstance(reply_markup, ReplyKeyboardMarkup)


def test_quick_reply_all_dispatches_prompt_and_restores_main_keyboard(monkeypatch, tmp_path: Path):
    """点击“全部按推荐”后应推送固定提示，并恢复主菜单键盘。"""

    origin = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_ALL_CALLBACK, origin)

    recorded: list[tuple[int, str, object, bool]] = []
    previews: list[tuple[int, str, object, object]] = []
    ack_calls: list[tuple[int, Path, object]] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, **_kwargs):
        recorded.append((chat_id, prompt, reply_to, ack_immediately))
        return True, tmp_path / "session_quick_all.jsonl"

    async def fake_preview(chat_id: int, preview_block: str, *, reply_to, parse_mode, reply_markup):
        previews.append((chat_id, preview_block, parse_mode, reply_markup))

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append((chat_id, session_path, reply_to))

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    async def _scenario() -> None:
        await bot.on_model_quick_reply_all(callback)
        assert recorded, "应推送到模型"
        chat_id, prompt, reply_to, ack_immediately = recorded[-1]
        assert chat_id == origin.chat.id
        assert prompt == "待决策项全部按模型推荐"
        assert reply_to is origin
        assert ack_immediately is False
        assert previews, "应回显推送预览"
        assert isinstance(previews[-1][3], ReplyKeyboardMarkup), "预览消息应恢复主菜单键盘"
        assert ack_calls, "应发送 session ack"

    asyncio.run(_scenario())


def test_quick_reply_all_dispatch_failure_restores_main_keyboard(monkeypatch):
    """“全部按推荐”推送失败时，也应恢复主菜单。"""

    origin = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_ALL_CALLBACK, origin)

    async def fake_dispatch(*_args, **_kwargs):
        return False, None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    async def _scenario() -> None:
        await bot.on_model_quick_reply_all(callback)
        assert callback.answers and callback.answers[-1] == ("推送失败：模型未就绪", True)
        assert origin.calls, "失败时应补发提示并恢复主菜单"
        text, _, reply_markup, _ = origin.calls[-1]
        assert "推送失败" in text
        assert isinstance(reply_markup, ReplyKeyboardMarkup)

    asyncio.run(_scenario())


def test_native_quick_reply_keyboard_places_commit_and_test_on_same_row():
    """原生会话消息：提交分支与更新为测试中应同排。"""

    markup = bot._build_model_quick_reply_keyboard(
        task_id="TASK_0200",
        native_quick_reply_payload="TASK_0200:deadbeef",
        native_commit_callback_payload="TASK_0200:commitbeef",
    )

    assert len(markup.inline_keyboard) == 2
    second_row = markup.inline_keyboard[1]
    assert [button.text for button in second_row] == ["⬆️ 提交分支", "🧪 任务状态更新为测试中"]


def test_parallel_quick_reply_keyboard_places_commit_and_test_together_and_title_reply_last():
    """并行消息：提交并行分支与更新为测试中同排，标题+回复放最后一排。"""

    markup = bot._build_model_quick_reply_keyboard(
        task_id="TASK_0201",
        parallel_task_title="并行任务标题",
        enable_parallel_actions=True,
        parallel_callback_payload="TASK_0201:deadbeef",
    )

    assert len(markup.inline_keyboard) == 3
    second_row = markup.inline_keyboard[1]
    third_row = markup.inline_keyboard[2]
    assert [button.text for button in second_row] == ["⬆️ 提交并行分支", "🧪 任务状态更新为测试中"]
    assert third_row[0].text.startswith("🏷 ")
    assert third_row[1].text.startswith("↩️ 回复 ")


def test_quick_reply_all_old_native_message_fails_closed(monkeypatch):
    """旧原生会话消息上的“全部按推荐”若已不是当前活动会话，应直接报失效。"""

    origin = DummyMessage(chat_id=66, user_id=66)
    callback = DummyCallback(f"{bot.MODEL_QUICK_REPLY_ALL_SESSION_PREFIX}TASK_0200:deadbeef", origin)
    bot.CHAT_SESSION_MAP[origin.chat.id] = "session-current"
    bot.SESSION_QUICK_REPLY_CALLBACK_BINDINGS["deadbeef"] = bot.SessionQuickReplyBinding(
        token="deadbeef",
        task_id="TASK_0200",
        session_key="session-old",
    )

    async def fake_dispatch(*_args, **_kwargs):
        raise AssertionError("旧原生会话 quick reply 失效时不应继续派发")

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    asyncio.run(bot.on_model_quick_reply_all(callback))

    assert callback.answers[-1] == ("该消息所属会话已失效，请在最新会话中重试。", True)
    assert origin.calls and "已失效" in origin.calls[-1][0]


def test_quick_reply_partial_old_native_message_fails_closed(monkeypatch):
    """旧原生会话消息上的“部分按推荐”若已不是当前活动会话，应直接报失效。"""

    origin = DummyMessage(chat_id=67, user_id=67)
    callback = DummyCallback(f"{bot.MODEL_QUICK_REPLY_PARTIAL_SESSION_PREFIX}TASK_0200:deadbeef", origin)
    state, _ = make_state(origin)
    bot.CHAT_SESSION_MAP[origin.chat.id] = "session-current"
    bot.SESSION_QUICK_REPLY_CALLBACK_BINDINGS["deadbeef"] = bot.SessionQuickReplyBinding(
        token="deadbeef",
        task_id="TASK_0200",
        session_key="session-old",
    )

    async def fake_dispatch(*_args, **_kwargs):
        raise AssertionError("旧原生会话 quick reply 失效时不应进入派发")

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        assert await state.get_state() is None

    asyncio.run(_scenario())

    assert callback.answers[-1] == ("该消息所属会话已失效，请在最新会话中重试。", True)
    assert origin.calls and "已失效" in origin.calls[-1][0]


def test_quick_reply_partial_native_submit_fails_closed_after_session_switch(monkeypatch):
    """原生“部分按推荐”进入补充后，若当前活动会话已切走，提交时应 fail-closed。"""

    origin = DummyMessage(chat_id=68, user_id=68)
    callback = DummyCallback(f"{bot.MODEL_QUICK_REPLY_PARTIAL_SESSION_PREFIX}TASK_0200:deadbeef", origin)
    state, _ = make_state(origin)
    session_key = "session-active"
    bot.CHAT_SESSION_MAP[origin.chat.id] = session_key
    bot.SESSION_QUICK_REPLY_CALLBACK_BINDINGS["deadbeef"] = bot.SessionQuickReplyBinding(
        token="deadbeef",
        task_id="TASK_0200",
        session_key=session_key,
    )

    async def fake_dispatch(*_args, **_kwargs):
        raise AssertionError("会话切走后不应继续派发")

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    supplement_message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
    supplement_message.text = "补充说明"

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        assert await state.get_state() == bot.ModelQuickReplyStates.waiting_partial_supplement.state
        bot.CHAT_SESSION_MAP[origin.chat.id] = "session-newer"
        await bot.on_model_quick_reply_partial_supplement(supplement_message, state)
        assert await state.get_state() is None

    asyncio.run(_scenario())

    assert supplement_message.calls and "已失效" in supplement_message.calls[-1][0]


def test_quick_reply_partial_supplement_dispatches_prompt(monkeypatch, tmp_path: Path):
    """补充阶段输入文案后，应推送“未提及按推荐 + 用户补充说明”到模型。"""

    origin = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_PARTIAL_CALLBACK, origin)
    state, _ = make_state(origin)

    recorded: list[tuple[int, str, object, bool]] = []
    previews: list[tuple[int, str, object, object]] = []
    ack_calls: list[tuple[int, Path, object]] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, **_kwargs):
        recorded.append((chat_id, prompt, reply_to, ack_immediately))
        return True, tmp_path / "session.jsonl"

    async def fake_preview(chat_id: int, preview_block: str, *, reply_to, parse_mode, reply_markup):
        previews.append((chat_id, preview_block, parse_mode, reply_markup))

    async def fake_ack(chat_id: int, session_path: Path, *, reply_to):
        ack_calls.append((chat_id, session_path, reply_to))

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    supplement_message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
    supplement_message.text = "我需要补充：只有第 3 个选项不按推荐，其余都按推荐。"

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        await bot.on_model_quick_reply_partial_supplement(supplement_message, state)
        assert recorded, "应推送到模型"
        chat_id, prompt, reply_to, ack_immediately = recorded[-1]
        assert chat_id == origin.chat.id
        assert reply_to is origin
        assert not ack_immediately
        assert "未提及的决策项全部按推荐。" in prompt
        assert "用户补充说明：" in prompt
        assert supplement_message.text in prompt
        assert await state.get_state() is None
        assert previews, "应回显推送预览"
        assert ack_calls, "应回显 session ack"

    asyncio.run(_scenario())


@pytest.mark.parametrize("input_text", ["跳过", "", None])
def test_quick_reply_partial_skip_sends_all_recommended(monkeypatch, tmp_path: Path, input_text):
    """补充阶段发送跳过/空消息时，应等价“全部按推荐”。"""

    origin = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_PARTIAL_CALLBACK, origin)
    state, _ = make_state(origin)

    recorded: list[str] = []

    async def fake_dispatch(chat_id: int, prompt: str, *, reply_to, ack_immediately: bool = True, **_kwargs):
        recorded.append(prompt)
        return True, tmp_path / "session.jsonl"

    async def fake_preview(*_args, **_kwargs):
        return None

    async def fake_ack(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)
    monkeypatch.setattr(bot, "_send_model_push_preview", fake_preview)
    monkeypatch.setattr(bot, "_send_session_ack", fake_ack)

    supplement_message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
    supplement_message.text = input_text

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        await bot.on_model_quick_reply_partial_supplement(supplement_message, state)
        assert recorded and recorded[-1] == "待决策项全部按模型推荐"
        assert await state.get_state() is None

    asyncio.run(_scenario())


def test_quick_reply_partial_cancel(monkeypatch):
    """补充阶段发送“取消”应退出流程且不推送到模型。"""

    origin = DummyMessage()
    callback = DummyCallback(bot.MODEL_QUICK_REPLY_PARTIAL_CALLBACK, origin)
    state, _ = make_state(origin)

    async def fake_dispatch(*_args, **_kwargs):
        raise AssertionError("取消时不应触发推送")

    monkeypatch.setattr(bot, "_dispatch_prompt_to_model", fake_dispatch)

    supplement_message = DummyMessage(chat_id=origin.chat.id, user_id=origin.from_user.id)
    supplement_message.text = "取消"

    async def _scenario() -> None:
        await bot.on_model_quick_reply_partial(callback, state)
        await bot.on_model_quick_reply_partial_supplement(supplement_message, state)
        assert await state.get_state() is None
        assert supplement_message.calls
        text, _, reply_markup, _ = supplement_message.calls[-1]
        assert "已取消" in text
        assert isinstance(reply_markup, ReplyKeyboardMarkup)

    asyncio.run(_scenario())
