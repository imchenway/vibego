"""验证 Markdown 降级为纯文本时的反转义逻辑。"""

from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods.base import TelegramMethod

import bot


@pytest.mark.asyncio()
async def test_markdown_guard_plain_fallback_preserves_original() -> None:
    """确保纯文本回退时返回原始内容（含必要转义）。"""

    delivered: list[str] = []

    class _DummyMethod(TelegramMethod):
        """构造最小化的 TelegramMethod 以便创建异常对象。"""

        __returning__ = bool

        @property
        def __api_method__(self) -> str:
            return "testMethod"

        def build_response(self, data, bot):  # pragma: no cover - 测试不会触发
            return True

        def build_request(self, bot):  # pragma: no cover - 测试不会触发
            return "testMethod", {}

    async def _failing_sender(_: str) -> None:
        raise TelegramBadRequest(_DummyMethod(), "Bad Request: can't parse entities")

    async def _raw_sender(payload: str) -> None:
        delivered.append(payload)

    original = "测试 \\_Markdown\\_ 转义"
    result = await bot._send_with_markdown_guard(original, _failing_sender, raw_sender=_raw_sender)

    assert delivered and delivered[0] == original
    assert result == original


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "original",
    [
        "条目 A\\)",
        "任务编码：/TASK\\_0022",
        "配置文件 config\\.json",
        "版本号 3\\.11\\.4",
        "保留连字符 use\\-case",
        "强调 \\*重要\\*",
        "变量 \\_value\\_",
        "选项 A\\|B",
        "集合 \\{a, b\\}",
        "说明 test\\.",
    ],
)
async def test_markdown_guard_plain_fallback_force_unescape(
    original: str,
) -> None:
    """验证纯文本回退会直接返回原始字符串。"""

    delivered: list[str] = []

    class _DummyMethod(TelegramMethod):
        __returning__ = bool

        @property
        def __api_method__(self) -> str:
            return "testMethod"

        def build_response(self, data, bot):  # pragma: no cover - 测试不会触发
            return True

        def build_request(self, bot):  # pragma: no cover - 测试不会触发
            return "testMethod", {}

    async def _failing_sender(_: str) -> None:
        raise TelegramBadRequest(_DummyMethod(), "Bad Request: can't parse entities")

    async def _raw_sender(payload: str) -> None:
        delivered.append(payload)

    result = await bot._send_with_markdown_guard(original, _failing_sender, raw_sender=_raw_sender)

    assert delivered and delivered[0] == original
    assert result == original
