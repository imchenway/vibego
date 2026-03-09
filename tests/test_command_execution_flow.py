import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot
from command_center import CommandDefinition


class _StubCommandService:
    """记录命令执行历史调用，避免依赖真实数据库。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record_history(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(id=321)


class _DummyReplyMessage:
    """仅保留命令执行链路需要的 chat 上下文。"""

    def __init__(self, chat_id: int = 42) -> None:
        self.chat = SimpleNamespace(id=chat_id)


class _DummyBot:
    """记录图片/文件发送顺序，并可按需制造失败。"""

    def __init__(self, events: list[tuple], *, fail_photo: bool = False) -> None:
        self.events = events
        self.fail_photo = fail_photo

    async def send_photo(self, chat_id: int, photo, caption: str | None = None):
        self.events.append(("photo", chat_id, caption, type(photo).__name__))
        if self.fail_photo:
            raise RuntimeError("photo send failed")

    async def send_document(self, chat_id: int, document, caption: str | None = None, **kwargs):
        self.events.append(("document", chat_id, caption, type(document).__name__, kwargs))


def _build_preview_command() -> CommandDefinition:
    """构造最小可执行的通用预览命令对象。"""

    return CommandDefinition(
        id=15,
        project_slug="__global__",
        scope="global",
        name=bot.WX_PREVIEW_COMMAND_NAME,
        title="生成微信开发预览二维码",
        command='echo "preview"',
        description="",
        timeout=600,
        enabled=True,
        aliases=(),
    )


@pytest.mark.asyncio
async def test_execute_command_sends_summary_before_qr_photo(monkeypatch, tmp_path: Path):
    """摘要消息应先发送，二维码图片应作为最后一条 Telegram 消息发送。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()
    photo_path = tmp_path / "wx-preview.jpg"
    photo_path.write_bytes(b"fake qr")

    async def fake_run_shell_command(command: str, timeout: int):
        return (
            0,
            "\n".join(
                [
                    "[完成] 预览二维码已生成",
                    f"TG_PHOTO_FILE: {photo_path}",
                ]
            ),
            "",
            1.23,
        )

    async def fake_answer_with_markdown(message, text: str, *, reply_markup=None):
        kind = "progress" if "命令执行中" in text else "summary"
        events.append((kind, text, reply_markup))
        return SimpleNamespace(message_id=100, chat=message.chat)

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", fake_answer_with_markdown)
    monkeypatch.setattr(bot, "current_bot", lambda: _DummyBot(events))

    await bot._execute_command_definition(
        command=_build_preview_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    assert [item[0] for item in events] == ["progress", "summary", "photo"]
    assert "二维码图片已发送" not in events[1][1]


@pytest.mark.asyncio
async def test_execute_command_falls_back_to_document_when_photo_send_fails(monkeypatch, tmp_path: Path):
    """图片发送失败时应在摘要之后降级为文件发送，避免二维码彻底丢失。"""

    events: list[tuple] = []
    reply_message = _DummyReplyMessage()
    service = _StubCommandService()
    photo_path = tmp_path / "wx-preview.jpg"
    photo_path.write_bytes(b"fake qr")

    async def fake_run_shell_command(command: str, timeout: int):
        return (
            0,
            "\n".join(
                [
                    "[完成] 预览二维码已生成",
                    f"TG_PHOTO_FILE: {photo_path}",
                ]
            ),
            "",
            1.23,
        )

    async def fake_answer_with_markdown(message, text: str, *, reply_markup=None):
        kind = "progress" if "命令执行中" in text else "summary"
        events.append((kind, text, reply_markup))
        return SimpleNamespace(message_id=101, chat=message.chat)

    monkeypatch.setattr(bot, "_run_shell_command", fake_run_shell_command)
    monkeypatch.setattr(bot, "_answer_with_markdown", fake_answer_with_markdown)
    monkeypatch.setattr(bot, "current_bot", lambda: _DummyBot(events, fail_photo=True))

    await bot._execute_command_definition(
        command=_build_preview_command(),
        reply_message=reply_message,
        trigger="按钮",
        actor_user=None,
        service=service,
        history_detail_prefix=bot.COMMAND_HISTORY_DETAIL_GLOBAL_PREFIX,
        fsm_state=None,
    )

    assert [item[0] for item in events] == ["progress", "summary", "photo", "document"]
    assert "降级为文件" in (events[-1][2] or "")
