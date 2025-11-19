import os

os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

import bot


def test_tail_lines_returns_last_n_lines():
    source = "\n".join(str(i) for i in range(1, 21))
    result = bot._tail_lines(source, 10)
    assert result == "\n".join(str(i) for i in range(11, 21))


def test_tail_lines_handles_short_input():
    source = "only\nthree\nlines"
    result = bot._tail_lines(source, 10)
    assert result == source


def test_tail_lines_strips_trailing_spaces():
    source = "line1  \nline2  "
    result = bot._tail_lines(source, 1)
    assert result == "line2"
