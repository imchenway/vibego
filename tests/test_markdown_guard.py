"""Verify anti-escaping logic when downgrading Markdown to plain text."""

from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods.base import TelegramMethod

import bot


@pytest.mark.asyncio()
async def test_markdown_guard_plain_fallback_preserves_original() -> None:
    """Ensure that plain text fallback returns the original content (with necessary escaping)."""

    delivered: list[str] = []

    class _DummyMethod(TelegramMethod):
        """Construct a minimal TelegramMethod for creating exception objects."""

        __returning__ = bool

        @property
        def __api_method__(self) -> str:
            return "testMethod"

        def build_response(self, data, bot):  # pragma: no cover - Test won't trigger
            return True

        def build_request(self, bot):  # pragma: no cover - Test won't trigger
            return "testMethod", {}

    async def _failing_sender(_: str) -> None:
        raise TelegramBadRequest(_DummyMethod(), "Bad Request: can't parse entities")

    async def _raw_sender(payload: str) -> None:
        delivered.append(payload)

    original = "test \\_Markdown\\_ escape"
    result = await bot._send_with_markdown_guard(original, _failing_sender, raw_sender=_raw_sender)

    assert delivered and delivered[0] == original
    assert result == original


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "original",
    [
        "Entry A\\)",
        "Task code:/TASK\\_0022",
        "Configuration file config\\.json",
        "Version number 3\\.11\\.4",
        "Keep hyphens use\\-case",
        "emphasize \\*important\\*",
        "Variable \\_value\\_",
        "Option A\\|B",
        "gather \\{a, b\\}",
        "Description test\\.",
    ],
)
async def test_markdown_guard_plain_fallback_force_unescape(
    original: str,
) -> None:
    """Validating plain text fallback returns the original string directly."""

    delivered: list[str] = []

    class _DummyMethod(TelegramMethod):
        __returning__ = bool

        @property
        def __api_method__(self) -> str:
            return "testMethod"

        def build_response(self, data, bot):  # pragma: no cover - Test won't trigger
            return True

        def build_request(self, bot):  # pragma: no cover - Test won't trigger
            return "testMethod", {}

    async def _failing_sender(_: str) -> None:
        raise TelegramBadRequest(_DummyMethod(), "Bad Request: can't parse entities")

    async def _raw_sender(payload: str) -> None:
        delivered.append(payload)

    result = await bot._send_with_markdown_guard(original, _failing_sender, raw_sender=_raw_sender)

    assert delivered and delivered[0] == original
    assert result == original
