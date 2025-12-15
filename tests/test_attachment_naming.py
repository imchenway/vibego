import re
from datetime import datetime

import bot


def test_build_obfuscated_filename_hides_source_name():
    fixed_now = datetime(2025, 12, 15, 10, 0, 0, 123000, tzinfo=bot.UTC)
    result = bot._build_obfuscated_filename(
        "photo_AQADTAtrGylIAVZ9.jpg",
        "image/jpeg",
        salt="salt",
        now=fixed_now,
        monotonic_ns=123456789,
    )

    assert result.endswith(".jpg")
    assert result.startswith("20251215_100000123")
    assert "photo_AQADTAtrGylIAVZ9" not in result
    assert re.fullmatch(r"20251215_100000123-[0-9a-f]{12}\.jpg", result)


def test_build_obfuscated_filename_sanitizes_extension():
    fixed_now = datetime(2025, 12, 15, 10, 0, 0, 0, tzinfo=bot.UTC)
    result = bot._build_obfuscated_filename(
        "weird name$$$.tar.gz",
        None,
        salt="salt",
        now=fixed_now,
        monotonic_ns=1,
    )

    assert result.startswith("20251215_100000000-")
    assert result.endswith(".gz")
