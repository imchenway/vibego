import os
import sys
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import ATTACHMENT_USAGE_HINT, TelegramSavedAttachment, _build_prompt_with_attachments


def _make_attachment(relative: str) -> TelegramSavedAttachment:
    """Construct a reusable attachment fixture for tests."""

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
    assert first_line == "Attachment list (files located under project workspace ./data/telegram/project/date/):"
    assert ATTACHMENT_USAGE_HINT in prompt


def test_prompt_handles_absolute_path_directory_prefix():
    prompt = _build_prompt_with_attachments(
        "",
        [_make_attachment("/var/tmp/attachments/photo.jpg")],
    )

    first_line = prompt.splitlines()[0]
    assert first_line == "Attachment list (files located under project workspace /var/tmp/attachments/):"


def test_prompt_falls_back_when_directory_unknown():
    prompt = _build_prompt_with_attachments(
        "",
        [_make_attachment("photo.jpg")],
    )

    first_line = prompt.splitlines()[0]
    assert first_line == "Attachment list (files reside within the project workspace):"
    assert ATTACHMENT_USAGE_HINT in prompt
