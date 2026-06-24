"""
验证 master 菜单与命令同步逻辑的健壮性。
"""
import asyncio
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
async def test_menu_sync_network_error_does_not_block_startup():
    """菜单同步遇到网络超时时只记录失败，不能阻断 master 启动。"""
    bot = SimpleNamespace()
    bot.set_chat_menu_button = AsyncMock(side_effect=TimeoutError("Proxy connection timed out: 60"))
    bot.get_chat_menu_button = AsyncMock()

    await master._ensure_master_menu_button(bot)

    bot.set_chat_menu_button.assert_awaited_once()
    bot.get_chat_menu_button.assert_not_called()


@pytest.mark.asyncio
async def test_menu_sync_hard_timeout_does_not_delay_startup(monkeypatch):
    """菜单同步接口长期无响应时应主动超时，避免 master 启动被 Telegram 代理拖住。"""
    monkeypatch.setattr(master, "MASTER_STARTUP_UI_SYNC_TIMEOUT", 0.01)
    calls = {"set": 0}

    async def slow_set_chat_menu_button(**_kwargs):
        calls["set"] += 1
        await asyncio.sleep(1)

    bot = SimpleNamespace(
        set_chat_menu_button=slow_set_chat_menu_button,
        get_chat_menu_button=AsyncMock(),
    )

    await asyncio.wait_for(master._ensure_master_menu_button(bot), timeout=0.2)

    assert calls["set"] == 1
    bot.get_chat_menu_button.assert_not_called()


@pytest.mark.asyncio
async def test_menu_verify_network_error_does_not_block_startup():
    """菜单校验遇到网络超时时只返回失败，不能让启动流程崩溃。"""
    bot = SimpleNamespace()
    bot.set_chat_menu_button = AsyncMock()
    bot.get_chat_menu_button = AsyncMock(side_effect=TimeoutError("Proxy connection timed out: 60"))

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


@pytest.mark.asyncio
async def test_command_sync_network_error_does_not_block_startup():
    """命令同步遇到网络超时时应尽快让出启动流程，避免 /upgrade 长时间卡住。"""
    bot = SimpleNamespace()
    bot.set_my_commands = AsyncMock(side_effect=TimeoutError("Proxy connection timed out: 60"))
    bot.get_my_commands = AsyncMock()

    await master._ensure_master_commands(bot)

    bot.set_my_commands.assert_awaited_once()
    bot.get_my_commands.assert_not_called()


@pytest.mark.asyncio
async def test_command_sync_hard_timeout_does_not_delay_startup(monkeypatch):
    """命令同步接口长期无响应时应主动超时，不能阻塞进入 polling。"""
    monkeypatch.setattr(master, "MASTER_STARTUP_UI_SYNC_TIMEOUT", 0.01)
    calls = {"set": 0}

    async def slow_set_my_commands(*_args, **_kwargs):
        calls["set"] += 1
        await asyncio.sleep(1)

    bot = SimpleNamespace(
        set_my_commands=slow_set_my_commands,
        get_my_commands=AsyncMock(),
    )

    await asyncio.wait_for(master._ensure_master_commands(bot), timeout=0.2)

    assert calls["set"] == 1
    bot.get_my_commands.assert_not_called()
