from __future__ import annotations

import sys

import pytest

import master


@pytest.fixture(autouse=True)
def _disable_bot_identity_fetch(monkeypatch):
    """测试环境默认禁止访问 Telegram API，避免外网依赖。"""

    def _raise(*_args, **_kwargs):
        raise master.BotIdentityError("skip telegram getMe in tests")

    monkeypatch.setattr(master, "_fetch_bot_identity", _raise)


@pytest.fixture(autouse=True)
def _isolate_worker_session_marker(monkeypatch):
    """避免本机运行 worker 的 marker 泄漏到临时 pointer 测试。"""

    monkeypatch.setenv("SESSION_BINDER_TOKEN_FILE", "")
    bot_module = sys.modules.get("bot")
    if bot_module is not None:
        monkeypatch.setattr(bot_module, "SESSION_BINDER_TOKEN_FILE", "")
        dispatch_proofs = getattr(bot_module, "CHAT_RECENT_MODEL_DISPATCH_PROOFS", None)
        if isinstance(dispatch_proofs, dict):
            dispatch_proofs.clear()
