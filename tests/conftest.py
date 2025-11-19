from __future__ import annotations

import pytest

import master


@pytest.fixture(autouse=True)
def _disable_bot_identity_fetch(monkeypatch):
    """测试环境默认禁止访问 Telegram API，避免外网依赖。"""

    def _raise(*_args, **_kwargs):
        raise master.BotIdentityError("skip telegram getMe in tests")

    monkeypatch.setattr(master, "_fetch_bot_identity", _raise)
