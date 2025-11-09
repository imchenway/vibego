import asyncio
from types import SimpleNamespace

import pytest

import bot
from tasks.fsm import TaskCreateStates

from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove


class DummyState:
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


class DummyMessage:
    def __init__(self, text):
        self.text = text
        self.chat = SimpleNamespace(id=1)
        self.from_user = SimpleNamespace(id=1, full_name="Tester")
        self.calls = []
        self.edits = []

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

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        self.edits.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )


class DummyCallback:
    def __init__(self, message, data="task:create_confirm"):
        self.message = message
        self.data = data
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(
            {
                "text": text,
                "show_alert": show_alert,
            }
        )


def test_task_new_interactive_sets_default_priority_and_prompt():
    state = DummyState()
    message = DummyMessage("/task_new")
    asyncio.run(bot.on_task_new(message, state))

    assert state.state == TaskCreateStates.waiting_title
    assert state.data["priority"] == bot.DEFAULT_PRIORITY
    assert message.calls and message.calls[-1]["text"] == "Please enter the task title:"


def test_task_new_command_rejects_priority_param():
    state = DummyState()
    message = DummyMessage("/task_new Fix login | priority=2 | type=need")
    asyncio.run(bot.on_task_new(message, state))

    assert message.calls
    assert "parameter is no longer supported" in message.calls[-1]["text"]


def test_task_create_title_moves_to_type_selection():
    state = DummyState(data={"priority": bot.DEFAULT_PRIORITY})
    message = DummyMessage("New task title")
    asyncio.run(bot.on_task_create_title(message, state))

    assert state.state == TaskCreateStates.waiting_type
    assert state.data["title"] == "New task title"
    assert message.calls
    assert isinstance(message.calls[-1]["reply_markup"], ReplyKeyboardMarkup)
    assert message.calls[-1]["text"].startswith("Please select a task type")


def test_task_create_type_valid_moves_to_description_prompt():
    state = DummyState(
        data={
            "title": "test title",
            "priority": bot.DEFAULT_PRIORITY,
        },
        state=TaskCreateStates.waiting_type,
    )
    message = DummyMessage(bot._format_task_type("task"))
    asyncio.run(bot.on_task_create_type(message, state))

    assert state.state == TaskCreateStates.waiting_description
    assert state.data["task_type"] == "task"
    assert message.calls
    prompt = message.calls[-1]["text"]
    assert prompt.startswith("Please enter the task description.")
    markup = message.calls[-1]["reply_markup"]
    assert isinstance(markup, ReplyKeyboardMarkup)
    buttons = [button.text for row in markup.keyboard for button in row]
    assert any(bot.SKIP_TEXT in text for text in buttons)
    assert any("Cancel" in text for text in buttons)


@pytest.mark.parametrize(
    "invalid_text",
    [
        "",
        " ",
        "Invalid type",
        "priority=2",
        "task*",
        "Task?",
        "---",
        "123",
        "🤖",
    ],
)
def test_task_create_type_invalid_reprompts(invalid_text):
    state = DummyState(
        data={
            "title": "Test Task",
            "priority": bot.DEFAULT_PRIORITY,
        },
        state=TaskCreateStates.waiting_type,
    )
    message = DummyMessage(invalid_text)
    asyncio.run(bot.on_task_create_type(message, state))

    assert state.state == TaskCreateStates.waiting_type
    assert message.calls
    assert message.calls[-1]["text"].startswith("Invalid task type.")
    assert isinstance(message.calls[-1]["reply_markup"], ReplyKeyboardMarkup)


def test_task_create_description_skip_produces_summary():
    state = DummyState(
        data={
            "title": "test title",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
        },
        state=TaskCreateStates.waiting_description,
    )
    message = DummyMessage(bot.SKIP_TEXT)
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert state.data["description"] == ""
    assert len(message.calls) >= 2
    summary = message.calls[-2]["text"]
    assert "Description: None yet" in summary
    assert isinstance(message.calls[-1]["reply_markup"], ReplyKeyboardMarkup)


def test_task_create_description_accepts_text():
    state = DummyState(
        data={
            "title": "test title",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
        },
        state=TaskCreateStates.waiting_description,
    )
    description = "This is the Task description, including background and expected results."
    message = DummyMessage(description)
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert state.data["description"] == description
    summary = message.calls[-2]["text"]
    assert "Description:" in summary
    assert description in summary


def test_task_create_description_too_long_reprompts():
    state = DummyState(
        data={
            "title": "test title",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
        },
        state=TaskCreateStates.waiting_description,
    )
    long_text = "a" * (bot.DESCRIPTION_MAX_LENGTH + 1)
    message = DummyMessage(long_text)
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state == TaskCreateStates.waiting_description
    assert message.calls
    assert "cannot exceed" in message.calls[-1]["text"]
    markup = message.calls[-1]["reply_markup"]
    assert isinstance(markup, ReplyKeyboardMarkup)
    buttons = [button.text for row in markup.keyboard for button in row]
    assert any(bot.SKIP_TEXT in text for text in buttons)
    assert any("Cancel" in text for text in buttons)


def test_task_create_description_cancel_aborts():
    state = DummyState(
        data={
            "title": "test title",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
        },
        state=TaskCreateStates.waiting_description,
    )
    message = DummyMessage("Cancel")
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state is None
    assert message.calls
    assert message.calls[-1]["text"] == "Task creation cancelled."


def test_task_create_description_cancel_keyboard_aborts():
    state = DummyState(
        data={
            "title": "test title",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
        },
        state=TaskCreateStates.waiting_description,
    )
    message = DummyMessage("2. Cancel")
    asyncio.run(bot.on_task_create_description(message, state))

    assert state.state is None
    assert message.calls
    assert message.calls[-1]["text"] == "Task creation cancelled."


def test_task_create_confirm_uses_default_priority(monkeypatch):
    state = DummyState(
        data={
            "title": "Test Task",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
            "actor": "Tester#1",
            "description": "",
        },
        state=TaskCreateStates.waiting_confirm,
    )
    message = DummyMessage("1")
    calls = []

    async def fake_create_root_task(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="TASK_9999")

    async def fake_render_detail(task_id):
        return "Detail text", None

    monkeypatch.setattr(
        bot,
        "TASK_SERVICE",
        SimpleNamespace(create_root_task=fake_create_root_task),
    )
    monkeypatch.setattr(bot, "_render_task_detail", fake_render_detail)

    asyncio.run(bot.on_task_create_confirm(message, state))

    assert calls and calls[0]["priority"] == bot.DEFAULT_PRIORITY
    assert state.state is None
    assert message.calls
    assert isinstance(message.calls[-2]["reply_markup"], ReplyKeyboardMarkup)
    assert message.calls[-1]["text"].startswith("Task created:")


def test_task_create_confirm_invalid_prompts_again():
    state = DummyState(
        data={
            "title": "Test Task",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
            "description": "",
        },
        state=TaskCreateStates.waiting_confirm,
    )
    message = DummyMessage("Enter whatever you want")

    asyncio.run(bot.on_task_create_confirm(message, state))

    assert state.state == TaskCreateStates.waiting_confirm
    assert message.calls
    assert "Please select \"✅ Confirm creation\" or \"❌ Cancel\"" in message.calls[-1]["text"]
    assert isinstance(message.calls[-1]["reply_markup"], ReplyKeyboardMarkup)


def test_task_create_confirm_cancel_via_number():
    state = DummyState(
        data={
            "title": "Test Task",
            "priority": bot.DEFAULT_PRIORITY,
            "task_type": "task",
        },
        state=TaskCreateStates.waiting_confirm,
    )
    message = DummyMessage("2")

    asyncio.run(bot.on_task_create_confirm(message, state))

    assert state.state is None
    assert len(message.calls) >= 2
    assert isinstance(message.calls[-2]["reply_markup"], ReplyKeyboardRemove)
    assert message.calls[-2]["text"] == "Task creation cancelled."
    assert message.calls[-1]["text"] == "Returned to main menu."


def test_task_child_command_reports_deprecation():
    state = DummyState(data={"stage": "child"}, state="waiting")
    message = DummyMessage("/task_child TASK_0001 New task")

    asyncio.run(bot.on_task_child(message, state))

    assert state.state is None
    assert not state.data
    assert message.calls
    assert "The sub-task function has been offline" in message.calls[-1]["text"]


def test_task_children_command_reports_deprecation():
    message = DummyMessage("/task_children TASK_0001")

    asyncio.run(bot.on_task_children(message))

    assert message.calls
    assert "The sub-task function has been offline" in message.calls[-1]["text"]


def test_task_add_child_callback_reports_deprecation():
    callback = DummyCallback(DummyMessage(""), "task:add_child:TASK_0001")
    state = DummyState(data={"stage": "child"}, state="waiting")

    asyncio.run(bot.on_add_child_callback(callback, state))

    assert state.state is None
    assert not state.data
    assert callback.answers
    assert "sub-task feature has been retired" in (callback.answers[-1]["text"] or "")
    assert callback.message.calls
    assert "Use /task_new to create a new task." in callback.message.calls[-1]["text"]


def test_task_list_children_callback_reports_deprecation():
    callback = DummyCallback(DummyMessage(""), "task:list_children:TASK_0001")

    asyncio.run(bot.on_list_children_callback(callback))

    assert callback.answers
    assert "The sub-task function has been offline" in (callback.answers[-1]["text"] or "")
    assert callback.message.calls
    assert "The sub-task function has been offline" in callback.message.calls[-1]["text"]
