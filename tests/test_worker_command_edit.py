import os

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import pytest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import ReplyKeyboardMarkup

import bot
from command_center.models import CommandDefinition


class _StubCommandService:
    """用于模拟 COMMAND_SERVICE.get_command 的桩对象。"""

    def __init__(self, command: CommandDefinition):
        self._command = command

    async def get_command(self, command_id: int) -> CommandDefinition:
        assert command_id == self._command.id
        return self._command


class _DummyMessage:
    """记录回答内容，方便断言。"""

    def __init__(self):
        self.answers: list[str] = []
        self.kwargs: list[dict] = []
        self.text: str | None = None

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)
        self.kwargs.append(kwargs)


class _DummyCallback:
    """模拟 CallbackQuery 对象，仅保留必要接口。"""

    def __init__(self, data: str, message: _DummyMessage):
        self.data = data
        self.message = message
        self._answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self._answers.append((text, show_alert))


def _make_state():
    storage = MemoryStorage()
    key = StorageKey(bot_id=0, chat_id=1, user_id=1)
    return storage, FSMContext(storage=storage, key=key)


def _build_command(**overrides):
    base = {
        "id": 10,
        "project_slug": "demo",
        "scope": "project",
        "name": "deploy",
        "title": "部署",
        "command": "./deploy.sh",
        "description": "",
        "timeout": 120,
        "aliases": ("deploy_api",),
    }
    base.update(overrides)
    return CommandDefinition(**base)


@pytest.mark.asyncio
async def test_on_command_field_select_prompts_full_command(monkeypatch):
    command = _build_command(command="line1\nline2")
    monkeypatch.setattr(bot, "COMMAND_SERVICE", _StubCommandService(command))
    storage, state = _make_state()
    message = _DummyMessage()
    callback = _DummyCallback(f"{bot.COMMAND_FIELD_PREFIX}command:{command.id}", message)
    try:
        await bot.on_command_field_select(callback, state)
        assert await state.get_state() == bot.CommandEditStates.waiting_value.state
        assert message.answers, "应提示当前命令值"
        payload = message.answers[-1]
        assert "当前指令" in payload and "line2" in payload
        assert callback._answers and callback._answers[-1][0] == "请发送新的值"
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_on_command_field_select_for_aliases_uses_alias_state(monkeypatch):
    command = _build_command(aliases=("alpha", "beta"))
    monkeypatch.setattr(bot, "COMMAND_SERVICE", _StubCommandService(command))
    storage, state = _make_state()
    message = _DummyMessage()
    callback = _DummyCallback(f"{bot.COMMAND_FIELD_PREFIX}aliases:{command.id}", message)
    try:
        await bot.on_command_field_select(callback, state)
        assert await state.get_state() == bot.CommandEditStates.waiting_aliases.state
        assert "当前别名：alpha, beta" in message.answers[-1]
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_on_command_field_select_attaches_cancel_keyboard(monkeypatch):
    command = _build_command()
    monkeypatch.setattr(bot, "COMMAND_SERVICE", _StubCommandService(command))
    storage, state = _make_state()
    message = _DummyMessage()
    callback = _DummyCallback(f"{bot.COMMAND_FIELD_PREFIX}title:{command.id}", message)
    try:
        await bot.on_command_field_select(callback, state)
        reply_markup = message.kwargs[-1]["reply_markup"]
        assert isinstance(reply_markup, ReplyKeyboardMarkup)
        assert reply_markup.keyboard[0][0].text.strip() == "取消"
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_on_command_edit_value_cancel_via_button():
    storage, state = _make_state()
    await state.set_state(bot.CommandEditStates.waiting_value)
    await state.update_data(command_id=1, field="title")
    message = _DummyMessage()
    message.text = "取消"
    try:
        await bot.on_command_edit_value(message, state)
        assert await state.get_state() is None
        assert message.answers and "命令编辑已取消" in message.answers[-1]
    finally:
        await storage.close()
