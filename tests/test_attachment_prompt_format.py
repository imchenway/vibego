import os
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

from bot import TelegramSavedAttachment, _build_prompt_with_attachments


def _make_attachment(relative: str) -> TelegramSavedAttachment:
    """构造测试附件，方便复用。"""

    return TelegramSavedAttachment(
        kind="photo",
        display_name="photo.jpg",
        mime_type="image/jpeg",
        absolute_path=Path("/tmp/photo.jpg"),
        relative_path=relative,
    )


def test_prompt_includes_relative_directory_prefix():
    prompt = _build_prompt_with_attachments(
        None,
        [_make_attachment("./data/telegram/project/date/photo.jpg")],
    )

    first_line = prompt.splitlines()[0]
    assert "附件列表" in first_line
    assert "文件位于项目工作目录（./data/telegram/project/date/），可直接读取" in first_line


def test_prompt_handles_absolute_path_directory_prefix():
    prompt = _build_prompt_with_attachments(
        "",
        [_make_attachment("/var/tmp/attachments/photo.jpg")],
    )

    first_line = prompt.splitlines()[0]
    assert "文件位于项目工作目录（/var/tmp/attachments/），可直接读取" in first_line


def test_prompt_falls_back_when_directory_unknown():
    prompt = _build_prompt_with_attachments(
        "",
        [_make_attachment("photo.jpg")],
    )

    first_line = prompt.splitlines()[0]
    assert "文件位于项目工作目录，可直接读取" in first_line
    assert "文件位于项目工作目录（）" not in first_line
