"""
验证 master 菜单与命令同步逻辑的健壮性。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import BotCommand, MenuButtonCommands

import master


@pytest.fixture(autouse=True)
def reset_flags(monkeypatch):
    """每个用例重置同步开关。"""
    monkeypatch.setattr(master, "MASTER_FORCE_MENU_RESYNC", True)
    monkeypatch.setattr(master, "MASTER_FORCE_COMMAND_RESYNC", True)


@pytest.mark.asyncio
async def test_menu_sync_skips_when_disabled(monkeypatch):
    """关闭菜单同步时不应调 Telegram API。"""
    monkeypatch.setattr(master, "MASTER_FORCE_MENU_RESYNC", False)
    bot = SimpleNamespace(
        set_chat_menu_button=AsyncMock(),
        get_chat_menu_button=AsyncMock(),
    )

    await master._ensure_master_menu_button(bot)

    bot.set_chat_menu_button.assert_not_called()
    bot.get_chat_menu_button.assert_not_called()


@pytest.mark.asyncio
async def test_menu_sync_verifies_latest_state():
    """菜单同步后应立即触发一次 get_chat_menu_button 校验。"""
    bot = SimpleNamespace()
    bot.set_chat_menu_button = AsyncMock()
    bot.get_chat_menu_button = AsyncMock(
        return_value=MenuButtonCommands(text=master.MASTER_MENU_BUTTON_TEXT)
    )

    await master._ensure_master_menu_button(bot)

    bot.set_chat_menu_button.assert_awaited_once()
    bot.get_chat_menu_button.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_sync_skips_when_disabled(monkeypatch):
    """关闭命令同步时不应调用 set_my_commands。"""
    monkeypatch.setattr(master, "MASTER_FORCE_COMMAND_RESYNC", False)
    bot = SimpleNamespace(
        set_my_commands=AsyncMock(),
        get_my_commands=AsyncMock(),
    )

    await master._ensure_master_commands(bot)

    bot.set_my_commands.assert_not_called()
    bot.get_my_commands.assert_not_called()


@pytest.mark.asyncio
async def test_command_sync_verifies_all_scopes():
    """命令同步应覆盖全部 scope 并逐个校验。"""
    bot = SimpleNamespace()
    bot.set_my_commands = AsyncMock()

    expected = [
        BotCommand(command=cmd, description=desc)
        for cmd, desc in master.MASTER_BOT_COMMANDS
    ]
    side_effect = [expected.copy() for _ in range(4)]
    bot.get_my_commands = AsyncMock(side_effect=side_effect)

    await master._ensure_master_commands(bot)

    # default + 3 scopes
    assert bot.set_my_commands.await_count == 4
    assert bot.get_my_commands.await_count == 4
