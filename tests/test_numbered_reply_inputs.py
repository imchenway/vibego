import asyncio
from types import SimpleNamespace

import pytest

import bot
from tasks.fsm import TaskCreateStates
from aiogram.types import InlineKeyboardMarkup


class StubState:
    def __init__(self, data=None, state=None):
        self._data = dict(data or {})
        self._state = state

    async def clear(self):
        self._data.clear()
        self._state = None

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def set_state(self, state):
        self._state = state

    async def get_data(self):
        return dict(self._data)

    @property
    def data(self):
        return dict(self._data)

    @property
    def state(self):
        return self._state


class StubMessage:
    def __init__(self, text):
        self.text = text
        self.chat = SimpleNamespace(id=1)
        self.from_user = SimpleNamespace(id=1, full_name="Tester")
        self.calls = []

    async def answer(self, text, parse_mode=None, reply_markup=None, **kwargs):
        self.calls.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(message_id=len(self.calls))


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1. 需求", "需求"),
        ("3. 3", "3"),
        ("5. 取消", "取消"),
        (" 无编号 ", "无编号"),
        ("", ""),
    ],
)
def test_strip_number_prefix(raw, expected):
    assert bot._strip_number_prefix(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("需求", "需求"),
        ("1. 需求", "需求"),
        ("2", bot._format_task_type("defect")),
        ("4. 风险", "风险"),
        ("5", "取消"),
        ("req", "req"),
        ("", ""),
        ("9", "9"),
    ],
)
def test_resolve_reply_choice_task_types(raw, expected):
    options = [bot._format_task_type(code) for code in bot.TASK_TYPES]
    options.append("取消")
    assert bot._resolve_reply_choice(raw, options=options) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1. 1", "1"),
        ("3", "3"),
        ("6. 跳过", bot.SKIP_TEXT),
        ("6", bot.SKIP_TEXT),
        ("8", "8"),
    ],
)
def test_resolve_reply_choice_priority(raw, expected):
    options = [str(i) for i in range(1, 6)]
    options.append(bot.SKIP_TEXT)
    assert bot._resolve_reply_choice(raw, options=options) == expected


@pytest.mark.parametrize(
    "raw, expected_type",
    [
        ("1. 需求", "requirement"),
        ("2", "defect"),
        ("3. 优化", "task"),
        ("4", "risk"),
    ],
)
def test_task_create_type_accepts_number_inputs(monkeypatch, raw, expected_type):
    async def fake_view(*, page: int):
        assert page == 1
        return "请选择关联任务：", InlineKeyboardMarkup(inline_keyboard=[])

    # 缺陷类型现在会进入“选择关联任务”阶段，避免测试依赖真实数据库，这里统一打桩视图构造。
    monkeypatch.setattr(bot, "_build_related_task_select_view", fake_view)

    state = StubState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
        },
        state=TaskCreateStates.waiting_type,
    )
    message = StubMessage(raw)
    asyncio.run(bot.on_task_create_type(message, state))

    expected_state = (
        TaskCreateStates.waiting_related_task
        if expected_type == "defect"
        else TaskCreateStates.waiting_description
    )
    assert state.state == expected_state
    assert state.data["task_type"] == expected_type


@pytest.mark.parametrize("raw", ["5", "5. 取消"])
def test_task_create_type_numeric_cancel(raw):
    state = StubState(
        data={
            "title": "测试标题",
            "priority": bot.DEFAULT_PRIORITY,
        },
        state=TaskCreateStates.waiting_type,
    )
    message = StubMessage(raw)
    asyncio.run(bot.on_task_create_type(message, state))

    assert state.state is None
    assert not state.data
    assert message.calls and "已取消创建任务。" in message.calls[-1]["text"]


@pytest.mark.parametrize(
    "task_type, expected_prefix",
    [
        ("requirement", "📌"),
        ("defect", "🐞"),
        ("task", "🛠️"),
        ("risk", "⚠️"),
    ],
)
def test_format_task_type_includes_emoji(task_type, expected_prefix):
    formatted = bot._format_task_type(task_type)
    assert formatted.startswith(f"{expected_prefix} ")


def test_format_task_type_handles_empty():
    assert bot._format_task_type(None) == "⚪ 未设置"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("📌 需求", "requirement"),
        ("🐞 缺陷", "defect"),
        ("🛠️ 优化", "task"),
        ("⚠️ 风险", "risk"),
        ("📌需求", "requirement"),
        ("1. 📌 需求", "requirement"),
    ],
)
def test_normalize_task_type_accepts_emoji(raw, expected):
    assert bot._normalize_task_type(raw) == expected
