from __future__ import annotations

import asyncio
import socket

import pytest

import telegram_proxy


def test_explicit_socks5_proxy_keeps_user_host_and_port() -> None:
    resolved = telegram_proxy.resolve_telegram_proxy(
        {"TELEGRAM_PROXY": "socks5://192.0.2.10:19080"}
    )

    assert resolved.url == "socks5://192.0.2.10:19080"
    assert resolved.source == "TELEGRAM_PROXY"
    assert resolved.is_socks5 is True


def test_system_proxy_uses_macos_socks_host_and_port() -> None:
    scutil_output = """
    <dictionary> {
      HTTPEnable : 1
      HTTPPort : 18080
      HTTPProxy : 127.0.0.1
      SOCKSEnable : 1
      SOCKSPort : 19080
      SOCKSProxy : 192.0.2.20
    }
    """

    resolved = telegram_proxy.resolve_telegram_proxy(
        {"TELEGRAM_PROXY": "system"},
        system_proxy_reader=lambda: scutil_output,
    )

    assert resolved.url == "socks5://192.0.2.20:19080"
    assert resolved.source == "macOS SOCKS5"
    assert resolved.is_socks5 is True


def test_empty_telegram_proxy_means_direct_and_ignores_terminal_proxy() -> None:
    resolved = telegram_proxy.resolve_telegram_proxy(
        {
            "TELEGRAM_PROXY": "",
            "https_proxy": "http://127.0.0.1:18080",
            "HTTP_PROXY": "http://127.0.0.1:18080",
        }
    )

    assert resolved.url is None
    assert resolved.source is None
    assert resolved.is_socks5 is False


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1:19080",
        "socks5://127.0.0.1",
        "ftp://127.0.0.1:19080",
    ],
)
def test_invalid_explicit_proxy_fails_closed(value: str) -> None:
    with pytest.raises(telegram_proxy.TelegramProxyConfigError):
        telegram_proxy.resolve_telegram_proxy({"TELEGRAM_PROXY": value})


def test_system_proxy_without_enabled_socks_fails_closed() -> None:
    with pytest.raises(telegram_proxy.TelegramProxyConfigError, match="SOCKS5"):
        telegram_proxy.resolve_telegram_proxy(
            {"TELEGRAM_PROXY": "system"},
            system_proxy_reader=lambda: "<dictionary> { SOCKSEnable : 0 }",
        )


def test_install_socks5_dns_override_uses_protected_ipv4() -> None:
    async def scenario() -> None:
        async def fake_resolve(_proxy_url: str) -> str:
            return "149.154.166.110"

        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        try:
            resolved_ip = await telegram_proxy.install_telegram_api_dns_override(
                "socks5://127.0.0.1:19080",
                resolver=fake_resolve,
            )
            infos = await loop.getaddrinfo(
                telegram_proxy.TELEGRAM_API_HOST,
                443,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        finally:
            loop.getaddrinfo = original

        assert resolved_ip == "149.154.166.110"
        assert infos == [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("149.154.166.110", 443),
            )
        ]

    asyncio.run(scenario())


def test_non_socks_proxy_does_not_install_dns_override() -> None:
    async def scenario() -> None:
        called = False

        async def fake_resolve(_proxy_url: str) -> str:
            nonlocal called
            called = True
            return "149.154.166.110"

        resolved_ip = await telegram_proxy.install_telegram_api_dns_override(
            "http://127.0.0.1:18080",
            resolver=fake_resolve,
        )

        assert resolved_ip is None
        assert called is False

    asyncio.run(scenario())


def test_configure_aiogram_session_uses_local_dns_for_socks5() -> None:
    class DummySession:
        def __init__(self) -> None:
            self._connector_init: dict[str, object] = {}

    session = DummySession()

    telegram_proxy.configure_aiogram_session(session, "socks5://127.0.0.1:19080")

    assert session._connector_init["family"] == socket.AF_INET
    assert session._connector_init["ttl_dns_cache"] == 60
    assert session._connector_init["rdns"] is False


def test_master_ignores_terminal_proxy_when_telegram_proxy_is_empty(monkeypatch) -> None:
    import master

    monkeypatch.setenv("TELEGRAM_PROXY", "")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:18080")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:18080")

    assert master._detect_proxy() == (None, None, None)


def test_worker_ignores_terminal_proxy_when_telegram_proxy_is_empty(monkeypatch) -> None:
    import bot

    monkeypatch.setenv("TELEGRAM_PROXY", "")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:18080")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:18080")

    assert bot._detect_proxy() == (None, None, None)


def test_worker_passes_proxy_credentials_to_socks_connector(monkeypatch) -> None:
    import bot

    monkeypatch.setenv(
        "TELEGRAM_PROXY",
        "socks5://proxy-user:proxy-password@127.0.0.1:19080",
    )
    monkeypatch.setattr(bot, "BOT_TOKEN", "123456:TEST_TOKEN")

    telegram_bot = bot.build_bot()

    assert telegram_bot.session._connector_init["username"] == "proxy-user"
    assert telegram_bot.session._connector_init["password"] == "proxy-password"
    assert telegram_bot.session._connector_init["port"] == 19080


def test_init_command_accepts_user_selected_telegram_proxy() -> None:
    from vibego_cli.main import build_parser

    args = build_parser().parse_args(
        ["init", "--telegram-proxy", "socks5://192.0.2.30:19080"]
    )

    assert args.telegram_proxy == "socks5://192.0.2.30:19080"
