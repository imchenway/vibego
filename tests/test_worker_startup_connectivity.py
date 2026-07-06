from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramNetworkError

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeBot:
    def __init__(self) -> None:
        self.session = FakeSession()


def test_worker_main_keeps_polling_when_initial_telegram_connectivity_fails(monkeypatch):
    """启动期 Telegram 握手失败不能让 worker 直接退出，应进入长轮询重试态。"""

    fake_bot = FakeBot()
    start_polling = AsyncMock(return_value=None)
    startup_commands = AsyncMock()
    startup_menu = AsyncMock()
    startup_broadcast = AsyncMock()

    async def fail_connectivity(_bot):
        raise RuntimeError("proxy timeout")

    monkeypatch.setattr(bot, "build_bot", lambda: fake_bot)
    monkeypatch.setattr(bot, "ensure_telegram_connectivity", fail_connectivity)
    monkeypatch.setattr(bot.TASK_SERVICE, "initialize", AsyncMock())
    monkeypatch.setattr(bot.COMMAND_SERVICE, "initialize", AsyncMock())
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "initialize", AsyncMock())
    monkeypatch.setattr(bot, "_reconcile_codex_trusted_paths", AsyncMock())
    monkeypatch.setattr(bot, "_ensure_primary_workdir_codex_trust", AsyncMock())
    monkeypatch.setattr(bot, "_ensure_bot_commands", startup_commands)
    monkeypatch.setattr(bot, "_ensure_worker_menu_button", startup_menu)
    monkeypatch.setattr(bot, "_broadcast_worker_keyboard", startup_broadcast)
    monkeypatch.setattr(bot.dp, "start_polling", start_polling)

    asyncio.run(bot.main())

    start_polling.assert_awaited_once_with(fake_bot)
    startup_commands.assert_not_awaited()
    startup_menu.assert_not_awaited()
    startup_broadcast.assert_not_awaited()
    assert fake_bot.session.closed is True


def test_worker_main_retries_polling_when_aiogram_preflight_network_fails(monkeypatch):
    """aiogram start_polling 自身的 bot.me() 网络失败也不能让 worker 退出。"""

    fake_bot = FakeBot()
    start_polling = AsyncMock(
        side_effect=[
            TelegramNetworkError(method=None, message="proxy timeout"),
            None,
        ]
    )
    startup_commands = AsyncMock()
    startup_menu = AsyncMock()
    startup_broadcast = AsyncMock()

    async def fail_connectivity(_bot):
        raise RuntimeError("proxy timeout")

    monkeypatch.setattr(bot, "WORKER_TELEGRAM_CONNECTIVITY_RETRY_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(bot, "build_bot", lambda: fake_bot)
    monkeypatch.setattr(bot, "ensure_telegram_connectivity", fail_connectivity)
    monkeypatch.setattr(bot.TASK_SERVICE, "initialize", AsyncMock())
    monkeypatch.setattr(bot.COMMAND_SERVICE, "initialize", AsyncMock())
    monkeypatch.setattr(bot.PARALLEL_SESSION_STORE, "initialize", AsyncMock())
    monkeypatch.setattr(bot, "_reconcile_codex_trusted_paths", AsyncMock())
    monkeypatch.setattr(bot, "_ensure_primary_workdir_codex_trust", AsyncMock())
    monkeypatch.setattr(bot, "_ensure_bot_commands", startup_commands)
    monkeypatch.setattr(bot, "_ensure_worker_menu_button", startup_menu)
    monkeypatch.setattr(bot, "_broadcast_worker_keyboard", startup_broadcast)
    monkeypatch.setattr(bot.dp, "start_polling", start_polling)

    asyncio.run(bot.main())

    assert start_polling.await_count == 2
    startup_commands.assert_not_awaited()
    startup_menu.assert_not_awaited()
    startup_broadcast.assert_not_awaited()
    assert fake_bot.session.closed is True
